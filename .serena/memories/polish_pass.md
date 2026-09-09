# Polish pass (docs/polish-pass-plan.html)

Follows the workflow rebuild. Decisions D1–D8 in the plan are fixed architecture.

- Tags: `applications.tags` normalized comma-joined set (migration 4). Whole-tag LIKE filter; `applications.tag` tool; `list(status, tag, query)`; `create()` returns `duplicate=True` on URL match.
- Event kinds (migration 5): `events.kind` ∈ manual | lifecycle | reminder. `upcoming()` hides lifecycle only. Marking Applied schedules one idempotent Follow-up reminder (+7d, dedupe_key `follow_up:{id}:1`).
- Attention is computed, never stored: `ApplicationService.attention(now)` → upcoming / follow_up_due / stale / idle_drafts; thresholds are constants in core.py. Surfaces: dashboard Today rail, `applications.attention` tool, MCP UI.
- `applications.log_update` is the one atomic write for a pasted email: event + status (never Applied) + tags + contact upsert. Agent classifies; server persists.
- Fewer approvals: `progress()` auto-applies an already-approved bank entry only at coverage == 1.0 (source_label `question_bank:auto`). prepare_* gate on any version existing; single `review_drafts` stop; `applications.approve_drafts`. An Applied application jumps to post_submission_follow_up.
- Page capture: `questions.detect_from_text` (agent browser or paste) + `capture_page_text` step — the one step with a client-dependent wait (paste fallback in you_must). `extract_from_posting(retry=)`.
- Cover letter inference (migration 6): `cover_letter_required_source` '' | inferred | user; inference only while ''.
- Standard answers: `app/services/standard_questions.py` seeds 12 canonical ATS questions (approved=0 until answered); readiness reports `standard_answers` non-blocking.

Not done: real-posting evidence run (needs user URLs); structured autofill payload; trimming the MCP tool surface.
