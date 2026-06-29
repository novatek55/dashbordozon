# Data Source Notice

Local workspace files are test/dev artifacts.

Do not use these as authoritative sources for business analysis:

- local SQLite databases such as `ozon_analytics.db`
- local PostgreSQL on `localhost`
- `.env` values that point to `localhost`
- files in `exports/`
- files in `logs/`
- generated Excel/CSV/Markdown reports in the workspace

For real sales, marketplace, stock, advertising, finance, or operational analysis,
use only the current production/server data source explicitly provided by the user
or verified from the production service environment.

Before analysis, verify and state:

- data source host
- database name
- latest available business date
- whether the source is production or a local test snapshot

If the source is ambiguous, ask for the production dashboard URL, server
environment, or production `DATABASE_URL` before analyzing.

