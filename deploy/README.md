# Ozon Dashboard Deployment

Recommended isolation from the existing WordPress site: use a separate vhost, proxied to the app on `127.0.0.1:18088`.
The current production setup allows direct IP access via `http://80.87.203.161/` and can also use `ozon.newtekpro.ru` later.

Why not `newtekpro.ru/ozon`: the dashboard currently uses absolute routes such as `/api/...` and `/shared-report-theme.css`. A subpath would collide with WordPress routing unless we rewrite a lot of frontend/backend URLs.

## Server Layout

- App: `/opt/ozon-dashboard`
- Env: `/etc/ozon-dashboard.env`
- Service: `ozon-dashboard.service`
- Daily sync: `ozon-dashboard-sync.timer` -> `ozon-dashboard-sync.service`
- Local app port: `127.0.0.1:18088`
- Nginx vhost: `80.87.203.161`, optional `ozon.newtekpro.ru`
- Basic Auth file: `/etc/nginx/ozon-dashboard.htpasswd`

## Deploy Steps

1. Build package locally:

```powershell
.\scripts\build_deploy_package.ps1
```

2. Upload the generated archive from `dist/` to the server.

3. Unpack into `/opt/ozon-dashboard`:

```bash
sudo mkdir -p /opt/ozon-dashboard
sudo tar -xzf ozon-dashboard-deploy.tar.gz -C /opt/ozon-dashboard
cd /opt/ozon-dashboard
sudo bash deploy/install_ozon_dashboard.sh
```

4. Fill secrets:

```bash
sudo nano /etc/ozon-dashboard.env
```

5. Restore database dump if you copied `ozon_analytics.dump`:

```bash
sudo -u postgres createdb ozon_analytics 2>/dev/null || true
sudo -u postgres pg_restore --clean --if-exists --no-owner --dbname=ozon_analytics /root/ozon_analytics.dump
```

6. Create Basic Auth credentials:

```bash
sudo apt-get install -y apache2-utils
sudo htpasswd -c /etc/nginx/ozon-dashboard.htpasswd admin
sudo chmod 640 /etc/nginx/ozon-dashboard.htpasswd
```

7. Start:

```bash
sudo systemctl enable --now ozon-dashboard
sudo systemctl enable --now ozon-dashboard-sync.timer
sudo systemctl reload nginx
```

8. Check:

```bash
curl -i http://127.0.0.1:18088/api/health
curl -i -u admin:'PASSWORD' http://80.87.203.161/api/health
systemctl list-timers ozon-dashboard-sync.timer
```

From the Windows workspace, always verify the public Basic Auth after any deploy or nginx/auth change:

```powershell
.\scripts\verify_dashboard_auth.ps1
```

Expected result: `dashboard_auth=200 noauth=401`. Do not consider deploy complete until this passes.

## Notes

- The WordPress vhost should not be edited for this deployment.
- The daily sync runs at `02:00` server time and logs to `journalctl -u ozon-dashboard-sync.service`.
- Add SSL after DNS is ready, for example:

```bash
sudo certbot --nginx -d ozon.newtekpro.ru
```
