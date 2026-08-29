# Jobtron 6900

A local-first, agent-driven job application workspace. SQLite owns structured history, `data/files` owns uploads, and the MCP server gives the agent the tools to manage both.

The conversation is the control plane. Jobtron's UI is an MCP App review artifact: the agent presents it after meaningful work or when you ask to see your applications. The UI is for inspecting, confirming, or requesting a revision—not for manually driving the workflow.

## Connect the agent

```bash
uv sync --extra test
codex mcp add jobtron6900 -- uv run --directory /absolute/path/to/jobtron6900 jobtron-mcp
codex mcp get jobtron6900
```

Then talk to the agent naturally:

- “Create an application from this posting.”
- “Continue the Acme application.”
- “Show me all the applications I've made.”

The agent uses data tools to do the work, then calls `ui.show_application` or `ui.show_applications` to render the appropriate review surface inline. There is no standalone workstation command in the product flow.

The `jobtron-mcp` stdio entrypoint also hosts the read-through frontend at `http://127.0.0.1:8765`. Presentation tools return stable frontend links, so the agent can keep driving each workflow step even when an MCP App popup is unavailable. For server development, `uv run uvicorn app.main:app --port 8765` exposes the same frontend and the Streamable HTTP MCP endpoint at `/mcp`.

## Test

```bash
uv run pytest
```

Back up `data/app.db` and `data/files/` together. Documents, answers, and submitted application snapshots are versioned rather than overwritten.
