"""Phase 1: candidate readiness must gate application work, and must say so plainly."""

from app.services.core import REQUIRED_PROFILE_FIELDS


def test_empty_candidate_is_draft_only(services):
    readiness = services.candidate.readiness()
    assert readiness["capability"] == "draft_only"
    assert readiness["profile_complete"] is False
    assert readiness["master_resume_available"] is False
    assert list(readiness["missing_profile_fields"]) == list(REQUIRED_PROFILE_FIELDS)
    assert readiness["must_share_url"] is True
    assert "original resume" in readiness["say_to_user"]
    assert readiness["setup_url"] in readiness["say_to_user"]


def test_capability_climbs_as_each_piece_lands(services):
    assert services.candidate.readiness()["capability"] == "draft_only"

    services.candidate.update_profile(
        name="Ada", email="ada@example.com", phone="555-0100", location="Seal Beach, CA"
    )
    partial = services.candidate.readiness()
    assert partial["capability"] == "draft_only"
    assert partial["profile_complete"] is True
    assert partial["blocked_by"] == ["master_resume"]

    services.documents.save("master_resume", "Master", text_content="Resume", approved=True)
    full = services.candidate.readiness()
    assert full["capability"] == "full"
    assert full["blocked_by"] == []
    assert full["must_share_url"] is False


def test_unapproved_master_resume_does_not_satisfy_the_gate(services):
    services.candidate.update_profile(
        name="Ada", email="ada@example.com", phone="555-0100", location="Seal Beach, CA"
    )
    services.documents.save("master_resume", "Draft", text_content="Resume", approved=False)
    readiness = services.candidate.readiness()
    assert readiness["master_resume_available"] is False
    assert readiness["master_resume_count"] == 1
    assert readiness["capability"] == "draft_only"


def test_whitespace_only_profile_field_is_not_complete(services):
    services.candidate.update_profile(name="Ada", email="ada@example.com", phone="   ", location="CA")
    readiness = services.candidate.readiness()
    assert readiness["missing_profile_fields"] == ["phone"]
    assert readiness["capability"] == "draft_only"


def test_gap_phrase_reads_as_a_sentence(services):
    phrase = services.candidate.gap_phrase(["phone", "location"], False)
    assert phrase == (
        "you have no master resume saved and your profile is missing your phone number and location"
    )
    assert services.candidate.gap_phrase([], True) == "your candidate setup is complete"
    assert services.candidate.gap_phrase(["email"], True) == "your profile is missing your email"


def test_setup_url_is_absolute_when_the_server_knows_its_base_url(tmp_path):
    from app.db import Database
    from app.services import Services
    from app.storage import FileStorage

    db = Database(tmp_path / "app.db")
    db.initialize()
    services = Services(db, FileStorage(tmp_path / "files"), "http://127.0.0.1:8765/")
    assert services.candidate.readiness()["setup_url"] == "http://127.0.0.1:8765/profile"


def test_progress_reports_the_gate_and_keeps_the_draft(services):
    """The draft is still created; the gate only blocks generation."""
    app = services.applications.create(
        "Boeing", "Guidance Navigation & Control Engineer", job_description="Build GNC systems"
    )
    progress = services.applications.progress(app["id"])

    assert app["status"] == "Draft"
    assert progress["capability"] == "draft_only"
    assert progress["ready_to_apply"] is False
    assert [check["key"] for check in progress["checks"]][:2] == ["master_resume", "job_description"]
    assert progress["next_action"]["code"] == "complete_candidate_setup"

    question = progress["next_action"]["question"]
    assert "master resume" in question
    assert "/profile" in question


def test_job_description_is_still_captured_before_the_setup_gate(services):
    """Capturing the posting costs the user nothing, so it stays first."""
    app = services.applications.create("Boeing", "GNC Engineer", url="https://jobs.boeing.com/x")
    progress = services.applications.progress(app["id"])
    assert progress["next_action"]["code"] == "capture_job_description"
