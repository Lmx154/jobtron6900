from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from app.db import Database
from app.services.extraction import extract_document_text
from app.services.palette import status_color
from app.services.timeutil import local_day, local_timezone, parse_stored, to_local, today_local
from app.storage import FileStorage


# The closed set of profile fields Jobtron treats as "basic information".
# A small model reads this decision as a boolean, so the predicate must be exact.
REQUIRED_PROFILE_FIELDS = ("name", "email", "phone", "location")

PROFILE_FIELD_LABELS = {
    "name": "name",
    "email": "email",
    "phone": "phone number",
    "location": "location",
}

# Attention thresholds (D2, D6): "attention" is computed on every read from
# events and application rows, never stored, so these are the only place the
# numbers live.
FOLLOW_UP_DAYS = 7
STALE_DAYS = 14
IDLE_DRAFT_DAYS = 7
HORIZON_DAYS = 7
TERMINAL_STATUSES = {"Rejected", "Withdrawn", "Closed"}
ACTIVE_STATUSES = {"Applied", "Assessment", "Interviewing"}
# Staleness also watches Offer: a silent offer is exactly the kind of thing
# that needs to surface, even though it is not one of the "waiting to hear
# back" statuses above.
STALE_STATUSES = ACTIVE_STATUSES | {"Offer"}
DRAFT_STATUSES = {"Draft", "Ready to Apply"}

EVENT_KINDS = {"manual", "lifecycle", "reminder"}


class NotFoundError(LookupError): pass


def required(row: dict[str, Any] | None, kind: str) -> dict[str, Any]:
    if row is None: raise NotFoundError(f"{kind} not found")
    return row


def normalize_tags(raw: str | list[str] | None) -> str:
    """Canonical tag storage form: split on comma, strip, lowercase, drop
    empties, dedupe, sort, join with ','. Both `update()` and `tag()` route
    every write through this so the column is always in the same shape."""
    if raw is None:
        return ""
    text = raw if isinstance(raw, str) else ",".join(raw)
    tags = {part.strip().lower() for part in text.split(",") if part.strip()}
    return ",".join(sorted(tags))


