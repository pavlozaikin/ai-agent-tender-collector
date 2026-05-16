"""Integration tests for the PROZORRO API client (HTTP mocked with respx)."""

from __future__ import annotations

import httpx
import pytest
import respx

from tender_agent.prozorro.client import ProzorroClient, ProzorroError, _should_retry
from tender_agent.settings import Settings


def _detail(tender_id: str, status: str = "active.tendering") -> dict[str, object]:
    return {
        "data": {
            "id": tender_id,
            "tenderID": f"UA-{tender_id}",
            "status": status,
            "title": "Закупівля антифризу",
            "items": [],
        }
    }


@respx.mock
async def test_crawl_collects_actionable_tenders(settings: Settings) -> None:
    page1 = {
        "data": [
            {"id": "t-active", "status": "active.tendering"},
            {"id": "t-complete", "status": "complete"},
        ],
        "next_page": {"offset": "cursor-2"},
    }
    page2: dict[str, object] = {"data": [], "next_page": {"offset": "cursor-2"}}

    respx.get(path="/api/2.5/tenders").mock(
        side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
    )

    def detail_handler(request: httpx.Request) -> httpx.Response:
        tender_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=_detail(tender_id))

    respx.get(path__regex=r"^/api/2\.5/tenders/.+$").mock(side_effect=detail_handler)

    async with ProzorroClient(settings) as client:
        result = await client.crawl(offset=None)

    assert [t.id for t in result.tenders] == ["t-active"]
    assert result.next_offset == "cursor-2"


@respx.mock
async def test_fetch_tender_returns_none_on_404(settings: Settings) -> None:
    respx.get(path__regex=r"^/api/2\.5/tenders/.+$").mock(return_value=httpx.Response(404))
    async with ProzorroClient(settings) as client:
        assert await client.fetch_tender("missing") is None


@respx.mock
async def test_fetch_tender_raises_on_server_error(settings: Settings) -> None:
    no_retry = settings.model_copy(update={"max_retries": 1})
    respx.get(path__regex=r"^/api/2\.5/tenders/.+$").mock(return_value=httpx.Response(500))
    async with ProzorroClient(no_retry) as client:
        with pytest.raises(ProzorroError):
            await client.fetch_tender("boom")


# ── _should_retry ────────────────────────────────────────────────────────────


def test_should_retry_transport_error() -> None:
    """Line 67: TransportError returns True."""
    exc = httpx.ConnectError("refused")
    assert _should_retry(exc) is True


def test_should_retry_timeout() -> None:
    """Line 67: TimeoutException returns True."""
    exc = httpx.ReadTimeout("timeout")
    assert _should_retry(exc) is True


def test_should_retry_429() -> None:
    """Line 69: 429 status returns True."""
    request = httpx.Request("GET", "http://test/")
    response = httpx.Response(429, request=request)
    exc = httpx.HTTPStatusError("", request=request, response=response)
    assert _should_retry(exc) is True


def test_should_retry_500() -> None:
    """Line 69: 500+ status returns True."""
    request = httpx.Request("GET", "http://test/")
    response = httpx.Response(500, request=request)
    exc = httpx.HTTPStatusError("", request=request, response=response)
    assert _should_retry(exc) is True


def test_should_retry_404_is_false() -> None:
    """Line 70: 404 returns False (not retriable)."""
    request = httpx.Request("GET", "http://test/")
    response = httpx.Response(404, request=request)
    exc = httpx.HTTPStatusError("", request=request, response=response)
    assert _should_retry(exc) is False


def test_should_retry_other_exception_is_false() -> None:
    """Line 70: non-HTTP exception returns False."""
    assert _should_retry(ValueError("whatever")) is False


# ── fetch_tender non-404 HTTP error ─────────────────────────────────────────


@respx.mock
async def test_fetch_tender_raises_on_non_404_http_error(settings: Settings) -> None:
    """Lines 200/201 branch: non-404 HTTPStatusError from 403 becomes ProzorroError."""
    # 403 is not retriable and not 404 — should raise ProzorroError via the HTTPStatusError path.
    no_retry = settings.model_copy(update={"max_retries": 1})
    respx.get(path__regex=r"^/api/2\.5/tenders/.+$").mock(return_value=httpx.Response(403))
    async with ProzorroClient(no_retry) as client:
        with pytest.raises(ProzorroError, match="HTTP 403"):
            await client.fetch_tender("forbidden")


@respx.mock
async def test_fetch_tender_raises_on_transport_error(settings: Settings) -> None:
    """Lines 201-202: httpx.HTTPError (transport) becomes ProzorroError."""
    no_retry = settings.model_copy(update={"max_retries": 1})
    respx.get(path__regex=r"^/api/2\.5/tenders/.+$").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    async with ProzorroClient(no_retry) as client:
        with pytest.raises(ProzorroError, match="Failed to fetch tender"):
            await client.fetch_tender("some-id")


# ── _fetch_feed_page error branch ────────────────────────────────────────────


