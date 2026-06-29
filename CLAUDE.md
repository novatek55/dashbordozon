## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

## Deployment Database Rules

- A deploy is not complete until SQL migrations in `migrations/` have been applied to the target server database before restarting or relying on the new app code.
- If the app fails after deploy with missing-column or missing-table errors, first check whether code was deployed before the migration.
- `deploy/install_ozon_dashboard.sh` does not automatically apply SQL migrations unless explicitly changed; run or verify the relevant `migrations/*.sql` step separately.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