def _escape_like(value: str) -> str:
    """Escape SQLite LIKE wildcards so free-text tags/search never behave as patterns."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class CandidateService:
    def __init__(self, db: Database):
        self.db = db
        self.documents: DocumentService | None = None
        self.services: "Services | None" = None

    def get_profile(self): return required(self.db.one("SELECT * FROM candidates WHERE id=1"), "candidate")
    def update_profile(self, **fields):
        allowed = ["name", "email", "phone", "location", "website", "github", "linkedin", "summary"]
        values = [str(fields.get(key, "")) for key in allowed]
        self.db.execute(f"UPDATE candidates SET {', '.join(f'{key}=?' for key in allowed)}, updated_at=CURRENT_TIMESTAMP WHERE id=1", values)
        return self.get_profile()
    def get_facts(self): return self.db.all("SELECT * FROM candidate_facts ORDER BY category,title")
    def add_fact(self, category: str, title: str, description: str, tags: str = "", source: str = "", verified: bool = False):
        fact_id = self.db.execute("INSERT INTO candidate_facts(category,title,description,tags,source,verified) VALUES(?,?,?,?,?,?)", (category,title,description,tags,source,int(verified)))
        return required(self.db.one("SELECT * FROM candidate_facts WHERE id=?", (fact_id,)), "fact")
    def update_fact(self, fact_id: int, **fields):
        current = required(self.db.one("SELECT * FROM candidate_facts WHERE id=?", (fact_id,)), "fact")
        allowed = ["category","title","description","tags","source","verified"]
        merged = {key: fields.get(key, current[key]) for key in allowed}; merged["verified"] = int(bool(merged["verified"]))
        self.db.execute(f"UPDATE candidate_facts SET {', '.join(f'{key}=?' for key in allowed)},updated_at=CURRENT_TIMESTAMP WHERE id=?", (*[merged[k] for k in allowed],fact_id))
        return required(self.db.one("SELECT * FROM candidate_facts WHERE id=?", (fact_id,)), "fact")
    def readiness(self):
        """Global candidate capability. Independent of any single application.

        Returns a closed `capability` enum plus the exact gaps, so the agent
        never has to infer what the user is missing.
        """
        assert self.documents
        base_url = self.services.base_url if self.services else ""
        profile = self.get_profile()
        missing = [
            field for field in REQUIRED_PROFILE_FIELDS
            if not str(profile.get(field) or "").strip()
        ]
        master_resumes = self.documents.list(None, "master_resume")
        approved_resumes = [document for document in master_resumes if document["approved"]]
        profile_complete = not missing
        master_resume_available = bool(approved_resumes)

        if profile_complete and master_resume_available:
            capability = "full"
        elif profile_complete or master_resume_available:
            capability = "partial"
        else:
            capability = "draft_only"

        setup_url = f"{base_url}/profile"
        blocked_by = ([] if master_resume_available else ["master_resume"]) + [
            f"profile.{field}" for field in missing
        ]
        gap = self.gap_phrase(missing, master_resume_available)
        say_to_user = (
            f"You are set up: your profile is complete and you have a master resume on file."
            if capability == "full"
            else (
                f"Before I can build an application, {gap}. "
                f"Open {setup_url} to upload your master resume and fill in your basic information, "
                f"then tell me you are done."
            )
        )
        return {
            "capability": capability,
            "profile_complete": profile_complete,
            "missing_profile_fields": missing,
            "master_resume_available": master_resume_available,
            "master_resume_count": len(master_resumes),
            "blocked_by": blocked_by,
            "gap_phrase": gap,
            "setup_url": setup_url,
            "say_to_user": say_to_user,
            "must_share_url": capability != "full",
        }

    @staticmethod
    def gap_phrase(missing_fields: list[str], master_resume_available: bool) -> str:
        """Human-readable statement of the gap, reused verbatim by the agent."""
        parts = []
        if not master_resume_available:
            parts.append("you have no master resume saved")
        if missing_fields:
            labels = [PROFILE_FIELD_LABELS.get(field, field) for field in missing_fields]
            listed = labels[0] if len(labels) == 1 else f"{', '.join(labels[:-1])} and {labels[-1]}"
            parts.append(f"your profile is missing your {listed}")
        if not parts:
            return "your candidate setup is complete"
        return " and ".join(parts)

    def get_context(self):
        assert self.documents
        academic_documents = self.documents.academic()
        for document in academic_documents:
            document["resource_uri"] = f"jobtron://documents/{document['id']}"
        return {
            "profile": self.get_profile(),
            "readiness": self.readiness(),
            "facts": self.get_facts(),
            "master_resumes": self.documents.list(None, "master_resume"),
            "academic_documents": academic_documents,
            "application_context_documents": [
                document for document in academic_documents if document["use_for_applications"]
            ],
        }


class DocumentService:
    ACADEMIC_TYPES = ("academic_transcript", "diploma", "certificate", "academic_record")

    def __init__(self, db: Database, storage: FileStorage):
        self.db = db
        self.storage = storage

    def list(self, application_id: int | None, document_type: str | None = None):
        where = "application_id IS NULL" if application_id is None else "application_id=?"
        args: list[Any] = [] if application_id is None else [application_id]
        if document_type:
            where += " AND document_type=?"
            args.append(document_type)
        return self.db.all(f"SELECT * FROM documents WHERE {where} ORDER BY document_type,version DESC", args)

    def academic(self, only_enabled: bool = False):
        placeholders = ",".join("?" for _ in self.ACADEMIC_TYPES)
        where = f"application_id IS NULL AND document_type IN ({placeholders})"
        args: list[Any] = list(self.ACADEMIC_TYPES)
        if only_enabled:
            where += " AND use_for_applications=1"
        return self.db.all(f"SELECT * FROM documents WHERE {where} ORDER BY document_type,version DESC", args)

    def get(self, document_id: int):
        return required(self.db.one("SELECT * FROM documents WHERE id=?", (document_id,)), "document")

    def save(
        self,
        document_type: str,
        title: str,
        application_id: int | None = None,
        text_content: str = "",
        filename: str | None = None,
        content: bytes | None = None,
        approved: bool = False,
        use_for_applications: bool = False,
    ):
        scope = "application_id IS NULL" if application_id is None else "application_id=?"
        args = [] if application_id is None else [application_id]
        latest = required(
            self.db.one(
                f"SELECT COALESCE(MAX(version),0) AS version FROM documents WHERE {scope} AND document_type=?",
                (*args, document_type),
            ),
            "document version",
        )
        path = None
        mime_type = "text/plain"
        extraction_status = "provided" if text_content.strip() else "not_requested"
        extraction_error = ""
        if filename and content is not None:
            path, mime_type = self.storage.save(filename, content)
            if not text_content.strip():
                text_content, extraction_status, extraction_error = extract_document_text(filename, content)
        doc_id = self.db.execute(
            """INSERT INTO documents(
                application_id,document_type,title,file_path,text_content,mime_type,
                extraction_status,extraction_error,use_for_applications,version,approved
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                application_id,
                document_type,
                title,
                path,
                text_content,
                mime_type,
                extraction_status,
                extraction_error,
                int(use_for_applications),
                int(latest["version"]) + 1,
                int(approved),
            ),
        )
        if approved:
            self.approve(doc_id)
        return self.get(doc_id)

    def approve(self, document_id: int):
        doc = self.get(document_id)
        if doc["application_id"] is None:
            self.db.execute(
                "UPDATE documents SET approved=0 WHERE application_id IS NULL AND document_type=?",
                (doc["document_type"],),
            )
        else:
            self.db.execute(
                "UPDATE documents SET approved=0 WHERE application_id=? AND document_type=?",
                (doc["application_id"], doc["document_type"]),
            )
        self.db.execute("UPDATE documents SET approved=1 WHERE id=?", (document_id,))
        return self.get(document_id)

    def set_application_use(self, document_id: int, use_for_applications: bool):
        doc = self.get(document_id)
        if doc["application_id"] is not None or doc["document_type"] not in self.ACADEMIC_TYPES:
            raise ValueError("Only candidate-level academic documents can be enabled for application context")
        self.db.execute(
            "UPDATE documents SET use_for_applications=? WHERE id=?",
            (int(use_for_applications), document_id),
        )
        return self.get(document_id)

    def mark_considered(self, application_id: int, document_id: int, note: str = ""):
        required(self.db.one("SELECT id FROM applications WHERE id=?", (application_id,)), "application")
        doc = self.get(document_id)
        if doc["application_id"] is not None or not doc["use_for_applications"]:
            raise ValueError("Document is not enabled as reusable candidate context")
        self.db.execute(
            """INSERT INTO application_document_usage(application_id,document_id,note)
               VALUES(?,?,?)
               ON CONFLICT(application_id,document_id)
               DO UPDATE SET note=excluded.note, considered_at=CURRENT_TIMESTAMP""",
            (application_id, document_id, note),
        )
        return required(
            self.db.one(
                "SELECT * FROM application_document_usage WHERE application_id=? AND document_id=?",
                (application_id, document_id),
            ),
            "document usage",
        )

    def usage(self, application_id: int):
        return self.db.all(
            """SELECT u.*,d.title,d.document_type,d.extraction_status,d.resource_uri
               FROM (
                 SELECT d.*,('jobtron://documents/' || d.id) resource_uri FROM documents d
               ) d
               JOIN application_document_usage u ON u.document_id=d.id
               WHERE u.application_id=? ORDER BY u.considered_at,d.id""",
            (application_id,),
        )

    def content(self, document_id: int) -> bytes:
        document = self.get(document_id)
        if document["file_path"]:
            return self.storage.path(document["file_path"]).read_bytes()
        return document["text_content"].encode()


