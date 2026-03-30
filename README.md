# BLE Gateway

A lightweight Python script that reads temperature and humidity from a SensorPush HT1 sensor via Bluetooth Low Energy, stores readings in SQLite, and sends SMS alerts via Twilio when humidity drops below a threshold — no expensive SensorPush gateway required.

## Requirements

- Python 3.8+
- A Bluetooth-capable machine (Raspberry Pi, Mac, Linux)
- SensorPush HT1 sensor
- Twilio account (for SMS alerts)

## Setup

Install dependencies:

```sh
pip install bleak
```

Create a `config.ini` in the project root:

```ini
[sensorpush]
mac = XX:XX:XX:XX:XX:XX

[alerts]
humidity_min = 30.0
alert_cooldown_minutes = 60
sender_id = SensorPush

[sampling]
interval_seconds = 300

[twilio]
account_sid = AC...
auth_token = ...
messaging_service_sid = MG...
to_phone = +1234567890
```

## Usage

```sh
python gateway.py
```

The script runs in a continuous loop — scanning for the sensor, logging readings to `sensordata.db`, and sending an SMS alert when humidity falls below the configured threshold.
