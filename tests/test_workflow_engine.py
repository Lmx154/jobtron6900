"""Phase 2: the engine must emit exactly one step, from a closed set, deterministically."""

import pytest

from app.services.core import ApplicationService, NotFoundError
from app.services.workflow import STEP_IDS, STEP_MAP, find_by_url, next_step


def test_every_progress_code_maps_to_a_step():
    """No next_action code may fall through to a default the agent cannot act on."""
    known = {
        "capture_job_description", "complete_candidate_setup", "consider_academic_documents",
        "extract_questions", "inventory_questions", "answer_required_question", "prepare_resume",
        "prepare_cover_letter", "review_drafts", "confirm_submission", "post_submission_follow_up",
    }
    assert set(STEP_MAP) == known


def test_then_call_is_always_null_or_a_real_tool():
    allowed = {
        "applications.create", "applications.update", "documents.save", "documents.mark_considered",
        "question_bank.search", "questions.add", "applications.set_questions_reviewed",
        "questions.extract_from_posting",
    }
    for step_id, then_call, you_must, say_template in STEP_MAP.values():
        assert step_id in STEP_IDS
        assert then_call is None or then_call in allowed, f"{step_id} names an unknown tool"
        assert you_must.strip(), f"{step_id} has no instruction"
        # A step that keeps working must state what it is doing; a step that
        # stops must ask the user something. Never the other way round.
        if then_call is None:
            assert say_template is None, f"{step_id} waits, so it must ask progress()'s question"
        else:
            assert say_template, f"{step_id} does not wait, so it must not ask a question"
            assert not say_template.rstrip().endswith("?"), f"{step_id} says a question but does not wait"


def test_a_bare_url_starts_by_capturing_the_posting(services):
    step = next_step(services, url="https://jobs.boeing.com/job/1")
    assert step["step_id"] == "capture_posting"
    assert step["then_call"] == "applications.create"
    assert step["waits_for_user"] is False
    assert "https://jobs.boeing.com/job/1" in step["say_to_user"]


def test_no_url_and_no_application_asks_for_one(services):
    step = next_step(services)
    assert step["step_id"] == "capture_posting"
    assert step["then_call"] is None
    assert step["waits_for_user"] is True


def test_re_pasting_a_known_url_resumes_instead_of_duplicating(services):
    app = services.applications.create(
        "Boeing", "GNC Engineer", url="https://jobs.boeing.com/job/1", job_description="Build GNC"
    )
    step = next_step(services, url="https://jobs.boeing.com/job/1/")
    assert step["application_id"] == app["id"]
    assert step["step_id"] != "capture_posting"
    assert len(services.applications.list()) == 1


def test_the_setup_gate_is_the_first_thing_after_a_draft(services):
    app = services.applications.create("Boeing", "GNC Engineer", job_description="Build GNC")
    step = next_step(services, app["id"])
    assert step["step_id"] == "need_candidate_setup"
    assert step["then_call"] is None
    assert step["waits_for_user"] is True
    assert step["capability"] == "draft_only"
    assert "master resume" in step["say_to_user"]
    assert step["frontend_url"].endswith("/profile")
    assert "/profile" in step["say_to_user"]


def test_every_step_that_stops_hands_over_the_link(ready_services):
    """'Always send the link' is data, not memory: it is inside the text to be said."""
    services = ready_services
    app = services.applications.create("Boeing", "GNC Engineer", job_description="Build GNC")
    advance = [
        lambda: services.applications.set_questions_reviewed(app["id"]),
        lambda: services.documents.save("application_resume", "T", app["id"], "x", approved=True),
        lambda: services.applications.set_status(app["id"], "Applied"),
    ]
    seen = []
    for step_forward in advance + [None]:
        step = next_step(services, app["id"])
        seen.append(step["step_id"])
        assert step["must_share_url"] is True
        if step["waits_for_user"]:
            assert step["frontend_url"] in step["say_to_user"], step["step_id"]
        if step_forward:
            step_forward()
    assert "confirm_submission" in seen


def test_a_working_step_reports_progress_rather_than_asking(ready_services):
    services = ready_services
    app = services.applications.create("Boeing", "GNC Engineer", job_description="Build GNC")
    services.applications.set_questions_reviewed(app["id"])
    step = next_step(services, app["id"])
    assert step["step_id"] == "generate_resume"
    assert step["waits_for_user"] is False
    assert step["say_to_user"] == "Writing your tailored resume from your master resume."


def test_the_application_question_is_never_read_back_to_the_user(ready_services):
    """say_to_user is addressed to the human; the question text is work input."""
    services = ready_services
    app = services.applications.create("Boeing", "GNC Engineer", job_description="Build GNC")
    services.questions.add(app["id"], "Why do you want to work at Boeing?", required_field=True)
    services.applications.set_questions_reviewed(app["id"])
    step = next_step(services, app["id"])
    assert step["step_id"] == "answer_question"
    assert "Why do you want to work at Boeing?" not in step["say_to_user"]
    assert step["say_to_user"] == "Answering question 1 of 1."


