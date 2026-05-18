"""Tests for the broad prefilter (CPV groups + keywords)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tender_agent.filters import CategoryConfig, DomainConfig, Filters, FiltersError, load_filters
from tests.conftest import make_tender

_MINIMAL_YAML = (
    "domain:\n"
    "  name_uk: тест\n"
    "  classify_system_uk: Класифікатор.\n"
    "  report_system_uk: Асистент.\n"
    "  relevant_field_description: True if relevant.\n"
    "categories:\n"
    "  coolant:\n"
    "    label_uk: Охолоджувальні рідини\n"
    "    cpv_prefixes:\n"
    "      - '2495'\n"
    "    keywords:\n"
    "      - антифриз\n"
    "  motor_oil:\n"
    "    label_uk: Моторні оливи\n"
    "    cpv_prefixes:\n"
    "      - '0921'\n"
    "    keywords:\n"
    "      - олива\n"
    "  brake_fluid:\n"
    "    label_uk: Гальмівні рідини\n"
    "    cpv_prefixes: []\n"
    "    keywords:\n"
    "      - гальмівн\n"
    "  other:\n"
    "    label_uk: Інша\n"
    "    cpv_prefixes: []\n"
    "    keywords: []\n"
)


def test_matches_by_cpv_prefix(filters_file: Path) -> None:
    filters = load_filters(filters_file)
    tender = make_tender(cpv="09211000-1", title="нейтральна назва", item_description="товар")
    assert filters.matches(tender) is True


def test_matches_by_keyword_when_cpv_absent(filters_file: Path) -> None:
    filters = load_filters(filters_file)
    tender = make_tender(cpv=None, title="Постачання антифризу", item_description="рідина")
    assert filters.matches(tender) is True


def test_no_match_for_unrelated_tender(filters_file: Path) -> None:
    filters = load_filters(filters_file)
    tender = make_tender(cpv="03000000-1", title="Закупівля картоплі", item_description="овочі")
    assert filters.matches(tender) is False


def test_load_filters_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FiltersError, match="not found"):
        load_filters(tmp_path / "nope.yaml")


def test_load_filters_rejects_empty_config(tmp_path: Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text(
        "domain:\n"
        "  name_uk: тест\n"
        "  classify_system_uk: x\n"
        "  report_system_uk: x\n"
        "  relevant_field_description: x\n"
        "categories:\n"
        "  other:\n"
        "    label_uk: Інша\n"
        "    cpv_prefixes: []\n"
        "    keywords: []\n",
        encoding="utf-8",
    )
    with pytest.raises(FiltersError, match="no cpv_prefixes or keywords"):
        load_filters(path)


def test_load_filters_rejects_non_mapping(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(FiltersError, match="mapping"):
        load_filters(path)


def test_load_filters_rejects_missing_domain(tmp_path: Path) -> None:
    path = tmp_path / "no_domain.yaml"
    path.write_text(
        "categories:\n"
        "  coolant:\n"
        "    label_uk: Охолоджувальні рідини\n"
        "    cpv_prefixes: ['0921']\n"
        "    keywords: []\n",
        encoding="utf-8",
    )
    with pytest.raises(FiltersError, match="domain"):
        load_filters(path)


def test_load_filters_rejects_missing_categories(tmp_path: Path) -> None:
    path = tmp_path / "no_cats.yaml"
    path.write_text(
        "domain:\n"
        "  name_uk: тест\n"
        "  classify_system_uk: x\n"
        "  report_system_uk: x\n"
        "  relevant_field_description: x\n",
        encoding="utf-8",
    )
    with pytest.raises(FiltersError, match="categories"):
        load_filters(path)


def test_keywords_are_lowercased() -> None:
    domain = DomainConfig(
        name_uk="тест",
        classify_system_uk="x",
        report_system_uk="x",
        relevant_field_description="x",
    )
    cat = CategoryConfig(cpv_prefixes=(), keywords=("антифриз",), label_uk="Охолоджувальні")
    filters = Filters(
        domain=domain,
        category_labels={"coolant": "Охолоджувальні", "other": "Інша"},
        categories=["coolant", "other"],
        _category_configs={
            "coolant": cat,
            "other": CategoryConfig(label_uk="Інша", cpv_prefixes=(), keywords=()),
        },
    )
    tender = make_tender(cpv=None, title="АНТИФРИЗ ОПТОМ", item_description="x")
    assert filters.matches(tender) is True


def test_load_filters_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("key: [\n  unclosed\n", encoding="utf-8")
    with pytest.raises(FiltersError, match="Invalid YAML"):
        load_filters(path)


def test_load_filters_exposes_domain_and_categories(tmp_path: Path) -> None:
    path = tmp_path / "filters.yaml"
    path.write_text(_MINIMAL_YAML, encoding="utf-8")
    filters = load_filters(path)
    assert filters.domain.name_uk == "тест"
    assert filters.domain.classify_system_uk == "Класифікатор."
    assert filters.domain.report_system_uk == "Асистент."
    assert filters.domain.relevant_field_description == "True if relevant."
    assert filters.category_labels["coolant"] == "Охолоджувальні рідини"
    assert "coolant" in filters.categories
    assert "other" in filters.categories
    assert filters.categories[-1] == "other"


def test_load_filters_category_order_preserved(tmp_path: Path) -> None:
    path = tmp_path / "filters.yaml"
    path.write_text(_MINIMAL_YAML, encoding="utf-8")
    filters = load_filters(path)
    assert filters.categories == ["coolant", "motor_oil", "brake_fluid", "other"]


_OFFICE_YAML = (
    "domain:\n"
    "  name_uk: канцелярія\n"
    "  classify_system_uk: Ти класифікатор канцелярських товарів.\n"
    "  report_system_uk: Ти асистент з канцелярії.\n"
    "  relevant_field_description: True only if the tender procures office supplies.\n"
    "categories:\n"
    "  paper:\n"
    "    label_uk: Папір\n"
    "    cpv_prefixes:\n"
    "      - '3019'\n"
    "    keywords:\n"
    "      - папір\n"
    "      - папір а4\n"
    "  pens:\n"
    "    label_uk: Ручки та олівці\n"
    "    cpv_prefixes: []\n"
    "    keywords:\n"
    "      - ручка\n"
    "      - олівець\n"
    "  other:\n"
    "    label_uk: Інша канцелярія\n"
    "    cpv_prefixes: []\n"
    "    keywords: []\n"
)


def test_custom_office_supplies_config_loads(tmp_path: Path) -> None:
    """A completely non-automotive config (office supplies) loads and works correctly."""
    path = tmp_path / "office.yaml"
    path.write_text(_OFFICE_YAML, encoding="utf-8")
    filters = load_filters(path)

    assert filters.domain.name_uk == "канцелярія"
    assert filters.domain.classify_system_uk == "Ти класифікатор канцелярських товарів."
    assert filters.domain.report_system_uk == "Ти асистент з канцелярії."
    assert (
        filters.domain.relevant_field_description
        == "True only if the tender procures office supplies."
    )
    assert filters.categories == ["paper", "pens", "other"]
    assert filters.category_labels["paper"] == "Папір"
    assert filters.category_labels["pens"] == "Ручки та олівці"
    assert filters.category_labels["other"] == "Інша канцелярія"


def test_custom_config_llm_client_uses_domain_prompts(tmp_path: Path) -> None:
    """LLMClient built with a custom (office supplies) Filters uses its domain prompts."""
    from unittest.mock import MagicMock

    import tender_agent.llm as llm_module
    from tender_agent.llm import LLMClient, _make_tender_relevance_schema
    from tender_agent.settings import Settings
    from tender_agent.storage import Storage

    path = tmp_path / "office.yaml"
    path.write_text(_OFFICE_YAML, encoding="utf-8")
    filters = load_filters(path)

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        data_dir=tmp_path / "data",
        filters_path=path,
        recipients_path=tmp_path / "recipients.yaml",
        openai_api_key="test-key",
    )
    db_path = settings.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)

    orig_build = llm_module._build_model
    llm_module._build_model = lambda _spec: MagicMock()  # type: ignore[assignment]
    try:
        with Storage(settings.db_path) as storage:
            client = LLMClient(settings, storage, filters)
    finally:
        llm_module._build_model = orig_build

    assert client._classify_system == "Ти класифікатор канцелярських товарів."
    assert client._report_system == "Ти асистент з канцелярії."

    schema = _make_tender_relevance_schema(
        filters.domain.relevant_field_description, filters.categories
    )
    category_field = schema.model_fields["category"]
    assert "paper" in (category_field.description or "")
    assert "pens" in (category_field.description or "")

    relevant_field = schema.model_fields["relevant"]
    assert "office supplies" in (relevant_field.description or "")
