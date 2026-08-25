#!/bin/sh
# Pull readings from remote scanning gateways into THIS box's sensordata.db.
#
# Runs on the collecting box (jcb-pi). Pull, not push, on purpose: jcb-pi stays
# the only process writing its own db, and remote scanners need no credentials
# for, or knowledge of, this machine.
#
# Correctness comes from the UNIQUE index readings_obs(source, device_name,
# timestamp) plus INSERT OR IGNORE, NOT from the watermark. Re-importing a
# batch is a no-op, so the watermark is only an optimisation to keep batches
# small -- if an import dies half way, the watermark simply isn't advanced and
# the next run re-sends the same rows harmlessly.
#
# `source` is carried through from the remote row, never assigned here: it
# records which radio actually heard the advert.
set -u

cd "$(dirname "$0")"
DB="$PWD/sensordata.db"
STATE_DIR="$HOME/.cache/ble-gateway"
KEY="$HOME/.ssh/id_blesync"
BATCH_LIMIT=5000

# <name>:<ssh-target>, space separated. Adding a third scanner is one entry.
REMOTES="${BLE_REMOTES:-pairdrop:jcb1973@pairdrop.local}"

mkdir -p "$STATE_DIR"
rc=0

for entry in $REMOTES; do
  name="${entry%%:*}"
  host="${entry#*:}"
  wm_file="$STATE_DIR/watermark-$name"

  wm=$(cat "$wm_file" 2>/dev/null || echo 0)
  case "$wm" in ''|*[!0-9]*) wm=0 ;; esac   # never interpolate junk into SQL

  batch=$(mktemp) || { rc=1; continue; }

  if ! ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=10 "$host" \
        "sqlite3 -csv ~/ble-gateway/sensordata.db \
         'SELECT id,timestamp,device_name,temperature_c,humidity_pct,rssi,source \
          FROM readings WHERE id > $wm ORDER BY id LIMIT $BATCH_LIMIT'" \
        > "$batch" 2>/dev/null; then
    echo "$(date -Is) $name: unreachable or query failed, skipping"
    rm -f "$batch"
    rc=1
    continue
  fi

  n=$(wc -l < "$batch" | tr -d ' ')
  if [ "$n" -eq 0 ]; then
    rm -f "$batch"
    continue
  fi

  maxid=$(tail -1 "$batch" | cut -d, -f1)
  case "$maxid" in ''|*[!0-9]*) echo "$(date -Is) $name: bad batch, skipping"; rm -f "$batch"; rc=1; continue ;; esac

  # Remote ids are imported only to compute the new watermark -- they are
  # meaningless locally, so the INSERT deliberately omits the id column.
  if sqlite3 "$DB" <<SQL
.output /dev/null
PRAGMA busy_timeout=15000;
.output stdout
CREATE TEMP TABLE incoming(
  id INTEGER, timestamp TEXT, device_name TEXT,
  temperature_c REAL, humidity_pct REAL, rssi INTEGER, source TEXT
);
.mode csv
.import "$batch" incoming
INSERT OR IGNORE INTO readings
  (timestamp, device_name, temperature_c, humidity_pct, rssi, source)
  SELECT timestamp, device_name, temperature_c, humidity_pct, rssi, source
  FROM incoming WHERE source <> '';
SQL
  then
    echo "$maxid" > "$wm_file"
    echo "$(date -Is) $name: imported $n rows (watermark -> $maxid)"
  else
    echo "$(date -Is) $name: import failed, watermark held at $wm"
    rc=1
  fi
  rm -f "$batch"
done

exit $rc
