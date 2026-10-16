import json
import pytest


def test_complete_application_context_and_immutable_archive(services):
    services.candidate.update_profile(name="Ada Engineer",email="ada@example.com",summary="Embedded systems engineer")
    services.candidate.add_fact("project","Flight computer","Built and validated avionics",tags="firmware,HIL",verified=True)
    services.documents.save("master_resume","Master",text_content="Ada resume",approved=True)
    app=services.applications.create("Orbital Labs","Flight Software Engineer",url="https://example.test/job",job_description="Build flight software")
    question=services.questions.add(app["id"],"Describe embedded systems experience",required_field=True,character_limit=500)
    first=services.questions.save_answer(question["id"],"First draft","model")
    final=services.questions.save_answer(question["id"],"Validated flight computers.","reviewed",approved=True)
    resume=services.documents.save("application_resume","Tailored resume",app["id"],"Tailored text",approved=True)
    services.documents.save("cover_letter","Cover letter",app["id"],"Letter text",approved=True)
    asset=services.assets.save("Questions screenshot","questions.png",b"not-a-real-png",app["id"],"screenshot")
    services.events.add(app["id"],"Assessment","2026-08-29T10:00","Coding exercise")
    services.applications.add_contact(app["id"],"Grace Recruiter","Recruiter","grace@example.com")

    context=services.applications.get_context(app["id"])
    assert context["candidate"]["profile"]["name"]=="Ada Engineer"
    assert context["questions"][0]["current_answer"]["id"]==final["id"]
    assert context["completeness"]["job_description"] is True
    assert context["pinned_resume"]["document_type"] == "master_resume"
    assert context["completeness"]["questions_found"]==1
    assert context["completeness"]["questions_answered"]==1
    assert services.assets.content(asset["id"])==b"not-a-real-png"
    assert services.documents.content(resume["id"])==b"Tailored text"

    services.applications.update(app["id"],status="Applied")
    snapshot=services.applications.get_archive(app["id"])
    services.questions.save_answer(question["id"],"Later rewrite","manual",approved=True)
    services.documents.save("application_resume","Later resume",app["id"],"Changed",approved=True)

    preserved=services.applications.get_archive(app["id"])
    assert preserved==snapshot
    assert preserved["questions"][0]["current_answer"]["id"]==final["id"]
    assert next(d for d in preserved["documents"] if d["document_type"]=="application_resume")["id"]==resume["id"]
    assert json.dumps(preserved)
    assert first["version"]==1 and final["version"]==2


def test_generation_request_is_limited_to_answers_and_letter(ready_services):
    services = ready_services
    app = services.applications.create("Orbital Labs", "Engineer", job_description="Build flight software")
    assert not services.applications.prepare_drafts(app["id"])["valid"]
    services.applications.set_questions_reviewed(app["id"])
    plan = services.applications.prepare_drafts(app["id"])
    assert plan["valid"]
    assert "ONLY the application answers and cover letter" in plan["agent_prompt"]
    assert "approved=false" in plan["agent_prompt"]
    assert "Never generate or change a resume" in plan["agent_prompt"]
    assert "applications.create_bundle" in plan["agent_prompt"]


def test_versions_are_scoped_and_approval_is_singular(services):
    app=services.applications.create("A","Engineer")
    one=services.documents.save("cover_letter","One",app["id"],"one",approved=True)
    two=services.documents.save("cover_letter","Two",app["id"],"two",approved=True)
    docs=services.documents.list(app["id"],"cover_letter")
    assert [d["version"] for d in docs]==[2,1]
    assert [d["id"] for d in docs if d["approved"]]==[two["id"]]
    assert one["id"]!=two["id"]
    with pytest.raises(ValueError,match="Unsupported application status"):
        services.applications.update(app["id"],status="Invented Workflow")


