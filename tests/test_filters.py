"""Tests for the broad prefilter (CPV groups + keywords)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tender_agent.filters import Filters, FiltersError, load_filters
from tests.conftest import make_tender


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
    path.write_text("cpv_prefixes: []\nkeywords: []\n", encoding="utf-8")
    with pytest.raises(FiltersError, match="no cpv_prefixes or keywords"):
        load_filters(path)


def test_load_filters_rejects_non_mapping(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(FiltersError, match="mapping"):
        load_filters(path)


def test_keywords_are_lowercased() -> None:
    filters = Filters(cpv_prefixes=(), keywords=("антифриз",))
    tender = make_tender(cpv=None, title="АНТИФРИЗ ОПТОМ", item_description="x")
    assert filters.matches(tender) is True


def test_load_filters_invalid_yaml(tmp_path: Path) -> None:
    """Lines 44-45: yaml.YAMLError branch."""
    path = tmp_path / "bad.yaml"
    path.write_text("key: [\n  unclosed\n", encoding="utf-8")
    with pytest.raises(FiltersError, match="Invalid YAML"):
        load_filters(path)
