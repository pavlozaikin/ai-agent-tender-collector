"""Pydantic models for the subset of the PROZORRO tender schema we use.

The PROZORRO API returns large, deeply-nested objects with many optional
fields. These models capture only what the pipeline needs and ignore the
rest (``extra="ignore"``), so schema additions upstream never break parsing.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

_PUBLIC_TENDER_URL = "https://prozorro.gov.ua/tender/{ident}"


class _Lenient(BaseModel):
    """Base model that tolerates unknown fields from the API."""

    model_config = ConfigDict(extra="ignore")


class Classification(_Lenient):
    """A ДК 021:2015 (CPV) or other classification code."""

    scheme: str | None = None
    id: str | None = None
    description: str | None = None


class Unit(_Lenient):
    name: str | None = None
    code: str | None = None


class Value(_Lenient):
    amount: float | None = None
    currency: str | None = None
    valueAddedTaxIncluded: bool | None = None


class TenderItem(_Lenient):
    id: str | None = None
    description: str | None = None
    quantity: float | None = None
    unit: Unit | None = None
    classification: Classification | None = None
    additionalClassifications: list[Classification] = Field(default_factory=list)
    deliveryDate: Period | None = None


class ProcuringEntity(_Lenient):
    name: str | None = None


class Period(_Lenient):
    startDate: str | None = None
    endDate: str | None = None


class Tender(_Lenient):
    """A PROZORRO tender (procurement)."""

    id: str
    tenderID: str | None = None
    dateModified: str | None = None
    status: str | None = None
    title: str | None = None
    description: str | None = None
    procurementMethodType: str | None = None
    value: Value | None = None
    procuringEntity: ProcuringEntity | None = None
    items: list[TenderItem] = Field(default_factory=list)
    tenderPeriod: Period | None = None

    @property
    def public_id(self) -> str:
        """Human-facing identifier (``tenderID``), falling back to the raw id."""
        return self.tenderID or self.id

    @property
    def web_url(self) -> str:
        """Direct, human-verifiable link to the tender on prozorro.gov.ua."""
        return _PUBLIC_TENDER_URL.format(ident=self.public_id)

    def delivery_window(self) -> tuple[str | None, str | None]:
        """Earliest startDate and latest endDate across all items' deliveryDate."""
        starts = [
            i.deliveryDate.startDate
            for i in self.items
            if i.deliveryDate and i.deliveryDate.startDate
        ]
        ends = [
            i.deliveryDate.endDate for i in self.items if i.deliveryDate and i.deliveryDate.endDate
        ]
        return (min(starts) if starts else None, max(ends) if ends else None)

    def classification_codes(self) -> list[str]:
        """All ДК 021:2015 / CPV codes attached to the tender's items."""
        codes: list[str] = []
        for item in self.items:
            if item.classification and item.classification.id:
                codes.append(item.classification.id)
            codes.extend(ac.id for ac in item.additionalClassifications if ac.id)
        return codes

    def searchable_text(self) -> str:
        """Concatenated free text (title, description, item descriptions)."""
        parts: list[str] = [self.title or "", self.description or ""]
        for item in self.items:
            parts.append(item.description or "")
            if item.classification and item.classification.description:
                parts.append(item.classification.description)
        return " ".join(p for p in parts if p)


class FeedEntry(_Lenient):
    """A single entry in the chronological ``/tenders`` feed.

    ``status`` is only populated when the feed request includes
    ``opt_fields=status``; it lets the crawler skip detail fetches for
    tenders that are not open for bidding.
    """

    id: str
    dateModified: str | None = None
    status: str | None = None


class NextPage(_Lenient):
    # The feed offset cursor may be an ISO date string or a numeric timestamp,
    # depending on API version; normalise it to a string.
    offset: str | float | int | None = None
    uri: str | None = None

    @property
    def offset_str(self) -> str | None:
        """The offset cursor as a string, or None when absent."""
        if self.offset is None:
            return None
        return str(self.offset)


class FeedPage(_Lenient):
    """One page of the ``/tenders`` feed."""

    data: list[FeedEntry] = Field(default_factory=list)
    next_page: NextPage | None = None
