# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## What this is

A lightweight Bluetooth Low Energy gateway monitoring environmental conditions via SensorPush HT1 sensors. Runs on jcb-pi (Raspberry Pi Zero 2W), logs to SQLite, sends SMS alerts when temperature/humidity breach thresholds. Also exposes a JSON status endpoint + web dashboard on jcblondon.

Includes a PicoClaw skill (`humidity-report`) to query the latest sensor state via Telegram.

## Running

```bash
# On Mac (development)
python3 -m venv .venv
.venv/bin/pip install bleak
cp .creds.example .creds  # edit with your device MACs + Twilio credentials
.venv/bin/python gateway.py

# On jcb-pi (after deploy)
cd ~/ble-gateway && git pull
./deploy-pi.sh
```

No build, no external services needed locally. Bleak (BLE library) and Twilio SDK are the only dependencies.

## Architecture

**Layers:**
- `gateway.py` — main scanner loop; connects to BLE devices, reads temperature/humidity, stores to SQLite, sends SMS alerts via Twilio
- `sensordata.db` — local time-series SQLite database (gitignored)
- `picoclaw/skills/humidity-report/` — Telegram skill to query latest reading and all-time stats
- `deploy-pi.sh` — Pi-side deploy script; pulls repo, restarts systemd service, copies skill to PicoClaw

**Secrets:**
- `.creds` (INI format, gitignored, mode 600) contains device MACs, Twilio credentials, alert thresholds
- Never commit `.creds`; use `.creds.example` as a template for new setups

**State files (gitignored):**
- `sensordata.db` — SQLite log of all readings per device
- `upload-status.log` — cron upload logs
- `.venv/` — Python virtual environment (PEP 668)

## Deploy (on the Pi)

```bash
ssh jcb1973@jcb-pi.local
cd ~/ble-gateway && git pull
./deploy-pi.sh  # pulls, restarts ble-gateway systemd service, refreshes picoclaw skill
```

The deploy script restarts the systemd service (defined in `/etc/systemd/system/ble-gateway.service` on the Pi). Test with:

```bash
sudo systemctl status ble-gateway
journalctl -u ble-gateway -f  # tail logs
```

## Cron & monitoring

- **Scanner:** runs as a systemd service (`ble-gateway.service`), not cron. Respawns on crash.
- **Status upload:** optional cron job (`*/5 * * * *` or similar) runs `./upload-status.sh` to export JSON to jcblondon's web server via scp
- **No staggering needed:** status upload is independent; if you add more cron jobs on jcb-pi, stagger them at 5-min intervals (see `infra/CONVENTIONS.md`)

## JSON export & web status

`gateway.py --json` dumps the latest reading from `sensordata.db`. The `upload-status.sh` script exports this and scps it to jcblondon for serving via Caddy. The web dashboard (`web/index.html`) polls it and displays current temperature/humidity.

## Testing locally (Mac)

Requires macOS to have Bluetooth hardware. Bleak runs on macOS; point `.creds` at your own sensors (if available) or test with mock data by stubbing the BLE scan loop.

## Known gotchas

- **PEP 668 on Debian:** Raspberry Pi OS locks system Python. Use `.venv` locally and on the Pi (deploy script doesn't explicitly create it, but the shebang in `gateway.py` uses system python — ensure Bleak is installed via `pip install --break-system-packages` OR set up `.venv` on the Pi manually if `sudo apt install python3-bleak` is unavailable).
- **Bluetooth permissions:** On Linux, user must be in the `bluetooth` group or run with `sudo`. On the Pi systemd unit, the `User=` directive handles this — no sudo needed inside the service.
- **MAC address format:** SensorPush device MACs are case-insensitive but must match Bleak's output exactly (usually lowercase, colon-separated).

## Related

- `infra/README.md` — jcb-pi machine setup and other services
- `picoclaw-setup` — Telegram bot framework hosting the humidity-report skill
- `sms-reminders` — Twilio integration patterns (similar alert architecture)
