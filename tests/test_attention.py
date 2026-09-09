"""Phase 2: event kinds, the follow-up reminder, and the computed attention() view."""

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.db import SCHEMA, Database, _migration_2_calendar, _migration_3_question_extraction, _migration_4_tags
from app.main import create_app
from app.services import Services
from app.services.core import FOLLOW_UP_DAYS, HORIZON_DAYS, IDLE_DRAFT_DAYS, STALE_DAYS
from app.services.timeutil import parse_stored, to_utc
from app.storage import FileStorage


def _set_fields(services: Services, application_id: int, **fields) -> None:
    """Bypass update()/set_status() to plant a status/timestamp directly, so tests can
    control last_activity_at without the automatic reminder or lifecycle events
    those service methods would also write."""
    columns = ", ".join(f"{key}=?" for key in fields)
    services.db.execute(f"UPDATE applications SET {columns} WHERE id=?", (*fields.values(), application_id))


# ------------------------------------------------------------- migration 5

def test_migration_5_backfills_kind_and_preserves_upcoming(tmp_path):
    path = tmp_path / "v4.db"
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    _migration_2_calendar(con)
    _migration_3_question_extraction(con)
    _migration_4_tags(con)
    con.execute("INSERT INTO companies(name) VALUES('Boeing')")
    company_id = con.execute("SELECT id FROM companies WHERE name='Boeing'").fetchone()[0]
    con.execute("INSERT INTO applications(company_id,formal_title,status) VALUES(?,?,?)", (company_id, "GNC Engineer", "Applied"))
    application_id = con.execute("SELECT id FROM applications").fetchone()[0]
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat(timespec="seconds")
    con.execute(
        "INSERT INTO events(application_id,event_type,occurred_at,starts_at_utc,all_day,dedupe_key) VALUES(?,?,?,?,?,?)",
        (application_id, "Status: Draft -> Applied", future, future, 0, f"{application_id}:applied"),
    )
    con.execute(
        "INSERT INTO events(application_id,event_type,occurred_at,starts_at_utc,all_day,dedupe_key) VALUES(?,?,?,?,?,?)",
        (application_id, "Interview", future, future, 0, ""),
    )
    con.execute("PRAGMA user_version = 4")
    con.commit()
    con.close()

    db = Database(path)
    db.initialize()

    rows = {r["event_type"]: r for r in db.all("SELECT * FROM events")}
    assert rows["Status: Draft -> Applied"]["kind"] == "lifecycle"
    assert rows["Interview"]["kind"] == "manual"

    services = Services(db, FileStorage(tmp_path / "files"))
    upcoming_types = {e["event_type"] for e in services.events.upcoming(10, include_automatic=False)}
    assert upcoming_types == {"Interview"}, "lifecycle rows must stay hidden from Upcoming after the migration"


# ------------------------------------------------------ reminder on Applied

def test_applied_creates_exactly_one_follow_up_reminder_idempotently(ready_services):
    services = ready_services
    app = services.applications.create("Boeing", "GNC Engineer")

    services.applications.update(app["id"], status="Applied")
    reminders = [e for e in services.events.list(app["id"]) if e["kind"] == "reminder"]
    assert len(reminders) == 1
    assert reminders[0]["event_type"] == "Follow-up"
    assert reminders[0]["dedupe_key"] == f"follow_up:{app['id']}:1"

    applied_moment, _ = parse_stored(services.applications.get(app["id"])["applied_at"])
    reminder_moment, _ = parse_stored(reminders[0]["starts_at_utc"])
    assert (reminder_moment - applied_moment).days == FOLLOW_UP_DAYS

    services.applications.update(app["id"], status="Applied")
    services.applications.set_status(app["id"], "Applied")
    reminders_again = [e for e in services.events.list(app["id"]) if e["kind"] == "reminder"]
    assert len(reminders_again) == 1, "a second Applied transition must not create a second reminder"