def test_the_full_sequence_advances_one_step_at_a_time(ready_services):
    services = ready_services
    app = services.applications.create("Boeing", "GNC Engineer", job_description="Build GNC")

    assert next_step(services, app["id"])["step_id"] == "inventory_questions"
    services.applications.set_questions_reviewed(app["id"])

    assert next_step(services, app["id"])["step_id"] == "generate_resume"
    services.documents.save("application_resume", "Tailored", app["id"], "Text", approved=True)

    step = next_step(services, app["id"])
    assert step["step_id"] == "confirm_submission"
    assert step["ready_to_apply"] is True
    assert step["percent_complete"] == 100


def test_a_missing_job_description_is_asked_for_first(services):
    app = services.applications.create("Boeing", "GNC Engineer", url="https://jobs.boeing.com/x")
    step = next_step(services, app["id"])
    assert step["step_id"] == "need_job_description"


def test_the_engine_is_deterministic(ready_services):
    app = ready_services.applications.create("Boeing", "GNC Engineer", job_description="Build GNC")
    first = next_step(ready_services, app["id"])
    assert all(next_step(ready_services, app["id"]) == first for _ in range(5))


def test_find_by_url_ignores_trailing_slashes_and_blanks(services):
    assert find_by_url(services, "") is None
    assert find_by_url(services, "   ") is None
    services.applications.create("Boeing", "GNC", url="https://x.test/job")
    assert find_by_url(services, "https://x.test/job/") is not None
    assert find_by_url(services, "https://other.test/job") is None


# Phase 4 · D3: an answer the user already approved is not approved again.

AUTHORIZATION = "Are you legally authorized to work in the United States?"


def _application_with_question(services, question_text):
    app = services.applications.create("Boeing", "GNC Engineer", job_description="Build GNC")
    question = services.questions.add(app["id"], question_text, required_field=True)
    services.applications.set_questions_reviewed(app["id"])
    return app, question


def test_a_fully_covered_approved_bank_answer_never_reaches_the_agent(ready_services):
    services = ready_services
    entry = services.questions.save_bank_entry(
        AUTHORIZATION, "Yes, I am authorized to work in the United States.", approved=True
    )
    app, question = _application_with_question(services, AUTHORIZATION)

    step = next_step(services, app["id"])

    assert step["step_id"] == "generate_resume", "the question was answered from the bank, so it is gone"
    answer = services.questions.list(app["id"])[0]["current_answer"]
    assert answer["source_label"] == "question_bank:auto"
    assert answer["approved"] == 1
    assert answer["content"] == entry["answer"]
    assert services.questions.search_bank(AUTHORIZATION)[0]["usage_count"] == entry["usage_count"] + 1
    # Idempotent: asking again neither re-applies nor re-counts.
    assert next_step(services, app["id"])["step_id"] == "generate_resume"
    assert len(services.questions.list(app["id"])[0]["answers"]) == 1
    assert services.questions.search_bank(AUTHORIZATION)[0]["usage_count"] == 1


def test_an_unapproved_bank_entry_is_never_auto_applied(ready_services):
    services = ready_services
    services.questions.save_bank_entry(AUTHORIZATION, "Yes.", approved=False)
    app, question = _application_with_question(services, AUTHORIZATION)

    step = next_step(services, app["id"])

    assert step["step_id"] == "answer_question"
    assert services.questions.list(app["id"])[0]["answers"] == []


def test_a_partly_covered_bank_entry_is_never_auto_applied(ready_services):
    services = ready_services
    services.questions.save_bank_entry("Are you willing to travel?", "Yes, up to 25%.", approved=True)
    app, question = _application_with_question(services, "Are you willing to relocate?")

    hit = services.questions.search_bank("Are you willing to relocate?", limit=1)[0]
    assert hit["approved"] == 1
    assert hit["coverage"] == pytest.approx(0.8)

    step = next_step(services, app["id"])

    assert step["step_id"] == "answer_question"
    assert services.questions.list(app["id"])[0]["answers"] == []


# Phase 4 · D4: every draft is reviewed at one stop, not one stop per draft.


def _drafted_application(services):
    """All required answers drafted, resume and cover letter drafted, nothing approved."""
    app = services.applications.create("Boeing", "GNC Engineer", job_description="Build GNC")
    services.applications.update(app["id"], cover_letter_required=True)
    question = services.questions.add(app["id"], "Why do you want to work at Boeing?", required_field=True)
    services.applications.set_questions_reviewed(app["id"])
    services.questions.save_answer(question["id"], "Because of the mission.", "model", approved=False)
    return app, question


