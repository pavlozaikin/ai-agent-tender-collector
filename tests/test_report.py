"""Tests for HTML report rendering."""

from __future__ import annotations

from datetime import datetime

from tender_agent.emailer.report import _format_end_date, _time_greeting, render_report
from tender_agent.state import CATEGORY_LABELS, ClassifiedTender
from tests.conftest import make_tender


def _classified(category: str, summary: str, public_id: str) -> ClassifiedTender:
    return ClassifiedTender(
        tender=make_tender(public_id=public_id),
        relevant=True,
        category=category,
        reason="підходить",
        summary=summary,
    )


def test_subject_has_count_and_date() -> None:
    items = [_classified("coolant", "Антифриз 200 л.", "UA-1")]
    report = render_report(items, datetime(2026, 5, 15, 9, 0))
    assert "1 нових" in report.subject
    assert "15.05.2026" in report.subject


def test_html_contains_link_label_and_summary() -> None:
    items = [_classified("coolant", "Закупівля антифризу 200 л.", "UA-2026-05-01-000777-a")]
    report = render_report(items, datetime(2026, 5, 15, 9, 0))
    assert "https://prozorro.gov.ua/tender/UA-2026-05-01-000777-a" in report.html
    assert "Закупівля антифризу 200 л." in report.html
    assert CATEGORY_LABELS["coolant"] in report.html


def test_groups_multiple_categories() -> None:
    items = [
        _classified("coolant", "антифриз", "UA-1"),
        _classified("brake_fluid", "гальмівна рідина", "UA-2"),
    ]
    report = render_report(items, datetime(2026, 5, 15, 9, 0))
    assert CATEGORY_LABELS["coolant"] in report.html
    assert CATEGORY_LABELS["brake_fluid"] in report.html


def test_empty_report_still_renders() -> None:
    report = render_report([], datetime(2026, 5, 15, 9, 0))
    assert "0 нових" in report.subject
    assert report.html  # non-empty HTML document


# ── _format_end_date ────────────────────────────────────────────────────────


def test_format_end_date_valid_iso() -> None:
    assert _format_end_date("2026-05-20T14:30:00+03:00") == "20.05.2026 14:30"


def test_format_end_date_bad_string_returns_raw() -> None:
    """Lines 21-25: ValueError/TypeError branch — raw string is returned as-is."""
    assert _format_end_date("not-a-date") == "not-a-date"


def test_format_end_date_none_returns_none() -> None:
    """TypeError branch for non-string input."""
    result = _format_end_date(None)  # type: ignore[arg-type]
    assert result is None


# ── _time_greeting ──────────────────────────────────────────────────────────


def test_time_greeting_morning() -> None:
    assert _time_greeting(8) == "ранку"


def test_time_greeting_afternoon() -> None:
    """Line 46: hour 12-17 branch."""
    assert _time_greeting(14) == "дня"


def test_time_greeting_evening() -> None:
    """Line 46: hour >= 18 branch."""
    assert _time_greeting(20) == "вечора"


# ── reminders block in summary ──────────────────────────────────────────────


def test_summary_includes_reminders() -> None:
    """Lines 68-74: _render_summary emits the reminders block."""
    items = [_classified("coolant", "Антифриз.", "UA-1")]
    reminders = [
        {
            "public_id": "UA-R1",
            "title": "Тендер з нагадуванням",
            "tender_period_end": "2026-05-18T10:00:00+03:00",
        },
    ]
    report = render_report(items, datetime(2026, 5, 15, 9, 0), reminders=reminders)
    assert "Дедлайн подачі" in report.summary
    assert "Тендер з нагадуванням" in report.summary


def test_summary_afternoon_greeting() -> None:
    """Afternoon hour goes through the 'дня' branch."""
    report = render_report([], datetime(2026, 5, 15, 15, 0))
    assert "Доброго дня" in report.summary


def test_summary_evening_greeting() -> None:
    """Evening hour goes through the 'вечора' branch."""
    report = render_report([], datetime(2026, 5, 15, 19, 0))
    assert "Доброго вечора" in report.summary
