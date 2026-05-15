"""Command-line entrypoint: one-off runs, the daily scheduler, and stats."""

from __future__ import annotations

import argparse
import asyncio
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from tender_agent.llm import LLMClient, apply_provider_keys
from tender_agent.logging import configure_logging, get_logger
from tender_agent.pipeline import PipelineContext, run_pipeline
from tender_agent.settings import Settings, get_settings
from tender_agent.storage import Storage

_log = get_logger(__name__)


async def _run_once(settings: Settings, *, dry_run: bool) -> None:
    """Execute a single report cycle."""
    apply_provider_keys(settings)
    with Storage(settings.db_path) as storage:
        llm = LLMClient(settings, storage)
        ctx = PipelineContext(settings=settings, storage=storage, llm=llm, dry_run=dry_run)
        final = await run_pipeline(ctx)
    counters = final.get("counters", {})
    _log.info(
        "run_complete",
        dry_run=dry_run,
        email_sent=final.get("email_sent", False),
        report_path=final.get("report_path"),
        **counters,
    )


def _cmd_run(settings: Settings, args: argparse.Namespace) -> int:
    asyncio.run(_run_once(settings, dry_run=bool(args.dry_run)))
    return 0


def _cmd_schedule(settings: Settings, args: argparse.Namespace) -> int:
    scheduler = BlockingScheduler(timezone=settings.timezone)
    trigger = CronTrigger.from_crontab(settings.schedule_cron, timezone=settings.timezone)

    def job() -> None:
        try:
            asyncio.run(_run_once(settings, dry_run=False))
        except Exception:
            _log.exception("scheduled_run_failed")

    scheduler.add_job(job, trigger, id="tender-report", max_instances=1, coalesce=True)
    _log.info("scheduler_started", cron=settings.schedule_cron, timezone=settings.timezone)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):  # pragma: no cover - signal handling
        _log.info("scheduler_stopped")
    return 0


def _cmd_stats(settings: Settings, args: argparse.Namespace) -> int:
    with Storage(settings.db_path) as storage:
        rows = storage.usage_rollup()
    if not rows:
        print("Поки що немає записів про використання LLM.")
        return 0

    header = (
        f"{'ДАТА':<12} {'ПРОВАЙДЕР':<14} {'МОДЕЛЬ':<18} {'РОЛЬ':<10} "
        f"{'ВИКЛ.':>6} {'ВХІД':>10} {'ВИХІД':>10} {'USD':>10}"
    )
    print(header)
    print("-" * len(header))
    total_cost = 0.0
    for row in rows:
        cost = float(row["estimated_cost_usd"])
        total_cost += cost
        print(
            f"{row['day']:<12} {row['provider']:<14} {row['model']:<18} "
            f"{row['role']:<10} {row['calls']:>6} {row['prompt_tokens']:>10} "
            f"{row['completion_tokens']:>10} {cost:>10.4f}"
        )
    print("-" * len(header))
    print(f"Орієнтовна сумарна вартість: ${total_cost:.4f}")
    return 0


def _cmd_healthcheck(settings: Settings, args: argparse.Namespace) -> int:
    try:
        with Storage(settings.db_path) as storage:
            storage.get_offset()
    except Exception as exc:  # noqa: BLE001 - report any failure as unhealthy
        print(f"unhealthy: {exc}", file=sys.stderr)
        return 1
    print("ok")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tender-agent",
        description="Collects Ukrainian automotive-chemistry tenders and emails a report.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="Run one report cycle immediately.")
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the pipeline without sending email or persisting state.",
    )
    run_parser.add_argument(
        "--now",
        action="store_true",
        help="Run immediately (default; accepted for convenience).",
    )

    sub.add_parser("schedule", help="Start the blocking daily scheduler.")

    stats_parser = sub.add_parser("stats", help="Show LLM token usage and estimated cost.")
    stats_parser.add_argument(
        "--usage", action="store_true", help="Show the usage rollup (default)."
    )

    sub.add_parser("healthcheck", help="Exit 0 when the agent is healthy.")
    return parser


def main() -> None:
    """CLI entrypoint."""
    args = _build_parser().parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)

    handlers = {
        "run": _cmd_run,
        "schedule": _cmd_schedule,
        "stats": _cmd_stats,
        "healthcheck": _cmd_healthcheck,
    }
    sys.exit(handlers[args.command](settings, args))


if __name__ == "__main__":
    main()
