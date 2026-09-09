from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS candidates (
  id INTEGER PRIMARY KEY CHECK (id = 1), name TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '', website TEXT NOT NULL DEFAULT '',
  github TEXT NOT NULL DEFAULT '', linkedin TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS candidate_facts (
  id INTEGER PRIMARY KEY, category TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL,
  tags TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '', verified INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS companies (id INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE, notes TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS applications (
  id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), formal_title TEXT NOT NULL,
  normalized_role TEXT NOT NULL DEFAULT '', role_family TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '',
  location TEXT NOT NULL DEFAULT '', job_description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'Draft',
  questions_reviewed INTEGER NOT NULL DEFAULT 0, cover_letter_required INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, applied_at TEXT, notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS question_bank (
  id INTEGER PRIMARY KEY, canonical_question TEXT NOT NULL, answer TEXT NOT NULL,
  keywords TEXT NOT NULL DEFAULT '', approved INTEGER NOT NULL DEFAULT 1, usage_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS questions (
  id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
  bank_entry_id INTEGER REFERENCES question_bank(id) ON DELETE SET NULL,
  question TEXT NOT NULL, field_type TEXT NOT NULL DEFAULT 'text', allowed_choices TEXT NOT NULL DEFAULT '',
  required INTEGER NOT NULL DEFAULT 0, character_limit INTEGER, sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS answers (
  id INTEGER PRIMARY KEY, question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  content TEXT NOT NULL, version INTEGER NOT NULL, approved INTEGER NOT NULL DEFAULT 0,
  source_label TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(question_id, version)
);
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY, application_id INTEGER REFERENCES applications(id) ON DELETE CASCADE,
  document_type TEXT NOT NULL, title TEXT NOT NULL, file_path TEXT, text_content TEXT NOT NULL DEFAULT '',
  mime_type TEXT NOT NULL DEFAULT 'text/plain', extraction_status TEXT NOT NULL DEFAULT 'provided',
  extraction_error TEXT NOT NULL DEFAULT '', use_for_applications INTEGER NOT NULL DEFAULT 0,
  version INTEGER NOT NULL, approved INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS documents_version ON documents(COALESCE(application_id, 0), document_type, version);
CREATE TABLE IF NOT EXISTS application_document_usage (
  id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  note TEXT NOT NULL DEFAULT '', considered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(application_id, document_id)
);
CREATE TABLE IF NOT EXISTS assets (
  id INTEGER PRIMARY KEY, application_id INTEGER REFERENCES applications(id) ON DELETE CASCADE,
  asset_type TEXT NOT NULL DEFAULT 'other', title TEXT NOT NULL, mime_type TEXT NOT NULL,
  file_path TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL, occurred_at TEXT NOT NULL, notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
  name TEXT NOT NULL, role TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS application_archives (
  id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL UNIQUE REFERENCES applications(id) ON DELETE CASCADE,
  snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO candidates(id) VALUES (1);
"""

# Version of the schema defined by SCHEMA above. Databases created before the
# migration runner existed match this exactly, so they are stamped here on
# first contact rather than being re-created.
BASE_SCHEMA_VERSION = 1


def _migration_2_calendar(connection: sqlite3.Connection) -> None:
    """Give events a real instant, an all-day flag, and idempotent auto-creation.

    `occurred_at` is deliberately kept as the display-local string it always
    was: nothing is dropped, and a failed backfill costs a blank cell rather
    than a lost event.
    """
    from app.services.timeutil import parse_stored

    for statement in (
        "ALTER TABLE events ADD COLUMN starts_at_utc TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE events ADD COLUMN ends_at_utc   TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE events ADD COLUMN all_day       INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE events ADD COLUMN timezone      TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE events ADD COLUMN dedupe_key    TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE events ADD COLUMN updated_at    TEXT NOT NULL DEFAULT ''",
    ):
        connection.execute(statement)

    rows = connection.execute("SELECT id, occurred_at FROM events").fetchall()
    for row in rows:
        moment, all_day = parse_stored(row["occurred_at"])
        if moment is None:
            continue
        connection.execute(
            "UPDATE events SET starts_at_utc=?, all_day=? WHERE id=?",
            (moment.isoformat(timespec="seconds"), int(all_day), row["id"]),
        )

    connection.execute("CREATE INDEX IF NOT EXISTS events_starts ON events(application_id, starts_at_utc)")
    # Partial index: hand-entered events keep an empty key and may repeat freely.
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS events_dedupe ON events(dedupe_key) WHERE dedupe_key != ''"
    )


def _migration_3_question_extraction(connection: sqlite3.Connection) -> None:
    """Remember whether the posting was already scraped for questions.

    Without this the workflow cannot tell "not tried yet" from "tried and the
    site refused", and would either loop or never offer the manual fallback.
    """
    connection.execute(
        "ALTER TABLE applications ADD COLUMN questions_extraction_status TEXT NOT NULL DEFAULT ''"
    )
    connection.execute(
        "ALTER TABLE applications ADD COLUMN questions_extraction_note TEXT NOT NULL DEFAULT ''"
    )


def _migration_4_tags(connection: sqlite3.Connection) -> None:
    """Give applications a free-text tag set for filtering and search.

    Stored as a single sorted, lowercase, comma-joined column (see
    `app.services.core.normalize_tags`): single-user SQLite has no use for a
    join table here, and a text column costs nothing extra to migrate.
    """
    connection.execute("ALTER TABLE applications ADD COLUMN tags TEXT NOT NULL DEFAULT ''")


# (target_version, migration) applied in order to any database below target.
MIGRATIONS: list[tuple[int, Any]] = [
    (2, _migration_2_calendar),
    (3, _migration_3_question_extraction),
    (4, _migration_4_tags),
]


class Database:
    def __init__(self, path: Path): self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Apply pending migrations under an exclusive write lock.

        Several Jobtron processes may start at once (one per MCP client), so the
        version check and the migration must be atomic. BEGIN IMMEDIATE takes the
        write lock before PRAGMA user_version is read, which makes a second
        starter block here and then find nothing left to do -- rather than both
        reading the old version and both running ALTER TABLE.

        A database at version 0 either is brand new or predates the runner; in
        both cases it now matches SCHEMA, so it is stamped at the base version
        rather than migrated from nothing.
        """
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("BEGIN IMMEDIATE")
            try:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version < BASE_SCHEMA_VERSION:
                    version = BASE_SCHEMA_VERSION
                    connection.execute(f"PRAGMA user_version = {BASE_SCHEMA_VERSION:d}")
                for target, migrate in MIGRATIONS:
                    if target > version:
                        migrate(connection)
                        connection.execute(f"PRAGMA user_version = {int(target):d}")
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        finally:
            connection.close()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def one(self, sql: str, parameters: Iterable[Any] = ()) -> dict[str, Any] | None:
        with self.connect() as connection: row = connection.execute(sql, tuple(parameters)).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, parameters: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self.connect() as connection: return [dict(row) for row in connection.execute(sql, tuple(parameters)).fetchall()]

    def execute(self, sql: str, parameters: Iterable[Any] = ()) -> int:
        with self.connect() as connection: cursor = connection.execute(sql, tuple(parameters)); return int(cursor.lastrowid or 0)
