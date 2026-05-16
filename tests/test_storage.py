"""Tests for the SQLite storage layer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tender_agent.storage import SeenRecord, Storage, UsageRecord


def test_offset_round_trip(storage: Storage) -> None:
    assert storage.get_offset() is None
    storage.set_offset("cursor-1")
    assert storage.get_offset() == "cursor-1"
    storage.set_offset("cursor-2")
    assert storage.get_offset() == "cursor-2"


def test_filter_unseen_and_mark_reported(storage: Storage) -> None:
    assert storage.filter_unseen(["a", "b", "c"]) == {"a", "b", "c"}

    storage.mark_reported(
        [SeenRecord(tender_id="b", public_id="UA-b", category="coolant", status="active")]
    )
    assert storage.filter_unseen(["a", "b", "c"]) == {"a", "c"}
    assert storage.is_seen("b") is True
    assert storage.is_seen("a") is False


def test_filter_unseen_handles_empty_input(storage: Storage) -> None:
    assert storage.filter_unseen([]) == set()


def test_mark_reported_is_idempotent(storage: Storage) -> None:
    record = SeenRecord(tender_id="x", public_id="UA-x", category="motor_oil", status="a")
    storage.mark_reported([record])
    storage.mark_reported([record])  # must not raise on conflict
    assert storage.is_seen("x") is True


def test_usage_rollup_aggregates(storage: Storage) -> None:
    storage.record_usage(UsageRecord("openai", "gpt-5.4-mini", "classify", 100, 20, 0.01, False))
    storage.record_usage(UsageRecord("openai", "gpt-5.4-mini", "classify", 200, 30, 0.02, False))
    storage.record_usage(UsageRecord("openai", "gpt-5.5", "report", 50, 80, 0.05, True))

    rollup = storage.usage_rollup()
    by_model = {(r["model"], r["role"]): r for r in rollup}

    classify = by_model[("gpt-5.4-mini", "classify")]
    assert classify["calls"] == 2
    assert classify["prompt_tokens"] == 300
    assert classify["completion_tokens"] == 50
    assert abs(classify["estimated_cost_usd"] - 0.03) < 1e-9

    report = by_model[("gpt-5.5", "report")]
    assert report["calls"] == 1


def test_usage_rollup_empty(storage: Storage) -> None:
    assert storage.usage_rollup() == []


def test_mark_reported_stores_new_fields(storage: Storage) -> None:
    record = SeenRecord(
        tender_id="t1",
        public_id="UA-t1",
        category="coolant",
        status="active",
        title="Антифриз для автопарку",
        summary="Закупівля охолоджувальної рідини на 200 л.",
        tender_period_end="2026-05-20T12:00:00+00:00",
    )
    storage.mark_reported([record])
    row = storage._conn.execute(
        "SELECT title, summary, tender_period_end FROM seen_tenders WHERE tender_id = ?", ("t1",)
    ).fetchone()
    assert row["title"] == "Антифриз для автопарку"
    assert row["summary"] == "Закупівля охолоджувальної рідини на 200 л."
    assert row["tender_period_end"] == "2026-05-20T12:00:00+00:00"


def test_get_deadline_reminders_within_window(storage: Storage) -> None:
    now = datetime.now(UTC)
    soon = (now + timedelta(days=2)).isoformat()
    far = (now + timedelta(days=10)).isoformat()
    past = (now - timedelta(days=1)).isoformat()

    storage.mark_reported(
        [
            SeenRecord("t-soon", "UA-soon", "coolant", "active", "Soon tender", "Summary A", soon),
            SeenRecord("t-far", "UA-far", "motor_oil", "active", "Far tender", "Summary B", far),
            SeenRecord(
                "t-past", "UA-past", "brake_fluid", "active", "Past tender", "Summary C", past
            ),
            SeenRecord("t-none", "UA-none", "washer_fluid", "active", "No deadline", "", ""),
        ]
    )

    reminders = storage.get_deadline_reminders(days_ahead=3)
    ids = {r["public_id"] for r in reminders}
    assert "UA-soon" in ids
    assert "UA-far" not in ids
    assert "UA-past" not in ids
    assert "UA-none" not in ids


def test_get_deadline_reminders_empty(storage: Storage) -> None:
    assert storage.get_deadline_reminders(days_ahead=3) == []


def test_clear_seen_deletes_all_seen_rows(storage: Storage) -> None:
    storage.mark_reported(
        [
            SeenRecord(tender_id="a", public_id="UA-a", category="coolant", status="active"),
            SeenRecord(tender_id="b", public_id="UA-b", category="motor_oil", status="active"),
        ]
    )
    assert storage.is_seen("a") is True
    assert storage.is_seen("b") is True

    deleted = storage.clear_seen()
    assert deleted == 2
    assert storage.is_seen("a") is False
    assert storage.is_seen("b") is False
