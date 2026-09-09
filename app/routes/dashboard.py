from datetime import date, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Request

from app.services.palette import STATUS_COLORS
from app.services.timeutil import today_local
from .helpers import services, templates

router=APIRouter()


def _parse_month(value: str | None, today: date) -> tuple[int, int]:
    """Return (year, month) for `?month=YYYY-MM`, falling back to today's month.

    Any malformed or out-of-range value is treated the same as no value at
    all: the dashboard must never 500 on a hand-edited query string.
    """
    if value:
        head, sep, tail = value.partition("-")
        if sep and head.isdigit() and tail.isdigit():
            year, month = int(head), int(tail)
            if 1 <= month <= 12 and 1 <= year <= 9999:
                return year, month
    return today.year, today.month


def _shift_month(year: int, month: int, delta: int) -> str:
    index = year * 12 + (month - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


@router.get("/")
def dashboard(request: Request, month: str | None = None, status: str | None = None, tag: str | None = None, q: str | None = None):
    svc=services(request); today=today_local()
    year, mon = _parse_month(month, today)
    if status not in svc.applications.STATUSES: status = None
    # Pre-encoded so a status containing a space ("Ready to Apply") produces a
    # valid href; the template escapes the ampersand.
    status_suffix = f"&status={quote(status)}" if status else ""
    buckets = svc.events.month(year, mon, status)

    first_of_month = date(year, mon, 1)
    grid_start = first_of_month - timedelta(days=first_of_month.weekday())
    weeks = []
    cursor = grid_start
    for _ in range(6):
        week = []
        for _ in range(7):
            iso = cursor.isoformat()
            week.append({
                "date": cursor,
                "iso": iso,
                "in_month": cursor.month == mon,
                "is_today": cursor == today,
                "events": buckets.get(iso, []),
            })
            cursor += timedelta(days=1)
        weeks.append(week)

    all_applications = svc.applications.list()
    counts = {s: sum(a["status"] == s for a in all_applications) for s in svc.applications.STATUSES}
    tag_counts: dict[str, int] = {}
    for application in all_applications:
        for t in (application["tags"] or "").split(","):
            if t: tag_counts[t] = tag_counts.get(t, 0) + 1
    tags = sorted(tag_counts, key=lambda t: (-tag_counts[t], t))

    applications = svc.applications.list(tag=tag, query=q)

    return templates(request).TemplateResponse(request, "dashboard.html", {
        "weeks": weeks,
        "month_label": first_of_month.strftime("%B %Y"),
        "current_month": f"{year:04d}-{mon:02d}",
        "prev_month": _shift_month(year, mon, -1),
        "next_month": _shift_month(year, mon, 1),
        "this_month": f"{today.year:04d}-{today.month:02d}",
        "status_suffix": status_suffix,
        "upcoming": svc.events.upcoming(10),
        "attention": svc.applications.attention(),
        "statuses": svc.applications.STATUSES,
        "status_colors": STATUS_COLORS,
        "selected_status": status,
        "selected_tag": tag,
        "query": q or "",
        "tags": tags,
        "tag_counts": tag_counts,
        "applications": applications[:8],
        "counts": counts,
    })
