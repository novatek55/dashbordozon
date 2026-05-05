# Metabase Setup

## 1. Start Metabase (local profile)

```bash
docker compose -f docker-compose.metabase-local.yml up -d
```

Metabase UI:
- `http://localhost:3000`

## 2. Auto-setup from `.env`

```bash
py metabase_auto_setup.py
```

Optional env vars in `.env`:

```env
METABASE_URL=http://127.0.0.1:3000
METABASE_ADMIN_EMAIL=admin@local.test
METABASE_ADMIN_PASSWORD=Admin12345!
METABASE_ADMIN_FIRST_NAME=Ozon
METABASE_ADMIN_LAST_NAME=Admin
METABASE_SITE_NAME=Ozon Analytics
METABASE_SOURCE_NAME=Ozon PostgreSQL
```

The script:
- waits until Metabase is ready,
- creates admin user,
- connects to your current `DATABASE_URL`,
- automatically maps `localhost` DB host to `host.docker.internal`.

## 3. Create 3 dashboards

Use SQL from:
- `metabase_queries/sales.sql`
- `metabase_queries/actions.sql`
- `metabase_queries/action_products.sql`

Recommended dashboards:
1. Sales
2. Actions
3. Products in Actions

## 4. Useful checks

```bash
docker compose -f docker-compose.metabase-local.yml ps
docker compose -f docker-compose.metabase-local.yml logs -f metabase
```

## 5. Stop

```bash
docker compose -f docker-compose.metabase-local.yml down
```
