"""The tracker workflow drafts answers and a letter, then stops for review."""

import asyncio
import pytest
from app.mcp.server import build_server
from app.services.core import ApplicationService, NotFoundError
from app.services.workflow import STEP_IDS, STEP_MAP, find_by_url, next_step

def test_then_call_is_always_null_or_a_real_tool():
    allowed = {
        "applications.create", "applications.update", "documents.save", "documents.mark_considered",
        "question_bank.search", "questions.add", "applications.set_questions_reviewed",
        "questions.extract_from_posting", "questions.detect_from_text",
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


def _drafted_application(services):
    """All required answers drafted, resume and cover letter drafted, nothing approved."""
    app = services.applications.create("Boeing", "GNC Engineer", job_description="Build GNC")
    services.applications.update(app["id"], cover_letter_required=True)
    question = services.questions.add(app["id"], "Why do you want to work at Boeing?", required_field=True)
    services.applications.set_questions_reviewed(app["id"])
    services.questions.save_answer(question["id"], "Because of the mission.", "model", approved=False)
    return app, question


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


def _application_with_a_failed_fetch(services, status="blocked", note="The site refused the request (HTTP 403)."):
    app = services.applications.create(
        "Boeing", "GNC Engineer", url="https://jobs.boeing.com/job/1", job_description="Build GNC"
    )
    services.applications.record_question_extraction(app["id"], status, note)
    return app


def test_retry_true_clears_a_recorded_status_and_refetches(ready_services, monkeypatch):
    services = ready_services
    app = _application_with_a_failed_fetch(services)
    calls = []

    def fake_fetch(url, timeout=10.0):
        calls.append(url)
        return {
            "status": "ok", "url": url, "text": "Design GNC systems.",
            "detected_questions": ["Why do you want to work here?"], "reason": "Retrieved.", "ats_markers": [],
        }

    import app.mcp.server as server_module

    monkeypatch.setattr(server_module, "fetch_posting", fake_fetch)
    mcp = build_server(services)

    result = asyncio.run(
        mcp.call_tool("questions.extract_from_posting", {"application_id": app["id"], "retry": True})
    )

    assert calls == ["https://jobs.boeing.com/job/1"], "retry=true must still fetch, exactly once"
    assert result.structured_content["status"] == "ok"
    assert result.structured_content.get("cached") is not True
    assert services.applications.get(app["id"])["questions_extraction_status"] == "ok"


def test_retry_false_with_a_recorded_status_is_cached_and_never_touches_the_network(ready_services, monkeypatch):
    services = ready_services
    app = _application_with_a_failed_fetch(services)
    calls = []

    def fake_fetch(url, timeout=10.0):
        calls.append(url)
        return {"status": "ok", "url": url, "text": "", "detected_questions": [], "reason": "", "ats_markers": []}

    import app.mcp.server as server_module

    monkeypatch.setattr(server_module, "fetch_posting", fake_fetch)
    mcp = build_server(services)

    result = asyncio.run(mcp.call_tool("questions.extract_from_posting", {"application_id": app["id"]}))

    assert calls == [], "a recorded status without retry must never call fetch_posting"
    assert result.structured_content["cached"] is True
    assert result.structured_content["status"] == "blocked"
    assert result.structured_content["detected_questions"] == []



AUTHORIZATION = "Are you legally authorized to work in the United States?"


def _application_with_question(services, question_text):
    app = services.applications.create("Boeing", "GNC Engineer", job_description="Build GNC")
    question = services.questions.add(app["id"], question_text, required_field=True)
    services.applications.set_questions_reviewed(app["id"])
    return app, question


def test_full_workflow_stops_for_review_and_never_generates_a_resume(ready_services):
    svc = ready_services
    app = svc.applications.create("Acme", "Engineer", job_description="Build systems")
    assert next_step(svc, app["id"])["step_id"] == "inventory_questions"
    q = svc.questions.add(app["id"], "Why this role?", required_field=False)
    svc.applications.set_questions_reviewed(app["id"])
    assert next_step(svc, app["id"])["step_id"] == "answer_question"
    svc.questions.save_answer(q["id"], "I enjoy systems engineering.", approved=False)
    assert next_step(svc, app["id"])["step_id"] == "generate_cover_letter"
    svc.documents.save("cover_letter", "Letter", app["id"], "My letter", approved=False)
    review = next_step(svc, app["id"])
    assert review["step_id"] == "review_drafts"
    assert review["then_call"] is None
    assert review["frontend_url"] in review["say_to_user"]
    assert len(review["pending"]) == 2
    svc.applications.approve_drafts(app["id"], all=True)
    assert next_step(svc, app["id"])["step_id"] == "done"
    assert not svc.documents.list(app["id"], "application_resume")
    svc.questions.save_answer(q["id"], "A revised draft.")
    assert next_step(svc, app["id"])["step_id"] == "review_drafts"
    svc.applications.set_active(app["id"], False)
    assert next_step(svc, app["id"])["step_id"] == "inactive"


def test_every_progress_code_maps_to_current_scope():
    assert set(STEP_MAP) == {"capture_job_description", "complete_candidate_setup", "inventory_questions", "answer_required_question", "prepare_cover_letter", "review_drafts", "package_complete", "inactive"}
