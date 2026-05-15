"""Pipeline domain models and the LangGraph state object."""

from __future__ import annotations

from typing import TypedDict

from pydantic import BaseModel

from tender_agent.prozorro.models import Tender

# Automotive-chemistry categories the agent recognises, with their Ukrainian
# labels used in the email report. "other" covers automotive chemistry that
# does not fit a specific bucket.
CATEGORY_LABELS: dict[str, str] = {
    "coolant": "Охолоджувальні рідини / антифризи",
    "brake_fluid": "Гальмівні рідини",
    "washer_fluid": "Рідини для омивача скла",
    "motor_oil": "Моторні оливи",
    "industrial_oil": "Індустріальні оливи",
    "base_oil": "Базові оливи",
    "other": "Інша автохімія",
}
CATEGORIES: list[str] = list(CATEGORY_LABELS)


class ClassifiedTender(BaseModel):
    """A tender after the LLM relevance decision."""

    tender: Tender
    relevant: bool
    category: str
    reason: str
    summary: str = ""

    @property
    def category_label(self) -> str:
        """Ukrainian label for the assigned category."""
        return CATEGORY_LABELS.get(self.category, CATEGORY_LABELS["other"])


class PipelineState(TypedDict, total=False):
    """State threaded through the LangGraph pipeline.

    Each node returns a partial dict that is merged into this state.
    """

    tenders: list[Tender]  # crawl: actionable tenders with full detail
    prefiltered: list[Tender]  # prefilter: passed the broad CPV/keyword net
    classified: list[ClassifiedTender]  # classify: LLM-confirmed relevant
    new_tenders: list[ClassifiedTender]  # dedupe: not previously reported
    report_subject: str  # render: email subject
    report_html: str  # render: full HTML report (saved to disk + used for PDF)
    report_summary: str  # render: plain-text email body
    report_pdf: bytes | None  # render: PDF bytes of the full report
    report_path: str | None  # render: saved report file path
    email_sent: bool  # notify: whether an email was dispatched
    new_offset: str | None  # crawl: feed cursor to persist
    counters: dict[str, int]  # per-stage counts for logging/summary
