# ble-gateway

A lightweight Bluetooth Low Energy gateway that monitors environmental conditions and sends SMS alerts when thresholds are breached. Built to run on a Raspberry Pi (or any Bluetooth-capable Linux/Mac).

## What it does

1. Scans for a SensorPush HT1 sensor via BLE
2. Reads temperature and humidity at configurable intervals
3. Logs every reading to a local SQLite database
4. Sends an SMS alert via Twilio if humidity drops below a threshold
5. Respects a cooldown period to avoid alert fatigue

## Why

Commercial IoT gateways are expensive and lock you into vendor clouds. This is a single Python script that does the job with hardware you already have.

## Architecture

```
SensorPush HT1  ──BLE──▶  Raspberry Pi (gateway.py)
                                │
                                ├──▶ SQLite (local time-series log)
                                │
                                ├──▶ Twilio SMS (threshold alerts)
                                │
                          cron + scp
                                ├──▶ status.json ──▶ Web server
                                                        │
                                                   index.html
                                                   (status page)
```

## Stack

- **Language:** Python 3.8+
- **BLE:** [Bleak](https://github.com/hbldh/bleak)
- **Alerts:** Twilio (REST API)
- **Storage:** SQLite
- **Hardware:** Any Bluetooth-capable device — Raspberry Pi, Mac, Linux box

## Setup

```bash
pip install bleak
cp config.ini.example config.ini  # edit with your settings
python gateway.py
```

## JSON export & web status

`gateway.py --json` dumps the latest reading from SQLite as JSON:

```json
{
  "timestamp": "2026-04-02T14:30:00",
  "temperature_c": 21.5,
  "humidity_pct": 45.2,
  "rssi": -67
}
```

`upload-status.sh` exports this JSON and uploads it to a web server via `scp`:

```bash
#!/bin/sh
cd "$(dirname "$0")"
/home/youruser/ble-gateway/bin/python gateway.py --json > status.json && scp status.json user@yourserver:/var/www/ble-data/status.json
```

Run it on a schedule with cron. For example, every 5 minutes:

```
crontab -e
```

```
*/5 * * * * /home/youruser/ble-gateway/upload-status.sh >> /home/youruser/ble-gateway/cron.log 2>&1
```

On the server, `index.html` fetches `status.json` and displays the current sensor state. Serve both files from the same directory (e.g. `/var/www/html/`).

## Running as a service

Create a systemd unit file:

```bash
sudo nano /etc/systemd/system/ble-gateway.service
```

```ini
[Unit]
Description=BLE Gateway
After=bluetooth.target

[Service]
ExecStart=/home/youruser/ble-gateway/bin/python /home/youruser/ble-gateway/gateway.py
WorkingDirectory=/home/youruser/ble-gateway
Restart=on-failure
RestartSec=10
User=youruser

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable ble-gateway
sudo systemctl start ble-gateway
```

Useful commands:

```bash
sudo systemctl status ble-gateway   # check status
sudo systemctl restart ble-gateway  # restart after config changes
journalctl -u ble-gateway -f        # follow logs
```

## Configuration

Edit `config.ini` (see `config.ini.example`):

```ini
[sensorpush]
mac = AA:BB:CC:DD:EE:FF

[alerts]
humidity_min = 30.0
alert_cooldown_minutes = 60
sender_id = BLE Gateway

[sampling]
interval_seconds = 300

[twilio]
account_sid = ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
auth_token = your_auth_token_here
messaging_service_sid = MGxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
to_phone = +1234567890
```
