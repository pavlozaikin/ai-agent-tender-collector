"""Tests for the CLI entrypoint wiring."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tender_agent import main as main_module
from tender_agent.logging import configure_logging
from tender_agent.main import (
    _build_parser,
    _cmd_healthcheck,
    _cmd_reset_db,
    _cmd_reset_seen,
    _cmd_run,
    _cmd_stats,
    _cmd_test_send,
    _run_once,
    main,
)
from tender_agent.settings import Settings
from tender_agent.storage import Storage, UsageRecord


def test_parser_run_defaults() -> None:
    args = _build_parser().parse_args(["run"])
    assert args.command == "run"
    assert args.dry_run is False


def test_parser_run_dry_run_flag() -> None:
    args = _build_parser().parse_args(["run", "--dry-run"])
    assert args.dry_run is True


def test_parser_requires_subcommand() -> None:
    with pytest.raises(SystemExit):
        _build_parser().parse_args([])


def test_parser_reset_db_requires_yes_flag_for_execution() -> None:
    args = _build_parser().parse_args(["reset-db"])
    assert args.command == "reset-db"
    assert args.yes is False


def test_healthcheck_ok(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    assert _cmd_healthcheck(settings, argparse.Namespace()) == 0
    assert "ok" in capsys.readouterr().out


def test_stats_empty(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    assert _cmd_stats(settings, argparse.Namespace()) == 0
    assert "немає записів" in capsys.readouterr().out


def test_stats_with_usage(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    with Storage(settings.db_path) as store:
        store.record_usage(UsageRecord("openai", "gpt-5.4-mini", "classify", 100, 20, 0.01, False))
    assert _cmd_stats(settings, argparse.Namespace()) == 0
    out = capsys.readouterr().out
    assert "gpt-5.4-mini" in out
    assert "Орієнтовна сумарна вартість" in out


# ── _run_once ───────────────────────────────────────────────────────────────


async def test_run_once_calls_pipeline(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    """_run_once should call run_pipeline and log the result."""
    fake_final: dict[str, object] = {
        "counters": {"a": 1},
        "email_sent": True,
        "report_path": "/tmp/r.pdf",
    }
    monkeypatch.setattr(main_module, "apply_provider_keys", lambda s: None)
    monkeypatch.setattr(main_module, "run_pipeline", AsyncMock(return_value=fake_final))
    # LLMClient init tries to build models; patch _build_model to avoid real LangChain calls.
    import tender_agent.llm as llm_module

    monkeypatch.setattr(llm_module, "_build_model", lambda _spec: MagicMock())
    await _run_once(settings, dry_run=True)


# ── _cmd_run ────────────────────────────────────────────────────────────────


def test_cmd_run_returns_zero(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(main_module, "_run_once", AsyncMock(return_value=None))
    args = argparse.Namespace(dry_run=False)
    assert _cmd_run(settings, args) == 0


# ── _cmd_schedule ────────────────────────────────────────────────────────────


def test_cmd_schedule_returns_zero(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    """Lines 50-65: _cmd_schedule creates scheduler, adds job, starts it."""
    from tender_agent.main import _cmd_schedule

    captured_jobs: list[object] = []

    class FakeScheduler:
        def __init__(self, *, timezone: str) -> None:
            self.started = False

        def add_job(self, func: object, trigger: object, **kwargs: object) -> None:
            captured_jobs.append(func)

        def start(self) -> None:
            self.started = True

    fake_scheduler = FakeScheduler(timezone=settings.timezone)

    class FakeCronTrigger:
        @classmethod
        def from_crontab(cls, cron: str, timezone: str) -> FakeCronTrigger:
            return cls()

    monkeypatch.setattr(main_module, "BlockingScheduler", lambda **kw: fake_scheduler)
    monkeypatch.setattr(main_module, "CronTrigger", FakeCronTrigger)

    result = _cmd_schedule(settings, argparse.Namespace())
    assert result == 0
    assert fake_scheduler.started is True
    assert len(captured_jobs) == 1


def test_cmd_schedule_job_handles_exception(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Lines 54-57: the job() exception handler logs and swallows errors."""
    from tender_agent.main import _cmd_schedule

    captured_jobs: list[object] = []

    class FakeScheduler:
        def add_job(self, func: object, trigger: object, **kwargs: object) -> None:
            captured_jobs.append(func)

        def start(self) -> None:
            pass

    class FakeCronTrigger:
        @classmethod
        def from_crontab(cls, cron: str, timezone: str) -> FakeCronTrigger:
            return cls()

    monkeypatch.setattr(main_module, "BlockingScheduler", lambda **kw: FakeScheduler())
    monkeypatch.setattr(main_module, "CronTrigger", FakeCronTrigger)
    # Make _run_once raise so the except branch is exercised.
    monkeypatch.setattr(main_module, "_run_once", AsyncMock(side_effect=RuntimeError("boom")))

    _cmd_schedule(settings, argparse.Namespace())

    # Call the captured job function directly — it should swallow the exception.
    job_fn = captured_jobs[0]
    job_fn()  # must not raise


