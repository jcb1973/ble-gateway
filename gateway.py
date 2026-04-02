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

from bleak import BleakScanner

# --- Setup ---
BASE_DIR = Path(__file__).parent
CONFIG = configparser.ConfigParser()
CONFIG.read(BASE_DIR / "config.ini")

DB_PATH = BASE_DIR / "sensordata.db"
TARGET_MAC = CONFIG.get("sensorpush", "mac")
HUMIDITY_MIN = CONFIG.getfloat("alerts", "humidity_min")
COOLDOWN = CONFIG.getint("alerts", "alert_cooldown_minutes") * 60
SENDER_ID = CONFIG.get("alerts", "sender_id")
INTERVAL = CONFIG.getint("sampling", "interval_seconds")

TWILIO_SID = CONFIG.get("twilio", "account_sid")
TWILIO_TOKEN = CONFIG.get("twilio", "auth_token")
TWILIO_MSG_SID = CONFIG.get("twilio", "messaging_service_sid")
TO_PHONE = CONFIG.get("twilio", "to_phone")

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
def send_sms(message):
    url = (
        f"https://api.twilio.com/2010-04-01/"
        f"Accounts/{TWILIO_SID}/Messages.json"
    )
    data = urlencode({
        "MessagingServiceSid": TWILIO_MSG_SID,
        "To": TO_PHONE,
        "Body": message,
    }).encode()
    auth = base64.b64encode(f"{TWILIO_SID}:{TWILIO_TOKEN}".encode()).decode()
    req = Request(url, data=data, headers={"Authorization": f"Basic {auth}"})
    try:
        response = urlopen(req)
        body = response.read().decode()
        log.info(f"SMS sent: {message}")
        log.info(f"Twilio response: {body}")
    except Exception as e:
        content = ""
        if hasattr(e, "read"):
            content = e.read().decode()
        log.error(f"SMS failed: {e} {content}")

# --- Alert check ---
def check_alerts(temp_c, humidity):
    global last_alert_time
    now = time.time()
    if humidity < HUMIDITY_MIN and (now - last_alert_time) > COOLDOWN:
        msg = (
            f"Low humidity alert! {humidity}% "
            f"(threshold: {HUMIDITY_MIN}%). "
            f"Temp: {temp_c}°C"
        )
        send_sms(msg)
        last_alert_time = now


# --- BLE scanning ---
async def take_reading():
    result = {}

    def callback(device, ad_data):
        if device.address != TARGET_MAC:
            return
        for cid, payload in ad_data.manufacturer_data.items():
            decoded = decode_ht1(cid, payload)
            if decoded:
                result["temp_c"] = decoded[0]
                result["humidity"] = decoded[1]
                result["rssi"] = ad_data.rssi

    scanner = BleakScanner(detection_callback=callback)
    await scanner.start()
    await asyncio.sleep(30)  # scan for 10 seconds
    await scanner.stop()
    return result if result else None


async def main():
    log.info("BLE Gateway starting...")
    log.info(f"Target: {TARGET_MAC}")
    log.info(f"Humidity alert threshold: {HUMIDITY_MIN}%")
    log.info(f"Sample interval: {INTERVAL}s")

    conn = init_db()

    while True:
        try:
            reading = await take_reading()
            if reading:
                store_reading(conn, reading["temp_c"], reading["humidity"], reading["rssi"])
                check_alerts(reading["temp_c"], reading["humidity"])
            else:
                log.warning("No reading from sensor")
        except Exception as e:
            log.error(f"Error: {e}")

        await asyncio.sleep(INTERVAL)


def dump_latest():
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT timestamp, temperature_c, humidity_pct, rssi "
        "FROM readings ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        print("{}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps({
        "timestamp": row[0],
        "temperature_c": row[1],
        "humidity_pct": row[2],
        "rssi": row[3],
    }))


if __name__ == "__main__":
    if "--json" in sys.argv:
        dump_latest()
    else:
        asyncio.run(main())
