"""explain_change end to end, with known numbers so every share can be checked by hand."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx2
import pytest

from gsc_mcp.updates import FEED_URL
from tests.conftest import QUERY, ApiStub, WebStub, call, call_error

if TYPE_CHECKING:
    from mcp import Client

pytestmark = pytest.mark.anyio

# Defaults with today = 2026-10-14: the 28 days to Oct 11 against the 28 before.
CURRENT_START = "2026-09-14"
# (clicks before, clicks after) per slice.
SLICES: dict[str, dict[str, tuple[int, int]]] = {
    "page": {"/a": (500, 250), "/b": (300, 290), "/c": (200, 160)},
    "query": {"shoes": (600, 400), "boots": (200, 250)},
    "country": {"usa": (900, 600), "gbr": (100, 100)},
    "device": {"MOBILE": (700, 420), "DESKTOP": (300, 280)},
    "searchAppearance": {},
}


def reply(request: httpx2.Request) -> httpx2.Response:
    body = json.loads(request.content)
    current = body["startDate"] == CURRENT_START
    if not body["dimensions"]:
        row = (
            {"clicks": 700, "impressions": 18_000, "ctr": 0.039, "position": 6.2}
            if current
            else {"clicks": 1000, "impressions": 20_000, "ctr": 0.05, "position": 5.0}
        )
        return httpx2.Response(200, json={"rows": [row]})
    [dim] = body["dimensions"]
    rows = [
        {"keys": [key], "clicks": after if current else before, "impressions": 100, "ctr": 0.1}
        for key, (before, after) in SLICES[dim].items()
    ]
    return httpx2.Response(200, json={"rows": rows[: body["rowLimit"]]})


@pytest.fixture(autouse=True)
def stubs(api: ApiStub, web: WebStub) -> None:
    api.on("POST", QUERY, reply)
    web.on(
        FEED_URL,
        httpx2.Response(
            200,
            json=[
                {
                    "id": "core",
                    "external_desc": "Test core update",
                    "begin": "2026-09-20T16:00:00+00:00",
                    "end": "2026-09-30T16:00:00+00:00",
                    "service_name": "Ranking",
                }
            ],
        ),
    )


def breakdown(result: dict[str, Any], dimension: str) -> dict[str, Any]:
    return next(b for b in result["breakdowns"] if b["dimension"] == dimension)


async def test_splits_the_change_and_names_the_slices(api: ApiStub, client: Client) -> None:
    result = await call(client, "explain_change", site_url="sc-domain:example.com")
    assert result["totals"]["change"]["clicks"] == -300
    # 2,000 fewer impressions at the old 5% CTR cost 100 clicks; the lower CTR cost 200.
    assert result["effects"] == {"impressions": -100, "ctr": -200}

    page = breakdown(result, "page")
    assert (page["gains"], page["losses"], page["net"], page["unattributed"]) == (0, -300, -300, 0)
    assert [(m["key"], m["change"], m["share_pct"]) for m in page["movers"]] == [
        ("/a", -250, 83.3),
        ("/c", -40, 13.3),
        ("/b", -10, 3.3),
    ]
    assert page["movers"][0]["previous_share_pct"] == 50.0
    assert page["top3_share_pct"] == 100.0

    # Queries: the loss is listed first, the offsetting gain after; anonymized queries
    # account for the rest of the change.
    query = breakdown(result, "query")
    assert [(m["key"], m["change"]) for m in query["movers"]] == [("shoes", -200), ("boots", 50)]
    assert (query["net"], query["unattributed"]) == (-150, -150)

    assert breakdown(result, "searchAppearance")["movers"] == []
    assert len(result["requests"]) == 12
    assert len(api.bodies("searchAnalytics/query")) == 12


async def test_summary_reads_as_findings(client: Client) -> None:
    result = await call(client, "explain_change", site_url="sc-domain:example.com")
    summary = result["summary"]
    assert summary[0] == (
        "Clicks fell by 300 (-30.0%), from 1,000 to 700 "
        "(2026-09-14 to 2026-10-11 against 2026-08-17 to 2026-09-13)."
    )
    assert summary[1] == (
        "That splits into -100 clicks from fewer impressions and -200 from a lower "
        "click-through rate."
    )
    assert summary[2] == "Average position worsened from 5.0 to 6.2 (lower is better)."
    assert (
        "By page, the top 3 pages make up 100% of the lost clicks: /a (-250), /c (-40), "
        "/b (-10); /a had 50% of clicks before." in summary
    )
    assert (
        "By country, usa (-300) makes up 100% of the lost clicks; usa had 90% of clicks "
        "before." in summary
    )
    assert any("Test core update ran 2026-09-20 to 2026-09-30" in line for line in summary)
    assert summary[-1].startswith("Timing alone does not show")


async def test_lists_google_updates_beside_the_change(client: Client) -> None:
    result = await call(client, "explain_change", site_url="sc-domain:example.com")
    updates = {u["name"]: u for u in result["google_updates"]}
    assert updates["Test core update"]["timing"] == "current"
    # From the bundled history: it ran Aug 18-21, in the previous period.
    assert updates["August 2026 spam update"]["timing"] == "previous"


async def test_only_offers_breakdowns_the_surface_has(api: ApiStub, client: Client) -> None:
    result = await call(
        client, "explain_change", site_url="sc-domain:example.com", search_type="discover"
    )
    assert [b["dimension"] for b in result["breakdowns"]] == ["page", "country", "searchAppearance"]
    message = await call_error(
        client,
        "explain_change",
        site_url="sc-domain:example.com",
        search_type="discover",
        breakdowns=["query"],
    )
    assert "no query data" in message


async def test_flags_cut_off_breakdowns_and_few_clicks(client: Client) -> None:
    result = await call(
        client, "explain_change", site_url="sc-domain:example.com", fetch_limit=2, metric="clicks"
    )
    page = breakdown(result, "page")
    assert not page["complete"]
    assert any("hit fetch_limit (2 rows)" in note for note in result["notes"])
