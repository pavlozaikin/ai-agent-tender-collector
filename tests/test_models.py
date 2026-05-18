"""Tests for the PROZORRO pydantic models."""

from __future__ import annotations

from tender_agent.prozorro.models import Classification, FeedPage, NextPage, Tender, TenderItem
from tender_agent.settings import Settings, get_settings
from tender_agent.state import ClassifiedTender
from tests.conftest import make_tender

_LABELS: dict[str, str] = {
    "coolant": "Охолоджувальні рідини / антифризи",
    "other": "Інша автохімія",
}


def test_web_url_uses_public_id() -> None:
    tender = make_tender(public_id="UA-2026-05-01-000123-a")
    assert tender.web_url == "https://prozorro.gov.ua/tender/UA-2026-05-01-000123-a"


def test_public_id_falls_back_to_raw_id() -> None:
    tender = Tender(id="rawhex", tenderID=None)
    assert tender.public_id == "rawhex"
    assert tender.web_url.endswith("/rawhex")


def test_classification_codes_collects_main_and_additional() -> None:
    item = TenderItem(
        classification=Classification(id="09210000-4"),
        additionalClassifications=[Classification(id="24951311-8")],
    )
    tender = Tender(id="x", items=[item])
    assert set(tender.classification_codes()) == {"09210000-4", "24951311-8"}


def test_searchable_text_includes_title_and_items() -> None:
    tender = make_tender(title="Антифриз", item_description="G12 червоний")
    text = tender.searchable_text()
    assert "Антифриз" in text
    assert "G12 червоний" in text


def test_extra_api_fields_are_ignored() -> None:
    tender = Tender.model_validate({"id": "x", "unexpectedField": {"deep": 1}, "title": "ok"})
    assert tender.title == "ok"


def test_next_page_offset_str_normalises_numeric() -> None:
    assert NextPage(offset=1714000000.5).offset_str == "1714000000.5"
    assert NextPage(offset="2026-05-01").offset_str == "2026-05-01"
    assert NextPage(offset=None).offset_str is None


def test_feed_page_parses_entries_with_status() -> None:
    page = FeedPage.model_validate(
        {
            "data": [{"id": "a", "status": "active.tendering"}],
            "next_page": {"offset": "cursor-1"},
        }
    )
    assert page.data[0].status == "active.tendering"
    assert page.next_page is not None
    assert page.next_page.offset_str == "cursor-1"


# ── settings.py line 85: sender_address with explicit smtp_from ──────────────


def test_settings_sender_address_uses_smtp_from(tmp_path: object) -> None:
    """Settings.sender_address returns smtp_from when set (line 85 branch)."""
    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        smtp_from="noreply@company.com",
        smtp_username="user@smtp.com",
    )
    assert s.sender_address == "noreply@company.com"


def test_settings_sender_address_falls_back_to_username() -> None:
    """Settings.sender_address falls back to smtp_username when smtp_from is empty."""
    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        smtp_from="",
        smtp_username="user@smtp.com",
    )
    assert s.sender_address == "user@smtp.com"


# ── state.py: category_label_for fallback for unknown category ────────────


def test_classified_tender_category_label_for_fallback() -> None:
    """ClassifiedTender.category_label_for uses the 'other' label for unknown categories."""
    ct = ClassifiedTender(
        tender=make_tender(),
        relevant=True,
        category="unknown_category",
        reason="test",
    )
    assert ct.category_label_for(_LABELS) == _LABELS["other"]


def test_classified_tender_category_label_for_known() -> None:
    """ClassifiedTender.category_label_for returns the matching label for a known category."""
    ct = ClassifiedTender(
        tender=make_tender(),
        relevant=True,
        category="coolant",
        reason="test",
    )
    assert ct.category_label_for(_LABELS) == _LABELS["coolant"]


# ── settings.py line 85: get_settings() singleton ───────────────────────────


def test_get_settings_returns_settings_instance() -> None:
    """Line 85: get_settings() constructs and returns a Settings instance."""
    result = get_settings()
    assert isinstance(result, Settings)
