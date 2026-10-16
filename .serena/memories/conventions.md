# Conventions

- Keep FastAPI routes and MCP handlers as thin transport adapters; implement reusable behavior once in synchronous service classes under `app/services/core.py`.
- `Services` is the facade injected into both web and MCP surfaces; tests construct it from a temporary `Database` and `FileStorage`.
- Prefer typed Python signatures and `from __future__ import annotations` in substantial modules. `Settings` is immutable (`@dataclass(frozen=True, slots=True)`) and computes all data-store paths.
- Database helpers return plain dictionaries, parameterize SQL, open short-lived connections, and own commit/rollback. Preserve foreign-key/WAL/busy-timeout setup when changing connection behavior.
- Missing domain records raise `NotFoundError` through the shared `required` helper; adapters translate/present domain outcomes.
- Uploaded filenames are sanitized; stored files receive UUID prefixes. Any path retrieval must retain the resolved-parent containment check.
- Version rather than overwrite documents, answers, and submitted application snapshots. Approval changes must retain the single-approved-version invariant within global/application scope.
- MCP tool names are dotted and domain-prefixed (`ui.*`, `workflow.*`, `candidate.*`, `applications.*`, `questions.*`, `answers.*`, `question_bank.*`, `documents.*`, `assets.*`); UI presentation tools return authoritative review links/resources.
- Tests emphasize externally observable workflow invariants across service, web, and MCP surfaces; use temporary data directories and avoid personal/local data.