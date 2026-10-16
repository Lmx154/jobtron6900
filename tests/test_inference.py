"""Phase 6 (D8): infer cover_letter_required from the posting, once, and never
overwrite what the user set."""

import asyncio
import json
import sqlite3

import pytest

from app.db import Database, SCHEMA, MIGRATIONS
from app.main import create_app
from app.services import Services
from app.services.posting import infer_cover_letter_required
from app.storage import FileStorage


# --------------------------------------------------------- pure inference


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("A cover letter is required.", True),
        ("Please attach your resume and cover letter.", True),
        ("Applicants must submit a covering letter", True),
        ("Cover letter optional.", False),
        ("A cover letter is not required but welcome", False),
        ("We value teamwork.", None),
    ],
)
def test_infer_cover_letter_required_reads_nearby_language(text, expected):
    assert infer_cover_letter_required(text) is expected


# --------------------------------------------------------------- create()


def test_create_with_a_requiring_description_infers_required(services):
    app = services.applications.create("Acme", "Engineer", job_description="A cover letter is required.")
    assert app["cover_letter_required"] == 1
    assert app["cover_letter_required_source"] == "inferred"


def test_create_with_an_optional_description_infers_not_required(services):
    app = services.applications.create("Acme", "Engineer", job_description="Cover letter optional.")
    assert app["cover_letter_required"] == 0
    assert app["cover_letter_required_source"] == "inferred"


def test_create_with_a_silent_description_leaves_the_flag_undecided(services):
    app = services.applications.create("Acme", "Engineer", job_description="We value teamwork.")
    assert app["cover_letter_required"] == 0
    assert app["cover_letter_required_source"] == ""


# ------------------------------------------------------------- D8 survival


def test_a_user_set_value_survives_a_later_description_update_that_would_infer_differently(services):
    app = services.applications.create("Acme", "Engineer")
    set_by_user = services.applications.update(app["id"], cover_letter_required=True)
    assert set_by_user["cover_letter_required"] == 1
    assert set_by_user["cover_letter_required_source"] == "user"

    # This description alone would infer False -- it must not move a user value.
    survived = services.applications.update(app["id"], job_description="Cover letter optional.")
    assert survived["cover_letter_required"] == 1
    assert survived["cover_letter_required_source"] == "user"


def test_an_inferred_value_is_not_re_inferred_on_a_later_description_update(services):
    app = services.applications.create("Acme", "Engineer", job_description="A cover letter is required.")
    assert app["cover_letter_required"] == 1
    assert app["cover_letter_required_source"] == "inferred"

    # A second, contrary description must not flip a value inference already set.
    after = services.applications.update(app["id"], job_description="Cover letter optional.")
    assert after["cover_letter_required"] == 1
    assert after["cover_letter_required_source"] == "inferred"


def test_detect_from_text_stores_a_job_description_that_also_gets_inferred(services):
    """QuestionService.detect_from_text's own job_description store goes through
    the same hook as any other write (Phase 6 scope: app/services/core.py)."""
    app = services.applications.create("Acme", "Engineer")
    services.questions.detect_from_text(app["id"], "Please attach a cover letter with your application.", "paste")
    stored = services.applications.get(app["id"])
    assert stored["job_description"].strip()
    assert stored["cover_letter_required"] == 1
    assert stored["cover_letter_required_source"] == "inferred"


# --------------------------------------------------------------- migration


def test_migration_6_on_a_v5_database_leaves_existing_rows_undecided(tmp_path):
    path = tmp_path / "app.db"
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    for version, migrate in MIGRATIONS:
        if version <= 5:
            migrate(con)
    con.execute("PRAGMA user_version = 5")
    con.execute("INSERT OR IGNORE INTO companies(name) VALUES('Boeing')")
    company_id = con.execute("SELECT id FROM companies WHERE name='Boeing'").fetchone()[0]
    con.execute(
        "INSERT INTO applications(company_id,formal_title,job_description) VALUES(?,?,?)",
        (company_id, "GNC Engineer", "A cover letter is required."),
    )
    con.commit()
    con.close()

    db = Database(path)
    db.initialize()  # a v5 database now has only migration 6 left to run

    row = db.one(
        "SELECT cover_letter_required,cover_letter_required_source FROM applications WHERE formal_title='GNC Engineer'"
    )
    assert row["cover_letter_required_source"] == ""
    assert row["cover_letter_required"] == 0

    services = Services(db, FileStorage(tmp_path / "files"))
    application_id = db.one("SELECT id FROM applications WHERE formal_title='GNC Engineer'")["id"]

    # Re-saving the description (already on file) is what infers, once.
    updated = services.applications.update(application_id, job_description="A cover letter is required.")
    assert updated["cover_letter_required"] == 1
    assert updated["cover_letter_required_source"] == "inferred"

    # And the guess stays put, D8, even against a contrary description.
    updated_again = services.applications.update(application_id, job_description="Cover letter optional.")
    assert updated_again["cover_letter_required"] == 1
    assert updated_again["cover_letter_required_source"] == "inferred"


# ------------------------------------------------------------------ progress()


def test_progress_check_label_names_an_inferred_requirement(services):
    app = services.applications.create("Acme", "Engineer", job_description="A cover letter is required.")
    progress = services.applications.progress(app["id"])
    check = next(item for item in progress["checks"] if item["key"] == "cover_letter")
    assert check["label"] == "Cover letter reviewed"
    assert check["required"] is True


def test_progress_check_label_names_a_user_set_requirement(services):
    app = services.applications.create("Acme", "Engineer")
    services.applications.update(app["id"], cover_letter_required=True)
    progress = services.applications.progress(app["id"])
    check = next(item for item in progress["checks"] if item["key"] == "cover_letter")
    assert check["label"] == "Cover letter reviewed"


# ------------------------------------------------------------------------ MCP


def test_mcp_no_longer_requires_material_configuration(settings):
    app = create_app(settings)
    names = {tool.name for tool in asyncio.run(app.state.mcp.list_tools())}
    assert "applications.set_requirements" not in names
    assert "applications.prepare_drafts" in names
