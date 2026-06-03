#!/bin/sh
# Deploy ble-gateway scanner changes on jcb-pi (the Raspberry Pi).
# Do NOT run on the webserver — that's a different host with a different
# job (Caddy/HTML); use deploy-web.sh there.
set -e
cd "$(dirname "$0")"
git pull
sudo systemctl restart ble-gateway

# Refresh the PicoClaw humidity-report skill. Must be a real directory copy —
# PicoClaw's skill discovery does not follow symlinks.
SKILLS_DIR="$HOME/.picoclaw/workspace/skills"
if [ -d "$SKILLS_DIR" ]; then
  rm -rf "$SKILLS_DIR/humidity-report"
  cp -r picoclaw/skills/humidity-report "$SKILLS_DIR/humidity-report"
  sudo systemctl restart picoclaw
  echo "picoclaw skill refreshed + picoclaw restarted"
fi

echo "deployed: $(git rev-parse --short HEAD)"
