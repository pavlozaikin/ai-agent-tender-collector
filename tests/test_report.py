"""Tests for HTML report rendering."""

from __future__ import annotations

from datetime import datetime

from tender_agent.emailer.report import _format_end_date, _time_greeting, render_report
from tender_agent.state import ClassifiedTender
from tests.conftest import make_tender

_DEFAULT_LABELS: dict[str, str] = {
    "coolant": "Охолоджувальні рідини / антифризи",
    "brake_fluid": "Гальмівні рідини",
    "washer_fluid": "Рідини для омивача скла",
    "motor_oil": "Моторні оливи",
    "industrial_oil": "Індустріальні оливи",
    "base_oil": "Базові оливи",
    "other": "Інша автохімія",
}


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
    report = render_report(items, datetime(2026, 5, 15, 9, 0), category_labels=_DEFAULT_LABELS)
    assert "1 нових" in report.subject
    assert "15.05.2026" in report.subject


def test_html_contains_link_label_and_summary() -> None:
    items = [_classified("coolant", "Закупівля антифризу 200 л.", "UA-2026-05-01-000777-a")]
    report = render_report(items, datetime(2026, 5, 15, 9, 0), category_labels=_DEFAULT_LABELS)
    assert "https://prozorro.gov.ua/tender/UA-2026-05-01-000777-a" in report.html
    assert "Закупівля антифризу 200 л." in report.html
    assert _DEFAULT_LABELS["coolant"] in report.html


def test_groups_multiple_categories() -> None:
    items = [
        _classified("coolant", "антифриз", "UA-1"),
        _classified("brake_fluid", "гальмівна рідина", "UA-2"),
    ]
    report = render_report(items, datetime(2026, 5, 15, 9, 0), category_labels=_DEFAULT_LABELS)
    assert _DEFAULT_LABELS["coolant"] in report.html
    assert _DEFAULT_LABELS["brake_fluid"] in report.html


def test_empty_report_still_renders() -> None:
    report = render_report([], datetime(2026, 5, 15, 9, 0), category_labels=_DEFAULT_LABELS)
    assert "0 нових" in report.subject
    assert report.html  # non-empty HTML document


def test_domain_name_appears_in_html_and_title() -> None:
    report = render_report(
        [],
        datetime(2026, 5, 15, 9, 0),
        category_labels=_DEFAULT_LABELS,
        domain_name="автохімія",
    )
    assert "автохімія" in report.html


def test_custom_domain_name_used_in_report() -> None:
    custom_labels = {"stationery": "Канцелярія", "other": "Інше"}
    items = [_classified("stationery", "Папір А4.", "UA-1")]
    report = render_report(
        items,
        datetime(2026, 5, 15, 9, 0),
        category_labels=custom_labels,
        domain_name="канцелярії",
    )
    assert "канцелярії" in report.summary
    assert "канцелярії" in report.html
    assert "Канцелярія" in report.html


# ── _format_end_date ────────────────────────────────────────────────────────


def test_format_end_date_valid_iso() -> None:
    assert _format_end_date("2026-05-20T14:30:00+03:00") == "20.05.2026 14:30"


def test_format_end_date_bad_string_returns_raw() -> None:
    assert _format_end_date("not-a-date") == "not-a-date"


def test_format_end_date_none_returns_none() -> None:
    result = _format_end_date(None)  # type: ignore[arg-type]
    assert result is None


# ── _time_greeting ──────────────────────────────────────────────────────────


def test_time_greeting_morning() -> None:
    assert _time_greeting(8) == "ранку"


def test_time_greeting_afternoon() -> None:
    assert _time_greeting(14) == "дня"


def test_time_greeting_evening() -> None:
    assert _time_greeting(20) == "вечора"


# ── reminders block in summary ──────────────────────────────────────────────


def test_summary_includes_reminders() -> None:
    items = [_classified("coolant", "Антифриз.", "UA-1")]
    reminders = [
        {
            "public_id": "UA-R1",
            "title": "Тендер з нагадуванням",
            "tender_period_end": "2026-05-18T10:00:00+03:00",
        },
    ]
    report = render_report(
        items,
        datetime(2026, 5, 15, 9, 0),
        reminders=reminders,
        category_labels=_DEFAULT_LABELS,
    )
    assert "Дедлайн подачі" in report.summary
    assert "Тендер з нагадуванням" in report.summary


def test_summary_afternoon_greeting() -> None:
    report = render_report([], datetime(2026, 5, 15, 15, 0), category_labels=_DEFAULT_LABELS)
    assert "Доброго дня" in report.summary


def test_summary_evening_greeting() -> None:
    report = render_report([], datetime(2026, 5, 15, 19, 0), category_labels=_DEFAULT_LABELS)
    assert "Доброго вечора" in report.summary
