# Jobtron 6900 — working rules

Local application tracker: FastAPI web frontend + one MCP server, SQLite in WAL mode. It stores job information, application answers and cover letters, an `active` flag, and dated bundles. The connected agent drafts only answers and a cover letter from the pinned original resume and approved answers. It never tailors resumes, fills forms or submits. The `jobtron` CLI (`app/cli.py`) runs the web UI as a detached background process; every stdio MCP session shares it. The plans in `docs/` (`workflow-rebuild-plan.html`, `polish-pass-plan.html`, the architecture blueprint) record earlier designs and are historical. Serena memories in `.serena/memories/` hold conventions and commands.

## Run and verify

- `uv run pytest -q` is the gate. Nothing is done until it passes.
- MCP/web/config/data-path/CLI changes: also `uv run jobtron-setup doctor` (with `JOBTRON_DATA_DIR` set to a scratch directory).
- Schema/migration/versioning/approval changes: run the whole suite and confirm existing user data is never overwritten or deleted.
- No linter/formatter/type-checker is configured; do not invent one as a gate.

## Invariants that must not regress

- `app/main.py` builds the ASGI app **lazily**. Importing it must never open or migrate the live database. Tests use temporary data directories via `tests/conftest.py` fixtures (`services`, `ready_services`).
- Any manual run must set `JOBTRON_DATA_DIR`; the resolved shared directory (`~/.local/share/jobtron6900`) is real user data.
- Migrations are `(version, fn)` pairs in `MIGRATIONS` in `app/db.py`, appended in order. Never renumber or reuse a version; never write a destructive migration.
- Workflow engine (`app/services/workflow.py`, `progress()` in `app/services/core.py`): the server decides, the agent obeys. Each step exposes at most one `then_call` (None means stop and wait for the user). **Working steps narrate, stopping steps ask** — a step never does both (asserted in tests). `STEP_MAP` must stay a literal table with total coverage over `next_action.code`.
- Application **status and the `active` flag are user-owned**. Nothing auto-advances either one. MCP exposes no status write; status changes only through the web UI. `applications.set_active` exists for explicit user requests; generation and bundling never touch it. `progress()` returns `inactive` for an inactive application and drafts nothing. `core.log_update` keeps its in-code guard: it refuses to set `Applied`.
- The agent never invents a candidate fact. Nothing unapproved is ever auto-approved.
- Documents, answers, and submitted snapshots are versioned, never overwritten; one approved version per scope.
- Routes and MCP handlers are thin adapters over `app/services/core.py`; MCP tool names are dotted and domain-prefixed (`applications.*`, `questions.*`, `question_bank.*`, `workflow.*`, `ui.*`, …).
- Dated bundles (`application_bundles`) store their ZIP bytes in SQLite and never change after creation. The resume version is pinned (`resume_document_id`) at create, first draft request or first bundle, and is never silently re-pointed.
- Posting fetch is server-side `httpx` with an SSRF guard; no headless browser in the server. Failure degrades honestly to agent-browser or paste.
- `progress()` is read-only: it never fills or approves answers. Drafts stay unapproved until the single `review_drafts` stop, and only versions the user explicitly approves go through `applications.approve_drafts`. (`_auto_apply_bank_answers` is still in `core.py` but nothing calls it. If it is ever wired back in, it may fire only for an already-approved entry at coverage == 1.0.)
- `events.kind` is manual | lifecycle | reminder; only lifecycle is hidden by `upcoming()`. Attention buckets are derived from existing rows and never stored. (The calendar, dashboard and attention surfaces were removed, so `upcoming()`/`attention()` have no UI or MCP caller; `/dashboard` redirects to `/applications`.)
- No workflow step's wait depends on the client. Question capture happens inside the stopping `inventory_questions` step, where `questions.extract_from_posting` and `questions.detect_from_text` (agent browser or paste) are optional helpers. Do not add a client-dependent step without recording why.
- `cover_letter_required_source` guards inference: it runs only while the source is empty; a user value is never overridden.

## Conventions

- `from __future__ import annotations`, typed signatures, plain-dict returns, parameterized SQL, short-lived connections.
- Missing records raise `NotFoundError` via the shared `required` helper.
- Tests assert externally observable behavior across service, web, and MCP surfaces; never use personal data.