def test_progress_needs_resume_but_not_duplicate_profile_fields(services):
    app = services.applications.create("Progress Co", "Engineer", job_description="Build systems")
    assert services.applications.progress(app["id"])["next_action"]["code"] == "complete_candidate_setup"
    services.documents.save("master_resume", "Master", text_content="Resume", approved=True)
    progress = services.applications.progress(app["id"])
    assert progress["capability"] == "full"
    assert progress["next_action"]["code"] == "inventory_questions"
    assert "candidate_profile" not in [check["key"] for check in progress["checks"]]


def test_progress_finishes_with_reviewed_letter_without_tailored_resume(ready_services):
    services = ready_services
    app = services.applications.create("Progress Co", "Engineer", job_description="Build systems")
    services.applications.set_questions_reviewed(app["id"])
    assert services.applications.progress(app["id"])["next_action"]["code"] == "prepare_cover_letter"
    services.documents.save("cover_letter", "Letter", app["id"], "Letter", approved=True)
    progress = services.applications.progress(app["id"])
    assert progress["complete"] is True
    assert progress["percent_complete"] == 100
    assert progress["next_action"]["code"] == "package_complete"
    assert services.documents.list(app["id"], "application_resume") == []


def test_academic_document_is_extracted_enabled_and_marked_considered(ready_services):
    services=ready_services
    transcript=services.documents.save(
        "academic_transcript",
        "Transcript",
        filename="transcript.txt",
        content=b"BSc Electrical Engineering\nGPA 3.8\nControl Systems",
        approved=True,
        use_for_applications=True,
    )
    assert transcript["extraction_status"]=="extracted"
    assert "GPA 3.8" in transcript["text_content"]
    candidate=services.candidate.get_context()
    assert candidate["application_context_documents"][0]["resource_uri"]==f"jobtron://documents/{transcript['id']}"

    app=services.applications.create("Academic Co","Controls Engineer",job_description="Design controls")
    progress=services.applications.progress(app["id"])
    assert progress["next_action"]["code"]=="inventory_questions"
    services.documents.mark_considered(app["id"],transcript["id"],"Used degree and coursework")
    progress=services.applications.progress(app["id"])
    assert progress["next_action"]["code"]=="inventory_questions"
    assert services.documents.usage(app["id"])[0]["document_id"]==transcript["id"]


def test_question_bank_searches_wording_variants_and_reuses_answers(services):
    first_app=services.applications.create("First Co","Engineer")
    first_question=services.questions.add(first_app["id"],"Are you legally authorized to work in the United States?",required_field=True)
    services.questions.save_answer(
        first_question["id"],
        "Yes, I am authorized to work in the United States.",
        approved=True,
        save_to_bank=True,
        keywords="work authorization eligibility united states",
    )

    matches=services.questions.search_bank("Do you have United States work authorization?")
    assert matches and matches[0]["answer"].startswith("Yes")
    second_app=services.applications.create("Second Co","Engineer")
    second_question=services.questions.add(second_app["id"],"Are you eligible to work in the US?",required_field=True)
    reused=services.questions.apply_bank_answer(second_question["id"],matches[0]["id"],approved=True)
    assert reused["source_label"]==f"question_bank:{matches[0]['id']}"
    assert services.questions.list(second_app["id"])[0]["current_answer"]["content"]==matches[0]["answer"]


def test_search_bank_reports_query_token_coverage(services):
    """Coverage is the auto-apply gate, so it must mean "every query token matched"."""
    services.questions.save_bank_entry("Are you willing to travel?","Yes, up to 25%.",approved=True)
    services.questions.save_bank_entry("Do you require visa sponsorship?","No.",approved=False)

    exact=services.questions.search_bank("Are you willing to travel?",limit=1)[0]
    assert exact["coverage"]==1.0
    partial=services.questions.search_bank("Are you willing to relocate?",limit=1)[0]
    assert partial["coverage"]==pytest.approx(0.8)
    sponsorship=services.questions.search_bank("Do you require visa sponsorship?")
    assert all(hit["canonical_question"]!="Do you require visa sponsorship?" for hit in sponsorship), "unapproved entries never surface"
