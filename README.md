# ble-gateway

A lightweight Bluetooth Low Energy gateway that monitors environmental conditions and sends SMS alerts when thresholds are breached. Built to run on a Raspberry Pi (or any Bluetooth-capable Linux/Mac).

## What it does

1. Scans for one or more SensorPush HT1 sensors via BLE
2. Reads temperature and humidity at configurable intervals
3. Logs every reading to a local SQLite database
4. Sends an SMS alert via Twilio if humidity or temperature falls outside configured min/max thresholds
5. Respects a per-device, per-alert-type cooldown period to avoid alert fatigue

## Why

Commercial IoT gateways are expensive and lock you into vendor clouds. This is a single Python script that does the job with hardware you already have.

## Architecture

```
SensorPush HT1(s) ──BLE──▶ Raspberry Pi (gateway.py)
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
*/5 * * * * /home/youruser/ble-gateway/upload-status.sh >> /home/youruser/ble-gateway/upload-status.log 2>&1
```

On the server, `index.html` fetches `status.json` and displays the current sensor state. Serve both files from the same directory (e.g. `/var/www/html/`).

### Deploying web changes

To deploy changes to `web/index.html`, run `./deploy.sh` on the server. It pulls the latest commit and restarts the Caddy container — the restart is required because Caddy bind-mounts `index.html` as a single file, and `git pull`'s inode swap is otherwise invisible to the running container.

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
[device:Bedroom]
mac = AA:BB:CC:DD:EE:FF

[device:Living Room]
mac = 11:22:33:44:55:66

[alerts]
humidity_min = 30.0
humidity_max = 70.0
temp_min = 10.0
temp_max = 30.0
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

Add one `[device:Name]` section per sensor. All readings are stored in the same SQLite table, distinguished by `device_name`. Alerts fire independently per device and per alert type (humidity low/high, temperature low/high), each with its own cooldown.

## Backups

`backup-sensordata.sh.example` is a template for a nightly cron job that takes a consistent snapshot of `sensordata.db` (using `sqlite3 .backup`), gzips it, and `scp`s it to a remote host. The header of the file contains the cron lines for both the Pi (snapshot) and the remote (30-day prune).

Requires the `sqlite3` CLI on the Pi:

```bash
sudo apt install sqlite3
```

Copy the example, edit the paths/host, make it executable, and add the cron line.
