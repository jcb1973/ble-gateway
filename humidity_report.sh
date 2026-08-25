#!/bin/bash

# Humidity analysis script for PicoClaw
#
# --matrix: ALSO push the D-28 status to the kitchen LED sign via
# /usr/local/bin/matrix (kitchen-sign repo, docs/consumer-skills.md).
# The printed report is unchanged; sign failures are swallowed.
# BLE_DB override exists so the report can be exercised against a copy of
# the db without touching the live one.
DB="${BLE_DB:-$HOME/ble-gateway/sensordata.db}"

MATRIX=0
[[ "${1:-}" == "--matrix" ]] && MATRIX=1

echo "=== 48-Hour Humidity Report ==="
echo ""

# Get latest readings for each device
sqlite3 "$DB" << EOF
SELECT 
  device_name,
  ROUND(CAST(humidity_pct AS FLOAT), 2) as current_pct
FROM readings 
WHERE (device_name, timestamp) IN (
  SELECT device_name, MAX(timestamp) FROM readings GROUP BY device_name
)
ORDER BY device_name;
EOF

echo ""
echo "48-Hour Averages:"
# The average is taken over 10-minute TIME BUCKETS, not over raw rows.
# With more than one gateway scanning, a sensor heard by two boxes produces
# twice the rows for the same instants -- averaging rows directly would then
# weight periods by how many radios happened to hear them rather than by
# duration, tilting the mean toward whenever coverage was densest. The two
# boxes never disagree about the value (they decode the same advert payload);
# it is purely the sample density that varies. Bucketing first makes the
# result independent of how many gateways were listening.
# MIN/MAX are unaffected by density, so they stay over raw readings.
sqlite3 "$DB" << EOF
WITH win AS (
  SELECT * FROM readings
  WHERE timestamp >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-48 hours')
),
buckets AS (
  SELECT device_name, substr(timestamp, 1, 15) AS b,
         AVG(CAST(humidity_pct AS FLOAT)) AS v
  FROM win GROUP BY device_name, b
),
avgs AS (
  SELECT device_name, ROUND(AVG(v), 2) AS avg_pct FROM buckets GROUP BY device_name
),
ranges AS (
  SELECT device_name,
         ROUND(MIN(CAST(humidity_pct AS FLOAT)), 2) AS min_pct,
         ROUND(MAX(CAST(humidity_pct AS FLOAT)), 2) AS max_pct
  FROM win GROUP BY device_name
)
SELECT a.device_name, a.avg_pct, r.min_pct, r.max_pct
FROM avgs a JOIN ranges r USING (device_name)
ORDER BY a.device_name;
EOF

echo ""
echo "=== D-28 Hourly Trend (last 48 hours) ==="

sqlite3 "$DB" << EOF
SELECT
  substr(timestamp, 1, 13) as hour,
  ROUND(AVG(CAST(humidity_pct AS FLOAT)), 2) as humidity_pct
FROM readings
WHERE device_name='d28'
  AND timestamp >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-48 hours')
GROUP BY substr(timestamp, 1, 13)
ORDER BY hour DESC;
EOF

echo ""
echo "Sparkline (oldest → newest):"
sqlite3 "$DB" << EOF | LC_NUMERIC=C awk -F'|' '
  BEGIN { split("▁ ▂ ▃ ▄ ▅ ▆ ▇ █", c, " ") }
  { v[++n] = $2; if (n==1 || $2<min) min=$2; if (n==1 || $2>max) max=$2 }
  END {
    r = max - min
    for (i=1; i<=n; i++) {
      idx = (r==0) ? 4 : int((v[i]-min)/r * 7) + 1
      printf "%s", c[idx]
    }
    printf "  (min %.2f → max %.2f)\n", min, max
  }
'
SELECT
  substr(timestamp, 1, 13) as hour,
  ROUND(AVG(CAST(humidity_pct AS FLOAT)), 2) as humidity_pct
FROM readings
WHERE device_name='d28'
  AND timestamp >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-48 hours')
GROUP BY substr(timestamp, 1, 13)
ORDER BY hour ASC;
EOF

echo ""
echo "=== D-28 Risk Assessment ==="

CURRENT=$(sqlite3 "$DB" "SELECT CAST(humidity_pct AS FLOAT) FROM readings WHERE device_name='d28' ORDER BY timestamp DESC LIMIT 1;")
EARLIEST=$(sqlite3 "$DB" "SELECT CAST(humidity_pct AS FLOAT) FROM readings WHERE device_name='d28' AND timestamp >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-48 hours') ORDER BY timestamp ASC LIMIT 1;")
CHANGE=$(awk -v c="$CURRENT" -v e="$EARLIEST" 'BEGIN{printf "%.2f", c-e}')

echo "Current: ${CURRENT}% (started at ${EARLIEST}% today)"
echo "Change: ${CHANGE}% over 48 hours"
echo ""

# Smart warnings
if (( $(echo "$CURRENT < 42" | awk '{if($1<42) print 1; else print 0}') )); then
  echo "🔴 ALERT: D-28 at ${CURRENT}% (below safe 45-50% range)"
  SIGN_COLOR=red
elif (( $(echo "$CURRENT < 45" | awk '{if($1<45) print 1; else print 0}') )); then
  echo "🟡 WARNING: D-28 at ${CURRENT}% (slightly low, keep monitoring)"
  SIGN_COLOR=yellow
else
  echo "🟢 HEALTHY: D-28 at ${CURRENT}% (optimal range)"
  SIGN_COLOR=green
fi

if [[ "$MATRIX" == "1" && -n "$CURRENT" ]]; then
  # fire-and-forget: output discarded, failures swallowed — the sign
  # must never break the report
  SIGN_TEXT=$(LC_NUMERIC=C awk -v c="$CURRENT" -v ch="$CHANGE" \
    'BEGIN{printf "D28 %.0f%%|48H %+.1f%%", c, ch}')
  /usr/local/bin/matrix show bottom "$SIGN_TEXT" \
    --color "$SIGN_COLOR" --ttl 1h >/dev/null 2>&1 || true
fi

if (( $(echo "$CHANGE < -2" | awk '{if($1<-2) print 1; else print 0}') )); then
  echo "⚠️  Dropping fast (${CHANGE}% in 48 hours)"
elif (( $(echo "$CHANGE < 0" | awk '{if($1<0) print 1; else print 0}') )); then
  echo "→ Gradually declining (${CHANGE}% in 48 hours)"
else
  echo "↗ Rising/stable"
fi
