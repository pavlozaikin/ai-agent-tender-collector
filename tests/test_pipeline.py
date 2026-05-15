"""End-to-end integration tests for the LangGraph pipeline.

PROZORRO HTTP, the LLM, and SMTP are all replaced with fakes; the filter
config and SQLite storage are real.
"""

from __future__ import annotations

from typing import Any

import pytest

from tender_agent import pipeline as pipeline_module
from tender_agent.llm import TenderRelevance
from tender_agent.pipeline import PipelineContext, run_pipeline
from tender_agent.prozorro.client import CrawlResult
from tender_agent.prozorro.models import Tender
from tender_agent.settings import Settings
from tender_agent.storage import Storage
from tests.conftest import make_tender


class FakeLLM:
    """Marks tenders relevant when their title mentions antifreeze."""

    async def classify(self, tender: Tender) -> TenderRelevance:
        relevant = "антифриз" in (tender.title or "").lower()
        return TenderRelevance(
            relevant=relevant,
            category="coolant" if relevant else "other",
            reason="тест",
        )

    async def summarize(self, tender: Tender) -> str:
        return f"Опис: {tender.title}"


class FakeEmailSender:
    """Captures sent emails instead of contacting an SMTP server."""

    sent: list[tuple[str, str, Any]] = []

    def __init__(self, _settings: Settings) -> None:
        pass

    def send(
        self,
        subject: str,
        body_text: str,
        recipients: Any,
        pdf_attachment: bytes | None = None,
        pdf_filename: str = "report.pdf",
    ) -> None:
        FakeEmailSender.sent.append((subject, body_text, recipients))


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch, tenders: list[Tender], next_offset: str = "cursor-final"
) -> None:
    class FakeClient:
        def __init__(self, _settings: Settings) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_exc: object) -> bool:
            return False

        async def crawl(self, _offset: str | None) -> CrawlResult:
            return CrawlResult(tenders=tenders, next_offset=next_offset)

    monkeypatch.setattr(pipeline_module, "ProzorroClient", FakeClient)
    monkeypatch.setattr(pipeline_module, "EmailSender", FakeEmailSender)
    FakeEmailSender.sent = []


def _context(settings: Settings, storage: Storage, *, dry_run: bool = False) -> PipelineContext:
    return PipelineContext(
        settings=settings,
        storage=storage,
        llm=FakeLLM(),
        dry_run=dry_run,  # type: ignore[arg-type]
    )


def _sample_tenders() -> list[Tender]:
    # All three pass the broad CPV prefilter (cpv 0921...); only the two
    # antifreeze tenders should survive the LLM relevance check.
    return [
        make_tender(tender_id="t1", public_id="UA-t1", title="Закупівля антифризу"),
        make_tender(tender_id="t2", public_id="UA-t2", title="Антифриз концентрат"),
        make_tender(tender_id="t3", public_id="UA-t3", title="Закупівля картоплі"),
    ]


async def test_pipeline_reports_new_relevant_tenders(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, storage: Storage
) -> None:
    settings.recipients_path.write_text("to:\n  - boss@example.com\n", encoding="utf-8")
    _install_fakes(monkeypatch, _sample_tenders())

    final = await run_pipeline(_context(settings, storage))

    counters = final["counters"]
    assert counters["crawled"] == 3
    assert counters["prefiltered"] == 3  # all pass the broad net
    assert counters["relevant"] == 2  # LLM is the final arbiter
    assert counters["new"] == 2

    assert final["email_sent"] is True
    assert len(FakeEmailSender.sent) == 1
    assert "2 нових тендерів" in FakeEmailSender.sent[0][0]

    assert storage.get_offset() == "cursor-final"
    assert storage.filter_unseen(["t1", "t2"]) == set()  # both recorded


async def test_pipeline_deduplicates_on_second_run(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, storage: Storage
) -> None:
    settings.recipients_path.write_text("to:\n  - boss@example.com\n", encoding="utf-8")

    _install_fakes(monkeypatch, _sample_tenders())
    await run_pipeline(_context(settings, storage))

    # Second run sees the same tenders — nothing new, no email.
    _install_fakes(monkeypatch, _sample_tenders())
    final = await run_pipeline(_context(settings, storage))

    assert final["counters"]["new"] == 0
    assert FakeEmailSender.sent == []
    assert not final.get("email_sent", False)


async def test_dry_run_sends_nothing_and_persists_nothing(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, storage: Storage
) -> None:
    settings.recipients_path.write_text("to:\n  - boss@example.com\n", encoding="utf-8")
    _install_fakes(monkeypatch, _sample_tenders())

    final = await run_pipeline(_context(settings, storage, dry_run=True))

    assert final["counters"]["new"] == 2
    assert FakeEmailSender.sent == []  # no email
    assert not final.get("email_sent", False)
    assert storage.get_offset() is None  # cursor not advanced
    assert storage.filter_unseen(["t1", "t2"]) == {"t1", "t2"}  # nothing recorded
    assert final["report_path"]  # report still rendered to disk
