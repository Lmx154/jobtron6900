"""Phase 3 (polish pass): one atomic applications.log_update tool (D5)."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.main import create_app
from app.services.core import FOLLOW_UP_DAYS
from app.services.timeutil import parse_stored


def test_full_call_writes_one_manual_and_one_lifecycle_event_merges_tags_and_creates_contact(services):
    app = services.applications.create("Boeing", "GNC Engineer")
    services.applications.tag(app["id"], add=["referral"])

    result = services.applications.log_update(
        app["id"],
        "Recruiter Contact",
        "2026-09-08",
        notes="Talked to the recruiter about the role.",
        status="Interviewing",
        add_tags=["phone-screen"],
        contact={"name": "Jane Doe", "role": "Recruiter", "email": "jane@example.com", "phone": "555-1234"},
    )

    assert result["status_changed"] is True
    assert result["application"]["status"] == "Interviewing"
    assert result["event"]["event_type"] == "Recruiter Contact"
    assert result["event"]["assumed_now"] is False

    context = services.applications.get_context(app["id"])
    assert sorted(context["application"]["tags"].split(",")) == ["phone-screen", "referral"]

    manual_events = [e for e in context["events"] if e["kind"] == "manual" and e["event_type"] == "Recruiter Contact"]
    assert len(manual_events) == 1
    assert manual_events[0]["notes"] == "Talked to the recruiter about the role."

    lifecycle_status_events = [
        e for e in context["events"] if e["kind"] == "lifecycle" and e["event_type"].startswith("Status:")
    ]
    assert len(lifecycle_status_events) == 1
    assert lifecycle_status_events[0]["event_type"] == "Status: Draft → Interviewing"

    assert len(context["contacts"]) == 1
    contact = context["contacts"][0]
    assert contact["name"] == "Jane Doe"
    assert contact["role"] == "Recruiter"
    assert contact["email"] == "jane@example.com"
    assert contact["phone"] == "555-1234"


def test_setting_applied_is_refused_with_the_same_message_as_set_status_and_writes_nothing(services):
    app = services.applications.create("Boeing", "GNC Engineer")
    events_before = len(services.events.list(app["id"]))
    context_before = services.applications.get_context(app["id"])
    tags_before = context_before["application"]["tags"]
    contacts_count_before = len(context_before["contacts"])

    with pytest.raises(ValueError, match="Applied means the user submitted this externally. Ask them first."):
        services.applications.log_update(
            app["id"], "Submitted", "2026-09-08",
            status="Applied",
            add_tags=["x"],
            contact={"name": "R", "email": "r@example.com"}
        )

    assert len(services.events.list(app["id"])) == events_before
    assert services.applications.get(app["id"])["status"] == "Draft"
    context_after = services.applications.get_context(app["id"])
    assert context_after["application"]["tags"] == tags_before
    assert len(context_after["contacts"]) == contacts_count_before


def test_unknown_status_leaves_no_event_behind(services):
    app = services.applications.create("Boeing", "GNC Engineer")
    events_before = len(services.events.list(app["id"]))
    context_before = services.applications.get_context(app["id"])
    tags_before = context_before["application"]["tags"]
    contacts_count_before = len(context_before["contacts"])

    with pytest.raises(ValueError, match="Unsupported application status"):
        services.applications.log_update(
            app["id"], "Note", "2026-09-08",
            status="Bogus",
            add_tags=["x"],
            contact={"name": "R", "email": "r@example.com"}
        )

    assert len(services.events.list(app["id"])) == events_before
    assert services.applications.get(app["id"])["status"] == "Draft"
    context_after = services.applications.get_context(app["id"])
    assert context_after["application"]["tags"] == tags_before
    assert len(context_after["contacts"]) == contacts_count_before


def test_contact_with_existing_email_on_same_application_updates_rather_than_duplicates(services):
    app = services.applications.create("Boeing", "GNC Engineer")

    services.applications.log_update(
        app["id"], "Intro email", "2026-09-01",
        contact={"name": "Jane Doe", "role": "Recruiter", "email": "Jane@Example.com", "phone": "555-0000"},
    )
    services.applications.log_update(
        app["id"], "Follow-up email", "2026-09-05",
        contact={"name": "Jane D.", "role": "Senior Recruiter", "email": "jane@example.com", "phone": "555-1111"},
    )

    contacts = services.applications.get_context(app["id"])["contacts"]
    assert len(contacts) == 1
    assert contacts[0]["name"] == "Jane D."
    assert contacts[0]["role"] == "Senior Recruiter"
    assert contacts[0]["phone"] == "555-1111"


def test_empty_occurred_at_uses_now_and_reports_assumed_now(services):
    app = services.applications.create("Boeing", "GNC Engineer")
    before = datetime.now(UTC)

    result = services.applications.log_update(app["id"], "Rejection email", "")

    after = datetime.now(UTC)
    assert result["event"]["assumed_now"] is True
    moment, _ = parse_stored(result["event"]["starts_at_utc"])
    assert moment is not None
    assert before - timedelta(minutes=1) <= moment <= after + timedelta(minutes=1)


def test_due_follow_up_reminder_is_resolved_by_a_later_log_update(ready_services):
    services = ready_services
    app = services.applications.create("Boeing", "GNC Engineer")
    services.applications.set_status(app["id"], "Applied")
    applied_moment, _ = parse_stored(services.applications.get(app["id"])["applied_at"])
    due_at = applied_moment + timedelta(days=FOLLOW_UP_DAYS)
    now = due_at + timedelta(hours=1)

    assert any(row["application_id"] == app["id"] for row in services.applications.attention(now=now)["follow_up_due"])

    services.applications.log_update(app["id"], "Recruiter call", (due_at + timedelta(minutes=30)).isoformat())

    assert not any(
        row["application_id"] == app["id"] for row in services.applications.attention(now=now)["follow_up_due"]
    ), "a manual event from log_update after the reminder must resolve it"


# --------------------------------------------------------------------------- MCP

def test_mcp_applications_log_update_round_trips(settings):
    app = create_app(settings)
    application = app.state.services.applications.create("Boeing", "GNC Engineer")

    result = asyncio.run(
        app.state.mcp.call_tool(
            "applications.log_update",
            {
                "application_id": application["id"],
                "event_type": "Recruiter Contact",
                "occurred_at": "2026-09-08",
                "status": "Interviewing",
                "add_tags": ["phone-screen"],
                "contact": {"name": "Jane Doe", "role": "Recruiter", "email": "jane@example.com", "phone": "555-1234"},
            },
        )
    )
    payload = json.loads(result.content[0].text)
    assert payload["status_changed"] is True
    assert payload["application"]["status"] == "Interviewing"
    assert payload["application"]["tags"] == "phone-screen"
    assert payload["event"]["event_type"] == "Recruiter Contact"

    contacts = app.state.services.applications.get_context(application["id"])["contacts"]
    assert contacts[0]["email"] == "jane@example.com"


def test_mcp_applications_log_update_refuses_applied_with_refused_dict(settings):
    app = create_app(settings)
    application = app.state.services.applications.create("Boeing", "GNC Engineer")
    events_before = len(app.state.services.events.list(application["id"]))

    result = asyncio.run(
        app.state.mcp.call_tool(
            "applications.log_update",
            {
                "application_id": application["id"],
                "event_type": "Submitted",
                "occurred_at": "2026-09-08",
                "status": "Applied",
            },
        )
    )
    payload = json.loads(result.content[0].text)
    assert payload["refused"] is True
    assert "confirm" in payload["say_to_user"]

    events_after = len(app.state.services.events.list(application["id"]))
    assert events_after == events_before
