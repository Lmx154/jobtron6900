"""Single source of truth for status colour.

Colour encodes application status, because status is the dimension being
tracked. The dashboard calendar and the MCP App both read this map, so a dot
means the same thing wherever it is drawn.
"""

from __future__ import annotations

STATUS_COLORS: dict[str, str] = {
    "Draft": "#8a8f87",
    "Ready to Apply": "#1f5540",
    "Applied": "#2f6fb0",
    "Assessment": "#8a5713",
    "Interviewing": "#6a3fa0",
    "Offer": "#1c7a4f",
    "Rejected": "#8e3b2f",
    "Withdrawn": "#5d6b62",
    "Closed": "#3d4a42",
}

FALLBACK_COLOR = "#5d6b62"


def status_color(status: str) -> str:
    return STATUS_COLORS.get(status, FALLBACK_COLOR)
