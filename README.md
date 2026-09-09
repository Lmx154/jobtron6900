# Jobtron 6900

A local-first, agent-driven job application workspace. SQLite owns structured history, the shared data directory owns uploads, and one MCP server gives Codex, OpenCode, or Claude Code the same tools and content.

The conversation is the control plane. Jobtron's MCP App and browser frontend are review surfaces: the agent presents them after meaningful work or when you ask to see your applications.

## Install

Install [uv](https://docs.astral.sh/uv/), then run from this repository:

```bash
uv sync --extra test
uv run jobtron-setup install --clients codex,opencode,claude
```

The setup command:

- safely copies a legacy repository-local `data/` directory to the platform user data directory when necessary;
- configures the selected clients at user scope;
- gives every client the same explicit `JOBTRON_DATA_DIR`;
- preserves unrelated client configuration; and
- never deletes the legacy database or application data.

Configure only selected clients with `--clients codex`, `--clients opencode`, or `--clients claude`.

Restart any configured client after installation. Several clients may run at once: each gets its own MCP process, the first one to start hosts the web frontend, and the rest detect it and share the same database. SQLite runs in WAL mode and schema migrations take an exclusive lock, so concurrent starts are safe.

## Verify

```bash
codex mcp get jobtron6900
opencode mcp list
claude mcp get jobtron6900
uv run jobtron-setup doctor
```

Ask the active agent:

> Use Jobtron to show me the dashboard with the jobs we have applied to. Include the live frontend link.

The presentation tool returns [http://127.0.0.1:8765/applications](http://127.0.0.1:8765/applications). The stdio entrypoint hosts this fallback frontend automatically, including for clients that cannot render MCP Apps inline.

## Shared data and migration

The canonical directory is resolved with `platformdirs` (normally `~/.local/share/jobtron6900` on Linux). Every installed client receives that same path. Before setup migration, Jobtron continues using an existing repository-local `data/app.db` as a compatibility fallback so an update cannot make current applications disappear.

Useful commands:

```bash
uv run jobtron-setup status
uv run jobtron-setup migrate
uv run jobtron-setup print-config --client opencode
uv run jobtron-setup uninstall --clients claude
```

Migration verifies SQLite integrity, copies through a temporary directory, promotes atomically, refuses to overwrite an existing database, and leaves the source untouched.

## Development

```bash
uv run pytest
uv run python main.py
```

The standalone development server exposes the frontend and Streamable HTTP MCP endpoint at `/mcp`. Production client configuration uses the guarded `jobtron-mcp` stdio entrypoint.

Back up `app.db` and `files/` together from the resolved shared data directory. Documents, answers, and submitted application snapshots are versioned rather than overwritten.
