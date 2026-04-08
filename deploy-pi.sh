#!/bin/sh
# Deploy ble-gateway scanner changes on jcb-pi (the Raspberry Pi).
# Do NOT run on the webserver — that's a different host with a different
# job (Caddy/HTML); use deploy-web.sh there.
set -e
cd "$(dirname "$0")"
git pull
sudo systemctl restart ble-gateway
echo "deployed: $(git rev-parse --short HEAD)"