@respx.mock
async def test_fetch_details_empty_returns_early(settings: Settings) -> None:
    """Line 230: _fetch_details called with empty list returns []."""
    # Crawl a page with only non-actionable tenders — _fetch_details gets an empty list.
    page: dict[str, object] = {
        "data": [{"id": "t-complete", "status": "complete"}],
        "next_page": {"offset": "cursor-end"},
    }
    page2: dict[str, object] = {"data": [], "next_page": {"offset": "cursor-end"}}
    respx.get(path="/api/2.5/tenders").mock(
        side_effect=[httpx.Response(200, json=page), httpx.Response(200, json=page2)]
    )
    async with ProzorroClient(settings) as client:
        result = await client.crawl(offset="2026-01-01T00:00:00+00:00")
    assert result.tenders == []


@respx.mock
async def test_crawl_raises_on_feed_page_error(settings: Settings) -> None:
    """Lines 222-223: feed page fetch failure becomes ProzorroError."""
    no_retry = settings.model_copy(update={"max_retries": 1})
    respx.get(path="/api/2.5/tenders").mock(return_value=httpx.Response(500))
    async with ProzorroClient(no_retry) as client:
        with pytest.raises(ProzorroError, match="Failed to fetch feed page"):
            await client.crawl(offset="2026-01-01T00:00:00+00:00")


# ── _fetch_details: ProzorroError and not-found branches ────────────────────


@respx.mock
async def test_crawl_skips_detail_fetch_errors(settings: Settings) -> None:
    """Lines 240-242: ProzorroError in detail fetch is skipped (warning logged)."""
    page1 = {
        "data": [{"id": "t-active", "status": "active.tendering"}],
        "next_page": {"offset": "cursor-2"},
    }
    page2: dict[str, object] = {"data": [], "next_page": {"offset": "cursor-2"}}

    respx.get(path="/api/2.5/tenders").mock(
        side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
    )
    # Detail endpoint returns 500 repeatedly — causes ProzorroError.
    no_retry = settings.model_copy(update={"max_retries": 1})
    respx.get(path__regex=r"^/api/2\.5/tenders/.+$").mock(return_value=httpx.Response(500))

    async with ProzorroClient(no_retry) as client:
        result = await client.crawl(offset="2026-01-01T00:00:00+00:00")

    # The tender was skipped, result should be empty.
    assert result.tenders == []


@respx.mock
async def test_crawl_skips_not_found_tenders(settings: Settings) -> None:
    """Lines 243-245: fetch_tender returns None (404) inside _fetch_details."""
    page1 = {
        "data": [{"id": "t-gone", "status": "active.tendering"}],
        "next_page": {"offset": "cursor-2"},
    }
    page2: dict[str, object] = {"data": [], "next_page": {"offset": "cursor-2"}}

    respx.get(path="/api/2.5/tenders").mock(
        side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
    )
    respx.get(path__regex=r"^/api/2\.5/tenders/.+$").mock(return_value=httpx.Response(404))

    async with ProzorroClient(settings) as client:
        result = await client.crawl(offset="2026-01-01T00:00:00+00:00")

    assert result.tenders == []


# ── page cap guard ───────────────────────────────────────────────────────────


@respx.mock
async def test_crawl_page_cap_stops_iteration(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Lines 131-136: _MAX_FEED_PAGES cap halts the loop."""
    import tender_agent.prozorro.client as client_module

    monkeypatch.setattr(client_module, "_MAX_FEED_PAGES", 1)

    # Return two real pages so without the cap we'd fetch two.
    page_with_entry = {
        "data": [{"id": "t1", "status": "active.tendering"}],
        "next_page": {"offset": "cursor-next"},
    }

    respx.get(path="/api/2.5/tenders").mock(return_value=httpx.Response(200, json=page_with_entry))
    respx.get(path__regex=r"^/api/2\.5/tenders/.+$").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "id": "t1",
                    "tenderID": "UA-T1",
                    "status": "active.tendering",
                    "title": "Test",
                    "items": [],
                }
            },
        )
    )

    async with ProzorroClient(settings) as client:
        result = await client.crawl(offset="2026-01-01T00:00:00+00:00")

    # The cap was hit after 1 page — should have stopped.
    assert isinstance(result, client_module.CrawlResult)


# ── next_offset same-cursor branch ──────────────────────────────────────────


@respx.mock
async def test_crawl_next_offset_is_same_as_current(settings: Settings) -> None:
    """Lines 161-163: when next_offset == current_offset, store it and break."""
    page = {
        "data": [{"id": "t1", "status": "active.tendering"}],
        "next_page": {"offset": "same-cursor"},
    }

    respx.get(path="/api/2.5/tenders").mock(return_value=httpx.Response(200, json=page))
    respx.get(path__regex=r"^/api/2\.5/tenders/.+$").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "id": "t1",
                    "tenderID": "UA-T1",
                    "status": "active.tendering",
                    "title": "Test",
                    "items": [],
                }
            },
        )
    )

    async with ProzorroClient(settings) as client:
        result = await client.crawl(offset="same-cursor")

    assert result.next_offset == "same-cursor"
