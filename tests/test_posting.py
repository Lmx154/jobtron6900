"""Phase 3: fetching a posting, and failing honestly when a site won't cooperate."""

from pathlib import Path

import pytest

from app.services.posting import MAX_BYTES, _TextExtractor, detect_questions, fetch_posting, is_fetchable

FIXTURES = Path(__file__).parent / "fixtures"


def test_private_and_non_http_targets_are_refused():
    for url in [
        "http://127.0.0.1:8765/applications",
        "http://localhost/admin",
        "file:///etc/passwd",
        "ftp://example.com/job",
        "http://169.254.169.254/latest/meta-data/",
    ]:
        allowed, reason = is_fetchable(url)
        assert allowed is False, url
        assert reason

    result = fetch_posting("http://127.0.0.1:8765/x")
    assert result["status"] == "error"
    assert "private address" in result["reason"]


def test_html_is_reduced_to_text_and_scripts_are_dropped():
    parser = _TextExtractor()
    parser.feed(
        "<html><head><style>.a{color:red}</style></head><body>"
        "<script>var secret='nope'</script>"
        "<h1>Guidance Engineer</h1><p>Design GNC systems.</p>"
        "<label>Are you authorized to work in the US?</label>"
        "<input aria-label='Years of experience'>"
        "</body></html>"
    )
    text = parser.text()
    assert "Guidance Engineer" in text
    assert "Design GNC systems." in text
    assert "nope" not in text and "color:red" not in text
    assert "Are you authorized to work in the US?" in parser.labels
    assert "Years of experience" in parser.labels


def test_question_detection_is_deterministic_and_deduplicated():
    text = "Why do you want to work here?\nShort\nDesign GNC systems.\nWhy do you want to work here?"
    found = detect_questions(text, ["Are you authorized to work in the US?", "Short"])
    assert "Why do you want to work here?" in found
    assert found.count("Why do you want to work here?") == 1, "duplicates collapse"
    assert "Are you authorized to work in the US?" in found
    assert "Short" not in found, "fragments below the length floor are ignored"
    assert "Design GNC systems." not in found, "a statement is not a question"


def test_ats_markers_are_recognised():
    parser = _TextExtractor()
    parser.feed('<div data-ph-at-id="job-description">Boeing</div>')
    assert "data-ph-at-id" in parser.ats_markers


class _Response:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


def _stub_client(monkeypatch, response):
    import httpx

    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, headers=None): return response

    monkeypatch.setattr(httpx, "Client", Client)


def test_a_javascript_rendered_posting_reports_empty_not_success(monkeypatch):
    """Boeing's board renders in JS. That must be a clean 'empty', not a crash."""
    _stub_client(monkeypatch, _Response(200, "<html><body><div id='root'></div></body></html>"))
    result = fetch_posting("https://jobs.boeing.com/job/1")
    assert result["status"] == "empty"
    assert "JavaScript" in result["reason"]
    assert result["detected_questions"] == []


def test_a_refusing_site_reports_blocked(monkeypatch):
    _stub_client(monkeypatch, _Response(403, "denied"))
    assert fetch_posting("https://jobs.boeing.com/job/1")["status"] == "blocked"


def test_an_interstitial_reports_blocked(monkeypatch):
    _stub_client(monkeypatch, _Response(200, "<html><body>" + "Please enable JavaScript to continue. " * 30 + "</body></html>"))
    result = fetch_posting("https://jobs.boeing.com/job/1")
    assert result["status"] == "blocked"
    assert "interstitial" in result["reason"]


def test_a_readable_posting_returns_text_and_questions(monkeypatch):
    body = (
        "<html><body><h1>Guidance Engineer</h1><p>"
        + "Design and validate guidance, navigation and control systems. " * 12
        + "</p><label>Why do you want to work at Boeing?</label></body></html>"
    )
    _stub_client(monkeypatch, _Response(200, body))
    result = fetch_posting("https://jobs.boeing.com/job/1")
    assert result["status"] == "ok"
    assert "Guidance Engineer" in result["text"]
    assert "Why do you want to work at Boeing?" in result["detected_questions"]


