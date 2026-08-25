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

## Sensors (live roster)

The device roster lives only in the gitignored `config.ini` (section per
sensor, `[device:<name>]`), so it is recorded here too. As of **2026-08-25**:

| Name | What it is | `alert` |
|---|---|---|
| `d28` | Martin D-28 case | `yes` |
| `ambient` | Bedroom room humidity — **not** an instrument | `no` |
| `mandolin` | Mandolin case | `no` |

- **`ambient` was `d18`.** The D-18 was sold on 2026-08-25 and its sensor
  repurposed as bedroom ambient. Only the config section was renamed, so
  **all pre-rename history stays under `device_name = 'd18'`** in
  `sensordata.db` (33,848 rows, 2026-04-10 → 2026-08-25) — deliberately
  left in place as the guitar's record. Consequence: blestatus.jcb1973.dev
  shows a **frozen `d18` card** alongside the live `ambient` one, because
  `dump_latest()` emits every `DISTINCT device_name` ever seen. That card
  is expected, not a fault.
- **`d28` is the mobile one — gaps in its series are normal.** It travels with
  the guitar, so it drops out of BLE range for hours at a time (a ~19 h gap on
  2026-08-25 was just that). Don't diagnose a silent `d28` as a dead sensor or a
  broken gateway until you've checked that `ambient`/`mandolin` are also quiet.
  Note that `humidity_report.sh` prints `Current: <x>%` and its 🟢/🟡/🔴 verdict
  from the newest `d28` row **with no staleness check**, so during a range gap
  the report reads as current while quoting day-old data.
- **Thresholds are global, not per-device** (`[alerts]` in `config.ini`:
  40–60% RH, 15–28°C) and are tuned for instrument cases. `alert` is only
  a per-device on/off switch. So keep `ambient` at `alert = no` — a bedroom
  judged against guitar-case thresholds would just cry wolf. Per-device
  thresholds would need a `gateway.py` change (`check_alerts` reads the one
  global `cfg`).
- Renaming a sensor = edit the section header in `config.ini` on the Pi,
  then `sudo systemctl restart ble-gateway`. Confirm via
  `journalctl -u ble-gateway -n 10` — startup logs one `Device:` line each.

## Multi-gateway scanning

SensorPush HT1s are pure **advertisers** — `take_readings()` runs a passive
`BleakScanner` and decodes `ad_data.manufacturer_data`; it never connects. So
any number of Pis can hear the same sensor at once with zero contention, and
adding a scanner is purely additive coverage. (This would not work with a
connect-based sensor, where one central would hold the link exclusively.)

**Topology.** Remote scanners log locally; jcb-pi **pulls** with
`sync-gateways.sh` and merges into its own `sensordata.db`. Pull rather than
push so jcb-pi is the only process writing its own db, and so remote scanners
need no credentials for it. `REMOTES` in that script is a list — a third
scanner is one more entry.

**`source`** is stamped on every row from `[gateway] name` in `config.ini`
(default: hostname) and records **which radio heard the advert**. It is set at
observation time and carried through the sync verbatim, never assigned on
import.

**Duplicates.** Two kinds, only one of which is a problem:

- *Same sensor heard by two boxes* — **not** duplicates. Both decode the same
  payload so the values are identical; only RSSI differs, and that difference
  is the point (whichever box hears `d28` loudest says roughly where the
  guitar is). Both rows are kept; they are collapsed at read time.
- *Re-imported rows* — real duplicates, prevented by the UNIQUE index
  `readings_obs(source, device_name, timestamp)` plus `INSERT OR IGNORE`.
  **Correctness comes from the constraint, not the watermark**: re-importing is
  a no-op, so a sync that dies mid-import simply doesn't advance its watermark
  and re-sends harmlessly next run.

**Traps this design already stepped in — don't undo these:**

- **`source` must be `NOT NULL`.** SQLite treats NULLs as *distinct* in a
  UNIQUE index, so a nullable `source` would wave duplicate imports straight
  through a constraint that looks like it's protecting you.
- **Order by `timestamp`, never by `id`.** Imported rows get local ids in
  *import* order, so a gateway returning from an outage lands its backlog with
  the highest ids and the oldest timestamps. `dump_latest()` ordering by id
  would then serve a stale reading as current.
- **Trend must compare same-`source` rows.** Consecutive rows by time can be
  two boxes' views of the same instant; differencing those measures the gap
  between gateways, not change over time, and the arrow flips on reporting
  order.
- **Averages must bucket by time, not average rows.** A sensor heard by two
  boxes yields double the rows for the same instants, so a plain `AVG` weights
  periods by how many radios were listening rather than by duration. The boxes
  never disagree on the value — it is purely sample density. See the 48-hour
  block in `humidity_report.sh`.
- **Never put the db on a network filesystem.** SQLite locking is unsafe over
  NFS/sshfs. Remote rows are shipped as CSV and imported *locally*.
- WAL is required once a second writer exists; the old rollback journal
  produced intermittent `database is locked`.

**Range matters more than you'd think.** Measured 2026-08-25: pairdrop hears
`d28` at ~−61 dBm avg (peak −45) while jcb-pi could not hear it at all for
23 h; jcb-pi hears `ambient` at −53 and `mandolin` at −69, both much better
than pairdrop's −73/−82. The boxes are complementary, not redundant. Test any
prospective new scanner's range before deploying it rather than assuming.

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