# ── _cmd_reset_seen ─────────────────────────────────────────────────────────


def test_cmd_reset_seen(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    assert _cmd_reset_seen(settings, argparse.Namespace()) == 0
    out = capsys.readouterr().out
    assert "seen_tenders" in out


# ── _cmd_reset_db ───────────────────────────────────────────────────────────


def test_cmd_reset_db_without_yes_returns_one(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    args = argparse.Namespace(yes=False)
    assert _cmd_reset_db(settings, args) == 1
    assert "reset-db --yes" in capsys.readouterr().err


def test_cmd_reset_db_with_yes_deletes_file(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    # Ensure the DB file exists first.
    db = settings.db_path
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"placeholder")
    args = argparse.Namespace(yes=True)
    assert _cmd_reset_db(settings, args) == 0
    assert not db.exists()
    assert "видалено" in capsys.readouterr().out


def test_cmd_reset_db_with_yes_file_missing(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    # DB file doesn't exist — should print "not found" message and return 0.
    args = argparse.Namespace(yes=True)
    assert _cmd_reset_db(settings, args) == 0
    assert "не знайдено" in capsys.readouterr().out


# ── _cmd_test_send ──────────────────────────────────────────────────────────


def test_cmd_test_send_no_reports(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    """Should return 1 and print an error when reports_dir has no HTML files."""
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    args = argparse.Namespace(no_email=False)
    assert _cmd_test_send(settings, args) == 1
    assert "Немає збережених звітів" in capsys.readouterr().err


def test_cmd_test_send_no_email_flag(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With --no-email, should render PDF and save it without sending."""
    reports_dir = settings.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)
    html_path = reports_dir / "report-2026-05-15.html"
    html_path.write_text("<html>test</html>", encoding="utf-8")

    monkeypatch.setattr(main_module, "render_pdf", lambda html: b"fake-pdf")
    args = argparse.Namespace(no_email=True)
    assert _cmd_test_send(settings, args) == 0
    out = capsys.readouterr().out
    assert "--no-email" in out
    # PDF file should have been written.
    assert (reports_dir / "report-2026-05-15.pdf").exists()


def test_cmd_test_send_sends_email(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Full path: render PDF and send email."""
    reports_dir = settings.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)
    html_path = reports_dir / "report-2026-05-15.html"
    html_path.write_text("<html>test</html>", encoding="utf-8")

    # Provide a recipients.yaml so load_recipients succeeds.
    rec_path = settings.recipients_path
    rec_path.parent.mkdir(parents=True, exist_ok=True)
    rec_path.write_text("to:\n  - a@example.com\n", encoding="utf-8")

    monkeypatch.setattr(main_module, "render_pdf", lambda html: b"fake-pdf")

    fake_sender = MagicMock()
    monkeypatch.setattr(main_module, "EmailSender", lambda s: fake_sender)

    args = argparse.Namespace(no_email=False)
    assert _cmd_test_send(settings, args) == 0
    assert fake_sender.send.called
    assert "Email надіслано" in capsys.readouterr().out


# ── _cmd_healthcheck unhealthy path ─────────────────────────────────────────


def test_healthcheck_unhealthy(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When storage.get_offset raises, should return 1 and print 'unhealthy'."""

    class _BadStorage:
        def __init__(self, _path: object) -> None:
            pass

        def __enter__(self) -> _BadStorage:
            return self

        def __exit__(self, *a: object) -> None:
            pass

        def get_offset(self) -> None:
            raise RuntimeError("disk gone")

    monkeypatch.setattr(main_module, "Storage", _BadStorage)
    result = _cmd_healthcheck(settings, argparse.Namespace())
    assert result == 1
    assert "unhealthy" in capsys.readouterr().err


# ── main() dispatcher ────────────────────────────────────────────────────────


# ── configure_logging ────────────────────────────────────────────────────────


def test_configure_logging_runs_without_error() -> None:
    """Lines 15-17 of logging.py: calling configure_logging should not raise."""
    configure_logging("DEBUG")
    configure_logging("INFO")


# ── main() dispatcher ────────────────────────────────────────────────────────


def test_main_dispatches_to_run(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    """main() should parse argv, load settings, and call the right handler."""
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(main_module, "configure_logging", lambda level: None)
    monkeypatch.setattr(main_module, "_run_once", AsyncMock(return_value=None))

    import sys

    monkeypatch.setattr(sys, "argv", ["tender-agent", "run"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
