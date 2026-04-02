#!/bin/sh
cd "$(dirname "$0")"
python3 gateway.py --json > status.json && scp status.json YOURSERVER:/var/www/html/status.json
