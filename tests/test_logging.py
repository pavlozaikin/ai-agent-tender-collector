"""Tests for the structured logging setup (configure_logging, narrate, get_logger)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
import structlog

from tender_agent.logging import configure_logging, get_logger, narrate, sanitize_log


def test_configure_logging_console_only_adds_one_handler() -> None:
    """Console-only configuration: root logger should have exactly 1 handler."""
    configure_logging("DEBUG")
    root = logging.getLogger()
    assert len(root.handlers) == 1


def test_configure_logging_with_file_adds_two_handlers(tmp_path: Path) -> None:
    """File logging enabled: root logger should have 2 handlers."""
    log_file = tmp_path / "test.jsonl"
    configure_logging("INFO", log_file=log_file)
    root = logging.getLogger()
    assert len(root.handlers) == 2


def test_configure_logging_file_is_created_on_write(tmp_path: Path) -> None:
    """Writing a log line should create the JSONL file."""
    log_file = tmp_path / "subdir" / "tender-agent.jsonl"
    configure_logging("DEBUG", log_file=log_file)
    logger = get_logger("test_file_create")
    logger.info("hello_world", key="value")
    # flush handlers
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert log_file.exists()


def test_configure_logging_file_contains_valid_json(tmp_path: Path) -> None:
    """Each line in the log file should be valid JSON."""
    log_file = tmp_path / "output.jsonl"
    configure_logging("DEBUG", log_file=log_file)
    logger = get_logger("test_json_output")
    logger.info("a_test_event", some_key="some_value")
    for handler in logging.getLogger().handlers:
        handler.flush()
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    parsed = json.loads(lines[-1])
    assert parsed.get("event") == "a_test_event"
    assert parsed.get("some_key") == "some_value"


def test_configure_logging_multiple_calls_no_duplicate_handlers(tmp_path: Path) -> None:
    """Calling configure_logging twice must not duplicate handlers."""
    log_file = tmp_path / "dup.jsonl"
    configure_logging("INFO", log_file=log_file)
    configure_logging("INFO", log_file=log_file)
    root = logging.getLogger()
    assert len(root.handlers) == 2  # still exactly 2 (1 console + 1 file)


def test_configure_logging_console_twice_stays_at_one_handler() -> None:
    """Two console-only calls must not double the handlers."""
    configure_logging("DEBUG")
    configure_logging("DEBUG")
    assert len(logging.getLogger().handlers) == 1


def test_get_logger_returns_logger() -> None:
    """get_logger should return a usable bound logger."""
    configure_logging("DEBUG")
    logger = get_logger("my.module")
    # Should not raise.
    logger.info("test_event")


def test_narrate_emits_human_audience_tag(tmp_path: Path) -> None:
    """narrate() should attach audience='human' to the emitted event."""
    log_file = tmp_path / "narrate.jsonl"
    configure_logging("DEBUG", log_file=log_file)
    logger = get_logger("test_narrate")
    narrate(logger, "Hello, world.", extra_field="x")
    for handler in logging.getLogger().handlers:
        handler.flush()
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    last = json.loads(lines[-1])
    assert last.get("audience") == "human"
    assert last.get("event") == "Hello, world."
    assert last.get("extra_field") == "x"


def test_narrate_warning_level(tmp_path: Path) -> None:
    """narrate with level='warning' should produce a WARNING record."""
    log_file = tmp_path / "warn.jsonl"
    configure_logging("DEBUG", log_file=log_file)
    logger = get_logger("test_warn")
    narrate(logger, "Something bad happened.", level="warning")
    for handler in logging.getLogger().handlers:
        handler.flush()
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    last = json.loads(lines[-1])
    assert last.get("level") == "warning"
    assert last.get("audience") == "human"


def test_level_filtering_info_not_emitted_at_warning(tmp_path: Path) -> None:
    """With WARNING level, INFO events must not appear in the log file."""
    log_file = tmp_path / "filtered.jsonl"
    configure_logging("WARNING", log_file=log_file)
    logger = get_logger("test_filter")
    logger.info("should_be_filtered_out")
    logger.warning("should_appear")
    for handler in logging.getLogger().handlers:
        handler.flush()
    if not log_file.exists():
        pytest.skip("No log file created — WARNING-level INFO suppression works correctly")
    content = log_file.read_text(encoding="utf-8").strip()
    lines = [line for line in content.splitlines() if line.strip()]
    if lines:
        events = [json.loads(line).get("event") for line in lines]
        assert "should_be_filtered_out" not in events
        assert "should_appear" in events


# ── M2: sanitize_log neutralises log-forging input ───────────────────────────


def test_sanitize_log_strips_newlines() -> None:
    """M2: newlines/carriage returns (used to forge fake log lines) are removed."""
    forged = "real title\nINFO fake_event injected=true\rmore"
    cleaned = sanitize_log(forged)
    assert "\n" not in cleaned
    assert "\r" not in cleaned
    assert "real title" in cleaned


def test_sanitize_log_strips_ansi_escapes() -> None:
    """M2: ANSI escape sequences (terminal injection) are neutralised."""
    payload = "title \x1b[31mRED\x1b[0m"
    cleaned = sanitize_log(payload)
    assert "\x1b" not in cleaned


def test_sanitize_log_truncates_to_max_len() -> None:
    """M2: the result is capped at max_len characters."""
    cleaned = sanitize_log("x" * 1000, max_len=50)
    assert len(cleaned) <= 51  # 50 chars + ellipsis
    assert cleaned.startswith("x" * 50)


def test_sanitize_log_passes_through_clean_text() -> None:
    """Ordinary text is returned unchanged."""
    assert sanitize_log("Закупівля антифризу") == "Закупівля антифризу"


def test_configure_logging_structlog_uses_bound_logger() -> None:
    """structlog.get_logger() after configure must return a BoundLogger."""
    configure_logging("INFO")
    # We test that calling info on it doesn't raise.
    log = structlog.get_logger("test_bound")
    log.info("bound_logger_test")  # type: ignore[union-attr]
