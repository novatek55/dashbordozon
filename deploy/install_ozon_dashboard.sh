#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/ozon-dashboard}"
ENV_FILE="${ENV_FILE:-/etc/ozon-dashboard.env}"
SERVICE_FILE="/etc/systemd/system/ozon-dashboard.service"
SYNC_SERVICE_FILE="/etc/systemd/system/ozon-dashboard-sync.service"
SYNC_TIMER_FILE="/etc/systemd/system/ozon-dashboard-sync.timer"
PRICE_CONTROL_SERVICE_FILE="/etc/systemd/system/ozon-price-control-sync.service"
PRICE_CONTROL_TIMER_FILE="/etc/systemd/system/ozon-price-control-sync.timer"
NGINX_CONF_D="/etc/nginx/conf.d"
NGINX_SITE="$NGINX_CONF_D/ozon-dashboard.conf"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo bash deploy/install_ozon_dashboard.sh"
  exit 1
fi

apt-get update
apt-get install -y python3 python3-venv python3-pip postgresql postgresql-client nginx chromium

mkdir -p "$APP_DIR" "$APP_DIR/logs" "$APP_DIR/uploads" "$APP_DIR/exports"

if [ ! -f "$ENV_FILE" ]; then
  cp deploy/ozon-dashboard.env.example "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "Created $ENV_FILE. Fill credentials before starting the service."
fi

if grep -Eq '^DATABASE_URL=.*@(localhost|127\.0\.0\.1|\[::1\])(:|/)' "$ENV_FILE"; then
  if ! grep -Eq '^DB_SOURCE_MODE=' "$ENV_FILE"; then
    printf '\nDB_SOURCE_MODE=server\n' >> "$ENV_FILE"
  fi
  if ! grep -Eq '^ALLOW_LOCAL_DATABASE=' "$ENV_FILE"; then
    printf 'ALLOW_LOCAL_DATABASE=true\n' >> "$ENV_FILE"
  fi
  if ! grep -Eq '^EXPECTED_DB_HOST=' "$ENV_FILE"; then
    db_host="$(sed -nE 's#^DATABASE_URL=.*@([^:/]+).*$#\1#p' "$ENV_FILE" | head -n 1)"
    printf 'EXPECTED_DB_HOST=%s\n' "${db_host:-127.0.0.1}" >> "$ENV_FILE"
  fi
fi

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

cp deploy/ozon-dashboard.service "$SERVICE_FILE"
cp deploy/ozon-dashboard-sync.service "$SYNC_SERVICE_FILE"
cp deploy/ozon-dashboard-sync.timer "$SYNC_TIMER_FILE"
cp deploy/ozon-price-control-sync.service "$PRICE_CONTROL_SERVICE_FILE"
cp deploy/ozon-price-control-sync.timer "$PRICE_CONTROL_TIMER_FILE"
mkdir -p "$NGINX_CONF_D"
cp deploy/nginx-ozon-dashboard.conf "$NGINX_SITE"

chown -R www-data:www-data "$APP_DIR"

systemctl daemon-reload
nginx -t

cat <<'MSG'
Install prepared.

Next:
1. Edit /etc/ozon-dashboard.env and fill real DATABASE_URL / Ozon credentials.
2. Create /etc/nginx/ozon-dashboard.htpasswd for Basic Auth.
3. Optional: create DNS A record for ozon.newtekpro.ru.
4. Run:
   systemctl enable --now ozon-dashboard
   systemctl enable --now ozon-dashboard-sync.timer
   systemctl enable --now ozon-price-control-sync.timer
   systemctl reload nginx
5. Check:
   curl -i http://127.0.0.1:18088/api/health
   systemctl list-timers ozon-dashboard-sync.timer
MSG
