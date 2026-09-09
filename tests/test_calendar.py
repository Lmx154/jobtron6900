"""Phase 4 data layer: migration safety, datetime normalisation, auto-events."""

import sqlite3

import pytest

from app.db import BASE_SCHEMA_VERSION, MIGRATIONS, Database
from app.services.core import ApplicationService
from app.services.palette import STATUS_COLORS, status_color
from app.services.timeutil import local_day, parse_stored, to_utc


# --------------------------------------------------------------- migrations

def test_fresh_database_lands_at_the_latest_version(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    with db.connect() as con:
        assert int(con.execute("PRAGMA user_version").fetchone()[0]) == MIGRATIONS[-1][0]


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "app.db"
    Database(path).initialize()
    with Database(path).connect() as con:
        con.execute("INSERT OR IGNORE INTO companies(name) VALUES('Acme')")
        company = con.execute("SELECT id FROM companies WHERE name='Acme'").fetchone()[0]
        con.execute(
            "INSERT INTO applications(company_id,formal_title) VALUES(?,'Engineer')", (company,)
        )
    Database(path).initialize()
    Database(path).initialize()
    with Database(path).connect() as con:
        assert con.execute("SELECT count(*) FROM applications").fetchone()[0] == 1
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_legacy_database_is_upgraded_without_losing_rows(tmp_path):
    """A pre-migration database: base schema, no user_version, legacy datetimes."""
    path = tmp_path / "legacy.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE, notes TEXT NOT NULL DEFAULT '');
        CREATE TABLE applications (
          id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), formal_title TEXT NOT NULL,
          normalized_role TEXT NOT NULL DEFAULT '', role_family TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '',
          location TEXT NOT NULL DEFAULT '', job_description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'Draft',
          questions_reviewed INTEGER NOT NULL DEFAULT 0, cover_letter_required INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, applied_at TEXT, notes TEXT NOT NULL DEFAULT '');
        CREATE TABLE events (
          id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
          event_type TEXT NOT NULL, occurred_at TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '');
        INSERT INTO companies(name) VALUES('Boeing');
        INSERT INTO applications(company_id,formal_title) VALUES(1,'GNC Engineer');
        INSERT INTO events(application_id,event_type,occurred_at) VALUES
          (1,'Interview','2026-08-29T10:00'),
          (1,'Applied','2026-08-31T02:09:48-07:00'),
          (1,'Noted','2026-08-31 02:09:48'),
          (1,'Broken','not-a-date');
        """
    )
    con.commit()
    assert int(con.execute("PRAGMA user_version").fetchone()[0]) == 0
    con.close()

    Database(path).initialize()

    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    assert int(con.execute("PRAGMA user_version").fetchone()[0]) == MIGRATIONS[-1][0]
    rows = {r["event_type"]: dict(r) for r in con.execute("SELECT * FROM events")}
    assert len(rows) == 4, "no event may be dropped by the migration"
    assert rows["Interview"]["starts_at_utc"]
    assert rows["Applied"]["starts_at_utc"].startswith("2026-08-31T09:09:48")
    assert rows["Noted"]["starts_at_utc"].startswith("2026-08-31T02:09:48")
    assert rows["Broken"]["starts_at_utc"] == "", "an unparseable value stays blank, not fatal"
    assert rows["Interview"]["occurred_at"] == "2026-08-29T10:00", "original string preserved"
    con.close()


def test_base_version_precedes_every_migration():
    assert all(target > BASE_SCHEMA_VERSION for target, _ in MIGRATIONS)
    assert [t for t, _ in MIGRATIONS] == sorted(t for t, _ in MIGRATIONS)


# ---------------------------------------------------------------- datetimes

@pytest.mark.parametrize(
    "value,expected_utc_hour,all_day",
    [
        ("2026-08-31T02:09:48-07:00", 9, False),   # offset honoured
        ("2026-08-31 02:09:48", 2, False),          # SQLite CURRENT_TIMESTAMP is UTC
        ("2026-08-29", 0, True),                    # date only is all-day
    ],
)
def test_each_legacy_shape_resolves(value, expected_utc_hour, all_day):
    moment, is_all_day = parse_stored(value)
    assert moment is not None
    assert is_all_day is all_day
    if not all_day:
        assert moment.hour == expected_utc_hour


def test_unparseable_values_are_skipped_not_raised():
    assert parse_stored("garbage") == (None, False)
    assert parse_stored("") == (None, False)
    with pytest.raises(ValueError):
        to_utc("garbage")


def test_late_evening_event_stays_on_its_local_day():
    """An event stored as the next UTC day must bucket into the local day."""
    day = local_day("2026-09-01T03:00:00+00:00")
    assert len(day) == 10 and day.startswith("2026-0")


# ------------------------------------------------------------------ palette

def test_every_status_has_a_colour():
    assert set(STATUS_COLORS) == set(ApplicationService.STATUSES)
    assert all(status_color(s).startswith("#") and len(status_color(s)) == 7
               for s in ApplicationService.STATUSES)


# -------------------------------------------------------------- auto-events

def test_creating_an_application_puts_it_on_the_calendar(services):
    app = services.applications.create("Boeing", "GNC Engineer")
    events = services.events.list(app["id"])
    assert [e["event_type"] for e in events] == ["Drafted"]
    assert events[0]["all_day"] == 1
    assert events[0]["dedupe_key"] == f"{app['id']}:drafted"
    assert events[0]["color"] == status_color("Draft")


def test_status_changes_are_recorded_and_never_duplicated(services):
    app = services.applications.create("Boeing", "GNC Engineer")
    services.applications.set_status(app["id"], "Ready to Apply")
    services.applications.set_status(app["id"], "Applied")
    first = len(services.events.list(app["id"]))

    services.applications.set_status(app["id"], "Ready to Apply")
    services.applications.set_status(app["id"], "Applied")
    assert len(services.events.list(app["id"])) == first, "replays must not duplicate"


def test_hand_entered_events_may_repeat(services):
    app = services.applications.create("Boeing", "GNC Engineer")
    services.events.add(app["id"], "Interview", "2026-09-01T10:00")
    services.events.add(app["id"], "Interview", "2026-09-01T10:00")
    interviews = [e for e in services.events.list(app["id"]) if e["event_type"] == "Interview"]
    assert len(interviews) == 2, "an empty dedupe_key must not collide"


def test_a_bad_event_time_is_rejected(services):
    app = services.applications.create("Boeing", "GNC Engineer")
    with pytest.raises(ValueError):
        services.events.add(app["id"], "Interview", "whenever")


def test_calendar_event_failure_never_loses_the_application(services, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("calendar is down")

    monkeypatch.setattr(services.events, "record_auto", explode)
    app = services.applications.create("Boeing", "GNC Engineer")
    assert services.applications.get(app["id"])["formal_title"] == "GNC Engineer"


def test_month_buckets_by_local_day_and_filters_by_status(services):
    from app.services.timeutil import today_local

    app = services.applications.create("Boeing", "GNC Engineer")
    today = today_local()
    buckets = services.events.month(today.year, today.month)
    assert today.isoformat() in buckets
    assert [e["event_type"] for e in buckets[today.isoformat()]] == ["Drafted"]

    assert services.events.month(today.year, today.month, status="Draft")
    assert services.events.month(today.year, today.month, status="Offer") == {}


def test_the_agenda_shows_real_events_not_lifecycle_noise(services):
    """Drafted/status rows are history. The agenda is for what is coming up."""
    from datetime import timedelta

    from app.services.timeutil import today_local

    app = services.applications.create("Boeing", "GNC Engineer")
    services.applications.set_status(app["id"], "Interviewing")
    assert services.events.upcoming(10) == [], "automatic rows must not fill the agenda"

    later = (today_local() + timedelta(days=5)).isoformat()
    services.events.add(app["id"], "Onsite", f"{later}T09:30", "Seal Beach")
    agenda = services.events.upcoming(10)
    assert [e["event_type"] for e in agenda] == ["Onsite"]
    assert agenda[0]["company_name"] == "Boeing"

    # They are still on the grid, and still reachable when explicitly asked for.
    assert len(services.events.upcoming(10, include_automatic=True)) > 1
