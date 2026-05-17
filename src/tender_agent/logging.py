"""Structured logging setup (structlog + stdlib routing)."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import structlog
from structlog.typing import FilteringBoundLogger


def configure_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    """Configure structlog + stdlib logging with console and optional file handlers.

    Routes structlog through stdlib logging so that every log event is
    dispatched to all registered handlers:

    * **Console** — always present; uses :class:`structlog.dev.ConsoleRenderer`.
    * **File** — added when *log_file* is not ``None``; writes JSON Lines via
      :class:`structlog.processors.JSONRenderer`.

    Calling this function multiple times is safe: existing root-logger handlers
    are cleared before new ones are installed, preventing duplicates.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # ── shared processor chain (runs before the per-handler renderer) ────────
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ]

    structlog.configure(
        processors=shared_processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # ── root logger ──────────────────────────────────────────────────────────
    root = logging.getLogger()
    # Clear existing handlers to prevent duplicate output on multiple calls.
    root.handlers.clear()
    root.setLevel(log_level)

    # Console handler
    console_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.dev.ConsoleRenderer(),
        foreign_pre_chain=shared_processors[:-1],  # skip wrap_for_formatter
    )
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)
    root.addHandler(console_handler)

    # File handler (JSON Lines)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        json_formatter = structlog.stdlib.ProcessorFormatter(
            processor=structlog.processors.JSONRenderer(),
            foreign_pre_chain=shared_processors[:-1],  # skip wrap_for_formatter
        )
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(json_formatter)
        root.addHandler(file_handler)


def narrate(logger: Any, message: str, level: str = "info", **fields: object) -> None:
    """Emit a plain-English summary line tagged with ``audience="human"``.

    This is a thin wrapper around the structlog bound-logger that attaches a
    standard ``audience="human"`` key so log consumers can easily filter or
    surface the human-readable event stream.

    Args:
        logger: Any structlog bound logger (e.g. from :func:`get_logger`).
        message: A complete English sentence describing what happened.
        level:   Log level name (``"info"``, ``"warning"``, ``"error"``, …).
        **fields: Additional structured key/value pairs to attach.
    """
    getattr(logger, level)(message, audience="human", **fields)


def get_logger(name: str) -> FilteringBoundLogger:
    """Return a bound structlog logger."""
    from typing import cast

    return cast(FilteringBoundLogger, structlog.get_logger(name))
