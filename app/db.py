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


class Database:
    def __init__(self, path: Path): self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection: connection.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback(); raise
        finally: connection.close()

    def one(self, sql: str, parameters: Iterable[Any] = ()) -> dict[str, Any] | None:
        with self.connect() as connection: row = connection.execute(sql, tuple(parameters)).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, parameters: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self.connect() as connection: return [dict(row) for row in connection.execute(sql, tuple(parameters)).fetchall()]

    def execute(self, sql: str, parameters: Iterable[Any] = ()) -> int:
        with self.connect() as connection: cursor = connection.execute(sql, tuple(parameters)); return int(cursor.lastrowid or 0)
