#!/bin/bash

# Humidity analysis script for PicoClaw
DB="$HOME/ble-gateway/sensordata.db"

echo "=== 24-Hour Humidity Report ==="
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
echo "24-Hour Averages:"
sqlite3 "$DB" << EOF
SELECT
  device_name,
  ROUND(AVG(CAST(humidity_pct AS FLOAT)), 2) as avg_pct,
  ROUND(MIN(CAST(humidity_pct AS FLOAT)), 2) as min_pct,
  ROUND(MAX(CAST(humidity_pct AS FLOAT)), 2) as max_pct
FROM readings
WHERE timestamp >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-24 hours')
GROUP BY device_name;
EOF

echo ""
echo "=== D-28 Hourly Trend (last 24 hours) ==="

sqlite3 "$DB" << EOF
SELECT
  substr(timestamp, 1, 13) as hour,
  ROUND(AVG(CAST(humidity_pct AS FLOAT)), 2) as humidity_pct
FROM readings
WHERE device_name='d28'
  AND timestamp >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-24 hours')
GROUP BY substr(timestamp, 1, 13)
ORDER BY hour DESC;
EOF

echo ""
echo "=== D-28 Risk Assessment ==="

CURRENT=$(sqlite3 "$DB" "SELECT CAST(humidity_pct AS FLOAT) FROM readings WHERE device_name='d28' ORDER BY timestamp DESC LIMIT 1;")
EARLIEST=$(sqlite3 "$DB" "SELECT CAST(humidity_pct AS FLOAT) FROM readings WHERE device_name='d28' AND timestamp >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-24 hours') ORDER BY timestamp ASC LIMIT 1;")
CHANGE=$(awk -v c="$CURRENT" -v e="$EARLIEST" 'BEGIN{printf "%.2f", c-e}')

echo "Current: ${CURRENT}% (started at ${EARLIEST}% today)"
echo "Change: ${CHANGE}% over 24 hours"
echo ""

# Smart warnings
if (( $(echo "$CURRENT < 42" | awk '{if($1<42) print 1; else print 0}') )); then
  echo "🔴 ALERT: D-28 at ${CURRENT}% (below safe 45-50% range)"
elif (( $(echo "$CURRENT < 45" | awk '{if($1<45) print 1; else print 0}') )); then
  echo "🟡 WARNING: D-28 at ${CURRENT}% (slightly low, keep monitoring)"
else
  echo "🟢 HEALTHY: D-28 at ${CURRENT}% (optimal range)"
fi

if (( $(echo "$CHANGE < -2" | awk '{if($1<-2) print 1; else print 0}') )); then
  echo "⚠️  Dropping fast (${CHANGE}% in 24 hours)"
elif (( $(echo "$CHANGE < 0" | awk '{if($1<0) print 1; else print 0}') )); then
  echo "→ Gradually declining (${CHANGE}% in 24 hours)"
else
  echo "↗ Rising/stable"
fi
