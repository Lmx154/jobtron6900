"""Phase 3: fetching a posting, and failing honestly when a site won't cooperate."""

import pytest

from app.services.posting import _TextExtractor, detect_questions, fetch_posting, is_fetchable


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
