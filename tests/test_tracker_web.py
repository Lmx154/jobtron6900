import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(services, settings):
    with TestClient(create_app(settings, services, include_mcp_http=False)) as client:
        yield client


def test_tracker_home_creation_filter_and_minimal_profile(client, services):
    response = client.get('/', follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == '/applications'
    created = client.post('/applications', data={'company_name': 'Example', 'formal_title': 'Engineer', 'job_description': 'Build useful tools.'})
    assert created.status_code == 200
    assert 'Questions & answers' in created.text
    assert 'Autofill' not in created.text
    assert 'application_resume' not in created.text
    assert client.post('/applications', data={'company_name': ' ', 'formal_title': 'Engineer'}).status_code == 422
    application = services.applications.list()[0]
    assert client.post(f"/applications/{application['id']}/active", data={'active': 'false'}).status_code == 200
    assert 'Engineer' not in client.get('/applications?active=active').text
    assert 'Engineer' in client.get('/applications?active=inactive&q=Example').text
    assert 'Engineer' not in client.get('/applications?active=inactive&q=nomatch').text
    profile = client.get('/profile')
    assert profile.status_code == 200
    assert 'Original resume' in profile.text
    assert 'Academic documents' not in profile.text
    assert 'Candidate facts' not in profile.text


def test_tracker_review_versions_draft_request_and_bundle(client, services):
    services.documents.save('master_resume', 'Original', text_content='I build Python tools.', approved=True)
    application = services.applications.create('Example', 'Engineer', job_description='Build Python tools.')
    base = f"/applications/{application['id']}"
    assert client.post(base + '/drafts', headers={'accept': 'application/json'}).status_code == 422
    assert client.post(base + '/questions', data={'question': 'Why this role?', 'required': 'on'}).status_code == 200
    question = services.questions.list(application['id'])[0]
    client.post(base + '/questions/reviewed', data={'questions_reviewed': 'on'})
    plan = client.post(base + '/drafts', headers={'accept': 'application/json'})
    assert plan.status_code == 200
    assert plan.json()['valid']
    assert 'Never generate or change a resume' in plan.json()['agent_prompt']
    assert client.post(base + '/drafts').status_code == 200
    answer_url = base + f"/questions/{question['id']}/answers"
    client.post(answer_url, data={'content': 'Reviewed older answer', 'approved': 'on'})
    client.post(answer_url, data={'content': 'Fresh draft answer'})
    client.post(base + '/documents', data={'document_type': 'cover_letter', 'text_content': 'Fresh cover letter draft'})
    page = client.get(base).text
    assert 'Fresh draft answer</textarea>' in page
    assert 'Previous answer versions (1)' in page
    assert 'Reviewed older answer' in page
    assert 'Fresh cover letter draft</textarea>' in page
    assert client.post(base + '/documents', data={'document_type': 'application_resume', 'text_content': 'Do not generate'}).status_code == 422
    assert client.post(base + '/bundles').status_code == 200
    bundle = services.applications.list_bundles(application['id'])[0]
    download = client.get(base + f"/bundles/{bundle['id']}")
    assert download.status_code == 200
    assert bundle['created_at'][:10].replace('-', '') in download.headers['content-disposition']
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        assert b'Fresh draft answer' in archive.read('answers.txt')
        assert b'[Draft]' in archive.read('answers.txt')
        assert archive.read('original-resume.txt') == b'I build Python tools.'


def test_tracker_rejects_cross_application_mutations_and_missing_records(client, services):
    first = services.applications.create('One', 'First')
    second = services.applications.create('Two', 'Second')
    question = services.questions.add(first['id'], 'Question')
    document = services.documents.save('cover_letter', 'Letter', first['id'], 'Text')
    bank = services.questions.save_bank_entry('Question', 'Bank answer')
    base = f"/applications/{second['id']}"
    assert client.post(base + f"/questions/{question['id']}/answers", data={'content': 'Wrong'}).status_code == 404
    assert client.post(base + f"/questions/{question['id']}/bank/{bank['id']}").status_code == 404
    assert client.post(base + f"/questions/{question['id']}/bank", data={'bank_entry_id': bank['id']}).status_code == 404
    assert client.post(base + f"/documents/{document['id']}/approve").status_code == 404
    assert client.get(base + f"/documents/{document['id']}").status_code == 404
    assert not services.questions.list(first['id'])[0]['answers']
    assert not services.documents.get(document['id'])['approved']
    assert client.get('/applications/999999').status_code == 404
    assert client.post('/applications/999999/active', data={'active': 'true'}).status_code == 404
    assert client.post('/applications/999999/bundles').status_code == 404
    assert client.get(base + '/bundles/999999').status_code == 404


def test_standard_answers_can_be_cleared_without_erasing_omitted_fields(client, services):
    from app.services.standard_questions import STANDARD_QUESTIONS
    first, second = STANDARD_QUESTIONS[:2]
    one = services.questions.save_bank_entry(first['canonical_question'], 'First answer')
    services.questions.save_bank_entry(second['canonical_question'], 'Keep this answer')
    response = client.post('/profile/standard-answers', data={f"answer_{first['key']}": ''})
    assert response.status_code == 200
    cleared = services.questions.get_bank_entry_by_question(first['canonical_question'])
    assert cleared['answer'] == '' and not cleared['approved']
    assert services.questions.get_bank_entry_by_question(second['canonical_question'])['answer'] == 'Keep this answer'
    assert client.get('/profile/resume/99999').status_code == 404
    assert client.post('/profile/resume').status_code == 422


def test_save_all_answers_saves_every_edit_in_one_submit(client, services):
    application = services.applications.create('Example', 'Engineer')
    first = services.questions.add(application['id'], 'Why us?')
    second = services.questions.add(application['id'], 'Why you?')
    third = services.questions.add(application['id'], 'Anything else?')
    services.questions.save_answer(second['id'], 'Line one\nLine two', 'agent')
    base = f"/applications/{application['id']}"
    page = client.get(base).text
    assert page.count('Save all answers') == 1
    assert 'Save answer version' not in page
    response = client.post(base + '/answers', data={
        f"content-{first['id']}": 'Because of the mission.',
        f"content-{second['id']}": 'Line one\r\nLine two',
        f"approved-{second['id']}": 'on',
        f"content-{third['id']}": '',
    })
    assert response.status_code == 200
    questions = {q['id']: q for q in services.questions.list(application['id'])}
    first_answers = questions[first['id']]['answers']
    assert [a['content'] for a in first_answers] == ['Because of the mission.']
    assert not first_answers[0]['approved']
    second_answers = questions[second['id']]['answers']
    assert len(second_answers) == 1 and second_answers[0]['approved']
    assert questions[third['id']]['answers'] == []
    assert client.post('/applications/999/answers', data={}).status_code == 404
