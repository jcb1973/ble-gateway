---
name: humidity-report
description: "Monitor D-28 guitar humidity with 48-hour trend analysis and risk assessment. Use when the user asks to check humidity, get guitar status, or monitor D-28 levels (e.g., 'humidity report', 'check my guitar', 'D-28 status', 'guitar humidity check')."
---

# D-28 Humidity Report

Execute the humidity monitoring script to retrieve real-time sensor data and provide trend analysis.

## Quick Start

Run the humidity script:

```bash
~/ble-gateway/humidity_report.sh
```

It prints plain-text sections:
- Current humidity for d28, d18, mandolin
- 48-hour average, min, max for each device
- Hourly breakdown + ASCII sparkline for d28
- Risk assessment (🟢/🟡/🔴 already computed by the script — do not re-derive)

## Analysis & Response

Base your reply ONLY on the script output. Report:

1. **Current Status**: the script's own 🟢/🟡/🔴 line for d28 (safe range 45-50%)
2. **Trend**: rising, stable, or declining over 48 hours (the Change line)
3. **Risk**: any ⚠️ warnings the script printed

Keep responses concise and actionable. Include the sparkline if the user asks
about the trend. Do not run other commands or speculate beyond the output;
if the script fails, say so plainly.
