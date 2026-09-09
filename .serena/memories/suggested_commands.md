# Suggested Commands

- Install/update development environment: `uv sync --extra test`.
- Run full tests: `uv run pytest`.
- Run a focused test file: `uv run pytest tests/test_workflow.py` or `uv run pytest tests/test_web_and_mcp.py`.
- Run standalone development server: `uv run python main.py` (web UI and Streamable HTTP MCP at `/mcp`).
- Install client registrations after closing active Jobtron sessions: `uv run jobtron-setup install --clients codex,opencode,claude`; narrow `--clients` as needed.
- Validate installed/shared-data setup: `uv run jobtron-setup doctor`; inspect with `uv run jobtron-setup status`.
- Data migration: `uv run jobtron-setup migrate`; inspect generated client config with `uv run jobtron-setup print-config --client opencode`.
- Verify client registrations: `codex mcp get jobtron6900`, `opencode mcp list`, `claude mcp get jobtron6900`.
- Codebase Memory graph project name is `jobtron6900`; check with `codebase-memory-mcp cli index_status --project jobtron6900`.
- Serena memory reference check from repo root: `serena memories check`.