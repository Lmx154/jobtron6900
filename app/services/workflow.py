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
    "capture_job_description": (
        "need_job_description",
        None,
        "Say say_to_user word for word, then stop. When the user replies with the posting text, "
        "call applications.update with that text as job_description, preserving every other field.",
        None,
    ),
    "complete_candidate_setup": (
        "need_candidate_setup",
        None,
        "Say say_to_user word for word, including the URL, then stop. Do not generate a resume, a "
        "cover letter, or any answer. When the user says they are done, call workflow.next again.",
        None,
    ),
    "consider_academic_documents": (
        "consider_academic",
        "documents.mark_considered",
        "Read each resource_uri listed in next_action, then call documents.mark_considered once per "
        "document id. Then call workflow.next again.",
        "Reading your saved academic documents before I write anything.",
    ),
    "extract_questions": (
        "extract_questions",
        "questions.extract_from_posting",
        "Call questions.extract_from_posting for this application. If it returns questions, add each "
        "one with questions.add and then call applications.set_questions_reviewed. If it returns none, "
        "call workflow.next again and relay what it says.",
        "Checking the posting for application questions.",
    ),
    # D7: this step's wait depends on the client (does it have a browser tool?),
    # not on server state, so it is a deliberate, documented exception to
    # "working steps narrate, stopping steps ask" -- it narrates (then_call is
    # set, waits_for_user reports False) even though the paste fallback inside
    # you_must may, in practice, leave the agent stopped and waiting.
    "capture_page_text": (
        "capture_page_text",
        "questions.detect_from_text",
        "If you have a browser tool, open next_action.url, read the page's visible text, and call "
        "questions.detect_from_text with source='browser'. If you have no browser tool, say say_to_user "
        "word for word and stop; when the user pastes the page, call questions.detect_from_text with "
        "source='paste'. Then call workflow.next again.",
        "Reading the posting page for application questions.",
    ),
    "inventory_questions": (
        "inventory_questions",
        None,
        "Say say_to_user word for word, then stop. When the user supplies questions, call "
        "questions.add once per question, then applications.set_questions_reviewed.",
        None,
    ),
    "answer_required_question": (
        "answer_question",
        "question_bank.search",
        "Call question_bank.search with the question text FIRST. If a match is returned, apply it with "
        "question_bank.apply. Only if there is no match, draft an answer from the master resume and the "
        "candidate facts, then call answers.save with approved=false. Invent nothing.",
        "Answering question {question_number} of {question_total}.",
    ),
    "prepare_resume": (
        "generate_resume",
        "documents.save",
        "Read the approved master resume through its resource_uri. Write a tailored resume using ONLY "
        "facts that appear in that document, in the candidate facts, or in the job description. Then "
        "call documents.save with document_type='application_resume' and approved=false, and call "
        "workflow.next again.",
        "Writing your tailored resume from your master resume.",
    ),
    "prepare_cover_letter": (
        "generate_cover_letter",
        "documents.save",
        "Write the cover letter using ONLY facts already in Jobtron. Then call documents.save with "
        "document_type='cover_letter' and approved=false, and call workflow.next again.",
        "Writing the cover letter this posting requires.",
    ),
    "review_drafts": (
        "review_drafts",
        None,
        "Say say_to_user word for word, including the URL, then stop. When the user approves, call "
        "applications.approve_drafts with the ids they named, or all=true. Then call workflow.next again.",
        None,
    ),
    "confirm_submission": (
        "confirm_submission",
        None,
        "Say say_to_user word for word, including the URL, then stop. Mark the application Applied only "
        "after the user confirms they submitted it externally.",
        None,
    ),
    "post_submission_follow_up": (
        "done",
        None,
        "Say say_to_user word for word, including the URL, then stop.",
        None,
    ),
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
