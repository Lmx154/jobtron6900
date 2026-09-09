"""Datetime normalisation.

Jobtron has historically written three incompatible shapes into TEXT columns:

    2026-08-29T10:00           naive, from an <input type="datetime-local">  -> LOCAL
    2026-08-31T02:09:48-07:00  offset-aware, from datetime.now().astimezone() -> as given
    2026-08-31 02:09:48        from SQLite CURRENT_TIMESTAMP                  -> UTC

A calendar cannot bucket by day until all three resolve to one instant, so every
read goes through `parse_stored` and every write through `to_utc`.
"""

from __future__ import annotations

from datetime import date, datetime, timezone, timedelta


def local_timezone() -> timezone:
    """The machine's current UTC offset, as a fixed zone."""
    offset = datetime.now().astimezone().utcoffset() or timedelta(0)
    return timezone(offset)


def parse_stored(value: str) -> tuple[datetime | None, bool]:
    """Return (aware datetime in UTC, is_all_day) for any legacy shape.

    Returns (None, False) when the value cannot be understood, so a damaged row
    is skipped rather than crashing the calendar.
    """
    text = (value or "").strip()
    if not text:
        return None, False

    # Date only -> an all-day event at local midnight.
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        try:
            day = date.fromisoformat(text)
        except ValueError:
            return None, False
        return datetime(day.year, day.month, day.day, tzinfo=local_timezone()).astimezone(timezone.utc), True

    # SQLite CURRENT_TIMESTAMP is space-separated and always UTC.
    space_separated = " " in text and "T" not in text
    normalized = text.replace(" ", "T", 1) if space_separated else text
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None, False

    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc), False
    # Naive: UTC if it came from SQLite, local if it came from the browser.
    assumed = timezone.utc if space_separated else local_timezone()
    return parsed.replace(tzinfo=assumed).astimezone(timezone.utc), False


def to_utc(value: str | datetime) -> str:
    """Canonical storage form: an ISO-8601 instant in UTC, seconds precision."""
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=local_timezone())
        return moment.astimezone(timezone.utc).isoformat(timespec="seconds")
    parsed, _ = parse_stored(value)
    if parsed is None:
        raise ValueError(f"Unrecognised datetime: {value!r}")
    return parsed.isoformat(timespec="seconds")


def to_local(utc_value: str) -> datetime | None:
    """Render a stored UTC instant back in the machine's local zone."""
    parsed, _ = parse_stored(utc_value)
    return parsed.astimezone(local_timezone()) if parsed else None


def local_day(utc_value: str) -> str:
    """The local calendar day a stored instant falls on, as YYYY-MM-DD.

    This is the grouping key for the month grid: an event at 23:00 local on the
    31st must land in the 31st's cell, not the 1st's, even though it is stored
    as the next day in UTC.
    """
    moment = to_local(utc_value)
    return moment.date().isoformat() if moment else ""


def today_local() -> date:
    return datetime.now(local_timezone()).date()
