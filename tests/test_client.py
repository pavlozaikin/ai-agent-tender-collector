"""Integration tests for the PROZORRO API client (HTTP mocked with respx)."""

from __future__ import annotations

import httpx
import pytest
import respx

from tender_agent.prozorro.client import ProzorroClient, ProzorroError
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