class QuestionService:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return {token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 1}

    def list(self, application_id: int):
        questions = self.db.all(
            "SELECT * FROM questions WHERE application_id=? ORDER BY sort_order,id",
            (application_id,),
        )
        for question in questions:
            question["answers"] = self.db.all(
                "SELECT * FROM answers WHERE question_id=? ORDER BY version DESC",
                (question["id"],),
            )
            question["current_answer"] = next(
                (answer for answer in question["answers"] if answer["approved"]),
                question["answers"][0] if question["answers"] else None,
            )
            question["bank_entry"] = (
                self.db.one("SELECT * FROM question_bank WHERE id=?", (question["bank_entry_id"],))
                if question["bank_entry_id"]
                else None
            )
        return questions

    def add(
        self,
        application_id: int,
        question: str,
        field_type: str = "text",
        allowed_choices: str = "",
        required_field: bool = False,
        character_limit: int | None = None,
        sort_order: int = 0,
        bank_entry_id: int | None = None,
    ):
        if bank_entry_id is not None:
            required(self.db.one("SELECT id FROM question_bank WHERE id=?", (bank_entry_id,)), "question bank entry")
        qid = self.db.execute(
            """INSERT INTO questions(
                application_id,bank_entry_id,question,field_type,allowed_choices,required,character_limit,sort_order
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                application_id,
                bank_entry_id,
                question,
                field_type,
                allowed_choices,
                int(required_field),
                character_limit,
                sort_order,
            ),
        )
        return required(self.db.one("SELECT * FROM questions WHERE id=?", (qid,)), "question")

    def update(self, question_id: int, **fields):
        old = required(self.db.one("SELECT * FROM questions WHERE id=?", (question_id,)), "question")
        allowed = ["question", "field_type", "allowed_choices", "required", "character_limit", "sort_order"]
        values = [
            int(bool(fields.get(key, old[key]))) if key == "required" else fields.get(key, old[key])
            for key in allowed
        ]
        self.db.execute(
            f"UPDATE questions SET {', '.join(f'{key}=?' for key in allowed)} WHERE id=?",
            (*values, question_id),
        )
        return required(self.db.one("SELECT * FROM questions WHERE id=?", (question_id,)), "question")

    def save_answer(
        self,
        question_id: int,
        content: str,
        source_label: str = "",
        approved: bool = False,
        save_to_bank: bool = False,
        keywords: str = "",
    ):
        question = required(self.db.one("SELECT * FROM questions WHERE id=?", (question_id,)), "question")
        latest = required(
            self.db.one("SELECT COALESCE(MAX(version),0) AS version FROM answers WHERE question_id=?", (question_id,)),
            "answer version",
        )
        aid = self.db.execute(
            "INSERT INTO answers(question_id,content,version,approved,source_label) VALUES(?,?,?,?,?)",
            (question_id, content, int(latest["version"]) + 1, int(approved), source_label),
        )
        if approved:
            self.db.execute(
                "UPDATE answers SET approved=CASE WHEN id=? THEN 1 ELSE 0 END WHERE question_id=?",
                (aid, question_id),
            )
        if save_to_bank:
            bank_entry = self.save_bank_entry(
                question["question"],
                content,
                keywords,
                approved=True,
                bank_entry_id=question["bank_entry_id"],
            )
            self.db.execute("UPDATE questions SET bank_entry_id=? WHERE id=?", (bank_entry["id"], question_id))
        return required(self.db.one("SELECT * FROM answers WHERE id=?", (aid,)), "answer")

    def save_bank_entry(
        self,
        canonical_question: str,
        answer: str,
        keywords: str = "",
        approved: bool = True,
        bank_entry_id: int | None = None,
    ):
        canonical_question = canonical_question.strip()
        answer = answer.strip()
        if not canonical_question or not answer:
            raise ValueError("Canonical question and answer are required")
        if bank_entry_id is None:
            bank_entry_id = self.db.execute(
                "INSERT INTO question_bank(canonical_question,answer,keywords,approved) VALUES(?,?,?,?)",
                (canonical_question, answer, keywords, int(approved)),
            )
        else:
            required(self.db.one("SELECT id FROM question_bank WHERE id=?", (bank_entry_id,)), "question bank entry")
            self.db.execute(
                """UPDATE question_bank
                   SET canonical_question=?,answer=?,keywords=?,approved=?,updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (canonical_question, answer, keywords, int(approved), bank_entry_id),
            )
        return required(self.db.one("SELECT * FROM question_bank WHERE id=?", (bank_entry_id,)), "question bank entry")

    def search_bank(self, query: str, limit: int = 10):
        rows = self.db.all("SELECT * FROM question_bank WHERE approved=1")
        query_tokens = self._tokens(query)
        normalized_query = " ".join(query.lower().split())
        matches = []
        for row in rows:
            question_tokens = self._tokens(row["canonical_question"])
            keyword_tokens = self._tokens(row["keywords"])
            searchable_tokens = question_tokens | keyword_tokens
            matched = sorted(query_tokens & searchable_tokens)
            if query_tokens and not matched and normalized_query not in row["canonical_question"].lower():
                continue
            coverage = len(matched) / len(query_tokens) if query_tokens else 1.0
            precision = len(matched) / len(searchable_tokens) if searchable_tokens else 0.0
            phrase_bonus = 0.5 if normalized_query and normalized_query in row["canonical_question"].lower() else 0.0
            row["match_score"] = round(coverage + (0.25 * precision) + phrase_bonus, 3)
            row["matched_keywords"] = matched
            matches.append(row)
        matches.sort(key=lambda row: (row["match_score"], row["usage_count"], row["updated_at"]), reverse=True)
        return matches[: max(1, min(limit, 50))]

    def apply_bank_answer(self, question_id: int, bank_entry_id: int, approved: bool = False):
        entry = required(self.db.one("SELECT * FROM question_bank WHERE id=?", (bank_entry_id,)), "question bank entry")
        self.db.execute("UPDATE questions SET bank_entry_id=? WHERE id=?", (bank_entry_id, question_id))
        self.db.execute("UPDATE question_bank SET usage_count=usage_count+1 WHERE id=?", (bank_entry_id,))
        return self.save_answer(
            question_id,
            entry["answer"],
            source_label=f"question_bank:{bank_entry_id}",
            approved=approved,
        )


class AssetService:
    def __init__(self, db: Database, storage: FileStorage): self.db=db; self.storage=storage
    def list(self, application_id: int|None):
        return self.db.all("SELECT * FROM assets WHERE application_id IS NULL ORDER BY created_at DESC" if application_id is None else "SELECT * FROM assets WHERE application_id=? ORDER BY created_at DESC", () if application_id is None else (application_id,))
    def get(self, asset_id: int): return required(self.db.one("SELECT * FROM assets WHERE id=?",(asset_id,)),"asset")
    def save(self, title: str, filename: str, content: bytes, application_id: int|None=None, asset_type: str="other", notes: str=""):
        path,mime=self.storage.save(filename,content); aid=self.db.execute("INSERT INTO assets(application_id,asset_type,title,mime_type,file_path,notes) VALUES(?,?,?,?,?,?)",(application_id,asset_type,title,mime,path,notes)); return self.get(aid)
    def content(self, asset_id: int):
        asset=self.get(asset_id); return self.storage.path(asset["file_path"]).read_bytes()


