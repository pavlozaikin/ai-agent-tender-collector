"""The broad, deterministic prefilter (CPV groups + keywords).

This stage is intentionally over-inclusive: it only narrows the PROZORRO
firehose cheaply before the LLM makes the final relevance decision. Missing a
relevant tender here is far worse than passing an irrelevant one through.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from tender_agent.prozorro.models import Tender


class FiltersError(Exception):
    """Raised when the prefilter configuration cannot be loaded."""


@dataclass(slots=True, frozen=True)
class Filters:
    """Broad CPV prefixes and keywords for the prefilter stage."""

    cpv_prefixes: tuple[str, ...]
    keywords: tuple[str, ...]

    def matches(self, tender: Tender) -> bool:
        """Return True if the tender passes the broad net (CPV or keyword)."""
        for code in tender.classification_codes():
            if any(code.startswith(prefix) for prefix in self.cpv_prefixes):
                return True
        text = tender.searchable_text().lower()
        return any(keyword in text for keyword in self.keywords)


def load_filters(path: Path) -> Filters:
    """Load the prefilter configuration from a YAML file."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FiltersError(f"Filters file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise FiltersError(f"Invalid YAML in filters file {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise FiltersError(f"Filters file {path} must contain a YAML mapping")

    cpv_prefixes = tuple(str(item) for item in raw.get("cpv_prefixes") or [])
    keywords = tuple(str(item).lower() for item in raw.get("keywords") or [])
    if not cpv_prefixes and not keywords:
        raise FiltersError(f"Filters file {path} defines no cpv_prefixes or keywords")
    return Filters(cpv_prefixes=cpv_prefixes, keywords=keywords)
