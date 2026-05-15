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
    """The rendered email subject line, HTML body, and plain-text summary."""

    subject: str
    html: str
    summary: str


def _time_greeting(hour: int) -> str:
    if hour < 12:
        return "ранку"
    if hour < 18:
        return "дня"
    return "вечора"


def _render_summary(
    sections: list[dict[str, object]],
    total: int,
    date_str: str,
    hour: int,
) -> str:
    greeting = _time_greeting(hour)
    lines = [
        f"Доброго {greeting}!",
        "",
        "ШІ-агент із пошуку потенційних тендерів опрацював щоденний моніторинг"
        f" майданчику Prozorro та виявив {total} нових тендерів з автохімії станом на {date_str}:",
    ]
    for section in sections:
        label = section["label"]
        tenders = section["tenders"]
        lines.append(f"  • {label} — {len(tenders)}")  # type: ignore[arg-type]
    lines += [
        "",
        "Детальний звіт із описами та аналітикою додається у вкладеному PDF-файлі.",
        "",
        "З повагою,",
        "ШІ-Тендерник",
    ]
    return "\n".join(lines)


def render_report(items: list[ClassifiedTender], generated_at: datetime) -> RenderedReport:
    """Group *items* by category and render the HTML report.

    Args:
        items: Classified (relevant) tenders, each with a populated ``summary``.
        generated_at: Timestamp for the report header and subject line.

    Returns:
        A :class:`RenderedReport` with the email subject, HTML body, and plain-text summary.
    """
    date_str = generated_at.strftime("%d.%m.%Y")
    subject = f"{len(items)} нових тендерів знайдено станом на {date_str}"

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

    summary = _render_summary(sections, len(items), date_str, generated_at.hour)

    log.info("report rendered", total_tenders=len(items), sections=len(sections))
    return RenderedReport(subject=subject, html=html, summary=summary)