class EventService:
    """The calendar's data layer.

    Every event carries a canonical UTC instant so the month grid can bucket by
    local day, plus an optional `dedupe_key` that makes automatic events safe to
    replay. Hand-entered events leave the key empty and may repeat freely.
    """

    def __init__(self, db: Database): self.db=db; self.services: "Services | None" = None

    SELECT = """SELECT e.*, a.formal_title, a.status, a.company_id, c.name company_name
                FROM events e
                JOIN applications a ON a.id=e.application_id
                JOIN companies c ON c.id=a.company_id"""

    def _decorate(self, rows: list[dict[str, Any]]):
        for row in rows:
            row["color"] = status_color(row["status"])
            row["local_day"] = local_day(row["starts_at_utc"]) or local_day(row["occurred_at"])
            moment = to_local(row["starts_at_utc"] or row["occurred_at"])
            row["local_time"] = "" if row["all_day"] or not moment else moment.strftime("%H:%M")
            row["local_label"] = moment.strftime("%a %d %b") if moment else ""
        return rows

    def list(self, application_id: int):
        return self._decorate(self.db.all(
            f"{self.SELECT} WHERE e.application_id=? ORDER BY e.starts_at_utc DESC, e.id DESC",
            (application_id,),
        ))

    def calendar(self):
        return self._decorate(self.db.all(f"{self.SELECT} ORDER BY e.starts_at_utc DESC, e.id DESC"))

    def month(self, year: int, month: int, status: str | None = None):
        """Every event falling in a local-calendar month, bucketed by local day.

        The UTC window is widened by a day at each end so an event stored in a
        neighbouring UTC day still lands in the right local cell.
        """
        start = datetime(year, month, 1, tzinfo=local_timezone())
        end = datetime(year + (month == 12), (month % 12) + 1, 1, tzinfo=local_timezone())
        window = (
            (start - timedelta(days=1)).astimezone(UTC).isoformat(timespec="seconds"),
            (end + timedelta(days=1)).astimezone(UTC).isoformat(timespec="seconds"),
        )
        sql = f"{self.SELECT} WHERE e.starts_at_utc >= ? AND e.starts_at_utc < ?"
        args: list[Any] = list(window)
        if status:
            sql += " AND a.status=?"
            args.append(status)
        rows = self._decorate(self.db.all(sql + " ORDER BY e.starts_at_utc, e.id", args))
        prefix = f"{year:04d}-{month:02d}"
        buckets: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            if row["local_day"].startswith(prefix):
                buckets.setdefault(row["local_day"], []).append(row)
        return buckets

    def upcoming(self, limit: int = 10, include_automatic: bool = False):
        """The agenda rail: real scheduled events, today onward, soonest first.

        Automatic lifecycle rows (Drafted, status transitions) are records of
        what already happened. They stay on the grid as dots, but they are not
        things coming up, and ten of them would bury every real interview.
        """
        floor = datetime.combine(today_local(), datetime.min.time(), local_timezone())
        clause = "" if include_automatic else " AND e.kind <> 'lifecycle'"
        return self._decorate(self.db.all(
            f"{self.SELECT} WHERE e.starts_at_utc >= ?{clause} ORDER BY e.starts_at_utc, e.id LIMIT ?",
            (floor.astimezone(UTC).isoformat(timespec="seconds"), limit),
        ))

    def get(self, event_id: int):
        rows = self._decorate(self.db.all(f"{self.SELECT} WHERE e.id=?", (event_id,)))
        return required(rows[0] if rows else None, "event")

    def add(
        self,
        application_id: int,
        event_type: str,
        occurred_at: str,
        notes: str = "",
        all_day: bool | None = None,
        dedupe_key: str = "",
        kind: str = "manual",
    ):
        if kind not in EVENT_KINDS:
            raise ValueError(f"Unsupported event kind: {kind!r}")
        moment, detected_all_day = parse_stored(occurred_at)
        if moment is None:
            raise ValueError(f"Unrecognised event time: {occurred_at!r}")
        event_id = self.db.execute(
            """INSERT INTO events(
                   application_id,event_type,occurred_at,notes,
                   starts_at_utc,all_day,timezone,dedupe_key,updated_at,kind
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                application_id, event_type, occurred_at, notes,
                moment.isoformat(timespec="seconds"),
                int(detected_all_day if all_day is None else all_day),
                str(local_timezone()),
                dedupe_key,
                datetime.now(UTC).isoformat(timespec="seconds"),
                kind,
            ),
        )
        return self.get(event_id)

    def record_auto(
        self,
        application_id: int,
        event_type: str,
        occurred_at: str,
        notes: str = "",
        key: str = "",
        all_day: bool = True,
    ):
        """Insert or refresh a lifecycle event. Safe to replay; never duplicates."""
        if key:
            existing = self.db.one("SELECT id FROM events WHERE dedupe_key=?", (key,))
            if existing:
                self.db.execute(
                    "UPDATE events SET event_type=?,occurred_at=?,notes=?,updated_at=? WHERE id=?",
                    (event_type, occurred_at, notes, datetime.now(UTC).isoformat(timespec="seconds"), existing["id"]),
                )
                return self.get(existing["id"])
        return self.add(application_id, event_type, occurred_at, notes, all_day=all_day, dedupe_key=key, kind="lifecycle")

    def update(self, event_id: int, event_type: str, occurred_at: str, notes: str = ""):
        moment, detected_all_day = parse_stored(occurred_at)
        if moment is None:
            raise ValueError(f"Unrecognised event time: {occurred_at!r}")
        self.db.execute(
            """UPDATE events SET event_type=?,occurred_at=?,notes=?,
                   starts_at_utc=?,all_day=?,updated_at=? WHERE id=?""",
            (
                event_type, occurred_at, notes,
                moment.isoformat(timespec="seconds"), int(detected_all_day),
                datetime.now(UTC).isoformat(timespec="seconds"), event_id,
            ),
        )
        return self.get(event_id)


class ApplicationService:
    STATUSES = (
        "Draft",
        "Ready to Apply",
        "Applied",
        "Assessment",
        "Interviewing",
        "Offer",
        "Rejected",
        "Withdrawn",
        "Closed",
    )

    def __init__(self, db: Database):
        self.db = db
        self.services: Services | None = None

    def list(self, status: str | None = None, tag: str | None = None, query: str | None = None):
        sql = "SELECT a.*,c.name company_name FROM applications a JOIN companies c ON c.id=a.company_id"
        clauses: list[str] = []
        args: list[Any] = []
        if status:
            clauses.append("a.status=?")
            args.append(status)
        if tag:
            # Whole-tag match: ',' || tags || ',' LIKE '%,x,%' so "remote" never matches "remote-ok".
            clauses.append("(',' || a.tags || ',') LIKE ('%,' || ? || ',%') ESCAPE '\\'")
            args.append(_escape_like(tag.strip().lower()))
        if query:
            like = f"%{_escape_like(query.strip())}%"
            clauses.append(
                "(c.name LIKE ? ESCAPE '\\' OR a.formal_title LIKE ? ESCAPE '\\' "
                "OR a.location LIKE ? ESCAPE '\\' OR a.tags LIKE ? ESCAPE '\\')"
            )
            args.extend([like, like, like, like])
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return self.db.all(sql + " ORDER BY COALESCE(a.applied_at,a.created_at) DESC,a.id DESC", tuple(args))

    def get(self, application_id: int):
        return required(
            self.db.one(
                """SELECT a.*,c.name company_name,c.notes company_notes
                   FROM applications a JOIN companies c ON c.id=a.company_id WHERE a.id=?""",
                (application_id,),
            ),
            "application",
        )

    def create(self, company_name: str, formal_title: str, allow_duplicate: bool = False, **fields):
        company_name = company_name.strip()
        formal_title = formal_title.strip()
        status = fields.get("status", "Draft")
        if not company_name or not formal_title:
            raise ValueError("Company and formal title are required")
        if status not in self.STATUSES:
            raise ValueError(f"Unsupported application status: {status}")
        url = str(fields.get("url", "") or "")
        if url.strip() and not allow_duplicate:
            existing = self.find_by_url(url)
            if existing is not None:
                return {**existing, "duplicate": True}
        with self.db.connect() as con:
            con.execute("INSERT OR IGNORE INTO companies(name) VALUES(?)", (company_name,))
            company_id = con.execute(
                "SELECT id FROM companies WHERE name=? COLLATE NOCASE",
                (company_name,),
            ).fetchone()[0]
            cur = con.execute(
                """INSERT INTO applications(
                    company_id,formal_title,normalized_role,role_family,url,location,job_description,
                    status,notes,questions_reviewed,cover_letter_required
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    company_id,
                    formal_title,
                    fields.get("normalized_role", ""),
                    fields.get("role_family", ""),
                    url,
                    fields.get("location", ""),
                    fields.get("job_description", ""),
                    status,
                    fields.get("notes", ""),
                    int(bool(fields.get("questions_reviewed", False))),
                    int(bool(fields.get("cover_letter_required", False))),
                ),
            )
            application_id = int(cur.lastrowid or 0)
        self._record_lifecycle_event(
            application_id,
            "Drafted",
            f"Draft created for {formal_title} at {company_name}.",
            key=f"{application_id}:drafted",
        )
        return {**self.get(application_id), "duplicate": False}

    def find_by_url(self, url: str) -> dict[str, Any] | None:
        """An existing application for this posting, so a re-paste never duplicates."""
        target = url.strip().rstrip("/")
        if not target:
            return None
        return self.db.one(
            """SELECT a.*,c.name company_name FROM applications a JOIN companies c ON c.id=a.company_id
               WHERE rtrim(trim(a.url),'/')=? ORDER BY a.id LIMIT 1""",
            (target,),
        )

    def _record_lifecycle_event(self, application_id: int, event_type: str, notes: str, key: str, when: str = ""):
        """Automatic calendar entry. Never raises: a calendar problem must not
        lose an application write."""
        if not self.services:
            return None
        try:
            return self.services.events.record_auto(
                application_id,
                event_type,
                when or today_local().isoformat(),
                notes,
                key=key,
                all_day=not when,
            )
        except Exception:
            return None

    def _schedule_follow_up_reminder(self, application_id: int, applied_at: str) -> None:
        """One follow-up reminder, seven days after Applied (D6).

        Idempotent by `dedupe_key`, the same mechanism lifecycle events use: a
        second Applied transition -- or a status-only replay -- finds the key
        already taken and writes nothing. Never raises, for the same reason
        `_record_lifecycle_event` never does: a calendar problem must not lose
        an application write.
        """
        if not self.services or not applied_at:
            return
        key = f"follow_up:{application_id}:1"
        if self.db.one("SELECT id FROM events WHERE dedupe_key=?", (key,)):
            return
        applied_moment, _ = parse_stored(applied_at)
        if applied_moment is None:
            return
        starts_at = (applied_moment + timedelta(days=FOLLOW_UP_DAYS)).isoformat(timespec="seconds")
        try:
            self.services.events.add(
                application_id,
                "Follow-up",
                starts_at,
                notes="Automatic follow-up reminder",
                all_day=True,
                dedupe_key=key,
                kind="reminder",
            )
        except Exception:
            pass

    def update(self, application_id: int, **fields):
        old = self.get(application_id)
        company_name = fields.pop("company_name", old["company_name"])
        with self.db.connect() as con:
            con.execute("INSERT OR IGNORE INTO companies(name) VALUES(?)", (company_name,))
            company_id = con.execute(
                "SELECT id FROM companies WHERE name=? COLLATE NOCASE",
                (company_name,),
            ).fetchone()[0]
            allowed = [
                "formal_title",
                "normalized_role",
                "role_family",
                "url",
                "location",
                "job_description",
                "status",
                "notes",
                "questions_reviewed",
                "cover_letter_required",
                "tags",
            ]
            values = []
            for key in allowed:
                if key in {"questions_reviewed", "cover_letter_required"}:
                    values.append(int(bool(fields.get(key, old[key]))))
                elif key == "tags":
                    values.append(normalize_tags(fields["tags"]) if "tags" in fields else old["tags"])
                else:
                    values.append(fields.get(key, old[key]))
            status = fields.get("status", old["status"])
            applied = old["applied_at"]
            if status not in self.STATUSES:
                raise ValueError(f"Unsupported application status: {status}")
            if status == "Applied" and not applied:
                applied = datetime.now().astimezone().isoformat(timespec="minutes")
            con.execute(
                f"UPDATE applications SET company_id=?,{', '.join(f'{key}=?' for key in allowed)},applied_at=? WHERE id=?",
                (company_id, *values, applied, application_id),
            )
        if status != old["status"]:
            self._record_lifecycle_event(
                application_id,
                f"Status: {old['status']} → {status}",
                f"Moved from {old['status']} to {status}.",
                key=f"{application_id}:status:{status}:{today_local().isoformat()}",
            )
        if status == "Applied":
            self._record_lifecycle_event(
                application_id,
                "Applied",
                "Application submitted.",
                key=f"{application_id}:applied",
                when=applied or "",
            )
            self._schedule_follow_up_reminder(application_id, applied or "")
            self.archive(application_id)
        return self.get(application_id)

    def set_status(self, application_id: int, status: str):
        """Change only the status.

        The full `update` path rebuilds every column from the submitted form, so
        a status-only POST through it silently clears `questions_reviewed` and
        `cover_letter_required`. This touches nothing else.
        """
        if status not in self.STATUSES:
            raise ValueError(f"Unsupported application status: {status}")
        return self.update(application_id, status=status)

    def tag(self, application_id: int, add: "list[str] | tuple[str, ...]" = (), remove: "list[str] | tuple[str, ...]" = ()) -> dict[str, Any]:
        """Add and/or remove tags in one call, touching nothing else on the row.

        Removing a tag that is not present is a no-op, not an error: the agent
        should never have to check membership before asking for a change.
        """
        old = self.get(application_id)
        current = {t for t in old["tags"].split(",") if t}
        added = {t.strip().lower() for t in add if t and t.strip()}
        removed = {t.strip().lower() for t in remove if t and t.strip()}
        updated = normalize_tags(",".join(sorted((current | added) - removed)))
        with self.db.connect() as con:
            con.execute("UPDATE applications SET tags=? WHERE id=?", (updated, application_id))
        return self.get(application_id)

    def record_question_extraction(self, application_id: int, status: str, note: str = ""):
        """Remember that the posting was scraped, and how it went."""
        self.get(application_id)
        self.db.execute(
            "UPDATE applications SET questions_extraction_status=?,questions_extraction_note=? WHERE id=?",
            (status, note, application_id),
        )
        return self.progress(application_id)

    def set_questions_reviewed(self, application_id: int, reviewed: bool = True):
        self.get(application_id)
        self.db.execute(
            "UPDATE applications SET questions_reviewed=? WHERE id=?",
            (int(reviewed), application_id),
        )
        return self.progress(application_id)

    def progress(self, application_id: int):
        assert self.services
        app = self.get(application_id)
        readiness = self.services.candidate.readiness()
        documents = self.services.documents.list(application_id)
        questions = self.services.questions.list(application_id)
        enabled_academic = self.services.documents.academic(only_enabled=True)
        usage = self.services.documents.usage(application_id)
        considered_ids = {item["document_id"] for item in usage}
        unconsidered_academic = [doc for doc in enabled_academic if doc["id"] not in considered_ids]
        approved_types = {doc["document_type"] for doc in documents if doc["approved"]}
        answered = sum(bool(question["current_answer"]) for question in questions)
        unanswered_required = [
            question for question in questions if question["required"] and not question["current_answer"]
        ]

        checks = [
            {
                "key": "candidate_profile",
                "label": "Basic profile information complete",
                "complete": readiness["profile_complete"],
                "required": True,
            },
            {
                "key": "master_resume",
                "label": "Master resume on file",
                "complete": readiness["master_resume_available"],
                "required": True,
            },
            {
                "key": "job_description",
                "label": "Job description captured",
                "complete": bool(app["job_description"].strip()),
                "required": True,
            },
            {
                "key": "academic_documents",
                "label": "Enabled academic documents considered",
                "complete": not unconsidered_academic,
                "required": bool(enabled_academic),
            },
            {
                "key": "questions_reviewed",
                "label": "Application questions inventoried",
                "complete": bool(app["questions_reviewed"]),
                "required": True,
            },
            {
                "key": "required_answers",
                "label": "Required questions answered",
                "complete": bool(app["questions_reviewed"]) and not unanswered_required,
                "required": True,
            },
            {
                "key": "application_resume",
                "label": "Tailored resume approved",
                "complete": "application_resume" in approved_types,
                "required": True,
            },
            {
                "key": "cover_letter",
                "label": "Cover letter approved",
                "complete": "cover_letter" in approved_types,
                "required": bool(app["cover_letter_required"]),
            },
        ]
        required_checks = [check for check in checks if check["required"]]
        complete_required = sum(check["complete"] for check in required_checks)
        percentage = round((complete_required / len(required_checks)) * 100) if required_checks else 100
        blockers = [check["label"] for check in required_checks if not check["complete"]]
        submitted = bool(app["applied_at"])

        if not app["job_description"].strip():
            next_action = {
                "code": "capture_job_description",
                "requires_user_input": True,
                "instruction": "Retrieve the posting from the saved URL when possible; otherwise ask the user for its text.",
                "question": (
                    f"Should I retrieve the full job description from {app['url']}?"
                    if app["url"]
                    else "Please share the job-posting URL or paste the job description."
                ),
            }
        elif readiness["capability"] != "full":
            application_url = f"{self.services.base_url}/applications/{application_id}"
            next_action = {
                "code": "complete_candidate_setup",
                "requires_user_input": True,
                "instruction": (
                    "Tell the user exactly what is missing and give them the setup URL. "
                    "Do not generate a resume, a cover letter, or any answer until this is resolved."
                ),
                "question": (
                    f"I saved a draft for {app['formal_title']} at {app['company_name']}: {application_url}. "
                    f"I cannot build the application yet because {readiness['gap_phrase']}. "
                    f"Open {readiness['setup_url']} to upload your master resume and fill in your basic "
                    f"information, then tell me you are done."
                ),
                "setup_url": readiness["setup_url"],
                "frontend_url": application_url,
                "must_share_url": True,
                "blocked_by": readiness["blocked_by"],
            }
        elif unconsidered_academic:
            next_action = {
                "code": "consider_academic_documents",
                "requires_user_input": False,
                "instruction": "Read each resource URI, use relevant facts, then call documents.mark_considered for each document.",
                "document_ids": [doc["id"] for doc in unconsidered_academic],
                "resource_uris": [f"jobtron://documents/{doc['id']}" for doc in unconsidered_academic],
                "question": "",
            }
        elif not app["questions_reviewed"] and app["url"].strip() and not app["questions_extraction_status"]:
            next_action = {
                "code": "extract_questions",
                "requires_user_input": False,
                "instruction": "Fetch the posting and list any application questions found on it.",
                "question": "",
                "url": app["url"],
            }
        elif not app["questions_reviewed"]:
            attempted = app["questions_extraction_status"]
            assets_url = f"{self.services.base_url}/applications/{application_id}#assets"
            if attempted and attempted != "ok":
                question = (
                    f"I could not read the application questions from that page. "
                    f"{app['questions_extraction_note']} "
                    f"Paste them here, drop screenshots at {assets_url}, or tell me there are none."
                )
            else:
                question = (
                    "Please paste the application questions, or confirm that this application "
                    "has no additional questions."
                )
            next_action = {
                "code": "inventory_questions",
                "requires_user_input": True,
                "instruction": "Capture every proprietary application question, or record that the application has none.",
                "question": question,
                "assets_url": assets_url,
                "extraction_status": attempted,
            }
        elif unanswered_required:
            question = unanswered_required[0]
            next_action = {
                "code": "answer_required_question",
                "requires_user_input": True,
                "instruction": "Search question_bank.search before drafting a new answer.",
                "question_id": question["id"],
                "question": question["question"],
            }
        elif "application_resume" not in approved_types:
            next_action = {
                "code": "prepare_resume",
                "requires_user_input": True,
                "instruction": "Draft or attach an application-specific resume and request approval.",
                "question": "Would you like me to prepare the tailored resume for this application now?",
            }
        elif app["cover_letter_required"] and "cover_letter" not in approved_types:
            next_action = {
                "code": "prepare_cover_letter",
                "requires_user_input": True,
                "instruction": "Draft the required cover letter and request approval.",
                "question": "Would you like me to draft the required cover letter now?",
            }
        elif not submitted:
            next_action = {
                "code": "confirm_submission",
                "requires_user_input": True,
                "instruction": "Do not mark Applied until the user confirms the external submission.",
                "question": "The required package is complete. Have you submitted it so I can mark it Applied and archive it?",
            }
        else:
            next_action = {
                "code": "post_submission_follow_up",
                "requires_user_input": True,
                "instruction": "Offer to record the next follow-up or deadline.",
                "question": "This application is submitted and archived. Would you like to add a follow-up reminder?",
            }

        return {
            "percent_complete": percentage,
            "ready_to_apply": not blockers,
            "complete": not blockers,
            "checks": checks,
            "blocking_items": blockers,
            "next_action": next_action,
            "capability": readiness["capability"],
            "candidate_readiness": readiness,
            "questions_found": len(questions),
            "questions_answered": answered,
            "required_questions_unanswered": [question["id"] for question in unanswered_required],
            "academic_documents_enabled": len(enabled_academic),
            "academic_documents_considered": len(enabled_academic) - len(unconsidered_academic),
            "submitted": submitted,
            "job_description": bool(app["job_description"].strip()),
            "application_resume": "application_resume" in approved_types,
            "cover_letter": "cover_letter" in approved_types,
        }

    def completeness(self, application_id: int):
        return self.progress(application_id)

    def get_context(self, application_id: int):
        assert self.services
        app = self.get(application_id)
        progress = self.progress(application_id)
        return {
            "application": app,
            "candidate": self.services.candidate.get_context(),
            "documents": self.services.documents.list(application_id),
            "academic_document_usage": self.services.documents.usage(application_id),
            "questions": self.services.questions.list(application_id),
            "assets": self.services.assets.list(application_id),
            "events": self.services.events.list(application_id),
            "contacts": self.db.all("SELECT * FROM contacts WHERE application_id=?", (application_id,)),
            "completeness": progress,
            "progress": progress,
        }

    def autofill_options(self, application_id: int):
        """Return compact, selectable source and output metadata for an agent autofill plan."""
        assert self.services
        context = self.get_context(application_id)
        app = context["application"]
        candidate = context["candidate"]
        sources: list[dict[str, Any]] = []

        def add_source(
            source_id: str,
            group: str,
            label: str,
            description: str,
            recommended: bool = False,
            available: bool = True,
        ):
            sources.append(
                {
                    "id": source_id,
                    "group": group,
                    "label": label,
                    "description": description,
                    "recommended": recommended,
                    "available": available,
                }
            )

        if app["job_description"].strip():
            add_source("job_posting", "Application", "Job posting", "Saved role requirements and responsibilities.", True)
        if candidate["profile"]:
            add_source("candidate_profile", "Candidate", "Candidate profile", "Contact and reusable profile information.", True)
        if candidate["facts"]:
            add_source(
                "candidate_facts",
                "Candidate",
                f"Verified candidate facts ({len(candidate['facts'])})",
                "Reusable experience, skills, and accomplishments.",
                True,
            )

        application_resumes = [document for document in context["documents"] if document["document_type"] == "application_resume"]
        for document in context["documents"]:
            document_type = document["document_type"].replace("_", " ").title()
            add_source(
                f"application_document:{document['id']}",
                "Application",
                f"{document_type}: {document['title']}",
                f"Application version {document['version']}{' · approved' if document['approved'] else ''}.",
                document["document_type"] == "application_resume" and bool(document["approved"]),
            )
        if not application_resumes:
            add_source("application_resume_unavailable", "Application", "Application-specific resume", "No tailored resume has been saved yet.", available=False)

        master_resumes = candidate["master_resumes"]
        for document in master_resumes:
            add_source(
                f"master_resume:{document['id']}",
                "Candidate",
                f"Master resume: {document['title']}",
                f"Version {document['version']}{' · approved' if document['approved'] else ''}.",
                bool(document["approved"]),
            )
        if not master_resumes:
            add_source("master_resume_unavailable", "Candidate", "Master resume", "No master resume is available yet.", available=False)

        question_bank = self.services.questions.search_bank("", 50)
        if question_bank:
            add_source(
                "question_bank",
                "Reusable answers",
                f"Application-wide question bank ({len(question_bank)})",
                "Approved answers matched to each question before drafting.",
                True,
            )
        else:
            add_source("question_bank_unavailable", "Reusable answers", "Application-wide question bank", "No approved reusable answers are available yet.", available=False)
        for document in candidate["application_context_documents"]:
            add_source(
                f"academic_document:{document['id']}",
                "Academic context",
                document["title"],
                f"{document['document_type'].replace('_', ' ').title()} · {document['extraction_status']} extraction.",
                True,
            )
        for asset in context["assets"]:
            add_source(
                f"asset:{asset['id']}",
                "Application uploads",
                asset["title"],
                asset["asset_type"].replace("_", " ").title(),
            )

        progress = context["progress"]
        return {
            "application_id": application_id,
            "application_label": f"{app['company_name']} · {app['formal_title']}",
            "sources": sources,
            "targets": [
                {
                    "id": "application_resume",
                    "label": "Tailored resume",
                    "description": "Create a new application-specific resume version.",
                    "recommended": not progress["application_resume"],
                },
                {
                    "id": "cover_letter",
                    "label": "Cover letter",
                    "description": "Create a role-specific cover letter draft.",
                    "recommended": bool(app["cover_letter_required"]) and not progress["cover_letter"],
                },
                {
                    "id": "application_questions",
                    "label": "Application questions",
                    "description": "Draft answers for unanswered questions, using the question bank first.",
                    "recommended": progress["questions_answered"] < progress["questions_found"],
                },
                {
                    "id": "custom_resource",
                    "label": "Another resource",
                    "description": "Describe another application artifact for the agent to create.",
                    "recommended": False,
                },
            ],
        }

    def prepare_autofill(
        self,
        application_id: int,
        source_ids: list[str],
        target_ids: list[str],
        custom_request: str = "",
    ):
        """Validate selections and return the exact non-mutating request to hand to the agent."""
        options = self.autofill_options(application_id)
        source_map = {source["id"]: source for source in options["sources"] if source["available"]}
        target_map = {target["id"]: target for target in options["targets"]}
        selected_sources = [source_map[source_id] for source_id in dict.fromkeys(source_ids) if source_id in source_map]
        selected_targets = [target_map[target_id] for target_id in dict.fromkeys(target_ids) if target_id in target_map]
        errors = []
        if not selected_sources:
            errors.append("Select at least one source.")
        if not selected_targets:
            errors.append("Select at least one item to create or complete.")
        if any(target["id"] == "custom_resource" for target in selected_targets) and not custom_request.strip():
            errors.append("Describe the additional resource you want created.")

        source_lines = "\n".join(f"- {source['label']} [{source['id']}]" for source in selected_sources)
        target_lines = "\n".join(f"- {target['label']} [{target['id']}]" for target in selected_targets)
        custom_line = f"\nAdditional resource request: {custom_request.strip()}" if custom_request.strip() else ""
        agent_prompt = (
            f"Autofill Jobtron application {application_id} ({options['application_label']}) using only these selected sources:\n"
            f"{source_lines}\n\nCreate or complete:\n{target_lines}{custom_line}\n\n"
            "Use the Jobtron MCP to read the selected sources. For application questions, search question_bank.search "
            "before drafting each answer. Save every generated answer or document as a new unapproved version unless I "
            "explicitly approve it. Do not mark the application Applied. If a factual choice is unsupported, ask me instead "
            "of inventing it. After the work, call applications.progress and ui.show_application, then share frontend_url."
        ) if not errors else ""
        return {
            "valid": not errors,
            "errors": errors,
            "application_id": application_id,
            "selected_sources": selected_sources,
            "selected_targets": selected_targets,
            "custom_request": custom_request.strip(),
            "agent_prompt": agent_prompt,
        }

    def archive(self, application_id: int):
        existing = self.db.one("SELECT * FROM application_archives WHERE application_id=?", (application_id,))
        if existing:
            return existing
        snapshot = json.dumps(self.get_context(application_id), ensure_ascii=False, sort_keys=True)
        archive_id = self.db.execute(
            "INSERT INTO application_archives(application_id,snapshot_json) VALUES(?,?)",
            (application_id, snapshot),
        )
        return required(self.db.one("SELECT * FROM application_archives WHERE id=?", (archive_id,)), "archive")

    def get_archive(self, application_id: int):
        row = self.db.one("SELECT * FROM application_archives WHERE application_id=?", (application_id,))
        return json.loads(row["snapshot_json"]) if row else None

    def add_contact(
        self,
        application_id: int,
        name: str,
        role: str = "",
        email: str = "",
        phone: str = "",
        notes: str = "",
    ):
        contact_id = self.db.execute(
            "INSERT INTO contacts(application_id,name,role,email,phone,notes) VALUES(?,?,?,?,?,?)",
            (application_id, name, role, email, phone, notes),
        )
        return required(self.db.one("SELECT * FROM contacts WHERE id=?", (contact_id,)), "contact")

    def last_activity_at(self, application_id: int, now: datetime | None = None) -> str:
        """Last touch on this application, computed fresh on every read (D2).

        max(created_at, applied_at, any non-lifecycle event that has already
        started as of `now`). Never cached: a stored last-activity value
        drifts the moment a write path forgets to update it.
        """
        app = self.get(application_id)
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        best, _ = parse_stored(app["created_at"])
        best = best or now
        if app["applied_at"]:
            applied_moment, _ = parse_stored(app["applied_at"])
            if applied_moment and applied_moment > best:
                best = applied_moment
        for row in self.db.all(
            "SELECT starts_at_utc FROM events WHERE application_id=? AND kind<>'lifecycle'",
            (application_id,),
        ):
            moment, _ = parse_stored(row["starts_at_utc"])
            if moment and moment <= now and moment > best:
                best = moment
        return best.isoformat(timespec="seconds")

    def next_event(self, application_id: int, now: datetime | None = None) -> dict[str, Any] | None:
        """The earliest upcoming non-lifecycle event for one application.

        The single-row sibling of `attention()`'s `upcoming` bucket, used by
        the detail page's "next:" line -- unlike that bucket it is not
        limited to `HORIZON_DAYS`.
        """
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        rows = self.db.all(
            "SELECT * FROM events WHERE application_id=? AND kind<>'lifecycle' ORDER BY starts_at_utc",
            (application_id,),
        )
        for row in rows:
            moment, _ = parse_stored(row["starts_at_utc"])
            if moment and moment >= now:
                return row
        return None

    @staticmethod
    def _reminder_resolved(reminder: dict[str, Any], application_events: list[dict[str, Any]]) -> bool:
        """A reminder is resolved once a manual event, or a status change, lands after it."""
        reminder_moment, _ = parse_stored(reminder["starts_at_utc"])
        if reminder_moment is None:
            return False
        for other in application_events:
            if other["id"] == reminder["id"]:
                continue
            other_moment, _ = parse_stored(other["starts_at_utc"])
            if other_moment is None or other_moment <= reminder_moment:
                continue
            if other["kind"] == "manual":
                return True
            if other["kind"] == "lifecycle" and str(other["event_type"]).startswith("Status:"):
                return True
        return False

    def attention(self, now: datetime | None = None) -> dict[str, list[dict[str, Any]]]:
        """Everything that needs the user's attention today (D2, D6).

        Four buckets -- upcoming, follow_up_due, stale, idle_drafts -- computed
        fresh from application and event rows every call. Nothing here is
        stored; thresholds live in the module constants above.
        """
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        horizon = now + timedelta(days=HORIZON_DAYS)

        apps = self.db.all(
            "SELECT a.*, c.name company_name FROM applications a JOIN companies c ON c.id=a.company_id"
        )
        events_by_app: dict[int, list[dict[str, Any]]] = {}
        for event in self.db.all("SELECT * FROM events"):
            events_by_app.setdefault(event["application_id"], []).append(event)

        upcoming: list[dict[str, Any]] = []
        follow_up_due: list[dict[str, Any]] = []
        stale: list[dict[str, Any]] = []
        idle_drafts: list[dict[str, Any]] = []

        for app in apps:
            status = app["status"]
            if status in TERMINAL_STATUSES:
                continue
            app_id = app["id"]
            app_events = events_by_app.get(app_id, [])
            last_activity = self.last_activity_at(app_id, now=now)
            base = {
                "application_id": app_id,
                "company_name": app["company_name"],
                "formal_title": app["formal_title"],
                "status": status,
                "last_activity_at": last_activity,
                "tags": app["tags"],
            }

            for event in app_events:
                if event["kind"] == "lifecycle":
                    continue
                moment, _ = parse_stored(event["starts_at_utc"])
                if moment is None or not (now <= moment < horizon):
                    continue
                local_moment = to_local(event["starts_at_utc"])
                when = local_moment.strftime("%a %b %d") if local_moment else event["starts_at_utc"]
                upcoming.append({
                    **base,
                    "days": (moment - now).days,
                    "reason": f"{event['event_type']} on {when}",
                    "event_id": event["id"],
                    "event_type": event["event_type"],
                    "starts_at_utc": event["starts_at_utc"],
                })

            for event in app_events:
                if event["kind"] != "reminder":
                    continue
                moment, _ = parse_stored(event["starts_at_utc"])
                if moment is None or moment > now:
                    continue
                if self._reminder_resolved(event, app_events):
                    continue
                applied_moment = parse_stored(app["applied_at"])[0] if app["applied_at"] else None
                days = (now - applied_moment).days if applied_moment else (now - moment).days
                follow_up_due.append({
                    **base,
                    "days": days,
                    "reason": f"Applied {days} days ago, no reply",
                    "event_id": event["id"],
                    "event_type": event["event_type"],
                    "starts_at_utc": event["starts_at_utc"],
                })

            if status in STALE_STATUSES:
                last_moment, _ = parse_stored(last_activity)
                if last_moment:
                    idle_days = (now - last_moment).days
                    if idle_days > STALE_DAYS:
                        stale.append({
                            **base,
                            "days": idle_days,
                            "reason": f"{status}, quiet for {idle_days} days",
                        })

            if status in DRAFT_STATUSES:
                created_moment, _ = parse_stored(app["created_at"])
                has_manual = any(event["kind"] == "manual" for event in app_events)
                if created_moment and not has_manual:
                    idle_days = (now - created_moment).days
                    if idle_days > IDLE_DRAFT_DAYS:
                        idle_drafts.append({
                            **base,
                            "days": idle_days,
                            "reason": f"Drafted {idle_days} days ago, never sent",
                        })

        upcoming.sort(key=lambda row: row["starts_at_utc"])
        follow_up_due.sort(key=lambda row: row["days"], reverse=True)
        stale.sort(key=lambda row: row["days"], reverse=True)
        idle_drafts.sort(key=lambda row: row["days"], reverse=True)

        return {
            "upcoming": upcoming,
            "follow_up_due": follow_up_due,
            "stale": stale,
            "idle_drafts": idle_drafts,
        }


class Services:
    def __init__(self, db: Database, storage: FileStorage, base_url: str = ""):
        self.db=db; self.storage=storage; self.base_url=base_url.rstrip("/")
        self.documents=DocumentService(db,storage); self.candidate=CandidateService(db); self.questions=QuestionService(db); self.assets=AssetService(db,storage); self.events=EventService(db); self.applications=ApplicationService(db)
        self.candidate.documents=self.documents; self.candidate.services=self
        self.applications.services=self; self.events.services=self
