"""Tracker guarantees: visible drafts, stable sources, immutable dated exports."""
import asyncio
import io
import json
import sqlite3
import zipfile
from datetime import datetime

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from app.db import Database, MIGRATIONS, SCHEMA
from app.mcp.server import build_server
from app.services import Services, NotFoundError
from app.storage import FileStorage


def test_migration_preserves_legacy_records_and_statuses(tmp_path):
    db = Database(tmp_path / 'legacy.db')
    with db.connect() as con:
        con.executescript(SCHEMA)
        for version, migrate in MIGRATIONS:
            if version <= 6:
                migrate(con)
        con.execute('PRAGMA user_version=6')
        con.execute("INSERT INTO companies(id,name) VALUES(1,'Legacy')")
        con.execute("INSERT INTO applications(id,company_id,formal_title,status,notes) VALUES(1,1,'Engineer','Rejected','Keep this')")
        con.execute("INSERT INTO applications(id,company_id,formal_title,status) VALUES(2,1,'Developer','Applied')")
        con.execute("INSERT INTO documents(application_id,document_type,title,text_content,version) VALUES(1,'application_resume','Old resume','Preserved',1)")
        con.execute("INSERT INTO application_archives(application_id,snapshot_json) VALUES(1,'{\"preserved\":true}')")
    db.initialize()
    db.initialize()
    svc = Services(db, FileStorage(tmp_path/'files'))
    assert svc.applications.get(1)['active'] == 0
    assert svc.applications.get(1)['status'] == 'Rejected'
    assert svc.applications.get(1)['notes'] == 'Keep this'
    assert svc.applications.get(2)['active'] == 1
    assert svc.documents.list(1)[0]['text_content'] == 'Preserved'
    assert svc.applications.get_archive(1) == {'preserved':True}


def test_active_flag_is_user_owned_and_filterable(ready_services):
    svc = ready_services
    a = svc.applications.create('Acme', 'Engineer', job_description='Build')
    b = svc.applications.create('Elsewhere', 'Analyst')
    svc.applications.set_active(a['id'], False)
    assert [x['id'] for x in svc.applications.list(active=False)] == [a['id']]
    assert [x['id'] for x in svc.applications.list(active=True)] == [b['id']]
    svc.applications.update(a['id'], notes='New note')
    svc.applications.create_bundle(a['id'])
    assert svc.applications.get(a['id'])['active'] == 0
    assert not svc.applications.prepare_drafts(a['id'])['valid']
    assert svc.applications.get(a['id'])['applied_at'] is None
    with pytest.raises(ValueError):
        svc.applications.set_active(a['id'], 'false')


def test_bundle_freezes_latest_versions_and_exact_original_resume(services):
    svc = services
    original = b'Original resume bytes\nExperience'
    resume = svc.documents.save('master_resume', 'Original', filename='resume.txt', content=original, approved=True)
    app = svc.applications.create('Acme', 'Engineer', job_description='Posting v1')
    q = svc.questions.add(app['id'], 'Why this role?')
    svc.questions.save_answer(q['id'], 'Old reviewed answer', approved=True)
    draft = svc.questions.save_answer(q['id'], 'New unreviewed answer')
    svc.documents.save('cover_letter', 'Letter', app['id'], 'Old letter', approved=True)
    letter = svc.documents.save('cover_letter', 'Letter', app['id'], 'New draft letter')
    svc.documents.save('master_resume', 'New source', text_content='Replacement resume', approved=True)
    bundle = svc.applications.create_bundle(app['id'])
    frozen = svc.applications.bundle_zip(app['id'], bundle['id'])
    assert datetime.fromisoformat(bundle['created_at']).utcoffset().total_seconds() == 0
    with zipfile.ZipFile(io.BytesIO(frozen)) as z:
        snapshot = json.loads(z.read('application.json'))
        assert z.read('original-resume.txt') == original
        assert z.read('job-posting.txt') == b'Posting v1'
        assert snapshot['resume']['id'] == resume['id']
        assert snapshot['questions'][0]['answer']['id'] == draft['id']
        assert snapshot['cover_letter']['id'] == letter['id']
        assert '[Draft]' in z.read('answers.txt').decode()
        assert z.read('cover-letter.txt').startswith(b'[Draft]')
    svc.questions.save_answer(q['id'], 'Later approved answer', approved=True)
    svc.documents.save('cover_letter', 'Later', app['id'], 'Later letter', approved=True)
    svc.applications.update(app['id'], job_description='Posting v2')
    # Even changes outside normal immutable-file usage cannot rewrite an existing export.
    svc.storage.path(resume['file_path']).write_bytes(b'Changed source bytes')
    assert svc.applications.bundle_zip(app['id'], bundle['id']) == frozen
    second = svc.applications.create_bundle(app['id'])
    assert second['id'] != bundle['id']
    assert len(svc.applications.list_bundles(app['id'])) == 2
    other = svc.applications.create('Other', 'Role')
    with pytest.raises(NotFoundError):
        svc.applications.bundle_zip(other['id'], bundle['id'])


