"""The LangGraph pipeline: crawl -> prefilter -> classify -> dedupe ->
(render -> notify) -> persist.

One graph run is one report cycle.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from langgraph.graph import END, StateGraph

from tender_agent.emailer.pdf import render_pdf
from tender_agent.emailer.recipients import load_recipients
from tender_agent.emailer.report import render_report
from tender_agent.emailer.sender import EmailSender
from tender_agent.filters import load_filters
from tender_agent.llm import LLMClient
from tender_agent.logging import get_logger
from tender_agent.prozorro.client import ProzorroClient
from tender_agent.prozorro.models import Tender
from tender_agent.settings import Settings
from tender_agent.state import ClassifiedTender, PipelineState
from tender_agent.storage import SeenRecord, Storage

_log = get_logger(__name__)

_CLASSIFY_CONCURRENCY = 8
_SUMMARY_CONCURRENCY = 6


@dataclass(slots=True)
class PipelineContext:
    """Dependencies shared by every pipeline node."""

    settings: Settings
    storage: Storage
    llm: LLMClient
    dry_run: bool = False


def _merge_counters(state: PipelineState, **counts: int) -> dict[str, int]:
    """Return the running counters dict updated with new values."""
    merged = dict(state.get("counters", {}))
    merged.update(counts)
    return merged


# ── nodes ───────────────────────────────────────────────────────────────────


async def _crawl(ctx: PipelineContext, state: PipelineState) -> dict[str, object]:
    """Incrementally crawl PROZORRO from the saved feed cursor."""
    offset = ctx.storage.get_offset()
    async with ProzorroClient(ctx.settings) as client:
        result = await client.crawl(offset)
    _log.info("crawl_node", tenders=len(result.tenders), first_run=offset is None)
    return {
        "tenders": result.tenders,
        "new_offset": result.next_offset,
        "counters": _merge_counters(state, crawled=len(result.tenders)),
    }


async def _prefilter(ctx: PipelineContext, state: PipelineState) -> dict[str, object]:
    """Apply the broad CPV/keyword net before any LLM call."""
    filters = load_filters(ctx.settings.filters_path)
    tenders = state.get("tenders", [])
    kept = [t for t in tenders if filters.matches(t)]
    _log.info("prefilter_node", kept=len(kept), total=len(tenders))
    return {"prefiltered": kept, "counters": _merge_counters(state, prefiltered=len(kept))}


async def _classify(ctx: PipelineContext, state: PipelineState) -> dict[str, object]:
    """Let the LLM make the final relevance decision on each tender."""
    prefiltered = state.get("prefiltered", [])
    sem = asyncio.Semaphore(_CLASSIFY_CONCURRENCY)

    async def classify_one(tender: Tender) -> ClassifiedTender:
        async with sem:
            verdict = await ctx.llm.classify(tender)
        return ClassifiedTender(
            tender=tender,
            relevant=verdict.relevant,
            category=verdict.category,
            reason=verdict.reason,
        )

    results = await asyncio.gather(*(classify_one(t) for t in prefiltered))
    relevant = [c for c in results if c.relevant]
    _log.info("classify_node", relevant=len(relevant), considered=len(prefiltered))
    return {"classified": relevant, "counters": _merge_counters(state, relevant=len(relevant))}


async def _dedupe(ctx: PipelineContext, state: PipelineState) -> dict[str, object]:
    """Drop tenders that were already reported in a previous run."""
    classified = state.get("classified", [])
    unseen = ctx.storage.filter_unseen([c.tender.id for c in classified])
    new = [c for c in classified if c.tender.id in unseen]
    _log.info("dedupe_node", new=len(new), classified=len(classified))
    return {"new_tenders": new, "counters": _merge_counters(state, new=len(new))}


def _save_report(ctx: PipelineContext, html: str, pdf: bytes, generated_at: datetime) -> Path:
    """Persist the rendered HTML + PDF report to disk for the audit trail."""
    reports_dir = ctx.settings.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)
    stem = f"report-{generated_at:%Y%m%d-%H%M%S}"
    html_path = reports_dir / f"{stem}.html"
    html_path.write_text(html, encoding="utf-8")
    pdf_path = reports_dir / f"{stem}.pdf"
    pdf_path.write_bytes(pdf)
    return html_path


async def _render(ctx: PipelineContext, state: PipelineState) -> dict[str, object]:
    """Generate Ukrainian summaries, render the HTML report, and convert to PDF."""
    new = state.get("new_tenders", [])
    sem = asyncio.Semaphore(_SUMMARY_CONCURRENCY)

    async def summarize_one(item: ClassifiedTender) -> None:
        async with sem:
            item.summary = await ctx.llm.summarize(item.tender)

    await asyncio.gather(*(summarize_one(c) for c in new))
    generated_at = datetime.now(ZoneInfo(ctx.settings.timezone))
    report = render_report(new, generated_at)
    pdf_bytes = render_pdf(report.html)
    path = _save_report(ctx, report.html, pdf_bytes, generated_at)
    _log.info("render_node", tenders=len(new), report_path=str(path))
    return {
        "report_subject": report.subject,
        "report_html": report.html,
        "report_summary": report.summary,
        "report_pdf": pdf_bytes,
        "report_path": str(path),
    }


async def _notify(ctx: PipelineContext, state: PipelineState) -> dict[str, object]:
    """Email the report to the managed recipient list."""
    if ctx.dry_run:
        _log.info("notify_node_skipped", reason="dry_run", report_path=state.get("report_path"))
        return {"email_sent": False}
    generated_at = datetime.now(ZoneInfo(ctx.settings.timezone))
    date_str = generated_at.strftime("%d.%m.%Y")
    recipients = load_recipients(ctx.settings.recipients_path)
    sender = EmailSender(ctx.settings)
    sender.send(
        subject=state["report_subject"],
        body_text=state["report_summary"],
        recipients=recipients,
        pdf_attachment=state.get("report_pdf"),
        pdf_filename=f"tenders-{date_str}.pdf",
    )
    _log.info("notify_node_sent")
    return {"email_sent": True}


async def _persist(ctx: PipelineContext, state: PipelineState) -> dict[str, object]:
    """Record reported tenders and advance the feed cursor."""
    new = state.get("new_tenders", [])
    if ctx.dry_run:
        _log.info("persist_node_skipped", reason="dry_run", would_mark=len(new))
        return {}
    records = [
        SeenRecord(
            tender_id=c.tender.id,
            public_id=c.tender.public_id,
            category=c.category,
            status=c.tender.status or "",
        )
        for c in new
    ]
    ctx.storage.mark_reported(records)
    offset = state.get("new_offset")
    if offset:
        ctx.storage.set_offset(offset)
    _log.info("persist_node", marked=len(records), offset_saved=bool(offset))
    return {}


# ── graph ─────────────────────────────────────────────────────────────────


def build_graph(ctx: PipelineContext) -> Any:
    """Build and compile the LangGraph pipeline for the given context."""
    graph = StateGraph(PipelineState)
    graph.add_node("crawl", partial(_crawl, ctx))
    graph.add_node("prefilter", partial(_prefilter, ctx))
    graph.add_node("classify", partial(_classify, ctx))
    graph.add_node("dedupe", partial(_dedupe, ctx))
    graph.add_node("render", partial(_render, ctx))
    graph.add_node("notify", partial(_notify, ctx))
    graph.add_node("persist", partial(_persist, ctx))

    graph.set_entry_point("crawl")
    graph.add_edge("crawl", "prefilter")
    graph.add_edge("prefilter", "classify")
    graph.add_edge("classify", "dedupe")

    def route_after_dedupe(state: PipelineState) -> str:
        has_work = bool(state.get("new_tenders")) or ctx.settings.send_when_empty
        return "render" if has_work else "persist"

    graph.add_conditional_edges(
        "dedupe", route_after_dedupe, {"render": "render", "persist": "persist"}
    )
    graph.add_edge("render", "notify")
    graph.add_edge("notify", "persist")
    graph.add_edge("persist", END)
    return graph.compile()


async def run_pipeline(ctx: PipelineContext) -> PipelineState:
    """Execute one full report cycle and return the final pipeline state."""
    graph = build_graph(ctx)
    final = await graph.ainvoke({"counters": {}})
    return cast(PipelineState, final)
