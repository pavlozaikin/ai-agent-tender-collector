"""The LangGraph pipeline: crawl -> prefilter -> classify -> dedupe ->
(render -> notify) -> persist.

One graph run is one report cycle.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
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
from tender_agent.errors import describe_exception
from tender_agent.filters import Filters, load_filters
from tender_agent.llm import LLMClient
from tender_agent.logging import get_logger, narrate
from tender_agent.prozorro.client import ProzorroClient
from tender_agent.prozorro.models import Tender
from tender_agent.settings import Settings
from tender_agent.state import ClassifiedTender, PipelineState
from tender_agent.storage import SeenRecord, Storage

_log = get_logger(__name__)

_CLASSIFY_CONCURRENCY = 8
_SUMMARY_CONCURRENCY = 6

# Type aliases for pipeline node callables.
_NodeFn = Callable[["PipelineContext", PipelineState], Awaitable[dict[str, object]]]
# A pre-bound node (context already captured via partial): only takes state.
_BoundNodeFn = Callable[[PipelineState], Awaitable[dict[str, object]]]


@dataclass(slots=True)
class PipelineContext:
    """Dependencies shared by every pipeline node."""

    settings: Settings
    storage: Storage
    llm: LLMClient
    filters: Filters | None = None
    dry_run: bool = False


def _merge_counters(state: PipelineState, **counts: int) -> dict[str, int]:
    """Return the running counters dict updated with new values."""
    merged = dict(state.get("counters", {}))
    merged.update(counts)
    return merged


# ── instrumentation ──────────────────────────────────────────────────────────


def _instrument(name: str, human_label: str, node: _BoundNodeFn) -> _BoundNodeFn:
    """Wrap a pre-bound pipeline node with start/finish/error logging.

    *node* must already have its :class:`PipelineContext` captured (e.g. via
    :func:`functools.partial`), so the wrapper matches the signature LangGraph
    expects: ``async (state: PipelineState) -> dict[str, object]``.
    """

    async def wrapped(state: PipelineState) -> dict[str, object]:
        _log.info(f"{name}_started", step=name)
        narrate(_log, f"Step started: {human_label}.")
        try:
            result = await node(state)
        except Exception as exc:
            err = describe_exception(exc)
            _log.error(
                f"{name}_failed",
                step=name,
                error_kind=err.kind,
                error=str(exc),
                exc_info=True,
            )
            narrate(_log, f"Step failed: {human_label} — {err.message}", level="error")
            raise
        _log.debug(f"{name}_instrumentation_done", step=name)
        return result

    return wrapped


# ── nodes ───────────────────────────────────────────────────────────────────


async def _crawl(ctx: PipelineContext, state: PipelineState) -> dict[str, object]:
    """Incrementally crawl PROZORRO from the saved feed cursor."""
    offset = ctx.storage.get_offset()
    async with ProzorroClient(ctx.settings) as client:
        result = await client.crawl(offset)
    _log.info("crawl_node", tenders=len(result.tenders), first_run=offset is None)
    narrate(
        _log,
        f"Searched PROZORRO and found {len(result.tenders)} tender(s) to review"
        + (" (first run — no cursor saved)." if offset is None else "."),
        tenders=len(result.tenders),
    )
    return {
        "tenders": result.tenders,
        "new_offset": result.next_offset,
        "counters": _merge_counters(state, crawled=len(result.tenders)),
    }


async def _prefilter(ctx: PipelineContext, state: PipelineState) -> dict[str, object]:
    """Apply the broad CPV/keyword net before any LLM call."""
    filters = ctx.filters if ctx.filters is not None else load_filters(ctx.settings.filters_path)
    tenders = state.get("tenders", [])
    kept = [t for t in tenders if filters.matches(t)]
    _log.info("prefilter_node", kept=len(kept), total=len(tenders))
    narrate(
        _log,
        f"Prefiltered by product category: {len(kept)} of {len(tenders)} tender(s) "
        "passed the broad product keyword check.",
        kept=len(kept),
        total=len(tenders),
    )
    return {"prefiltered": kept, "counters": _merge_counters(state, prefiltered=len(kept))}


async def _classify(ctx: PipelineContext, state: PipelineState) -> dict[str, object]:
    """Let the LLM make the final relevance decision on each tender."""
    filters = ctx.filters
    domain_name = filters.domain.name_uk if filters is not None else "automotive chemistry"
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
    narrate(
        _log,
        f"AI classified {len(relevant)} of {len(prefiltered)} tender(s) as relevant "
        f"for {domain_name}.",
        relevant=len(relevant),
        considered=len(prefiltered),
    )
    return {"classified": relevant, "counters": _merge_counters(state, relevant=len(relevant))}


async def _dedupe(ctx: PipelineContext, state: PipelineState) -> dict[str, object]:
    """Drop tenders that were already reported in a previous run."""
    classified = state.get("classified", [])
    unseen = ctx.storage.filter_unseen([c.tender.id for c in classified])
    new = [c for c in classified if c.tender.id in unseen]
    _log.info("dedupe_node", new=len(new), classified=len(classified))
    narrate(
        _log,
        f"Deduplication complete: {len(new)} new tender(s) not seen in previous runs.",
        new=len(new),
        classified=len(classified),
    )
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


async def _deadline_check(ctx: PipelineContext, state: PipelineState) -> dict[str, object]:
    """Query the DB for previously reported tenders whose deadline is approaching."""
    reminders = ctx.storage.get_deadline_reminders(ctx.settings.deadline_reminder_days)
    _log.info("deadline_check_node", reminders=len(reminders))
    narrate(
        _log,
        f"Deadline check: {len(reminders)} previously reported tender(s) have a deadline "
        f"within {ctx.settings.deadline_reminder_days} day(s).",
        reminders=len(reminders),
    )
    return {"deadline_reminders": reminders}


async def _render(ctx: PipelineContext, state: PipelineState) -> dict[str, object]:
    """Generate Ukrainian summaries, render the HTML report, and convert to PDF."""
    filters = ctx.filters
    category_labels = filters.category_labels if filters is not None else None
    domain_name = filters.domain.name_uk if filters is not None else "автохімії"

    new = state.get("new_tenders", [])
    sem = asyncio.Semaphore(_SUMMARY_CONCURRENCY)

    async def summarize_one(item: ClassifiedTender) -> None:
        async with sem:
            item.summary = await ctx.llm.summarize(item.tender)

    await asyncio.gather(*(summarize_one(c) for c in new))
    generated_at = datetime.now(ZoneInfo(ctx.settings.timezone))
    reminders = state.get("deadline_reminders", [])
    report = render_report(
        new,
        generated_at,
        reminders=reminders,
        category_labels=category_labels,
        domain_name=domain_name,
    )
    pdf_bytes = render_pdf(report.html)
    path = _save_report(ctx, report.html, pdf_bytes, generated_at)
    _log.info("render_node", tenders=len(new), report_path=str(path))
    narrate(
        _log,
        f"Report generated with {len(new)} new tender(s) and {len(reminders)} reminder(s). "
        f"Saved to {path}.",
        tenders=len(new),
        reminders=len(reminders),
        report_path=str(path),
    )
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
        narrate(_log, "Email skipped — running in dry-run mode (no email will be sent).")
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
    narrate(_log, "Email report sent successfully to all recipients.")
    return {"email_sent": True}


async def _persist(ctx: PipelineContext, state: PipelineState) -> dict[str, object]:
    """Record reported tenders and advance the feed cursor."""
    new = state.get("new_tenders", [])
    if ctx.dry_run:
        _log.info("persist_node_skipped", reason="dry_run", would_mark=len(new))
        narrate(_log, f"State not saved — dry-run mode. Would have recorded {len(new)} tender(s).")
        return {}
    records = [
        SeenRecord(
            tender_id=c.tender.id,
            public_id=c.tender.public_id,
            category=c.category,
            status=c.tender.status or "",
            title=c.tender.title or "",
            summary=c.summary,
            tender_period_end=(
                c.tender.tenderPeriod.endDate
                if c.tender.tenderPeriod and c.tender.tenderPeriod.endDate
                else ""
            ),
        )
        for c in new
    ]
    ctx.storage.mark_reported(records)
    offset = state.get("new_offset")
    if offset:
        ctx.storage.set_offset(offset)
    _log.info("persist_node", marked=len(records), offset_saved=bool(offset))
    narrate(
        _log,
        f"Saved {len(records)} tender(s) to the database and advanced the feed cursor.",
    )
    return {}


# ── graph ─────────────────────────────────────────────────────────────────


def build_graph(ctx: PipelineContext) -> Any:
    """Build and compile the LangGraph pipeline for the given context."""
    graph = StateGraph(PipelineState)
    graph.add_node(  # type: ignore[call-overload]
        "crawl", _instrument("crawl", "Crawling PROZORRO for new tenders", partial(_crawl, ctx))
    )
    graph.add_node(  # type: ignore[call-overload]
        "prefilter",
        _instrument(
            "prefilter",
            "Filtering tenders by product category",
            partial(_prefilter, ctx),
        ),
    )
    graph.add_node(  # type: ignore[call-overload]
        "classify",
        _instrument("classify", "AI relevance classification", partial(_classify, ctx)),
    )
    graph.add_node(  # type: ignore[call-overload]
        "dedupe",
        _instrument("dedupe", "Deduplicating against seen tenders", partial(_dedupe, ctx)),
    )
    graph.add_node(  # type: ignore[call-overload]
        "deadline_check",
        _instrument(
            "deadline_check",
            "Checking for upcoming deadlines",
            partial(_deadline_check, ctx),
        ),
    )
    graph.add_node(  # type: ignore[call-overload]
        "render",
        _instrument("render", "Rendering the HTML/PDF report", partial(_render, ctx)),
    )
    graph.add_node(  # type: ignore[call-overload]
        "notify",
        _instrument("notify", "Sending email notification", partial(_notify, ctx)),
    )
    graph.add_node(  # type: ignore[call-overload]
        "persist",
        _instrument("persist", "Persisting run state to database", partial(_persist, ctx)),
    )

    graph.set_entry_point("crawl")
    graph.add_edge("crawl", "prefilter")
    graph.add_edge("prefilter", "classify")
    graph.add_edge("classify", "dedupe")
    graph.add_edge("dedupe", "deadline_check")

    def route_after_deadline_check(state: PipelineState) -> str:
        has_work = (
            bool(state.get("new_tenders"))
            or bool(state.get("deadline_reminders"))
            or ctx.settings.send_when_empty
        )
        if not has_work:
            _log.info(
                "route_no_work",
                new_tenders=len(state.get("new_tenders", [])),
                reminders=len(state.get("deadline_reminders", [])),
            )
            narrate(
                _log,
                "No email will be sent — no new tenders found and no upcoming deadlines.",
                new_tenders=len(state.get("new_tenders", [])),
                reminders=len(state.get("deadline_reminders", [])),
            )
            return "persist"
        return "render"

    graph.add_conditional_edges(
        "deadline_check",
        route_after_deadline_check,
        {"render": "render", "persist": "persist"},
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
