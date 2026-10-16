"""The workflow engine: one state in, one step out.

The agent does not choose. It calls `next_step`, says `say_to_user` verbatim,
calls `then_call` if it is not None, and repeats. That is the entire protocol,
and it is followable by a small model.

`ApplicationService.progress` remains the single source of truth for *what* is
outstanding. This module is only the presentation layer that turns that into
the small-model contract: a closed `step_id`, a sentence to say, and at most
one tool to call next.
"""

from __future__ import annotations

from typing import Any


# next_action.code -> (step_id, then_call, you_must, say_template)
#
# `then_call` is the ONE tool the agent may call next. None means stop and wait
# for the user. Keeping this a literal table means the agent's choice set is
# never larger than one, and the mapping is testable for total coverage.
#
# `say_template` is what to say on a step that does NOT wait for the user: such
# a step needs a progress statement, not a question. None means the user is
# genuinely being asked something, so progress()'s own question text is used.
STEP_MAP: dict[str, tuple[str, str | None, str, str | None]] = {
    "capture_job_description": ("need_job_description", None,
        "Ask for the posting text or retrieve the public posting. Save it with applications.update; never interact with an application form.", None),
    "complete_candidate_setup": ("need_candidate_setup", None,
        "Share the setup URL and wait for the user to upload their original resume. Do not generate a resume.", None),
    "inventory_questions": ("inventory_questions", None,
        "Ask for the actual application questions. Save each with questions.add. Call applications.set_questions_reviewed only when the user has supplied the complete list or confirmed there are none.", None),
    "answer_required_question": ("answer_question", "question_bank.search",
        "Search approved reusable answers. Read applications.get_context and its pinned_resume; read that resume resource if needed. Use ONLY candidate facts in the resume or user-approved answers. A job requirement is not a candidate fact. Ask about unknown facts. Save a new visible answer with answers.save approved=false, respecting choices and character limits. Never auto-approve reused answers. Then call workflow.next.",
        "Drafting answer {question_number} of {question_total} for your review."),
    "prepare_cover_letter": ("generate_cover_letter", "documents.save",
        "Read applications.get_context, including pinned_resume. Write a cover letter using only supported candidate facts and the job description for role context. Never write a resume. Call documents.save with document_type='cover_letter', application_id and approved=false. Call applications.create_bundle to date-stamp the draft package, then workflow.next.",
        "Drafting your cover letter for review."),
    "review_drafts": ("review_drafts", None,
        "Show ui.show_application and its frontend_url so the user can read every draft. Approve only versions the user explicitly approves using applications.approve_drafts. Then call workflow.next.", None),
    "package_complete": ("done", None,
        "Share the review URL. If the user requests a bundle, call applications.create_bundle. For regeneration use applications.prepare_drafts. Never submit or change the active flag automatically.", None),
    "inactive": ("inactive", None,
        "Share the application URL. Leave the active flag unchanged unless the user requests a change.", None),
}

# Every step_id the engine can emit. A closed set is assertable; prose is not.
STEP_IDS = tuple(dict.fromkeys([entry[0] for entry in STEP_MAP.values()] + ["capture_posting"]))


def _step(
    step_id: str,
    say_to_user: str,
    you_must: str,
    then_call: str | None,
    frontend_url: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "say_to_user": say_to_user,
        "you_must": you_must,
        "then_call": then_call,
        "waits_for_user": then_call is None,
        "frontend_url": frontend_url,
        "must_share_url": True,
        **extra,
    }


def next_step(services: Any, application_id: int | None = None, url: str = "") -> dict[str, Any]:
    """Return the single next step. Deterministic for a given database state."""
    base_url = services.base_url

    if application_id is None:
        match = find_by_url(services, url) if url.strip() else None
        if match is None:
            return _step(
                "capture_posting",
                (
                    f"Saving this posting now: {url.strip()}"
                    if url.strip()
                    else "Send me a job posting URL, or paste the job description, and I will start an application."
                ),
                (
                    "Retrieve the posting details, then call applications.create with the company name and "
                    "the formal job title. Then call workflow.next with the new application_id."
                    if url.strip()
                    else "Say say_to_user word for word, then stop."
                ),
                "applications.create" if url.strip() else None,
                f"{base_url}/applications",
                application_id=None,
            )
        application_id = match["id"]

    progress = services.applications.progress(application_id)
    action = progress["next_action"]
    application_url = f"{base_url}/applications/{application_id}"
    step_id, then_call, you_must, say_template = STEP_MAP.get(
        action["code"], ("done", None, "Say say_to_user word for word, then stop.", None)
    )

    if say_template:
        # A step that does not stop for the user states what it is doing.
        say = say_template.format(
            question_number=progress["questions_answered"] + 1,
            question_total=progress["questions_found"],
        )
    else:
        # A step that stops for the user asks progress()'s own question, and
        # carries the link, because that is the moment the user needs it.
        say = action.get("question") or action.get("instruction") or ""
        setup_url = action.get("setup_url") or ""
        if application_url not in say and (not setup_url or setup_url not in say):
            say = f"{say} Review it here: {application_url}".strip()

    # The ids of what the user is being asked about travel with the step, so the
    # agent never has to guess which draft the user meant.
    pending = {"pending": action["pending"]} if action.get("pending") else {}

    return _step(
        step_id,
        say,
        you_must,
        then_call,
        action.get("setup_url") or application_url,
        **pending,
        application_id=application_id,
        application_url=application_url,
        capability=progress["capability"],
        percent_complete=progress["percent_complete"],
        blocked_by=action.get("blocked_by", progress["blocking_items"]),
        ready_to_apply=progress["ready_to_apply"],
    )


def find_by_url(services: Any, url: str) -> dict[str, Any] | None:
    """An existing application for this posting, so a re-paste never duplicates.

    The lookup itself lives on `ApplicationService.find_by_url` (reused by
    `ApplicationService.create` for its own duplicate check); this stays as a
    thin wrapper so the workflow engine keeps its existing call shape.
    """
    return services.applications.find_by_url(url)
