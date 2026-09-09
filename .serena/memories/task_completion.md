# Task Completion

- Always run `uv run pytest`.
- For MCP/web/config/data-path changes, also run `uv run jobtron-setup doctor`.
- For stdio MCP changes, ensure the handshake/process tests in `tests/test_web_and_mcp.py` pass; do not leave another Jobtron-enabled process holding the data-store lease.
- For schema, migration, storage, archive, versioning, or approval changes, run both workflow and web/MCP suites and verify existing user data is not overwritten/deleted.
- For browser/UI changes, exercise the affected review route through the relevant HTTPX test and verify the frontend URL/resource returned by MCP remains authoritative.
- No project linter, formatter, or type-checker is currently configured in `pyproject.toml`; do not invent a completion gate.
- Review `git status --short` and preserve unrelated user changes before handoff.
- Validate Serena memory links after memory changes with `serena memories check`.