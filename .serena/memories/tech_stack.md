# Tech Stack

- Python package `jobtron6900`; Python >=3.12, repository pin currently `.python-version` 3.14; dependency/lock workflow is uv + `uv.lock`; Hatchling builds wheels from `app`.
- FastAPI + Uvicorn serve browser routes and static/Jinja2 UI.
- MCP Python SDK/FastMCP (`mcp>=2.1,<3`) provides stdio and Streamable HTTP MCP plus MCP App UI resources.
- SQLite stdlib backend uses foreign keys, 10s busy timeout, WAL journaling, synchronous=NORMAL, commit-on-success/rollback-on-error.
- Persistence helpers: platformdirs for canonical user data; filelock for the web single-instance lease and the CLI start lock; pypdf and python-docx for document extraction; python-multipart for uploads.
- Test stack: pytest, HTTPX; test roots configured as `tests`.
- Console scripts: `jobtron-mcp = app.mcp.server:run_stdio`; `jobtron-setup = app.config:setup_main`; `jobtron = app.cli:main`.