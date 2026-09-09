"""Server-side job-posting retrieval.

Jobtron fetches, not the client, so the workflow behaves identically in Codex,
OpenCode, and Claude Code regardless of which one happens to have web tools.

Every failure mode returns a status rather than raising: the workflow engine
turns a failed fetch into a step that asks the user to paste or screenshot the
posting, which is a normal outcome, not an error.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urlparse

MAX_BYTES = 2_000_000
TIMEOUT_SECONDS = 10.0
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)

# Anchors used by the major applicant tracking systems.
ATS_HINTS = (
    "data-automation-id",   # Workday
    "data-ph-at-id",        # Phenom People (Boeing)
    "application-form",     # Lever
    "application_question", # Greenhouse
)

_BLOCKED_MARKERS = (
    "enable javascript", "captcha", "access denied", "are you a robot",
    "cf-browser-verification", "request unsuccessful",
)


class _TextExtractor(HTMLParser):
    """Reduce HTML to text, and collect anything that looks like a form label."""

    SKIP = {"script", "style", "noscript", "svg", "head"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.labels: list[str] = []
        self._skip_depth = 0
        self._label_depth = 0
        self.ats_markers: set[str] = set()

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1
        if tag in {"label", "legend"}:
            self._label_depth += 1
        for name, value in attrs:
            if name in ATS_HINTS or name.startswith("data-automation-id"):
                self.ats_markers.add(name)
            if name == "aria-label" and value and value.strip():
                self.labels.append(value.strip())

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
        if tag in {"label", "legend"} and self._label_depth:
            self._label_depth -= 1
        if tag in {"p", "div", "li", "br", "tr", "h1", "h2", "h3", "h4"}:
            self.chunks.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        text = data.strip()
        if not text:
            return
        self.chunks.append(text + " ")
        if self._label_depth:
            self.labels.append(text)

    def text(self) -> str:
        joined = "".join(self.chunks)
        joined = re.sub(r"[ \t]+", " ", joined)
        return re.sub(r"\n\s*\n+", "\n\n", joined).strip()


def is_fetchable(url: str) -> tuple[bool, str]:
    """Refuse anything that is not a public http(s) page.

    Jobtron runs on the user's machine with access to their private network, and
    the URL reaches it through a model. Restricting the scheme and refusing
    private address space keeps a prompt-injected link from turning the fetcher
    into an internal scanner.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        return False, "Only http and https job postings can be retrieved."
    if not parsed.hostname:
        return False, "That link has no host name."
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        return False, f"{parsed.hostname} could not be resolved."
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            return False, "That link points at a private address, so Jobtron will not fetch it."
    return True, ""


def detect_questions(text: str, labels: list[str]) -> list[str]:
    """Deterministic candidates only. The model confirms; it does not detect."""
    found: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.endswith("?") and 10 <= len(stripped) <= 300:
            found.append(stripped)
    for label in labels:
        stripped = label.strip()
        if 10 <= len(stripped) <= 300 and stripped not in found:
            found.append(stripped)
    seen: set[str] = set()
    unique: list[str] = []
    for item in found:
        key = re.sub(r"\W+", " ", item.lower()).strip()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique[:40]


def fetch_posting(url: str, timeout: float = TIMEOUT_SECONDS) -> dict:
    """Retrieve a posting. Never raises."""
    result = {"status": "error", "url": url, "text": "", "detected_questions": [], "reason": ""}

    allowed, refusal = is_fetchable(url)
    if not allowed:
        result["reason"] = refusal
        return result

    try:
        import httpx

        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            response = client.get(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"})
    except Exception as exc:
        result["reason"] = f"The posting could not be retrieved ({type(exc).__name__})."
        return result

    if response.status_code in {401, 403, 429} or response.status_code >= 500:
        result["status"] = "blocked"
        result["reason"] = f"The site refused the request (HTTP {response.status_code})."
        return result
    if response.status_code >= 400:
        result["reason"] = f"The posting returned HTTP {response.status_code}."
        return result

    body = response.text[:MAX_BYTES]
    parser = _TextExtractor()
    try:
        parser.feed(body)
    except Exception:
        pass
    text = parser.text()
    lowered = text[:4000].lower()

    if any(marker in lowered for marker in _BLOCKED_MARKERS):
        result["status"] = "blocked"
        result["text"] = text
        result["reason"] = "The site served an interstitial instead of the posting."
        return result
    if len(text) < 400:
        result["status"] = "empty"
        result["text"] = text
        result["reason"] = (
            "The page rendered its content with JavaScript, so there was nothing to read."
        )
        return result

    result["status"] = "ok"
    result["text"] = text
    result["detected_questions"] = detect_questions(text, parser.labels)
    result["ats_markers"] = sorted(parser.ats_markers)
    result["reason"] = f"Retrieved {len(text)} characters."
    return result
