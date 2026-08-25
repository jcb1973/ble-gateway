import asyncio
import configparser
import json
import logging
import socket
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
import base64

# --- Setup ---
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "sensordata.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ble-gateway")

last_alert_times = {}  # keyed by (device name, alert type)


# --- Database ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    # WAL + a busy timeout. Once a second gateway's rows are synced in, an
    # importer writes to this file alongside the scanner loop. The default
    # rollback journal locks the whole db for each write, which surfaces as
    # intermittent "database is locked"; WAL lets one writer and the readers
    # (dump_latest, humidity_report.sh) proceed concurrently. WAL is a
    # property of the file, so this sticks once set.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            device_name TEXT NOT NULL DEFAULT '',
            temperature_c REAL NOT NULL,
            humidity_pct REAL NOT NULL,
            rssi INTEGER,
            source TEXT NOT NULL DEFAULT ''
        )
    """)
    # Migrate old schema: add device_name if missing
    try:
        conn.execute("ALTER TABLE readings ADD COLUMN device_name TEXT NOT NULL DEFAULT ''")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    # Migrate: which gateway heard the advert. Every row that predates this
    # column was logged before multi-gateway scanning existed and was therefore
    # heard by jcb-pi, so that is the correct backfill. (A brand-new db gets the
    # column from CREATE TABLE above and this ALTER is a no-op.)
    #
    # NOT NULL is load-bearing: sqlite treats NULLs as DISTINCT in a UNIQUE
    # index, so a nullable source would let duplicate imports slip straight
    # through the constraint below while appearing to be protected.
    try:
        conn.execute("ALTER TABLE readings ADD COLUMN source TEXT NOT NULL DEFAULT 'jcb-pi'")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    # One observation == one (gateway, sensor, instant). This is what makes
    # re-importing a sync batch a no-op, which in turn demotes the sync
    # watermark from a correctness dependency to a mere optimisation.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS readings_obs "
        "ON readings(source, device_name, timestamp)"
    )
    # readings_obs leads with `source`, so it does NOT serve the
    # "WHERE device_name = ? ORDER BY timestamp" access pattern that
    # dump_latest and humidity_report.sh use -- that needs its own index.
    # (The table carried no indexes at all before this.)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS readings_device_time "
        "ON readings(device_name, timestamp)"
    )
    conn.commit()
    return conn


def store_reading(conn, source, device_name, temp_c, humidity, rssi):
    ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO readings "
        "(timestamp, device_name, temperature_c, humidity_pct, rssi, source) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (ts, device_name, temp_c, humidity, rssi, source),
    )
    conn.commit()
    log.info(f"[{device_name}] Stored: {temp_c}°C, {humidity}%, RSSI {rssi}")


# --- SensorPush HT1 decode ---
def decode_ht1(company_id, payload):
    cid_bytes = int(company_id).to_bytes(2, byteorder="little")
    data = cid_bytes + payload
    if len(data) < 4:
        return None
    device_type = (data[3] & 0x7C) >> 2
    if device_type != 1:
        return None
    hum_raw = (data[0] & 0xFF) + ((data[1] & 0x0F) << 8)
    temp_raw = (data[1] >> 4) + ((data[2] & 0xFF) << 4) + ((data[3] & 0x03) << 12)
    humidity = max(0.0, min(100.0, -6.0 + 125.0 * (hum_raw / 4096.0)))
    temp_c = -46.85 + 175.72 * (temp_raw / 16384.0)
    return round(temp_c, 2), round(humidity, 2)


# --- Twilio SMS ---
def send_sms(message, twilio_sid, twilio_token, twilio_msg_sid, to_phone):
    url = (
        f"https://api.twilio.com/2010-04-01/"
        f"Accounts/{twilio_sid}/Messages.json"
    )
    data = urlencode({
        "MessagingServiceSid": twilio_msg_sid,
        "To": to_phone,
        "Body": message,
    }).encode()
    auth = base64.b64encode(f"{twilio_sid}:{twilio_token}".encode()).decode()
    req = Request(url, data=data, headers={"Authorization": f"Basic {auth}"})
    try:
        response = urlopen(req, timeout=10)
        body = response.read().decode()
        log.info(f"SMS sent: {message}")
        log.info(f"Twilio response: {body}")
        return True
    except Exception as e:
        content = ""
        if hasattr(e, "read"):
            content = e.read().decode()
        log.error(f"SMS failed: {e} {content}")
        return False

# --- Alert check ---
def check_alerts(device_name, temp_c, humidity, cfg):
    now = time.time()
    checks = [
        ("humidity_low", humidity < cfg["humidity_min"],
         f"Low humidity alert! {humidity}% (threshold: {cfg['humidity_min']}%)"),
        ("humidity_high", humidity > cfg["humidity_max"],
         f"High humidity alert! {humidity}% (threshold: {cfg['humidity_max']}%)"),
        ("temp_low", temp_c < cfg["temp_min"],
         f"Low temperature alert! {temp_c}°C (threshold: {cfg['temp_min']}°C)"),
        ("temp_high", temp_c > cfg["temp_max"],
         f"High temperature alert! {temp_c}°C (threshold: {cfg['temp_max']}°C)"),
    ]
    for alert_type, triggered, detail in checks:
        if not triggered:
            continue
        key = (device_name, alert_type)
        last = last_alert_times.get(key, 0)
        if (now - last) <= cfg["cooldown"]:
            continue
        msg = f"[{device_name}] {detail}. Temp: {temp_c}°C, Humidity: {humidity}%"
        if send_sms(msg, cfg["twilio_sid"], cfg["twilio_token"],
                    cfg["twilio_msg_sid"], cfg["to_phone"]):
            last_alert_times[key] = now


# --- BLE scanning ---
async def take_readings(devices):
    from bleak import BleakScanner
    mac_to_name = {d["mac"]: d["name"] for d in devices}
    results = {}

    def callback(device, ad_data):
        name = mac_to_name.get(device.address)
        if name is None:
            return
        for cid, payload in ad_data.manufacturer_data.items():
            decoded = decode_ht1(cid, payload)
            if decoded:
                results[name] = {
                    "temp_c": decoded[0],
                    "humidity": decoded[1],
                    "rssi": ad_data.rssi,
                }

    scan_duration = 30
    scanner = BleakScanner(detection_callback=callback)
    await scanner.start()
    try:
        await asyncio.sleep(scan_duration)
    finally:
        await scanner.stop()
    return results


def load_config():
    config = configparser.ConfigParser()
    config.read(BASE_DIR / "config.ini")
    devices = []
    for section in config.sections():
        if section.startswith("device:"):
            name = section.split(":", 1)[1]
            alert = config.getboolean(section, "alert", fallback=True)
            devices.append({"name": name, "mac": config.get(section, "mac"), "alert": alert})
    return {
        "devices": devices,
        # Which gateway this process is. Stamped on every row so observations
        # from several scanning Pis can share one db (see readings_obs).
        "source": config.get("gateway", "name", fallback=socket.gethostname()),
        "humidity_min": config.getfloat("alerts", "humidity_min"),
        "humidity_max": config.getfloat("alerts", "humidity_max"),
        "temp_min": config.getfloat("alerts", "temp_min"),
        "temp_max": config.getfloat("alerts", "temp_max"),
        "cooldown": config.getint("alerts", "alert_cooldown_minutes") * 60,
        "sender_id": config.get("alerts", "sender_id"),
        "interval": config.getint("sampling", "interval_seconds"),
        "twilio_sid": config.get("twilio", "account_sid"),
        "twilio_token": config.get("twilio", "auth_token"),
        "twilio_msg_sid": config.get("twilio", "messaging_service_sid"),
        "to_phone": config.get("twilio", "to_phone"),
    }


async def main():
    cfg = load_config()
    log.info(f"BLE Gateway starting... (gateway: {cfg['source']})")
    for d in cfg["devices"]:
        alert_status = "yes" if d.get("alert", True) else "no"
        log.info(f"Device: {d['name']} ({d['mac']}) alert={alert_status}")
    log.info(
        f"Alert thresholds: humidity {cfg['humidity_min']}–{cfg['humidity_max']}%, "
        f"temperature {cfg['temp_min']}–{cfg['temp_max']}°C"
    )
    log.info(f"Sample interval: {cfg['interval']}s")

    conn = init_db()

    while True:
        t0 = time.monotonic()
        try:
            readings = await take_readings(cfg["devices"])
        except Exception:
            log.exception("BLE scan failed")
            readings = {}
        for d in cfg["devices"]:
            name = d["name"]
            if name not in readings:
                log.warning(f"[{name}] No reading from sensor")
                continue
            try:
                r = readings[name]
                store_reading(conn, cfg["source"], name, r["temp_c"],
                              r["humidity"], r["rssi"])
                if d.get("alert", True):
                    check_alerts(name, r["temp_c"], r["humidity"], cfg)
            except Exception:
                log.exception(f"[{name}] Failed to process reading")

        elapsed = time.monotonic() - t0
        await asyncio.sleep(max(0, cfg["interval"] - elapsed))


def _trend(current, previous):
    if previous is None:
        return "stable"
    diff = current - previous
    if diff > 0.1:
        return "rising"
    elif diff < -0.1:
        return "falling"
    return "stable"


def dump_latest():
    conn = init_db()
    # Get distinct device names
    devices = conn.execute(
        "SELECT DISTINCT device_name FROM readings"
    ).fetchall()
    if not devices:
        print("{}", file=sys.stderr)
        sys.exit(1)
    out = {}
    for (name,) in devices:
        # Newest observation of this sensor, whichever gateway heard it. With
        # several scanners this is the merge: freshest wins, so a sensor drops
        # off the dashboard only when NO gateway can hear it.
        # Order by timestamp, NOT by id. Rows pulled from a remote gateway are
        # assigned local ids in IMPORT order, so after that gateway has been
        # offline for a while its backlog lands with the highest ids while
        # carrying the oldest timestamps -- ordering by id would then serve a
        # stale reading as the current one. Timestamps are ISO-8601 UTC, so
        # lexicographic order is chronological order.
        cur = conn.execute(
            "SELECT timestamp, temperature_c, humidity_pct, rssi, source "
            "FROM readings WHERE device_name = ? ORDER BY timestamp DESC LIMIT 1",
            (name,),
        ).fetchone()
        if cur is None:
            continue
        # Trend has to compare like with like. Two gateways' rows interleave,
        # so the previous row by id can be another box's view of the SAME
        # instant -- differencing those measures the gap between gateways, not
        # change over time, and the arrow would flip on reporting order. Take
        # the previous row from the same gateway instead.
        prev = conn.execute(
            "SELECT temperature_c, humidity_pct FROM readings "
            "WHERE device_name = ? AND source = ? AND timestamp < ? "
            "ORDER BY timestamp DESC LIMIT 1",
            (name, cur[4], cur[0]),
        ).fetchone()
        out[name] = {
            "timestamp": cur[0],
            "temperature_c": cur[1],
            "humidity_pct": cur[2],
            "rssi": cur[3],
            "source": cur[4],
            "temp_trend": _trend(cur[1], prev[0] if prev else None),
            "humidity_trend": _trend(cur[2], prev[1] if prev else None),
        }
    conn.close()
    print(json.dumps(out))


if __name__ == "__main__":
    if "--json" in sys.argv:
        dump_latest()
    else:
        asyncio.run(main())
