#!/bin/sh
set -eu

data_root="${1:-/data/video-evidence-mcp}"

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root: sudo $0 $data_root" >&2
  exit 1
fi

install -d -m 0755 "$data_root"
install -d -m 0750 "$data_root/app" "$data_root/models"
chown 10001:10001 "$data_root/app" "$data_root/models"
install -d -m 0700 "$data_root/secrets"
chown 10001:10001 "$data_root/secrets"
install -d -m 0750 "$data_root/redis"
install -d -m 0750 "$data_root/caddy-data" "$data_root/caddy-config"
chown 1000:1000 "$data_root/caddy-data" "$data_root/caddy-config"
install -d -m 0700 "$data_root/tunnel"

if getent passwd video-evidence >/dev/null 2>&1; then
  chown video-evidence:video-evidence "$data_root/tunnel"
fi

echo "prepared $data_root"
