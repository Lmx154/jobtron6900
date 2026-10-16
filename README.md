# Jobtron 6900

A local application tracker for job information, application answers, and cover letters. Your original resume stays a source document. Jobtron does not tailor resumes, fill employer forms, or submit applications.

## The workflow

1. Open **Resume & answers**, upload your original resume, and optionally save answers to common questions.
2. Create an application with the company, role, and job description. Add the actual application questions and confirm the list, including when there are none.
3. Choose **Prepare agent request** and copy it to your connected AI agent. It drafts or regenerates only the answers and cover letter, using your resume and approved reusable answers. Generation runs in the connected agent, not a separate model service inside the website.
4. Read and edit every answer and the letter in the application record. Each save creates a version; generated versions are drafts until you explicitly review them. New drafts remain visible even when an older version was approved.
5. Save a **dated bundle** whenever you want a permanent copy. Generation also asks the agent to bundle its drafts. Download the ZIP with the posting, answers, letter, original resume, review labels, and a JSON snapshot. Bundles use UTC timestamps and preserve their exact contents through later edits.
6. Set the application **Active** or **Inactive** whenever you choose. Drafting and bundling never change that flag or claim that you submitted anything.

The resume version is pinned when an application is created with a readable active resume, or when its first draft request is prepared. Uploading a newer resume does not silently change existing application sources. Missing personal facts must be supplied by you; the agent is instructed to ask rather than guess.

## Install

Install [uv](https://docs.astral.sh/uv/), then run from this repository:

```bash
uv sync --extra test
uv run jobtron-setup install --clients codex,opencode,claude
```

Choose only the clients you use, for example `--clients codex`. Setup preserves unrelated configuration and safely copies legacy repository-local data to the shared platform data directory when needed. Restart configured clients after installation or after updating the app's MCP tools.

Ask your agent:

> Use Jobtron to show my applications and give me the frontend link.

The default frontend is [http://127.0.0.1:8765/applications](http://127.0.0.1:8765/applications). The stdio server starts it as a detached background process if it is not already running (check with `uv run jobtron status`); it keeps running after the agent session ends. Multiple clients share the same database and frontend.

To reach the web UI yourself, use the `jobtron` command. It runs the UI as its own detached background process, not tied to systemd or any client session, and writes only inside the data directory (its log is `jobtron.log` there):

```bash
uv run jobtron open     # start if needed, then open the browser
uv run jobtron start    # start in the background
uv run jobtron status   # running or not, URL, pid, and which agents have Jobtron registered
uv run jobtron stop     # stop the background UI
```

`JOBTRON_DATA_DIR`, `JOBTRON_HOST`, and `JOBTRON_PORT` apply as usual. If another program holds the port, `jobtron start` says so and exits instead of picking a different port; set `JOBTRON_PORT` to change it.

## Existing data

The tracker migration adds the active flag and dated bundles without deleting old records, documents, answers, or submitted snapshots. Existing Rejected, Withdrawn, and Closed applications start inactive; other records start active. Old status values remain stored. Previously saved application documents remain downloadable in each record.

The shared directory is resolved with `platformdirs` (normally `~/.local/share/jobtron6900` on Linux). An existing repository-local `data/app.db` remains the fallback until setup migration. Back up `app.db` and `files/` together. Bundle ZIP bytes are stored in SQLite so later source changes cannot alter a saved download.

```bash
uv run jobtron-setup status
uv run jobtron-setup doctor
uv run jobtron-setup migrate
```

## Development

```bash
uv run pytest -q
JOBTRON_DATA_DIR=/tmp/jobtron-dev uv run python main.py
```

Always use a separate `JOBTRON_DATA_DIR` for manual testing. Importing `app.main` does not open or migrate the live database. Tests use temporary directories. The standalone development server also exposes Streamable HTTP MCP at `/mcp`.
