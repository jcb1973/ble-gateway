#!/bin/sh
# Deploy ble-gateway web changes on jcblondon (vultr webserver).
# Do NOT run on the Pi — that's a different host with a different job
# (scanner/gateway.py); use deploy-pi.sh there.
#
# Required because Caddy (owned by the sms-reminders compose) bind-mounts
# web/index.html as a single file. `git pull` replaces the file via
# unlink+rename, creating a new inode, but the container still holds the
# old one — so HTML changes are invisible until the container restarts.
set -e
cd "$(dirname "$0")"
git pull
docker restart sms-reminders-caddy-1
echo "deployed: $(git rev-parse --short HEAD)"
