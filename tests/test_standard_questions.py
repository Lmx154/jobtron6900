"""Phase 7: standard answers, asked once during setup and auto-applied by P4."""

import asyncio
import json

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.standard_questions import STANDARD_QUESTIONS
from app.services.workflow import next_step

# Three real-world phrasings per canonical entry (collected from common ATS
# forms), keyed by `key`. The tokenizer (`QuestionService._tokens`, core.py)
# has no stopword list, so every token of each phrasing here must land in
# that entry's canonical_question + keywords for coverage == 1.0.
PHRASINGS = {
    "work_authorization": [
        "Are you legally authorized to work in the United States?",
        "Are you authorized to work in this country?",
        "Do you have the legal right to work in the US?",
    ],
    "sponsorship": [
        "Will you now or in the future require sponsorship for employment visa status?",
        "Do you require visa sponsorship to work for our company?",
        "Will you need sponsorship to obtain or maintain work authorization in the future?",
    ],
    "salary_expectation": [
        "What are your salary expectations?",
        "What is your desired salary range?",
        "What salary are you seeking for this role?",
    ],
    "start_date": [
        "When can you start?",
        "What is your earliest available start date?",
        "How soon can you begin employment?",
    ],
    "relocation": [
        "Are you willing to relocate?",
        "Would you be willing to relocate for this position?",
        "Are you open to relocation?",
    ],
    "work_location_preference": [
        "Do you prefer remote, hybrid, or onsite work?",
        "What is your work location preference: remote, hybrid, or in office?",
        "Are you looking for a remote, hybrid, or on-site role?",
    ],
    "referral_source": [
        "How did you hear about this position?",
        "Where did you hear about this job opening?",
        "How did you find out about our company?",
    ],
    "previously_applied": [
        "Have you previously applied to or worked for this company?",
        "Have you ever been employed by this company before?",
        "Have you applied to this company in the past?",
    ],
    "security_clearance": [
        "Do you hold an active security clearance?",
        "Do you currently have a government security clearance?",
        "What level of security clearance do you hold?",
    ],
    "eeo_disclosure": [
        "Do you voluntarily wish to self-identify your veteran or disability status for EEO purposes?",
        "Do you identify as a protected veteran?",
        "Would you like to self-identify your disability status voluntarily?",
    ],
    "linkedin_portfolio_url": [
        "What is your LinkedIn profile URL?",
        "Please share a link to your portfolio or personal website.",
        "What is your GitHub or portfolio link?",
    ],
    "years_experience": [
        "How many years of experience do you have in this role?",
        "How many years of work experience do you have?",
        "What is your total number of years in this field?",
    ],
}

UNRELATED_QUESTION = "Describe a time you resolved a conflict on your team."


def _entry(key):
    return next(entry for entry in STANDARD_QUESTIONS if entry["key"] == key)


def _answer_seed(services, key, content="A candid, specific answer."):
    """Answer a seeded entry through the same save path `question_bank.save` uses."""
    entry = _entry(key)
    bank_entry = services.questions.get_bank_entry_by_question(entry["canonical_question"])
    return services.questions.save_bank_entry(
        entry["canonical_question"], content, entry["keywords"], approved=True,
        bank_entry_id=bank_entry["id"] if bank_entry else None,
    )


def test_seeding_twice_inserts_twelve_once(services):
    assert services.questions.seed_defaults() == {"inserted": 12, "existing": 0}
    assert services.questions.seed_defaults() == {"inserted": 0, "existing": 12}
    assert len(services.db.all("SELECT * FROM question_bank")) == 12


def test_an_unanswered_seed_never_appears_in_search_bank(services):
    services.questions.seed_defaults()
    for entry in STANDARD_QUESTIONS:
        assert services.questions.search_bank(entry["canonical_question"]) == []


def test_each_standard_entry_matches_its_realistic_phrasings_at_full_coverage(services):
    services.questions.seed_defaults()
    for entry in STANDARD_QUESTIONS:
        _answer_seed(services, entry["key"])

    for entry in STANDARD_QUESTIONS:
        for phrasing in PHRASINGS[entry["key"]]:
            hits = services.questions.search_bank(phrasing, limit=50)
            hit = next((row for row in hits if row["canonical_question"] == entry["canonical_question"]), None)
            assert hit is not None, f"{entry['key']!r}: {phrasing!r} did not match its own seed"
            assert hit["coverage"] == 1.0, f"{entry['key']!r}: {phrasing!r} coverage was {hit['coverage']}"

    unrelated_hits = services.questions.search_bank(UNRELATED_QUESTION, limit=50)
    assert all(hit["coverage"] != 1.0 for hit in unrelated_hits)


