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
                                └──▶ Twilio SMS (threshold alerts)
```

## Stack

- **Language:** Python 3.8+
- **BLE:** [Bleak](https://github.com/hbldh/bleak)
- **Alerts:** Twilio SDK
- **Storage:** SQLite
- **Hardware:** Any Bluetooth-capable device — Raspberry Pi, Mac, Linux box

## Setup

```bash
pip install bleak twilio
cp config.ini.example config.ini  # edit with your settings
python gateway.py
```

## Configuration

Edit `config.ini`:

```ini
[sensor]
mac = AA:BB:CC:DD:EE:FF

[thresholds]
humidity_min = 30.0

[alerts]
cooldown_minutes = 60

[sampling]
interval_seconds = 300

[twilio]
account_sid = ...
auth_token = ...
messaging_service_sid = ...
to_number = +1234567890
```
