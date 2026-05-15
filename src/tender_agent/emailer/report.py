"""Report rendering: groups classified tenders and renders the HTML report."""

from __future__ import annotations

from datetime import datetime

import jinja2
from pydantic import BaseModel

from tender_agent.logging import get_logger
from tender_agent.state import CATEGORY_LABELS, ClassifiedTender

log = get_logger(__name__)


def _format_end_date(raw: str) -> str:
    """Parse an ISO-8601 date string and return a human-readable Ukrainian date.

    Falls back to the raw string if parsing fails.
    """
    try:
        dt = datetime.fromisoformat(raw)
        return dt.strftime("%d.%m.%Y %H:%M")
    except (ValueError, TypeError):
        return raw


_loader = jinja2.PackageLoader("tender_agent.emailer", "templates")
_env = jinja2.Environment(loader=_loader, autoescape=True)
_env.filters["format_end_date"] = _format_end_date


class RenderedReport(BaseModel):
    """The rendered email subject line and HTML body."""

    subject: str
    html: str


def render_report(items: list[ClassifiedTender], generated_at: datetime) -> RenderedReport:
    """Group *items* by category and render the HTML report.

    Args:
        items: Classified (relevant) tenders, each with a populated ``summary``.
        generated_at: Timestamp for the report header and subject line.

    Returns:
        A :class:`RenderedReport` with the email subject and HTML body.
    """
    date_str = generated_at.strftime("%d.%m.%Y")
    subject = f"Тендери з автохімії — {len(items)} нових ({date_str})"

    # Group by category in canonical CATEGORY_LABELS order.
    grouped: dict[str, list[ClassifiedTender]] = {key: [] for key in CATEGORY_LABELS}
    for item in items:
        bucket = item.category if item.category in grouped else "other"
        grouped[bucket].append(item)

    sections = [
        {"label": CATEGORY_LABELS[key], "tenders": tenders}
        for key, tenders in grouped.items()
        if tenders
    ]

    template = _env.get_template("report.html.j2")
    html = template.render(
        sections=sections,
        total=len(items),
        generated_at=generated_at,
        date_str=date_str,
    )

    log.info("report rendered", total_tenders=len(items), sections=len(sections))
    return RenderedReport(subject=subject, html=html)
