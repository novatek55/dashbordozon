# Data Source Rules

- Local files and local databases in this workspace are test/dev artifacts only.
- Do not use local SQLite files, local PostgreSQL on `localhost`, local Excel exports, `exports/`, `logs/`, or generated report files as authoritative sources for business analysis.
- For sales, marketplace, stock, advertising, finance, or operational analysis, use only the current production/server data source explicitly provided by the user or verified from the production service environment.
- Before any analysis, verify and state the actual data source, host, database name, and latest available business date.
- If `DATABASE_URL` points to `localhost`, treat it as a local test snapshot unless the user explicitly says this exact local database is the intended source.
- If production access is unavailable or ambiguous, stop and ask for the correct production dashboard URL, server environment, or production `DATABASE_URL`.

## Deployment Database Rules

- A deploy is not complete until SQL migrations in `migrations/` have been applied to the target server database before restarting or relying on the new app code.
- When new code reads or writes new columns/tables, verify the production schema first. A health check alone is not enough if the changed route may fail later.
- If the server app fails after deploy with `UndefinedColumnError`, `UndefinedTableError`, or similar schema errors, treat the primary suspicion as "code deployed before migration", not as a broken database connection.
- For this project, `deploy/install_ozon_dashboard.sh` installs dependencies and service files but does not automatically apply SQL migrations unless explicitly changed. Run or verify the relevant `migrations/*.sql` step separately.
- After deploy, verify all three: `/etc/ozon-dashboard.env` DB source policy, required schema objects, and `curl -i http://127.0.0.1:18088/api/health`.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, invoke the `skill` tool with `skill: "graphify"` before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
