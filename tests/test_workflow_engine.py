"""Phase 2: the engine must emit exactly one step, from a closed set, deterministically."""

import pytest

from app.services.core import ApplicationService
from app.services.workflow import STEP_IDS, STEP_MAP, find_by_url, next_step


def test_every_progress_code_maps_to_a_step():
    """No next_action code may fall through to a default the agent cannot act on."""
    known = {
        "capture_job_description", "complete_candidate_setup", "consider_academic_documents",
        "extract_questions", "inventory_questions", "answer_required_question", "prepare_resume",
        "prepare_cover_letter", "confirm_submission", "post_submission_follow_up",
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