def test_incomplete_bundle_is_honest_about_missing_material(services):
    app = services.applications.create('Acme', 'Engineer')
    services.questions.add(app['id'], 'Unknown answer')
    bundle = services.applications.create_bundle(app['id'])
    with zipfile.ZipFile(io.BytesIO(services.applications.bundle_zip(app['id'], bundle['id']))) as z:
        assert '[Unanswered]' in z.read('answers.txt').decode()
        assert z.read('cover-letter.txt').startswith(b'[Missing]')
        assert not any(name.startswith('original-resume') for name in z.namelist())


def test_generation_pins_resume_uploaded_after_application_and_leaves_drafts_visible(services):
    svc = services
    app = svc.applications.create('Acme', 'Engineer', job_description='Build')
    svc.applications.set_questions_reviewed(app['id'])
    assert not svc.applications.prepare_drafts(app['id'])['valid']
    original = svc.documents.save('master_resume', 'Original', text_content='Candidate experience', approved=True)
    assert svc.applications.prepare_drafts(app['id'])['valid']
    svc.documents.save('master_resume', 'Replacement', text_content='New experience', approved=True)
    assert svc.applications.pinned_resume(app['id'])['id'] == original['id']
    q = svc.questions.add(app['id'], 'Optional question')
    svc.questions.save_answer(q['id'], 'Reviewed', approved=True)
    latest = svc.questions.save_answer(q['id'], 'Regenerated', save_to_bank=True)
    assert svc.questions.list(app['id'])[0]['current_answer']['id'] == latest['id']
    assert svc.questions.search_bank('Optional question') == []
    svc.documents.save('cover_letter','Letter',app['id'],'Draft letter')
    assert len(svc.applications.pending_drafts(app['id'])) == 2
    svc.applications.approve_drafts(app['id'], all=True)
    assert svc.applications.progress(app['id'])['complete']


def test_unreadable_resume_blocks_generation_without_blocking_tracking(services):
    services.documents.save('master_resume', 'Unreadable', approved=True)
    app = services.applications.create('Acme', 'Engineer', job_description='Build')
    services.applications.set_questions_reviewed(app['id'])
    assert not services.applications.prepare_drafts(app['id'])['valid']
    assert services.candidate.readiness()['capability'] == 'draft_only'
    assert services.applications.create_bundle(app['id'])['id']


def test_mcp_generation_scope_and_bundle_round_trip(ready_services):
    svc = ready_services
    app = svc.applications.create('Acme', 'Engineer', job_description='Build')
    svc.applications.set_questions_reviewed(app['id'])
    server = build_server(svc)
    async def exercise():
        with pytest.raises(ToolError, match='cover_letter'):
            await server.call_tool('documents.save', {'application_id':app['id'], 'document_type':'application_resume', 'title':'Bad', 'text_content':'No'})
        good = await server.call_tool('documents.save', {'application_id':app['id'], 'document_type':'cover_letter', 'title':'Letter', 'text_content':'Draft'})
        assert not good.is_error
        result = await server.call_tool('applications.prepare_drafts', {'application_id':app['id']})
        assert result.structured_content['valid']
        result = await server.call_tool('applications.create_bundle', {'application_id':app['id']})
        assert result.structured_content['download_url'].endswith('/bundles/1')
        result = await server.call_tool('applications.update', {'application_id':app['id'], 'notes':'Preserve other fields'})
        assert not result.is_error
    asyncio.run(exercise())
    assert svc.applications.get(app['id'])['job_description'] == 'Build'
    assert svc.documents.list(app['id'])[0]['approved'] == 0
