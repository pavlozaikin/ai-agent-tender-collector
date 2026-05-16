"""PROZORRO public procurement API client.

Fetches the chronological tender feed and resolves full tender details
concurrently.  All HTTP I/O is done through a single :class:`httpx.AsyncClient`
that is shared across the lifetime of a :class:`ProzorroClient` instance.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from tenacity import (
    RetryError,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from tender_agent.logging import get_logger
from tender_agent.prozorro.models import FeedPage, Tender
from tender_agent.settings import Settings

_log = get_logger(__name__)

# Tender statuses that are worth fetching full details for.
ACTIONABLE_STATUSES: frozenset[str] = frozenset({"active.tendering"})

# Maximum feed pages consumed in a single crawl() call (runaway-loop guard).
_MAX_FEED_PAGES = 2_000

# Maximum concurrent detail-endpoint requests.
_DETAIL_CONCURRENCY = 10


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CrawlResult:
    """Result of a single :meth:`ProzorroClient.crawl` run."""

    tenders: list[Tender] = field(default_factory=list)
    """Full tender objects whose status is in :data:`ACTIONABLE_STATUSES`."""

    next_offset: str = ""
    """Feed cursor to persist; pass as ``offset`` on the next run."""


class ProzorroError(Exception):
    """Raised when an API call fails after all retries are exhausted."""


# ---------------------------------------------------------------------------
# Retry predicate
# ---------------------------------------------------------------------------


def _should_retry(exc: BaseException) -> bool:
    """Return True for transport errors, timeouts, and 429 / 5xx responses."""
    if isinstance(exc, httpx.TransportError | httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class ProzorroClient:
    """Async client for the PROZORRO openprocurement API.

    Use as an async context manager so the underlying :class:`httpx.AsyncClient`
    is properly closed::

        async with ProzorroClient(settings) as client:
            result = await client.crawl(offset=None)
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._http = httpx.AsyncClient(
            base_url=settings.prozorro_api_base,
            timeout=settings.request_timeout_seconds,
            headers={"Accept": "application/json"},
        )

    async def __aenter__(self) -> ProzorroClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def crawl(self, offset: str | None) -> CrawlResult:
        """Crawl the PROZORRO feed from *offset* (or from *lookback_days* ago).

        Pages through the chronological ``/tenders`` feed, keeps only entries
        with an :data:`ACTIONABLE_STATUSES` status, fetches their full details
        concurrently, and returns a :class:`CrawlResult`.

        :param offset:
            Opaque feed cursor returned by a previous :class:`CrawlResult`.
            Pass ``None`` on the first run to start from
            ``now - crawl_lookback_days``.
        :returns: Collected tenders and the next cursor to persist.
        """
        start_offset = offset or self._lookback_offset()
        current_offset: str = start_offset
        last_next_offset: str = start_offset

        all_actionable_ids: list[str] = []
        pages_fetched = 0

        _log.info("prozorro_crawl_start", offset=current_offset)

        while True:
            if pages_fetched >= _MAX_FEED_PAGES:
                _log.warning(
                    "prozorro_feed_page_cap_reached",
                    pages=pages_fetched,
                    cap=_MAX_FEED_PAGES,
                )
                break

            page = await self._fetch_feed_page(current_offset)
            pages_fetched += 1

            if not page.data:
                _log.info("prozorro_feed_empty_page", pages_fetched=pages_fetched)
                break

            next_offset_str = page.next_page.offset_str if page.next_page else None

            actionable = [e for e in page.data if e.status in ACTIONABLE_STATUSES]
            all_actionable_ids.extend(e.id for e in actionable)

            _log.info(
                "prozorro_feed_page",
                page=pages_fetched,
                entries=len(page.data),
                actionable=len(actionable),
                next_offset=next_offset_str,
            )

            # Advance the cursor.
            if next_offset_str is None or next_offset_str == current_offset:
                # No progress — we have caught up to the live edge.
                if next_offset_str:
                    last_next_offset = next_offset_str
                break

            last_next_offset = next_offset_str
            current_offset = next_offset_str

        # Fetch full details concurrently, then re-check status to guard against
        # race conditions where a tender moved out of ACTIONABLE_STATUSES between
        # the feed scan and the detail fetch.
        tenders = [
            t
            for t in await self._fetch_details(all_actionable_ids)
            if t.status in ACTIONABLE_STATUSES
        ]

        _log.info(
            "prozorro_crawl_done",
            pages=pages_fetched,
            actionable_ids=len(all_actionable_ids),
            tenders_fetched=len(tenders),
            next_offset=last_next_offset,
        )

        return CrawlResult(tenders=tenders, next_offset=last_next_offset)

    async def fetch_tender(self, tender_id: str) -> Tender | None:
        """Fetch the full detail for *tender_id*.

        :param tender_id: The PROZORRO tender UUID.
        :returns: A parsed :class:`~tender_agent.prozorro.models.Tender`, or
            ``None`` when the API returns HTTP 404.
        :raises ProzorroError: When retries are exhausted for non-404 errors.
        """
        try:
            data = await self._get_with_retry(f"/tenders/{tender_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise ProzorroError(f"HTTP {exc.response.status_code} for {tender_id}") from exc
        except (RetryError, httpx.HTTPError) as exc:
            raise ProzorroError(f"Failed to fetch tender {tender_id}") from exc

        return Tender.model_validate(data["data"])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _lookback_offset(self) -> str:
        """ISO-8601 UTC datetime string for *now* minus *crawl_lookback_days*."""
        dt = datetime.now(UTC) - timedelta(days=self._settings.crawl_lookback_days)
        return dt.isoformat()

    async def _fetch_feed_page(self, offset: str) -> FeedPage:
        """Fetch one page of the ``/tenders`` feed at *offset*."""
        try:
            data = await self._get_with_retry(
                "/tenders",
                params={"offset": offset, "opt_fields": "status"},
            )
        except (RetryError, httpx.HTTPError) as exc:
            raise ProzorroError(f"Failed to fetch feed page at offset {offset!r}") from exc

        return FeedPage.model_validate(data)

    async def _fetch_details(self, tender_ids: list[str]) -> list[Tender]:
        """Fetch full details for *tender_ids* with bounded concurrency."""
        if not tender_ids:
            return []

        sem = asyncio.Semaphore(_DETAIL_CONCURRENCY)
        results: list[Tender] = []
        lock = asyncio.Lock()

        async def _fetch_one(tid: str) -> None:
            async with sem:
                try:
                    tender = await self.fetch_tender(tid)
                except ProzorroError as exc:
                    _log.warning("prozorro_detail_fetch_failed", tender_id=tid, error=str(exc))
                    return
                if tender is None:
                    _log.warning("prozorro_tender_not_found", tender_id=tid)
                    return
                async with lock:
                    results.append(tender)

        await asyncio.gather(*(_fetch_one(tid) for tid in tender_ids))
        return results

    async def _get_with_retry(
        self,
        path: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, object]:
        """GET *path* with exponential-backoff retry.

        Retries on transport errors, timeouts, 429, and 5xx responses.
        A 404 is **not** retried — callers handle it explicitly.

        :returns: Parsed JSON body as a dict.
        :raises httpx.HTTPStatusError: On 4xx / 5xx after all retries.
        :raises RetryError: When retries are exhausted on retriable errors.
        """
        max_attempts = self._settings.max_retries

        @retry(
            retry=retry_if_exception(_should_retry),
            wait=wait_exponential(multiplier=1, min=1, max=30),
            stop=stop_after_attempt(max_attempts),
            reraise=True,
        )
        async def _do_get() -> dict[str, object]:
            response = await self._http.get(path, params=params)
            if response.status_code == 404:
                # Raise immediately so the caller can detect 404 without retry.
                response.raise_for_status()
            if _should_retry(
                httpx.HTTPStatusError(
                    message="",
                    request=response.request,
                    response=response,
                )
            ):
                response.raise_for_status()
            response.raise_for_status()
            result: dict[str, object] = response.json()
            return result

        return await _do_get()
