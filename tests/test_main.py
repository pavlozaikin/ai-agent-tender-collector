"""Tests for the CLI entrypoint wiring."""

from __future__ import annotations

import argparse

import pytest

from tender_agent.main import _build_parser, _cmd_healthcheck, _cmd_stats
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
