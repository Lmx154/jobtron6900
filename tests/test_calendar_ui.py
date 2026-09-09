from fastapi.testclient import TestClient

from app.main import create_app
from app.services.timeutil import today_local


def test_dashboard_renders_with_no_events(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "Recent applications" in response.text
        assert "Upcoming" in response.text
        assert "No upcoming events." in response.text


def test_created_application_drafted_dot_appears_in_current_month(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/applications",
            data={"company_name": "Acme", "formal_title": "Engineer"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        page = client.get("/")
        assert page.status_code == 200
        assert "Drafted" in page.text
        assert "dot-wrap" in page.text
        today = today_local()
        assert f'data-iso="{today.isoformat()}"' in page.text


def test_month_navigation(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/?month=2026-03")
        assert response.status_code == 200
        assert "March 2026" in response.text
        assert 'href="/?month=2026-02"' in response.text
        assert 'href="/?month=2026-04"' in response.text


def test_malformed_month_falls_back_to_current_month(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        today = today_local()
        expected_label = today.strftime("%B %Y")
        for bogus in ("not-a-month", "2026-13", "2026", "0000-01", "2026-08-01"):
            response = client.get(f"/?month={bogus}")
            assert response.status_code == 200
            assert expected_label in response.text


def test_status_filter(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        client.post("/applications", data={"company_name": "Acme", "formal_title": "Engineer"})
        drafted = client.get("/?status=Draft")
        assert drafted.status_code == 200
        grid_html = drafted.text.split('<aside class="agenda">')[0]
        assert "Drafted" in grid_html
        offer = client.get("/?status=Offer")
        assert offer.status_code == 200
        grid_html_offer = offer.text.split('<aside class="agenda">')[0]
        assert "Drafted" not in grid_html_offer
        # An unknown status must not error; it is treated as no filter.
        ignored = client.get("/?status=NotAStatus")
        assert ignored.status_code == 200


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
