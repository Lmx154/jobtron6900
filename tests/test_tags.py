"""Phase 1 (polish pass): tags, text search, and one job per URL."""

import asyncio
import json
import sqlite3

from fastapi.testclient import TestClient

from app.db import MIGRATIONS, SCHEMA, Database
from app.main import create_app
from app.services.core import normalize_tags


# --------------------------------------------------------------- normalize_tags

def test_normalize_tags_strips_lowercases_dedupes_and_sorts():
    assert normalize_tags(" Remote, remote ,REFERRAL") == "referral,remote"


def test_normalize_tags_handles_none_and_empty():
    assert normalize_tags(None) == ""
    assert normalize_tags("") == ""


# --------------------------------------------------------------------- list()

def test_list_by_tag_matches_whole_tags_only(services):
    remote = services.applications.create("Orbital Labs", "Engineer")
    remote_ok = services.applications.create("Second Co", "Engineer")
    services.applications.update(remote["id"], tags="remote")
    services.applications.update(remote_ok["id"], tags="remote-ok")

    matches = services.applications.list(tag="remote")
    assert [app["id"] for app in matches] == [remote["id"]]


def test_list_by_query_matches_company_title_location_and_tag_not_job_description(services):
    boeing = services.applications.create(
        "Boeing", "GNC Engineer", location="Seal Beach, CA", job_description="Design guidance software"
    )
    services.applications.update(boeing["id"], tags="referral")
    other = services.applications.create("Acme", "Widget Maker", job_description="Boeing is not mentioned here")

    assert [app["id"] for app in services.applications.list(query="boeing")] == [boeing["id"]]
    assert [app["id"] for app in services.applications.list(query="GNC")] == [boeing["id"]]
    assert [app["id"] for app in services.applications.list(query="Seal Beach")] == [boeing["id"]]
    assert [app["id"] for app in services.applications.list(query="referral")] == [boeing["id"]]
    # "guidance software" only appears in job_description -> no match at all.
    assert services.applications.list(query="guidance software") == []
    assert other["id"] not in [app["id"] for app in services.applications.list(query="boeing")]


# ----------------------------------------------------------------------- tag()

def test_tag_add_and_remove_round_trips(services):
    app = services.applications.create("Boeing", "GNC Engineer")
    tagged = services.applications.tag(app["id"], add=["Remote", "Referral"])
    assert tagged["tags"] == "referral,remote"

    untagged = services.applications.tag(app["id"], remove=["referral"])
    assert untagged["tags"] == "remote"

    back = services.applications.tag(app["id"], add=["referral"])
    assert back["tags"] == "referral,remote"


def test_tag_remove_of_a_missing_tag_is_a_noop(services):
    app = services.applications.create("Boeing", "GNC Engineer")
    services.applications.tag(app["id"], add=["remote"])
    result = services.applications.tag(app["id"], remove=["not-present"])
    assert result["tags"] == "remote"


# -------------------------------------------------------------------- create()

def test_create_with_a_known_url_returns_the_existing_row_marked_duplicate(services):
    first = services.applications.create("Boeing", "GNC Engineer", url="https://jobs.boeing.com/x")
    assert first["duplicate"] is False

    second = services.applications.create("Boeing", "GNC Engineer", url="https://jobs.boeing.com/x")
    assert second["duplicate"] is True
    assert second["id"] == first["id"]
    assert len(services.applications.list()) == 1


def test_create_with_allow_duplicate_inserts_a_second_row(services):
    first = services.applications.create("Boeing", "GNC Engineer", url="https://jobs.boeing.com/x")
    second = services.applications.create(
        "Boeing", "GNC Engineer", url="https://jobs.boeing.com/x", allow_duplicate=True
    )
    assert second["duplicate"] is False
    assert second["id"] != first["id"]
    assert len(services.applications.list()) == 2


# ----------------------------------------------------------------- migration 4

