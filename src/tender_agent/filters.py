"""The broad, deterministic prefilter (CPV groups + keywords) and domain config.

This stage is intentionally over-inclusive: it only narrows the PROZORRO
firehose cheaply before the LLM makes the final relevance decision. Missing a
relevant tender here is far worse than passing an irrelevant one through.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from tender_agent.logging import get_logger
from tender_agent.prozorro.models import Tender

_log = get_logger(__name__)


class FiltersError(Exception):
    """Raised when the prefilter configuration cannot be loaded."""


@dataclass(slots=True, frozen=True)
class DomainConfig:
    """Domain-level configuration driving LLM prompts and report labels."""

    name_uk: str
    classify_system_uk: str
    report_system_uk: str
    relevant_field_description: str


@dataclass(slots=True, frozen=True)
class CategoryConfig:
    """Per-category CPV prefixes and keywords."""

    label_uk: str
    cpv_prefixes: tuple[str, ...]
    keywords: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class Filters:
    """Parsed filters.yaml: domain config, per-category definitions, and matching logic."""

    domain: DomainConfig
    category_labels: dict[str, str]
    categories: list[str]
    _category_configs: dict[str, CategoryConfig]

    def matches(self, tender: Tender) -> bool:
        """Return True if the tender passes the broad net (CPV or keyword)."""
        all_prefixes = tuple(
            p
            for key, cfg in self._category_configs.items()
            if key != "other"
            for p in cfg.cpv_prefixes
        )
        all_keywords = tuple(
            kw
            for key, cfg in self._category_configs.items()
            if key != "other"
            for kw in cfg.keywords
        )
        for code in tender.classification_codes():
            if any(code.startswith(prefix) for prefix in all_prefixes):
                return True
        text = tender.searchable_text().lower()
        return any(keyword in text for keyword in all_keywords)


def load_filters(path: Path) -> Filters:
    """Load the prefilter and domain configuration from a YAML file."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FiltersError(f"Filters file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise FiltersError(f"Invalid YAML in filters file {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise FiltersError(f"Filters file {path} must contain a YAML mapping")

    raw_domain = raw.get("domain")
    if not isinstance(raw_domain, dict):
        raise FiltersError(f"Filters file {path} must contain a 'domain' mapping")

    domain = DomainConfig(
        name_uk=str(raw_domain.get("name_uk", "")),
        classify_system_uk=str(raw_domain.get("classify_system_uk", "")).rstrip("\n"),
        report_system_uk=str(raw_domain.get("report_system_uk", "")).rstrip("\n"),
        relevant_field_description=str(raw_domain.get("relevant_field_description", "")),
    )

    raw_categories = raw.get("categories")
    if not isinstance(raw_categories, dict) or not raw_categories:
        raise FiltersError(f"Filters file {path} must contain a non-empty 'categories' mapping")

    category_configs: dict[str, CategoryConfig] = {}
    for key, val in raw_categories.items():
        if not isinstance(val, dict):
            raise FiltersError(f"Category {key!r} in {path} must be a YAML mapping")
        category_configs[key] = CategoryConfig(
            label_uk=str(val.get("label_uk", key)),
            cpv_prefixes=tuple(str(p) for p in val.get("cpv_prefixes") or []),
            keywords=tuple(str(kw).lower() for kw in val.get("keywords") or []),
        )

    non_other_prefixes = sum(
        len(cfg.cpv_prefixes) for k, cfg in category_configs.items() if k != "other"
    )
    non_other_keywords = sum(
        len(cfg.keywords) for k, cfg in category_configs.items() if k != "other"
    )
    if non_other_prefixes == 0 and non_other_keywords == 0:
        raise FiltersError(f"Filters file {path} defines no cpv_prefixes or keywords")

    category_labels = {key: cfg.label_uk for key, cfg in category_configs.items()}
    categories = list(category_configs.keys())

    filters = Filters(
        domain=domain,
        category_labels=category_labels,
        categories=categories,
        _category_configs=category_configs,
    )
    _log.info(
        "filters_loaded",
        categories=len(category_configs),
        cpv_groups=non_other_prefixes,
        keywords=non_other_keywords,
    )
    return filters