def test_the_drafting_run_stops_only_once_at_review_drafts(ready_services):
    services = ready_services
    app, question = _drafted_application(services)

    seen = []
    step = next_step(services, app["id"])
    seen.append(step["step_id"])
    assert step["waits_for_user"] is False
    resume = services.documents.save("application_resume", "Tailored", app["id"], "Resume", approved=False)

    step = next_step(services, app["id"])
    seen.append(step["step_id"])
    assert step["waits_for_user"] is False
    letter = services.documents.save("cover_letter", "Letter", app["id"], "Letter", approved=False)

    step = next_step(services, app["id"])
    assert seen == ["generate_resume", "generate_cover_letter"], "no stop between the two drafts"
    assert step["step_id"] == "review_drafts"
    assert step["waits_for_user"] is True
    assert step["then_call"] is None
    assert {(item["kind"], item["id"]) for item in step["pending"]} == {
        ("answer", services.questions.list(app["id"])[0]["answers"][0]["id"]),
        ("document", resume["id"]),
        ("document", letter["id"]),
    }
    assert "1 answer, the tailored resume and the cover letter are drafted." in step["say_to_user"]
    assert step["frontend_url"] in step["say_to_user"]


def test_approving_every_draft_goes_straight_to_confirm_submission(ready_services):
    services = ready_services
    app, question = _drafted_application(services)
    services.documents.save("application_resume", "Tailored", app["id"], "Resume", approved=False)
    services.documents.save("cover_letter", "Letter", app["id"], "Letter", approved=False)
    assert next_step(services, app["id"])["step_id"] == "review_drafts"

    result = services.applications.approve_drafts(app["id"], all=True)

    assert len(result["approved_answers"]) == 1
    assert len(result["approved_documents"]) == 2
    assert result["pending"] == []
    step = next_step(services, app["id"])
    assert step["step_id"] == "confirm_submission"
    assert step["percent_complete"] == 100
    assert step["ready_to_apply"] is True


def test_a_partial_approval_leaves_review_drafts_with_the_rest(ready_services):
    services = ready_services
    app, question = _drafted_application(services)
    resume = services.documents.save("application_resume", "Tailored", app["id"], "Resume", approved=False)
    letter = services.documents.save("cover_letter", "Letter", app["id"], "Letter", approved=False)

    result = services.applications.approve_drafts(app["id"], document_ids=[resume["id"]])

    assert [doc["id"] for doc in result["approved_documents"]] == [resume["id"]]
    assert services.documents.get(resume["id"])["approved"] == 1
    step = next_step(services, app["id"])
    assert step["step_id"] == "review_drafts"
    assert {(item["kind"], item["id"]) for item in step["pending"]} == {
        ("answer", services.questions.list(app["id"])[0]["answers"][0]["id"]),
        ("document", letter["id"]),
    }


def test_approve_drafts_refuses_an_id_from_another_application(ready_services):
    services = ready_services
    app, question = _drafted_application(services)
    mine = services.documents.save("application_resume", "Tailored", app["id"], "Resume", approved=False)
    services.documents.save("cover_letter", "Letter", app["id"], "Letter", approved=False)
    other = services.applications.create("Other Co", "Engineer", job_description="Elsewhere")
    theirs = services.documents.save("application_resume", "Theirs", other["id"], "Resume", approved=False)

    with pytest.raises(NotFoundError):
        services.applications.approve_drafts(app["id"], document_ids=[mine["id"], theirs["id"]])

    assert services.documents.get(mine["id"])["approved"] == 0, "a refused call approves nothing"
    assert services.documents.get(theirs["id"])["approved"] == 0
    assert next_step(services, app["id"])["step_id"] == "review_drafts"


def test_an_applied_application_is_never_asked_for_questions_or_drafts_again(ready_services):
    """Applied ends the build loop; the unfinished items stay visible as status, not as gates."""
    services = ready_services
    app = services.applications.create("Boeing", "GNC Engineer", job_description="Build GNC")
    services.questions.add(app["id"], "Why do you want to work at Boeing?", required_field=True)
    services.applications.set_questions_reviewed(app["id"])
    services.documents.save("application_resume", "Tailored", app["id"], "Resume", approved=False)
    services.applications.set_status(app["id"], "Applied")

    step = next_step(services, app["id"])

    assert step["step_id"] == "done"
    assert step["waits_for_user"] is True
    progress = services.applications.progress(app["id"])
    assert progress["next_action"]["code"] == "post_submission_follow_up"
    assert progress["percent_complete"] < 100
    assert progress["ready_to_apply"] is False
    assert "Tailored resume approved" in progress["blocking_items"]
    assert "Required questions answered" in progress["blocking_items"]
    assert services.questions.list(app["id"])[0]["answers"] == [], "nothing is written after Applied"