def test_migration_4_on_a_v3_database_reads_back_empty_tags(tmp_path):
    """Construct a database at version 3 by hand, then let the runner finish the job."""
    path = tmp_path / "v3.db"
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    for target, migrate in MIGRATIONS:
        if target <= 3:
            migrate(con)
    con.execute("PRAGMA user_version = 3")
    con.execute("INSERT OR IGNORE INTO companies(name) VALUES('Acme')")
    company_id = con.execute("SELECT id FROM companies WHERE name='Acme'").fetchone()[0]
    con.execute("INSERT INTO applications(company_id,formal_title) VALUES(?,?)", (company_id, "Engineer"))
    con.commit()
    con.close()

    Database(path).initialize()

    with Database(path).connect() as con2:
        row = con2.execute("SELECT tags FROM applications WHERE formal_title='Engineer'").fetchone()
        assert row["tags"] == ""
        assert int(con2.execute("PRAGMA user_version").fetchone()[0]) == MIGRATIONS[-1][0]


# --------------------------------------------------------------------------- web

def test_dashboard_tag_filter_shows_only_the_matching_application(settings):
    app = create_app(settings)
    remote = app.state.services.applications.create("Boeing", "GNC Engineer")
    other = app.state.services.applications.create("Acme", "Widget Maker")
    app.state.services.applications.update(remote["id"], tags="remote")

    with TestClient(app) as client:
        response = client.get("/?tag=remote")
        assert response.status_code == 200
        # The calendar/agenda panels intentionally still show every application's
        # lifecycle events; only the "Recent applications" list is tag-filtered.
        recent = response.text.split("Recent applications</h2>", 1)[1]
        assert "GNC Engineer" in recent
        assert "Widget Maker" not in recent


def test_applications_list_query_filter_shows_only_the_matching_application(settings):
    app = create_app(settings)
    boeing = app.state.services.applications.create("Boeing", "GNC Engineer")
    other = app.state.services.applications.create("Acme", "Widget Maker")

    with TestClient(app) as client:
        response = client.get("/applications?q=boeing")
        assert response.status_code == 200
        assert "GNC Engineer" in response.text
        assert "Widget Maker" not in response.text


def test_detail_post_updates_tags(settings):
    app = create_app(settings)
    application = app.state.services.applications.create("Boeing", "GNC Engineer")

    with TestClient(app) as client:
        response = client.post(
            f"/applications/{application['id']}",
            data={
                "company_name": "Boeing",
                "formal_title": "GNC Engineer",
                "status": "Draft",
                "tags": "Onsite, remote",
            },
        )
        assert response.status_code == 200
        detail = client.get(f"/applications/{application['id']}")
        assert "onsite" in detail.text and "remote" in detail.text

    assert app.state.services.applications.get(application["id"])["tags"] == "onsite,remote"


# --------------------------------------------------------------------------- MCP

def test_mcp_applications_list_filters_by_tag(settings):
    app = create_app(settings)
    remote = app.state.services.applications.create("Boeing", "GNC Engineer")
    app.state.services.applications.create("Acme", "Widget Maker")
    app.state.services.applications.update(remote["id"], tags="remote")

    result = asyncio.run(app.state.mcp.call_tool("applications.list", {"tag": "remote"}))
    payload = result.structured_content["result"]
    assert [row["id"] for row in payload] == [remote["id"]]


def test_mcp_applications_tag_round_trips(settings):
    app = create_app(settings)
    application = app.state.services.applications.create("Boeing", "GNC Engineer")

    added = asyncio.run(
        app.state.mcp.call_tool(
            "applications.tag", {"application_id": application["id"], "add": ["Remote", "Referral"]}
        )
    )
    assert json.loads(added.content[0].text)["tags"] == "referral,remote"

    removed = asyncio.run(
        app.state.mcp.call_tool(
            "applications.tag", {"application_id": application["id"], "remove": ["referral"]}
        )
    )
    assert json.loads(removed.content[0].text)["tags"] == "remote"
