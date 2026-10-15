"""Contract tests against the real Search Console API. Opt-in and read-only.

export GSC_SERVICE_ACCOUNT_FILE=/abs/path/key.json   # or sign in with `auth login`
export GSC_TEST_SITE=sc-domain:example.com
uv run pytest -m live
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
from mcp import Client

from gsc_mcp.config import Settings
from gsc_mcp.server import build_server

pytestmark = [
    pytest.mark.live,
    pytest.mark.anyio,
    pytest.mark.skipif(not os.environ.get("GSC_TEST_SITE"), reason="GSC_TEST_SITE is not set"),
]


@pytest.fixture
def site() -> str:
    return os.environ["GSC_TEST_SITE"]


@pytest.fixture
async def live() -> AsyncIterator[Client]:
    settings = Settings.from_env()
    async with Client(build_server(settings), raise_exceptions=True) as client:
        yield client


async def ok(client: Client, name: str, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool(name, arguments)
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content


async def test_site_is_listed(live: Client, site: str) -> None:
    sites = [s["site_url"] for s in (await ok(live, "list_sites"))["sites"]]
    assert site in sites


async def test_search_analytics(live: Client, site: str) -> None:
    result = await ok(live, "query_search_analytics", site_url=site, dimensions=["date"])
    assert result["site_url"] == site
    assert result["data_state"] == "all"


async def test_hourly(live: Client, site: str) -> None:
    result = await ok(live, "query_hourly_analytics", site_url=site)
    assert result["dimensions"] == ["hour"]


async def test_compare(live: Client, site: str) -> None:
    result = await ok(live, "compare_periods", site_url=site, dimensions=[])
    assert result["totals"]["current"] is not None


async def test_sitemaps(live: Client, site: str) -> None:
    await ok(live, "list_sitemaps", site_url=site)


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("query_discover_analytics", {}),
        ("query_image_analytics", {}),
        ("query_video_analytics", {}),
        ("query_google_news_analytics", {}),
        ("query_news_tab_analytics", {}),
        ("list_search_appearances", {}),
    ],
)
async def test_surface_tools(live: Client, site: str, tool: str, arguments: dict[str, Any]) -> None:
    result = await ok(live, tool, site_url=site, **arguments)
    assert result["site_url"] == site