def test_manual_event_after_reminder_resolves_it_but_not_before(ready_services):
    services = ready_services
    app = services.applications.create("Boeing", "GNC Engineer")
    services.applications.set_status(app["id"], "Applied")
    applied_moment, _ = parse_stored(services.applications.get(app["id"])["applied_at"])
    due_at = applied_moment + timedelta(days=FOLLOW_UP_DAYS)
    now = due_at + timedelta(hours=1)

    assert any(row["application_id"] == app["id"] for row in services.applications.attention(now=now)["follow_up_due"])

    services.events.add(app["id"], "Note", (due_at - timedelta(days=1)).isoformat())
    assert any(row["application_id"] == app["id"] for row in services.applications.attention(now=now)["follow_up_due"]), \
        "an earlier manual event must not resolve the reminder"

    services.events.add(app["id"], "Recruiter call", (due_at + timedelta(minutes=30)).isoformat())
    assert not any(row["application_id"] == app["id"] for row in services.applications.attention(now=now)["follow_up_due"]), \
        "a later manual event must resolve the reminder"


def test_status_change_after_reminder_resolves_it(ready_services):
    services = ready_services
    app = services.applications.create("Boeing", "GNC Engineer")
    services.applications.set_status(app["id"], "Applied")
    applied_moment, _ = parse_stored(services.applications.get(app["id"])["applied_at"])
    due_at = applied_moment + timedelta(days=FOLLOW_UP_DAYS)
    now = due_at + timedelta(hours=1)
    assert any(row["application_id"] == app["id"] for row in services.applications.attention(now=now)["follow_up_due"])

    services.events.add(
        app["id"], "Status: Applied -> Interviewing", (due_at + timedelta(hours=2)).isoformat(), kind="lifecycle",
    )
    assert not any(row["application_id"] == app["id"] for row in services.applications.attention(now=now)["follow_up_due"])


# ---------------------------------------------------------------- buckets

def test_upcoming_bucket_thresholds_and_terminal_exclusion(ready_services):
    services = ready_services
    now = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)

    within = services.applications.create("Boeing", "Within Horizon")
    services.events.add(within["id"], "Interview", to_utc(now + timedelta(days=HORIZON_DAYS - 1)))

    outside = services.applications.create("Boeing", "Outside Horizon")
    services.events.add(outside["id"], "Interview", to_utc(now + timedelta(days=HORIZON_DAYS)))

    terminal = services.applications.create("Boeing", "Terminal Upcoming")
    _set_fields(services, terminal["id"], status="Rejected")
    services.events.add(terminal["id"], "Interview", to_utc(now + timedelta(days=1)))

    ids = {row["application_id"] for row in services.applications.attention(now=now)["upcoming"]}
    assert within["id"] in ids
    assert outside["id"] not in ids
    assert terminal["id"] not in ids


def test_follow_up_due_bucket_thresholds_and_terminal_exclusion(ready_services):
    services = ready_services
    now = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)

    due = services.applications.create("Boeing", "Due Co")
    _set_fields(services, due["id"], status="Applied", applied_at=to_utc(now - timedelta(days=10)))
    services.events.add(due["id"], "Follow-up", to_utc(now - timedelta(hours=1)), all_day=True, dedupe_key=f"follow_up:{due['id']}:1", kind="reminder")

    not_yet = services.applications.create("Boeing", "Not Due Yet Co")
    _set_fields(services, not_yet["id"], status="Applied", applied_at=to_utc(now - timedelta(days=3)))
    services.events.add(not_yet["id"], "Follow-up", to_utc(now + timedelta(hours=1)), all_day=True, dedupe_key=f"follow_up:{not_yet['id']}:1", kind="reminder")

    terminal = services.applications.create("Boeing", "Terminal Follow-up Co")
    _set_fields(services, terminal["id"], status="Rejected", applied_at=to_utc(now - timedelta(days=10)))
    services.events.add(terminal["id"], "Follow-up", to_utc(now - timedelta(hours=1)), all_day=True, dedupe_key=f"follow_up:{terminal['id']}:1", kind="reminder")

    ids = {row["application_id"] for row in services.applications.attention(now=now)["follow_up_due"]}
    assert due["id"] in ids
    assert not_yet["id"] not in ids
    assert terminal["id"] not in ids


