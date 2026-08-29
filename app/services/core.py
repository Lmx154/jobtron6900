from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from app.db import Database
from app.services.extraction import extract_document_text
from app.storage import FileStorage


class NotFoundError(LookupError): pass


def required(row: dict[str, Any] | None, kind: str) -> dict[str, Any]:
    if row is None: raise NotFoundError(f"{kind} not found")
    return row


class CandidateService:
    def __init__(self, db: Database): self.db = db; self.documents: DocumentService | None = None

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
    def get_context(self):
        assert self.documents
        academic_documents = self.documents.academic()
        for document in academic_documents:
            document["resource_uri"] = f"jobtron://documents/{document['id']}"
        return {
            "profile": self.get_profile(),
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
    def __init__(self, db: Database): self.db=db
    def list(self, application_id: int): return self.db.all("SELECT * FROM events WHERE application_id=? ORDER BY occurred_at DESC,id DESC",(application_id,))
    def calendar(self): return self.db.all("SELECT e.*,a.formal_title,c.name company_name FROM events e JOIN applications a ON a.id=e.application_id JOIN companies c ON c.id=a.company_id ORDER BY occurred_at DESC")
    def add(self, application_id: int, event_type: str, occurred_at: str, notes: str=""):
        eid=self.db.execute("INSERT INTO events(application_id,event_type,occurred_at,notes) VALUES(?,?,?,?)",(application_id,event_type,occurred_at,notes)); return required(self.db.one("SELECT * FROM events WHERE id=?",(eid,)),"event")
    def update(self,event_id:int,event_type:str,occurred_at:str,notes:str=""):
        self.db.execute("UPDATE events SET event_type=?,occurred_at=?,notes=? WHERE id=?",(event_type,occurred_at,notes,event_id)); return required(self.db.one("SELECT * FROM events WHERE id=?",(event_id,)),"event")


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

    def list(self, status: str | None = None):
        sql = "SELECT a.*,c.name company_name FROM applications a JOIN companies c ON c.id=a.company_id"
        args = ()
        if status:
            sql += " WHERE a.status=?"
            args = (status,)
        return self.db.all(sql + " ORDER BY COALESCE(a.applied_at,a.created_at) DESC,a.id DESC", args)

    def get(self, application_id: int):
        return required(
            self.db.one(
                """SELECT a.*,c.name company_name,c.notes company_notes
                   FROM applications a JOIN companies c ON c.id=a.company_id WHERE a.id=?""",
                (application_id,),
            ),
            "application",
        )

    def create(self, company_name: str, formal_title: str, **fields):
        company_name = company_name.strip()
        formal_title = formal_title.strip()
        status = fields.get("status", "Draft")
        if not company_name or not formal_title:
            raise ValueError("Company and formal title are required")
        if status not in self.STATUSES:
            raise ValueError(f"Unsupported application status: {status}")
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
                    fields.get("url", ""),
                    fields.get("location", ""),
                    fields.get("job_description", ""),
                    status,
                    fields.get("notes", ""),
                    int(bool(fields.get("questions_reviewed", False))),
                    int(bool(fields.get("cover_letter_required", False))),
                ),
            )
            application_id = int(cur.lastrowid or 0)
        return self.get(application_id)

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
            ]
            values = [
                int(bool(fields.get(key, old[key]))) if key in {"questions_reviewed", "cover_letter_required"}
                else fields.get(key, old[key])
                for key in allowed
            ]
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
        if status == "Applied":
            self.archive(application_id)
        return self.get(application_id)

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
        elif unconsidered_academic:
            next_action = {
                "code": "consider_academic_documents",
                "requires_user_input": False,
                "instruction": "Read each resource URI, use relevant facts, then call documents.mark_considered for each document.",
                "document_ids": [doc["id"] for doc in unconsidered_academic],
                "resource_uris": [f"jobtron://documents/{doc['id']}" for doc in unconsidered_academic],
                "question": "",
            }
        elif not app["questions_reviewed"]:
            next_action = {
                "code": "inventory_questions",
                "requires_user_input": True,
                "instruction": "Capture every proprietary application question, or record that the application has none.",
                "question": "Please paste the application questions, or confirm that this application has no additional questions.",
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


class Services:
    def __init__(self, db: Database, storage: FileStorage):
        self.db=db; self.storage=storage; self.documents=DocumentService(db,storage); self.candidate=CandidateService(db); self.questions=QuestionService(db); self.assets=AssetService(db,storage); self.events=EventService(db); self.applications=ApplicationService(db)
        self.candidate.documents=self.documents; self.applications.services=self
