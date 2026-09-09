# Project Core

- Local-first job application workspace; SQLite is authoritative for structured history, shared data directory owns uploads, and one FastMCP server exposes the same workflow to Codex/OpenCode/Claude.
- Runtime composition: `app/main.py:create_app` wires `Settings`, `Database`, `FileStorage`, the service facade, FastAPI routers/templates/static assets, and optional Streamable HTTP MCP at `/mcp`.
- Domain logic lives in `app/services/core.py`; HTTP routes and MCP tools should remain thin adapters over the same service methods so web and agent behavior stay aligned.
- Source map: `app/config.py` data-path/client install/single-instance lease; `app/db.py` schema + transaction wrapper; `app/storage.py` upload persistence/path containment; `app/services/` workflow and extraction; `app/routes/` web adapters; `app/mcp/` MCP tools/resources/UI and stdio entrypoint; `app/templates/` + `app/static/` review UI; `tests/` end-to-end domain/web/MCP behavior.
- Data safety invariants: canonical user data is outside the repo via platformdirs; legacy repo-local data is migration fallback only; migration never overwrites an existing DB and leaves the source intact; back up `app.db` and `files/` together.
- One OS lease permits one Jobtron MCP process per data store. Production clients use guarded stdio; standalone development can serve web + Streamable HTTP.
- Application archive snapshots are immutable/idempotent after first creation. Document versions are retained; approval is singular per document type and scope.
- Read stack/build details in `mem:tech_stack`; runnable workflows in `mem:suggested_commands`; implementation norms in `mem:conventions`; completion gates in `mem:task_completion`.