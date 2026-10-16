# Task Completion

- Always run `uv run pytest -q`.
- For MCP/web/config/data-path/CLI changes, also run `uv run jobtron-setup doctor`.
- For stdio MCP or CLI changes, ensure `tests/test_web_and_mcp.py`, `tests/test_mcp_startup.py` and `tests/test_cli.py` pass; stop any background UI you started (`uv run jobtron stop` with the same `JOBTRON_DATA_DIR`).
- For schema, migration, storage, archive, versioning, or approval changes, run the whole suite and verify existing user data is not overwritten/deleted.
- For browser/UI changes, exercise the affected review route through the relevant HTTPX test and verify the frontend URL/resource returned by MCP remains authoritative.
- No project linter, formatter, or type-checker is currently configured in `pyproject.toml`; do not invent a completion gate.
- Review `git status --short` and preserve unrelated user changes before handoff.
- Validate Serena memory links after memory changes with `serena memories check`.