"""SQLite persistence: crawl cursor, deduplication, and LLM usage stats."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS crawl_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seen_tenders (
    tender_id   TEXT PRIMARY KEY,
    public_id   TEXT,
    category    TEXT,
    status      TEXT,
    first_seen  TEXT NOT NULL,
    reported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_usage (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                 TEXT NOT NULL,
    day                TEXT NOT NULL,
    provider           TEXT NOT NULL,
    model              TEXT NOT NULL,
    role               TEXT NOT NULL,
    prompt_tokens      INTEGER NOT NULL,
    completion_tokens  INTEGER NOT NULL,
    estimated_cost_usd REAL NOT NULL,
    fallback_used      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_day ON llm_usage (day);
"""

_OFFSET_KEY = "feed_offset"


@dataclass(slots=True)
class SeenRecord:
    """A tender that has been reported, to be persisted for deduplication."""

    tender_id: str
    public_id: str
    category: str
    status: str


@dataclass(slots=True)
class UsageRecord:
    """One LLM call's token usage and estimated cost."""

    provider: str
    model: str
    role: str
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: float
    fallback_used: bool


class Storage:
    """Thin SQLite repository. Use as a context manager (one per pipeline run)."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ── lifecycle ───────────────────────────────────────────────────────────
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Storage:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ── crawl cursor ────────────────────────────────────────────────────────
    def get_offset(self) -> str | None:
        """Return the saved feed offset cursor, or None on a first run."""
        row = self._conn.execute(
            "SELECT value FROM crawl_state WHERE key = ?", (_OFFSET_KEY,)
        ).fetchone()
        return str(row["value"]) if row else None

    def set_offset(self, offset: str) -> None:
        """Persist the feed offset cursor for the next run."""
        self._conn.execute(
            "INSERT INTO crawl_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_OFFSET_KEY, offset),
        )
        self._conn.commit()

    # ── deduplication ───────────────────────────────────────────────────────
    def is_seen(self, tender_id: str) -> bool:
        """Whether this tender has already been reported."""
        row = self._conn.execute(
            "SELECT 1 FROM seen_tenders WHERE tender_id = ?", (tender_id,)
        ).fetchone()
        return row is not None

    def filter_unseen(self, tender_ids: list[str]) -> set[str]:
        """Return the subset of ``tender_ids`` not previously reported."""
        if not tender_ids:
            return set()
        placeholders = ",".join("?" * len(tender_ids))
        seen = {
            str(row["tender_id"])
            for row in self._conn.execute(
                f"SELECT tender_id FROM seen_tenders WHERE tender_id IN ({placeholders})",
                tender_ids,
            )
        }
        return {tid for tid in tender_ids if tid not in seen}

    def mark_reported(self, records: list[SeenRecord]) -> None:
        """Record tenders as reported so they are not sent again."""
        if not records:
            return
        now = datetime.now(UTC).isoformat()
        self._conn.executemany(
            "INSERT INTO seen_tenders "
            "(tender_id, public_id, category, status, first_seen, reported_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(tender_id) DO UPDATE SET "
            "  status = excluded.status, reported_at = excluded.reported_at",
            [(r.tender_id, r.public_id, r.category, r.status, now, now) for r in records],
        )
        self._conn.commit()

    def clear_seen(self) -> int:
        """Delete all persisted deduplication records (seen tenders).

        Returns the number of deleted rows.
        """
        cur = self._conn.execute("DELETE FROM seen_tenders")
        self._conn.commit()
        return int(cur.rowcount)

    # ── LLM usage statistics ────────────────────────────────────────────────
    def record_usage(self, usage: UsageRecord) -> None:
        """Log one LLM call's token usage and estimated cost."""
        now = datetime.now(UTC)
        self._conn.execute(
            "INSERT INTO llm_usage "
            "(ts, day, provider, model, role, prompt_tokens, completion_tokens, "
            " estimated_cost_usd, fallback_used) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                now.isoformat(),
                now.date().isoformat(),
                usage.provider,
                usage.model,
                usage.role,
                usage.prompt_tokens,
                usage.completion_tokens,
                usage.estimated_cost_usd,
                int(usage.fallback_used),
            ),
        )
        self._conn.commit()

    def usage_rollup(self) -> list[dict[str, Any]]:
        """Per-day / per-model / per-role token and cost totals, newest first."""
        rows = self._conn.execute(
            "SELECT day, provider, model, role, "
            "       COUNT(*) AS calls, "
            "       SUM(prompt_tokens) AS prompt_tokens, "
            "       SUM(completion_tokens) AS completion_tokens, "
            "       SUM(estimated_cost_usd) AS estimated_cost_usd "
            "FROM llm_usage "
            "GROUP BY day, provider, model, role "
            "ORDER BY day DESC, provider, model, role"
        ).fetchall()
        return [dict(row) for row in rows]
