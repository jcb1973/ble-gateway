import asyncio
import configparser
import json
import logging
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

last_alert_time = 0


# --- Database ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            temperature_c REAL NOT NULL,
            humidity_pct REAL NOT NULL,
            rssi INTEGER
        )
    """)
    conn.commit()
    return conn


def store_reading(conn, temp_c, humidity, rssi):
    ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO readings (timestamp, temperature_c, humidity_pct, rssi) "
        "VALUES (?, ?, ?, ?)",
        (ts, temp_c, humidity, rssi),
    )
    conn.commit()
    log.info(f"Stored: {temp_c}°C, {humidity}%, RSSI {rssi}")


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
        response = urlopen(req)
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
def check_alerts(temp_c, humidity, cfg):
    global last_alert_time
    now = time.time()
    if humidity < cfg["humidity_min"] and (now - last_alert_time) > cfg["cooldown"]:
        msg = (
            f"Low humidity alert! {humidity}% "
            f"(threshold: {cfg['humidity_min']}%). "
            f"Temp: {temp_c}°C"
        )
        if send_sms(msg, cfg["twilio_sid"], cfg["twilio_token"],
                     cfg["twilio_msg_sid"], cfg["to_phone"]):
            last_alert_time = now


# --- BLE scanning ---
async def take_reading(target_mac):
    from bleak import BleakScanner
    result = {}

    def callback(device, ad_data):
        if device.address != target_mac:
            return
        for cid, payload in ad_data.manufacturer_data.items():
            decoded = decode_ht1(cid, payload)
            if decoded:
                result["temp_c"] = decoded[0]
                result["humidity"] = decoded[1]
                result["rssi"] = ad_data.rssi

    scan_duration = 30
    scanner = BleakScanner(detection_callback=callback)
    await scanner.start()
    await asyncio.sleep(scan_duration)
    await scanner.stop()
    return result if result else None


def load_config():
    config = configparser.ConfigParser()
    config.read(BASE_DIR / "config.ini")
    return {
        "target_mac": config.get("sensorpush", "mac"),
        "humidity_min": config.getfloat("alerts", "humidity_min"),
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
    log.info("BLE Gateway starting...")
    log.info(f"Target: {cfg['target_mac']}")
    log.info(f"Humidity alert threshold: {cfg['humidity_min']}%")
    log.info(f"Sample interval: {cfg['interval']}s")

    conn = init_db()

    while True:
        t0 = time.monotonic()
        try:
            reading = await take_reading(cfg["target_mac"])
            if reading:
                store_reading(conn, reading["temp_c"], reading["humidity"], reading["rssi"])
                check_alerts(reading["temp_c"], reading["humidity"], cfg)
            else:
                log.warning("No reading from sensor")
        except Exception as e:
            log.error(f"Error: {e}")

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
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT timestamp, temperature_c, humidity_pct, rssi "
        "FROM readings ORDER BY id DESC LIMIT 2"
    ).fetchall()
    conn.close()
    if not rows:
        print("{}", file=sys.stderr)
        sys.exit(1)
    cur = rows[0]
    prev = rows[1] if len(rows) > 1 else None
    print(json.dumps({
        "timestamp": cur[0],
        "temperature_c": cur[1],
        "humidity_pct": cur[2],
        "rssi": cur[3],
        "temp_trend": _trend(cur[1], prev[1] if prev else None),
        "humidity_trend": _trend(cur[2], prev[2] if prev else None),
    }))


if __name__ == "__main__":
    if "--json" in sys.argv:
        dump_latest()
    else:
        asyncio.run(main())
