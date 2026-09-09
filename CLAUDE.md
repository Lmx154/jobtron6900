# Jobtron 6900 — working rules

Local-first job-application workspace: FastAPI web frontend + one MCP server, SQLite in WAL mode. The conversation is the control plane; web and MCP UI are review surfaces. Plans live in `docs/` (`workflow-rebuild-plan.html`, `polish-pass-plan.html`); Serena memories in `.serena/memories/` hold conventions and commands.

## Run and verify

- `uv run pytest -q` is the gate. Nothing is done until it passes.
- MCP/web/config/data-path changes: also `uv run jobtron-setup doctor`.
- Schema/migration/versioning/approval changes: run the whole suite and confirm existing user data is never overwritten or deleted.
- No linter/formatter/type-checker is configured; do not invent one as a gate.

## Invariants that must not regress

- `app/main.py` builds the ASGI app **lazily**. Importing it must never open or migrate the live database. Tests use temporary data directories via `tests/conftest.py` fixtures (`services`, `ready_services`).
- Any manual run must set `JOBTRON_DATA_DIR`; the resolved shared directory (`~/.local/share/jobtron6900`) is real user data.
- Migrations are `(version, fn)` pairs in `MIGRATIONS` in `app/db.py`, appended in order. Never renumber or reuse a version; never write a destructive migration.
- Workflow engine (`app/services/workflow.py`, `progress()` in `app/services/core.py`): the server decides, the agent obeys. Each step exposes exactly one `then_call`. **Working steps narrate, stopping steps ask** — a step never does both (asserted in tests). `STEP_MAP` must stay a literal table with total coverage over `next_action.code`.
- Application **status is user-owned**. Nothing auto-advances it. `Applied` is set only after the user confirms they submitted externally; the guard lives in code (`app/mcp/server.py`), not prose.
- The agent never invents a candidate fact. Nothing unapproved is ever auto-approved.
- Documents, answers, and submitted snapshots are versioned, never overwritten; one approved version per scope.
- Routes and MCP handlers are thin adapters over `app/services/core.py`; MCP tool names are domain-prefixed (`applications_*`, `questions_*`, …).
- Posting fetch is server-side `httpx` with an SSRF guard; no headless browser in the server. Failure degrades honestly to agent-browser or paste.

## Conventions

- `from __future__ import annotations`, typed signatures, plain-dict returns, parameterized SQL, short-lived connections.
- Missing records raise `NotFoundError` via the shared `required` helper.
- Tests assert externally observable behavior across service, web, and MCP surfaces; never use personal data.
