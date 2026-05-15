"""Shared pytest fixtures and factories."""

from __future__ import annotations

from pathlib import Path

import pytest

from tender_agent.prozorro.models import Classification, Tender, TenderItem, Unit
from tender_agent.settings import Settings
from tender_agent.storage import Storage


def make_tender(
    *,
    tender_id: str = "abc123",
    public_id: str = "UA-2026-05-01-000001-a",
    status: str = "active.tendering",
    title: str = "Закупівля антифризу для автопарку",
    description: str | None = None,
    cpv: str | None = "09210000-4",
    item_description: str = "Антифриз G12 червоний, 200 л",
) -> Tender:
    """Build a Tender for tests."""
    classification = Classification(scheme="ДК021", id=cpv, description="Мастильні засоби")
    item = TenderItem(
        id="item-1",
        description=item_description,
        quantity=200,
        unit=Unit(name="л"),
        classification=classification if cpv else None,
    )
    return Tender(
        id=tender_id,
        tenderID=public_id,
        dateModified="2026-05-01T12:00:00+03:00",
        status=status,
        title=title,
        description=description,
        items=[item],
    )


@pytest.fixture
def filters_file(tmp_path: Path) -> Path:
    """A minimal filters.yaml written to a temp directory."""
    path = tmp_path / "filters.yaml"
    path.write_text(
        "cpv_prefixes:\n  - '0921'\n  - '2495'\nkeywords:\n  - антифриз\n  - олива\n  - гальмівн\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def settings(tmp_path: Path, filters_file: Path) -> Settings:
    """Hermetic Settings pointing at temp paths (no .env, no real keys)."""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        data_dir=tmp_path / "data",
        filters_path=filters_file,
        recipients_path=tmp_path / "recipients.yaml",
        openai_api_key="test-key",
        smtp_username="agent@example.com",
        smtp_password="secret",
    )


@pytest.fixture
def storage(settings: Settings) -> Storage:
    """An open Storage on a temp database."""
    store = Storage(settings.db_path)
    yield store
    store.close()