def test_readiness_reports_standard_answers_without_touching_capability(services):
    before = services.candidate.readiness()
    assert before["standard_answers"] == {
        "answered": 0, "total": 12, "missing": [entry["canonical_question"] for entry in STANDARD_QUESTIONS],
    }
    capability_before = before["capability"]

    services.questions.seed_defaults()
    for entry in STANDARD_QUESTIONS[:3]:
        _answer_seed(services, entry["key"])

    after = services.candidate.readiness()
    assert after["standard_answers"]["answered"] == 3
    assert len(after["standard_answers"]["missing"]) == 9
    assert set(after["standard_answers"]["missing"]) == {
        entry["canonical_question"] for entry in STANDARD_QUESTIONS[3:]
    }
    assert after["capability"] == capability_before


def test_two_standard_answers_clear_a_required_question_apiece_before_the_resume_step(ready_services):
    """P4's auto-apply gate (coverage == 1.0, approved) is fed by seeded answers."""
    services = ready_services
    services.questions.seed_defaults()
    _answer_seed(services, "work_authorization", "Yes, I am authorized to work in the United States.")
    _answer_seed(services, "relocation", "Yes, I am open to relocation.")

    app = services.applications.create("Boeing", "GNC Engineer", job_description="Build GNC")
    services.questions.add(app["id"], PHRASINGS["work_authorization"][1], required_field=True)
    services.questions.add(app["id"], PHRASINGS["relocation"][1], required_field=True)
    services.applications.set_questions_reviewed(app["id"])

    step = next_step(services, app["id"])

    assert step["step_id"] == "generate_resume", "both required questions should auto-fill from the seeded bank"
    questions = services.questions.list(app["id"])
    assert len(questions) == 2
    for question in questions:
        assert question["current_answer"] is not None
        assert question["current_answer"]["source_label"] == "question_bank:auto"
        assert question["current_answer"]["approved"] == 1


def test_mcp_seed_defaults_round_trips(settings):
    app = create_app(settings)
    first = asyncio.run(app.state.mcp.call_tool("question_bank.seed_defaults", {}))
    assert json.loads(first.content[0].text) == {"inserted": 12, "existing": 0}
    second = asyncio.run(app.state.mcp.call_tool("question_bank.seed_defaults", {}))
    assert json.loads(second.content[0].text) == {"inserted": 0, "existing": 12}
    assert len(app.state.services.db.all("SELECT * FROM question_bank")) == 12


def test_profile_page_lists_and_saves_standard_answers(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        html = client.get("/profile").text
        for entry in STANDARD_QUESTIONS:
            assert entry["canonical_question"] in html

        response = client.post(
            "/profile/standard-answers",
            data={"answer_work_authorization": "Yes, I am authorized to work in the United States."},
            follow_redirects=False,
        )
        assert response.status_code == 303

        html = client.get("/profile").text
        assert "Yes, I am authorized to work in the United States." in html

    entry = _entry("work_authorization")
    bank_entry = app.state.services.questions.get_bank_entry_by_question(entry["canonical_question"])
    assert bank_entry is not None
    assert bank_entry["approved"] == 1
    assert bank_entry["answer"] == "Yes, I am authorized to work in the United States."
    # Only the one answered field was written; the other eleven stayed seeded-but-empty.
    assert len(app.state.services.db.all("SELECT * FROM question_bank")) == 12
    other = app.state.services.questions.get_bank_entry_by_question(_entry("relocation")["canonical_question"])
    assert other["answer"] == ""
    assert other["approved"] == 0


ADVERSARIAL_QUESTIONS = [
    # Each of these shares every token with some seed's keywords in an earlier draft,
    # and would have been auto-applied without review. Citizenship is not authorization.
    "Are you a US citizen?",
    "Do you have relevant experience?",
    "Do you have experience with Python?",
    "Are you available to start?",
    "What is your current salary?",
    "Have you worked here before?",
    "Do you have a website?",
]


def test_adversarial_questions_never_reach_full_coverage_against_any_seed():
    import re

    from app.services.standard_questions import STANDARD_QUESTIONS

    def tokens(value: str) -> set[str]:
        return {tok for tok in re.findall(r"[a-z0-9]+", value.lower()) if len(tok) > 1}

    for question in ADVERSARIAL_QUESTIONS:
        query = tokens(question)
        for entry in STANDARD_QUESTIONS:
            searchable = tokens(entry["canonical_question"] + " " + entry["keywords"])
            coverage = len(query & searchable) / len(query)
            assert coverage < 1.0, f"{question!r} fully covers seed {entry['key']!r}"