def test_a_network_failure_is_reported_not_raised(monkeypatch):
    import httpx

    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, headers=None): raise httpx.ConnectError("no route")

    monkeypatch.setattr(httpx, "Client", Client)
    result = fetch_posting("https://jobs.boeing.com/job/1")
    assert result["status"] == "error"
    assert "could not be retrieved" in result["reason"]


# Phase 5 · D7: page capture through the agent's own browser, never the server's.


def test_detect_from_text_on_the_fixture_finds_the_same_questions_detect_questions_finds(services):
    """The saved Workday-like fixture never touches the network."""
    html = (FIXTURES / "workday_sample.html").read_text()
    parser = _TextExtractor()
    parser.feed(html)
    expected = detect_questions(parser.text(), parser.labels)
    assert len(expected) == 3, "fixture should carry three deduplicated question-like lines"

    app = services.applications.create("Boeing", "GNC Engineer", url="https://jobs.boeing.com/job/1")
    result = services.questions.detect_from_text(app["id"], html, "browser")

    assert result["detected_questions"] == expected
    assert result["count"] == 3
    assert result["extraction_status"] == "ok"
    assert services.applications.get(app["id"])["questions_extraction_status"] == "ok"
    assert services.applications.get(app["id"])["questions_extraction_note"] == "browser"


def test_detect_from_text_accepts_plain_text_when_there_are_no_html_tags(services):
    app = services.applications.create("Boeing", "GNC Engineer")
    text = (
        "Design and validate guidance, navigation and control systems.\n"
        "Why do you want to work here?\n"
        "Are you authorized to work in the US?\n"
    )
    result = services.questions.detect_from_text(app["id"], text, "paste")
    assert result["detected_questions"] == [
        "Why do you want to work here?",
        "Are you authorized to work in the US?",
    ]
    assert result["count"] == 2
    assert result["extraction_status"] == "ok"


def test_detect_from_text_with_empty_text_records_nothing(services):
    app = services.applications.create("Boeing", "GNC Engineer", url="https://jobs.boeing.com/job/2")
    services.applications.record_question_extraction(app["id"], "blocked", "The site refused the request.")

    result = services.questions.detect_from_text(app["id"], "   \n\t  ", "browser")

    assert result == {
        "detected_questions": [],
        "count": 0,
        "stored_job_description": False,
        "extraction_status": "blocked",
    }
    refreshed = services.applications.get(app["id"])
    assert refreshed["questions_extraction_status"] == "blocked", "an empty paste must not clear the status"
    assert refreshed["questions_extraction_note"] == "The site refused the request."


def test_detect_from_text_never_overwrites_a_non_empty_job_description(services):
    app = services.applications.create("Boeing", "GNC Engineer", job_description="Original description.")

    result = services.questions.detect_from_text(app["id"], "Some other page text. Why do you want this job?", "paste")

    assert result["stored_job_description"] is False
    assert services.applications.get(app["id"])["job_description"] == "Original description."


def test_detect_from_text_stores_the_page_as_job_description_when_none_is_saved(services):
    app = services.applications.create("Boeing", "GNC Engineer")

    result = services.questions.detect_from_text(app["id"], "Design GNC systems. Why do you want this job?", "browser")

    assert result["stored_job_description"] is True
    assert services.applications.get(app["id"])["job_description"] == "Design GNC systems. Why do you want this job?"


def test_detect_from_text_truncates_oversized_text_instead_of_rejecting_it(services):
    app = services.applications.create("Boeing", "GNC Engineer")
    oversized = ("A" * (MAX_BYTES + 1000)) + " Why do you want to work here?"

    result = services.questions.detect_from_text(app["id"], oversized, "paste")

    assert result["extraction_status"] == "ok", "the call succeeds; it does not reject oversized text"
    stored = services.applications.get(app["id"])["job_description"]
    assert len(stored) == MAX_BYTES
    assert result["detected_questions"] == [], "the trailing question sits past the cap"
