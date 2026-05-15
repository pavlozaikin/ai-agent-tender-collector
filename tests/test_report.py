"""Tests for HTML report rendering."""

from __future__ import annotations

from datetime import datetime

from tender_agent.emailer.report import render_report
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
