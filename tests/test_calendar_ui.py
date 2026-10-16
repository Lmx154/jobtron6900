from fastapi.testclient import TestClient

from app.main import create_app
from app.services.timeutil import today_local


def test_home_opens_empty_application_notebook(settings):
    with TestClient(create_app(settings)) as client:
        response = client.get('/')
        assert response.status_code == 200
        assert str(response.url).endswith('/applications')
        assert 'Your next application starts here' in response.text
        assert 'Upcoming' not in response.text


def test_created_application_appears_with_date_and_active_flag(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post('/applications', data={'company_name':'Acme','formal_title':'Engineer'}, follow_redirects=False)
        assert response.status_code == 303
        record = app.state.services.applications.list()[0]
        page = client.get('/')
        assert 'Engineer' in page.text and 'Acme' in page.text
        assert record['created_at'][:10] in page.text
        assert '>Active<' in page.text
        assert 'dot-wrap' not in page.text


def test_old_calendar_links_redirect_to_tracker(settings):
    with TestClient(create_app(settings)) as client:
        response = client.get('/?month=2026-03', follow_redirects=False)
        assert response.status_code == 303
        assert response.headers['location'] == '/applications'


def test_malformed_legacy_calendar_parameters_do_not_break_tracker(settings):
    with TestClient(create_app(settings)) as client:
        for bogus in ('not-a-month', '2026-13', '0000-01'):
            response = client.get(f'/?month={bogus}')
            assert response.status_code == 200
            assert 'Your notebook' in response.text


def test_active_filter_and_toggle(settings):
    app = create_app(settings)
    record = app.state.services.applications.create('Acme','Engineer')
    with TestClient(app) as client:
        assert 'Engineer' in client.get('/applications?active=active').text
        response = client.post(f"/applications/{record['id']}/active", data={'active':'false'}, follow_redirects=False)
        assert response.status_code == 303
        assert not app.state.services.applications.get(record['id'])['active']
        assert 'Engineer' not in client.get('/applications?active=active').text
        assert 'Engineer' in client.get('/applications?active=inactive').text
        assert client.get('/applications?active=invalid').status_code == 422


def test_posting_status_change_redirects_and_changes_status(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        created = client.post(
            "/applications",
            data={"company_name": "Acme", "formal_title": "Engineer"},
            follow_redirects=False,
        )
        application_id = int(created.headers["location"].rsplit("/", 1)[1])
        response = client.post(
            f"/applications/{application_id}/status",
            data={"status": "Applied", "next": "/?month=2026-08"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/?month=2026-08"
        assert app.state.services.applications.get(application_id)["status"] == "Applied"


def test_posting_unknown_status_returns_422(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        created = client.post(
            "/applications",
            data={"company_name": "Acme", "formal_title": "Engineer"},
            follow_redirects=False,
        )
        application_id = int(created.headers["location"].rsplit("/", 1)[1])
        response = client.post(
            f"/applications/{application_id}/status",
            data={"status": "Not A Real Status"},
            follow_redirects=False,
        )
        assert response.status_code == 422


def test_next_redirect_rejects_absolute_offsite_url(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        created = client.post(
            "/applications",
            data={"company_name": "Acme", "formal_title": "Engineer"},
            follow_redirects=False,
        )
        application_id = int(created.headers["location"].rsplit("/", 1)[1])
        response = client.post(
            f"/applications/{application_id}/status",
            data={"status": "Draft", "next": "https://evil.example/steal"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/"

        protocol_relative = client.post(
            f"/applications/{application_id}/status",
            data={"status": "Draft", "next": "//evil.example/steal"},
            follow_redirects=False,
        )
        assert protocol_relative.status_code == 303
        assert protocol_relative.headers["location"] == "/"
