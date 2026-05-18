"""Pipeline domain models and the LangGraph state object."""

from __future__ import annotations

from typing import TypedDict

from pydantic import BaseModel

from tender_agent.prozorro.models import Tender


class ClassifiedTender(BaseModel):
    """A tender after the LLM relevance decision."""

    tender: Tender
    relevant: bool
    category: str
    reason: str
    summary: str = ""
    _category_labels: dict[str, str] = {}

    def category_label_for(self, category_labels: dict[str, str]) -> str:
        """Ukrainian label for the assigned category, given a label map."""
        return category_labels.get(self.category, category_labels.get("other", self.category))


class PipelineState(TypedDict, total=False):
    """State threaded through the LangGraph pipeline.

    Each node returns a partial dict that is merged into this state.
    """

    tenders: list[Tender]  # crawl: actionable tenders with full detail
    prefiltered: list[Tender]  # prefilter: passed the broad CPV/keyword net
    classified: list[ClassifiedTender]  # classify: LLM-confirmed relevant
    new_tenders: list[ClassifiedTender]  # dedupe: not previously reported
    deadline_reminders: list[dict[str, str]]  # deadline_check: approaching tenderPeriod.endDate
    report_subject: str  # render: email subject
    report_html: str  # render: full HTML report (saved to disk + used for PDF)
    report_summary: str  # render: plain-text email body
    report_pdf: bytes | None  # render: PDF bytes of the full report
    report_path: str | None  # render: saved report file path
    email_sent: bool  # notify: whether an email was dispatched
    new_offset: str | None  # crawl: feed cursor to persist
    counters: dict[str, int]  # per-stage counts for logging/summary
    errors: list[dict[str, str]]  # errors accumulated during the run (kind + message)