def test_stale_bucket_thresholds_and_terminal_exclusion(ready_services):
    services = ready_services
    now = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)

    stale = services.applications.create("Boeing", "Stale Co")
    backdated = to_utc(now - timedelta(days=STALE_DAYS + 1))
    _set_fields(services, stale["id"], status="Applied", applied_at=backdated, created_at=backdated)

    fresh = services.applications.create("Boeing", "Fresh Co")
    boundary = to_utc(now - timedelta(days=STALE_DAYS))
    _set_fields(services, fresh["id"], status="Applied", applied_at=boundary, created_at=boundary)

    terminal = services.applications.create("Boeing", "Terminal Stale Co")
    very_old = to_utc(now - timedelta(days=STALE_DAYS + 30))
    _set_fields(services, terminal["id"], status="Rejected", applied_at=very_old, created_at=very_old)

    ids = {row["application_id"] for row in services.applications.attention(now=now)["stale"]}
    assert stale["id"] in ids
    assert fresh["id"] not in ids, "exactly STALE_DAYS old is not yet stale"
    assert terminal["id"] not in ids


def test_idle_drafts_bucket_thresholds_and_terminal_exclusion(ready_services):
    services = ready_services
    now = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)

    idle = services.applications.create("Boeing", "Idle Draft Co")
    _set_fields(services, idle["id"], created_at=to_utc(now - timedelta(days=IDLE_DRAFT_DAYS + 1)))

    fresh = services.applications.create("Boeing", "Fresh Draft Co")
    _set_fields(services, fresh["id"], created_at=to_utc(now - timedelta(days=IDLE_DRAFT_DAYS)))

    terminal = services.applications.create("Boeing", "Terminal Draft Co")
    _set_fields(services, terminal["id"], created_at=to_utc(now - timedelta(days=IDLE_DRAFT_DAYS + 30)), status="Closed")

    ids = {row["application_id"] for row in services.applications.attention(now=now)["idle_drafts"]}
    assert idle["id"] in ids
    assert fresh["id"] not in ids, "exactly IDLE_DRAFT_DAYS old is not yet idle"
    assert terminal["id"] not in ids


def test_idle_drafts_excludes_a_draft_with_a_manual_event(ready_services):
    services = ready_services
    now = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    app = services.applications.create("Boeing", "Touched Draft Co")
    _set_fields(services, app["id"], created_at=to_utc(now - timedelta(days=IDLE_DRAFT_DAYS + 5)))
    services.events.add(app["id"], "Note", to_utc(now - timedelta(days=1)))
    ids = {row["application_id"] for row in services.applications.attention(now=now)["idle_drafts"]}
    assert app["id"] not in ids


def test_attention_on_empty_database_returns_four_empty_lists(services):
    assert services.applications.attention() == {
        "upcoming": [], "follow_up_due": [], "stale": [], "idle_drafts": [],
    }


# -------------------------------------------------------------------- web

def test_dashboard_renders_nothing_waiting_when_attention_is_empty(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "Today" in page.text
        assert "Nothing is waiting on you." in page.text


def test_dashboard_renders_today_rail_with_a_due_follow_up(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        created = client.post("/applications", data={"company_name": "Boeing", "formal_title": "GNC Engineer"}, follow_redirects=False)
        application_id = int(created.headers["location"].rsplit("/", 1)[-1])
        services = app.state.services
        services.applications.set_status(application_id, "Applied")
        reminder = next(e for e in services.events.list(application_id) if e["kind"] == "reminder")
        services.db.execute(
            "UPDATE events SET starts_at_utc=? WHERE id=?",
            (to_utc(datetime.now(UTC) - timedelta(hours=1)), reminder["id"]),
        )
        page = client.get("/")
        assert page.status_code == 200
        assert "Follow-up due" in page.text
        assert "no reply" in page.text


def test_detail_page_shows_last_activity_line(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        created = client.post("/applications", data={"company_name": "Boeing", "formal_title": "GNC Engineer"}, follow_redirects=False)
        application_id = int(created.headers["location"].rsplit("/", 1)[-1])
        page = client.get(f"/applications/{application_id}")
        assert page.status_code == 200
        assert "Last activity" in page.text
        assert "ago" in page.text


# -------------------------------------------------------------------- mcp

def test_mcp_applications_attention_returns_buckets_and_frontend_url(settings):
    app = create_app(settings)
    app.state.services.applications.create("Boeing", "GNC Engineer")
    result = asyncio.run(app.state.mcp.call_tool("applications.attention", {}))
    data = result.structured_content
    assert set(data.keys()) >= {"upcoming", "follow_up_due", "stale", "idle_drafts", "frontend_url"}
    assert data["frontend_url"] == "http://127.0.0.1:8765/applications"
