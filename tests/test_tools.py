"""End-to-end through the MCP SDK's in-memory client, with the Google API stubbed."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest
from mcp import Client
from mcp.server import MCPServer

from tests.conftest import ApiStub, Handler

pytestmark = pytest.mark.anyio

DOMAIN_PATH = "webmasters/v3/sites/sc-domain%3Aexample.com"
QUERY = f"{DOMAIN_PATH}/searchAnalytics/query"
INSPECT = "v1/urlInspection/index:inspect"

READ_TOOLS = {
    "list_sites",
    "get_site",
    "query_search_analytics",
    "query_hourly_analytics",
    "list_search_appearances",
    "query_search_appearance",
    "query_discover_analytics",
    "query_image_analytics",
    "query_video_analytics",
    "query_google_news_analytics",
    "query_news_tab_analytics",
    "compare_periods",
    "inspect_url",
    "batch_inspect_urls",
    "list_sitemaps",
    "get_sitemap",
}
WRITE_TOOLS = {"add_site", "delete_site", "submit_sitemap", "delete_sitemap"}


async def call(client: Client, name: str, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool(name, arguments)
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content


async def call_error(client: Client, name: str, **arguments: Any) -> str:
    result = await client.call_tool(name, arguments)
    assert result.is_error
    text = result.content[0]
    assert text.type == "text"
    return text.text


# --- Registration ---------------------------------------------------------------------


async def test_read_only_by_default(client: Client) -> None:
    tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == READ_TOOLS
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.output_schema is not None


@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_no_resources_capability(make_server: Callable[..., MCPServer], mode: str) -> None:
    async with Client(make_server(), mode=mode) as connected:
        capabilities = connected.server_capabilities
        assert capabilities is not None
        assert capabilities.resources is None
        assert capabilities.tools is not None
        assert capabilities.prompts is not None


async def test_write_tools_only_when_enabled(write_client: Client) -> None:
    tools = {t.name: t for t in (await write_client.list_tools()).tools}
    assert set(tools) == READ_TOOLS | WRITE_TOOLS
    for name in ("delete_site", "delete_sitemap"):
        annotations = tools[name].annotations
        assert annotations is not None
        assert annotations.destructive_hint is True
    annotations = tools["submit_sitemap"].annotations
    assert annotations is not None
    assert (annotations.destructive_hint, annotations.idempotent_hint) == (False, True)


# --- Sites ----------------------------------------------------------------------------


async def test_list_sites(client: Client) -> None:
    result = await call(client, "list_sites")
    assert result["sites"] == [
        {
            "site_url": "sc-domain:example.com",
            "permission_level": "siteOwner",
            "property_type": "domain",
        },
        {
            "site_url": "https://www.example.com/",
            "permission_level": "siteFullUser",
            "property_type": "url_prefix",
        },
    ]


async def test_unknown_site_lists_the_available_ones(client: Client) -> None:
    message = await call_error(client, "query_search_analytics", site_url="other.org")
    assert "'sc-domain:example.com'" in message
    assert "'https://www.example.com/'" in message


# --- Search Analytics -------------------------------------------------------------------


async def test_query_search_analytics(api: ApiStub, client: Client) -> None:
    api.on(
        "POST",
        QUERY,
        {
            "rows": [
                {
                    "keys": ["shoes", "https://example.com/a"],
                    "clicks": 10.0,
                    "impressions": 200.0,
                    "ctr": 0.05,
                    "position": 3.456,
                },
                {
                    "keys": ["boots", "https://example.com/b"],
                    "clicks": 5.0,
                    "impressions": 100.0,
                    "ctr": 0.05,
                    "position": 7.0,
                },
            ],
            "metadata": {"firstIncompleteDate": "2026-10-12"},
            "responseAggregationType": "byPage",
        },
    )
    result = await call(
        client,
        "query_search_analytics",
        site_url="example.com",
        dimensions=["query", "page"],
        filters=[{"dimension": "country", "operator": "equals", "expression": "USA"}],
        row_limit=2,
    )
    assert api.bodies("searchAnalytics/query") == [
        {
            "startDate": "2026-09-17",
            "endDate": "2026-10-14",
            "dimensions": ["query", "page"],
            "type": "web",
            "dataState": "all",
            "aggregationType": "auto",
            "rowLimit": 2,
            "startRow": 0,
            "dimensionFilterGroups": [
                {
                    "groupType": "and",
                    "filters": [
                        {"dimension": "country", "operator": "equals", "expression": "usa"}
                    ],
                }
            ],
        }
    ]
    assert result["site_url"] == "sc-domain:example.com"
    assert result["rows"][0] == {
        "query": "shoes",
        "page": "https://example.com/a",
        "clicks": 10,
        "impressions": 200,
        "ctr": 0.05,
        "position": 3.46,
    }
    assert result["next_start_row"] == 2
    assert result["first_incomplete_date"] == "2026-10-12"
    assert result["aggregation_type"] == "byPage"
    notes = " ".join(result["notes"])
    assert "Resolved 'example.com'" in notes
    assert "start_row=2" in notes
    assert "anonymized" in notes


async def test_guard_errors_never_reach_google(api: ApiStub, client: Client) -> None:
    message = await call_error(
        client,
        "query_search_analytics",
        site_url="sc-domain:example.com",
        dimensions=["searchAppearance", "date"],
    )
    assert "list_search_appearances" in message
    assert not api.bodies("searchAnalytics/query")


async def test_schema_rejects_unknown_values(client: Client) -> None:
    message = await call_error(
        client, "query_search_analytics", site_url="sc-domain:example.com", search_type="shopping"
    )
    assert "search_type" in message


async def test_discover_has_no_position(api: ApiStub, client: Client) -> None:
    api.on(
        "POST",
        QUERY,
        {
            "rows": [
                {
                    "keys": ["https://example.com/a"],
                    "clicks": 1,
                    "impressions": 9,
                    "ctr": 0.11,
                    "position": 0,
                }
            ]
        },
    )
    result = await call(
        client,
        "query_search_analytics",
        site_url="sc-domain:example.com",
        search_type="discover",
        dimensions=["page"],
    )
    assert "position" not in result["rows"][0]


async def test_google_errors_are_explained(api: ApiStub, client: Client) -> None:
    api.on(
        "POST", QUERY, httpx2.Response(403, json={"error": {"code": 403, "message": "Forbidden"}})
    )
    message = await call_error(client, "query_search_analytics", site_url="sc-domain:example.com")
    assert "added as a user on the property" in message


async def test_hourly(api: ApiStub, client: Client) -> None:
    api.on(
        "POST",
        QUERY,
        {
            "rows": [
                {
                    "keys": ["2026-10-14T03:00:00-07:00", "MOBILE"],
                    "clicks": 1,
                    "impressions": 4,
                    "ctr": 0.25,
                    "position": 2,
                }
            ],
            "metadata": {"firstIncompleteHour": "2026-10-14T03:00:00-07:00"},
        },
    )
    result = await call(
        client, "query_hourly_analytics", site_url="sc-domain:example.com", dimensions=["device"]
    )
    body = api.bodies("searchAnalytics/query")[0]
    assert (body["dataState"], body["dimensions"]) == ("hourly_all", ["hour", "device"])
    assert (body["startDate"], body["endDate"]) == ("2026-10-12", "2026-10-14")
    assert result["rows"][0]["hour"] == "2026-10-14T03:00:00-07:00"
    assert result["first_incomplete_hour"] == "2026-10-14T03:00:00-07:00"


async def test_list_search_appearances(api: ApiStub, client: Client) -> None:
    api.on(
        "POST",
        QUERY,
        {"rows": [{"keys": ["VIDEO"], "clicks": 3, "impressions": 30, "ctr": 0.1, "position": 4}]},
    )
    result = await call(client, "list_search_appearances", site_url="sc-domain:example.com")
    body = api.bodies("searchAnalytics/query")[0]
    assert (body["dimensions"], body["rowLimit"]) == (["searchAppearance"], 1000)
    assert result["rows"][0]["searchAppearance"] == "VIDEO"
    assert any("query_search_appearance" in n for n in result["notes"])


async def test_compare_periods(api: ApiStub, client: Client) -> None:
    def reply(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        current = body["startDate"] == "2026-09-14"
        if not body["dimensions"]:
            row = {"clicks": 100 if current else 80, "impressions": 1000, "ctr": 0.1, "position": 5}
            return httpx2.Response(200, json={"rows": [row]})
        rows = (
            [
                {"keys": ["shoes"], "clicks": 50, "impressions": 500, "ctr": 0.1, "position": 3},
                {"keys": ["new"], "clicks": 9, "impressions": 90, "ctr": 0.1, "position": 8},
            ]
            if current
            else [
                {"keys": ["shoes"], "clicks": 20, "impressions": 400, "ctr": 0.05, "position": 6},
                {"keys": ["gone"], "clicks": 40, "impressions": 300, "ctr": 0.1, "position": 2},
            ]
        )
        return httpx2.Response(200, json={"rows": rows})

    api.on("POST", QUERY, reply)
    result = await call(client, "compare_periods", site_url="sc-domain:example.com")
    assert result["current_period"] == ["2026-09-14", "2026-10-11"]
    assert result["previous_period"] == ["2026-08-17", "2026-09-13"]
    assert result["data_state"] == "final"
    assert result["totals"]["change"]["clicks"] == 20
    assert result["totals"]["change"]["clicks_pct"] == 25.0
    # A vanished key (40 clicks) outranks a 30-click gain, which outranks a new 9-click key.
    gone, shoes, new = result["rows"]
    assert [r["keys"]["query"] for r in (gone, shoes, new)] == ["gone", "shoes", "new"]
    assert shoes["change"]["clicks"] == 30
    assert shoes["change"]["position"] == -3.0
    # Neither period hit fetch_limit, so a key missing from one had no traffic there.
    zero = {"clicks": 0, "impressions": 0, "ctr": 0.0, "position": None}
    assert gone["current"] == zero
    assert (gone["change"]["clicks"], gone["change"]["clicks_pct"]) == (-40, -100.0)
    assert new["previous"] == zero
    assert (new["change"]["clicks"], new["change"]["clicks_pct"]) == (9, None)
    assert not any("fetch_limit" in n for n in result["notes"])
    assert len(api.bodies("searchAnalytics/query")) == 4


def rows_by_period(current: list[Any], previous: list[Any]) -> Handler:
    """Reply with these (query, clicks, impressions) rows per period, and matching totals."""

    def reply(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        rows = current if body["startDate"] == "2026-09-14" else previous
        if not body["dimensions"]:
            total = {"clicks": sum(r[1] for r in rows), "impressions": sum(r[2] for r in rows)}
            return httpx2.Response(200, json={"rows": [{**total, "ctr": 0, "position": 1}]})
        out = [
            {"keys": [q], "clicks": c, "impressions": i, "ctr": 0, "position": 5}
            for q, c, i in rows
        ]
        return httpx2.Response(200, json={"rows": out[: body["rowLimit"]]})

    return reply


async def test_compare_breaks_ties_instead_of_returning_noise(api: ApiStub, client: Client) -> None:
    # Most queries have no clicks in either period. Without tie-breaking they came back in
    # arbitrary order, and a one-impression query that vanished could top the list.
    api.on(
        "POST",
        QUERY,
        rows_by_period(
            current=[("won", 3, 40), ("rising", 0, 90), ("steady big", 0, 500), ("steady", 0, 5)],
            previous=[("rising", 0, 10), ("steady big", 0, 500), ("steady", 0, 5), ("gone", 0, 1)],
        ),
    )
    result = await call(client, "compare_periods", site_url="sc-domain:example.com")
    order = [r["keys"]["query"] for r in result["rows"]]
    assert order == ["won", "rising", "gone", "steady big", "steady"]
    assert all(r["change"] is not None for r in result["rows"])


async def test_compare_keeps_change_unknown_past_the_cut_off(api: ApiStub, client: Client) -> None:
    api.on(
        "POST",
        QUERY,
        rows_by_period(
            current=[("a", 9, 90), ("b", 5, 50), ("c", 3, 30), ("e", 1, 10)],
            previous=[("a", 8, 80), ("d", 2, 20)],
        ),
    )
    result = await call(client, "compare_periods", site_url="sc-domain:example.com", fetch_limit=3)
    rows = {r["keys"]["query"]: r for r in result["rows"]}
    # The current period hit fetch_limit=3, so "d" may just rank third there: unknown.
    assert (rows["d"]["current"], rows["d"]["change"]) == (None, None)
    # The previous period returned fewer rows than the limit, so "b" really had none.
    assert rows["b"]["previous"]["clicks"] == 0
    assert rows["b"]["change"]["clicks"] == 5
    notes = " ".join(result["notes"])
    assert "The current period returned the full fetch_limit of 3 rows" in notes


async def test_compare_reports_google_errors(api: ApiStub, client: Client) -> None:
    api.on("POST", QUERY, httpx2.Response(403, json={"error": {"code": 403, "message": "Nope"}}))
    message = await call_error(client, "compare_periods", site_url="sc-domain:example.com")
    assert "added as a user on the property" in message


async def test_compare_rejects_unequal_periods(client: Client) -> None:
    message = await call_error(
        client,
        "compare_periods",
        site_url="sc-domain:example.com",
        current_start="2026-10-01",
        current_end="2026-10-07",
        previous_start="2026-09-01",
        previous_end="2026-09-10",
    )
    assert "same length" in message


# --- URL Inspection ---------------------------------------------------------------------

INSPECTION = {
    "inspectionResult": {
        "inspectionResultLink": "https://search.google.com/search-console/inspect?x",
        "indexStatusResult": {
            "verdict": "PASS",
            "coverageState": "Submitted and indexed",
            "googleCanonical": "https://example.com/a",
            "userCanonical": "https://example.com/a?ref=1",
            "sitemap": ["https://example.com/sitemap.xml"],
        },
        "richResultsResult": {
            "verdict": "PARTIAL",
            "detectedItems": [
                {
                    "richResultType": "Product snippets",
                    "items": [
                        {
                            "issues": [
                                {"issueMessage": "Missing field 'price'", "severity": "WARNING"}
                            ]
                        }
                    ],
                }
            ],
        },
        "mobileUsabilityResult": {"verdict": "VERDICT_UNSPECIFIED"},
    }
}


async def test_inspect_url(api: ApiStub, client: Client) -> None:
    api.on("POST", INSPECT, INSPECTION)
    result = await call(client, "inspect_url", site_url="example.com", url="https://example.com/a")
    assert result["verdict"] == "PASS"
    assert result["canonical_mismatch"] is True
    assert result["rich_results"] == [
        {
            "type": "Product snippets",
            "issues": [{"severity": "WARNING", "message": "Missing field 'price'"}],
        }
    ]
    assert "mobile" not in str(result).lower()


async def test_inspect_url_outside_the_property(api: ApiStub, client: Client) -> None:
    message = await call_error(
        client, "inspect_url", site_url="https://www.example.com/", url="https://example.com/a"
    )
    assert "not inside the property" in message
    assert not api.bodies("index:inspect")


async def test_batch_inspect(api: ApiStub, client: Client) -> None:
    api.on("POST", INSPECT, INSPECTION)
    reports: list[float] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        reports.append(progress)

    result = await client.call_tool(
        "batch_inspect_urls",
        {
            "site_url": "sc-domain:example.com",
            "urls": ["https://example.com/a", "https://example.com/a", "https://other.org/x"],
        },
        progress_callback=on_progress,
    )
    assert not result.is_error
    data = result.structured_content
    assert data is not None
    statuses = {i["url"]: i["status"] for i in data["items"]}
    assert statuses == {"https://example.com/a": "ok", "https://other.org/x": "error"}
    assert data["verdict_counts"] == {"PASS": 1}
    assert reports == [1, 2]
    assert any("duplicate" in n for n in data["notes"])


async def test_batch_skips_the_rest_after_quota_runs_out(api: ApiStub, client: Client) -> None:
    api.on(
        "POST",
        INSPECT,
        httpx2.Response(429, json={"error": {"code": 429, "message": "Quota exceeded"}}),
    )
    urls = [f"https://example.com/{i}" for i in range(8)]
    data = await call(client, "batch_inspect_urls", site_url="sc-domain:example.com", urls=urls)
    statuses = [i["status"] for i in data["items"]]
    assert "skipped" in statuses
    assert "ok" not in statuses
    assert any("Quota ran out" in n for n in data["notes"])


# --- Sitemaps ---------------------------------------------------------------------------


async def test_list_sitemaps(api: ApiStub, client: Client) -> None:
    api.on(
        "GET",
        f"{DOMAIN_PATH}/sitemaps",
        {
            "sitemap": [
                {
                    "path": "https://example.com/sitemap.xml",
                    "isSitemapsIndex": True,
                    "warnings": "2",
                    "errors": "1",
                    "contents": [{"type": "web", "submitted": "73", "indexed": "0"}],
                }
            ]
        },
    )
    result = await call(client, "list_sitemaps", site_url="sc-domain:example.com")
    sitemap = result["sitemaps"][0]
    assert (sitemap["is_index"], sitemap["warnings"], sitemap["errors"]) == (True, 2, 1)
    assert sitemap["contents"] == [{"type": "web", "submitted": 73}]


async def test_get_sitemap_outside_the_property(client: Client) -> None:
    message = await call_error(
        client,
        "get_sitemap",
        site_url="sc-domain:example.com",
        sitemap_url="https://other.org/s.xml",
    )
    assert "not inside the property" in message


# --- Writes -----------------------------------------------------------------------------


async def test_submit_and_delete_sitemap(api: ApiStub, write_client: Client) -> None:
    path = f"{DOMAIN_PATH}/sitemaps/https%3A%2F%2Fexample.com%2Fsitemap.xml"
    api.on("PUT", path, httpx2.Response(204))
    api.on("DELETE", path, httpx2.Response(204))
    submitted = await call(
        write_client,
        "submit_sitemap",
        site_url="sc-domain:example.com",
        sitemap_url="https://example.com/sitemap.xml",
    )
    deleted = await call(
        write_client,
        "delete_sitemap",
        site_url="sc-domain:example.com",
        sitemap_url="https://example.com/sitemap.xml",
    )
    assert submitted["done"]
    assert deleted["done"]
    assert [r.method for r in api.requests if "sitemaps" in str(r.url)] == ["PUT", "DELETE"]


async def test_add_site_validates_and_normalizes(api: ApiStub, write_client: Client) -> None:
    api.on("PUT", "webmasters/v3/sites/https%3A%2F%2Fnew.example.org%2F", httpx2.Response(204))
    result = await call(write_client, "add_site", site_url="https://new.example.org")
    assert result["site_url"] == "https://new.example.org/"
    message = await call_error(write_client, "add_site", site_url="new.example.org")
    assert "not a valid property" in message


async def test_delete_site_refreshes_the_property_cache(api: ApiStub, write_client: Client) -> None:
    def listings() -> int:
        return sum(r.url.raw_path.decode() == "/webmasters/v3/sites" for r in api.requests)

    api.on("DELETE", DOMAIN_PATH, httpx2.Response(204))
    api.on("GET", f"{DOMAIN_PATH}/sitemaps", {"sitemap": []})
    await call(write_client, "delete_site", site_url="sc-domain:example.com")
    before = listings()
    await call(write_client, "list_sitemaps", site_url="sc-domain:example.com")
    assert listings() == before + 1


async def test_write_tools_refuse_loose_site_names(api: ApiStub, write_client: Client) -> None:
    for name, extra in (
        ("delete_site", {}),
        ("delete_sitemap", {"sitemap_url": "https://www.example.com/s.xml"}),
        ("submit_sitemap", {"sitemap_url": "https://www.example.com/s.xml"}),
    ):
        message = await call_error(write_client, name, site_url="www.example.com", **extra)
        assert "exact site_url" in message
        assert "Did you mean 'sc-domain:example.com'?" in message
    assert all(r.method == "GET" for r in api.requests)


async def test_batch_survives_malformed_and_broken_items(api: ApiStub, client: Client) -> None:
    api.on("POST", INSPECT, {"inspectionResult": ["not", "an", "object"]})
    data = await call(
        client,
        "batch_inspect_urls",
        site_url="sc-domain:example.com",
        urls=["https://[example.com/b", "https://example.com/a"],
    )
    errors = {i["url"]: i["error"] for i in data["items"]}
    assert "not inside the property" in errors["https://[example.com/b"]
    assert "Unexpected error" in errors["https://example.com/a"]


async def test_date_errors_name_the_field_that_was_given(client: Client) -> None:
    message = await call_error(
        client, "query_search_analytics", site_url="sc-domain:example.com", end_date="garbage"
    )
    assert "end_date" in message
    message = await call_error(
        client, "compare_periods", site_url="sc-domain:example.com", current_start="2026-10-13"
    )
    assert "current_end defaults to 2026-10-11" in message


# --- Search appearance, step two -------------------------------------------------------


async def test_query_search_appearance(api: ApiStub, client: Client) -> None:
    api.on("POST", QUERY, {"rows": []})
    await call(
        client,
        "query_search_appearance",
        site_url="sc-domain:example.com",
        appearance="review_snippet",
        dimensions=["page"],
        filters=[{"dimension": "country", "operator": "equals", "expression": "usa"}],
    )
    body = api.bodies("searchAnalytics/query")[0]
    assert body["dimensions"] == ["page"]
    assert body["dimensionFilterGroups"][0]["filters"] == [
        {"dimension": "searchAppearance", "operator": "equals", "expression": "REVIEW_SNIPPET"},
        {"dimension": "country", "operator": "equals", "expression": "usa"},
    ]


async def test_query_search_appearance_defaults_to_page_for_discover(
    api: ApiStub, client: Client
) -> None:
    api.on("POST", QUERY, {"rows": []})
    await call(
        client,
        "query_search_appearance",
        site_url="sc-domain:example.com",
        appearance="NEWS_SHOWCASE",
        search_type="discover",
    )
    assert api.bodies("searchAnalytics/query")[0]["dimensions"] == ["page"]


async def test_appearance_goes_in_its_own_parameter(client: Client) -> None:
    message = await call_error(
        client,
        "query_search_appearance",
        site_url="sc-domain:example.com",
        appearance="VIDEO",
        filters=[{"dimension": "searchAppearance", "operator": "equals", "expression": "X"}],
    )
    assert "as `appearance`" in message


# --- Surface tools -----------------------------------------------------------------------


async def test_surface_tools_send_their_search_type(api: ApiStub, client: Client) -> None:
    api.on("POST", QUERY, {"rows": []})
    cases: list[tuple[str, dict[str, str], str, list[str]]] = [
        ("query_discover_analytics", {}, "discover", ["page"]),
        ("query_image_analytics", {}, "image", ["query"]),
        ("query_video_analytics", {}, "video", ["query"]),
        ("query_google_news_analytics", {}, "googleNews", ["page"]),
        ("query_news_tab_analytics", {}, "news", ["query"]),
    ]
    for name, extra, search_type, dimensions in cases:
        await call(client, name, site_url="sc-domain:example.com", **extra)
        body = api.bodies("searchAnalytics/query")[-1]
        assert (body["type"], body["dimensions"]) == (search_type, dimensions), name


async def test_discover_schema_offers_only_what_discover_supports(client: Client) -> None:
    tools = {t.name: t for t in (await client.list_tools()).tools}
    schema = tools["query_discover_analytics"].input_schema
    dims = schema["properties"]["dimensions"]["anyOf"][0]["items"]["enum"]
    assert dims == ["date", "page", "country"]
    filter_dims = schema["$defs"]["DiscoverFilter"]["properties"]["dimension"]["enum"]
    assert filter_dims == ["page", "country", "searchAppearance"]
    assert "byProperty" not in str(schema["properties"]["aggregation_type"])


async def test_discover_rejects_query_and_device_at_the_schema(client: Client) -> None:
    for dimensions in (["query"], ["device"]):
        message = await call_error(
            client,
            "query_discover_analytics",
            site_url="sc-domain:example.com",
            dimensions=dimensions,
        )
        assert "dimensions" in message


async def test_each_surface_offers_only_its_options(client: Client) -> None:
    tools = {t.name: t.input_schema for t in (await client.list_tools()).tools}

    def options(tool: str, field: str) -> list[str]:
        prop = tools[tool]["properties"][field]
        return prop["anyOf"][0]["items"]["enum"] if "anyOf" in prop else prop["enum"]

    for tool in ("query_image_analytics", "query_video_analytics", "query_news_tab_analytics"):
        assert options(tool, "aggregation_type") == ["auto", "byPage", "byProperty"], tool
        assert "query" in options(tool, "dimensions"), tool
    for tool in ("query_discover_analytics", "query_google_news_analytics"):
        assert options(tool, "aggregation_type") == ["auto", "byPage", "byNewsShowcasePanel"]
    assert options("query_google_news_analytics", "dimensions") == [
        "date",
        "page",
        "country",
        "device",
    ]
    news_filters = tools["query_google_news_analytics"]["$defs"]["GoogleNewsFilter"]
    assert "query" not in news_filters["properties"]["dimension"]["enum"]


async def test_google_news_has_no_query(api: ApiStub, client: Client) -> None:
    message = await call_error(
        client,
        "query_google_news_analytics",
        site_url="sc-domain:example.com",
        dimensions=["query"],
    )
    assert "dimensions[0] must be 'date', 'page', 'country' or 'device'; got 'query'." in message
    assert not api.bodies("searchAnalytics/query")


@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_deprecated_news_tool_still_answers(
    api: ApiStub, make_server: Callable[..., MCPServer], mode: str
) -> None:
    # query_news_analytics was split by source in 1.2.0. Old callers keep working until
    # 2.0.0, but the name is not listed, so models only see the current tools.
    api.on("POST", QUERY, {"rows": []})
    async with Client(make_server(), mode=mode, raise_exceptions=True) as connected:
        listed = {t.name for t in (await connected.list_tools()).tools}
        assert "query_news_analytics" not in listed
        for extra, search_type, dimensions in (
            ({}, "googleNews", ["page"]),
            ({"source": "google_news", "dimensions": ["country"]}, "googleNews", ["country"]),
            ({"source": "news_tab"}, "news", ["query"]),
        ):
            await call(connected, "query_news_analytics", site_url="sc-domain:example.com", **extra)
            body = api.bodies("searchAnalytics/query")[-1]
            assert (body["type"], body["dimensions"]) == (search_type, dimensions)
        message = await call_error(
            connected, "query_news_analytics", site_url="sc-domain:example.com", source="web"
        )
        assert message == (
            "Error executing tool query_news_analytics: source must be 'google_news' or "
            "'news_tab'; got 'web'."
        )


@pytest.mark.parametrize("tool", ["query_discover_analytics", "query_google_news_analytics"])
async def test_showcase_panels_default_to_date(api: ApiStub, client: Client, tool: str) -> None:
    api.on("POST", QUERY, {"rows": []})
    showcase = {
        "dimension": "searchAppearance",
        "operator": "equals",
        "expression": "NEWS_SHOWCASE",
    }
    await call(
        client,
        tool,
        site_url="sc-domain:example.com",
        aggregation_type="byNewsShowcasePanel",
        filters=[showcase],
    )
    assert api.bodies("searchAnalytics/query")[0]["dimensions"] == ["date"]
    message = await call_error(
        client, tool, site_url="sc-domain:example.com", aggregation_type="byNewsShowcasePanel"
    )
    assert "NEWS_SHOWCASE" in message
    assert "group by" not in message


# --- Plain-word errors -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "arguments", "expected"),
    [
        (
            "query_search_analytics",
            {"row_limit": 0},
            "row_limit must be between 1 and 25,000; got 0.",
        ),
        (
            "query_search_analytics",
            {"row_limit": 25_001},
            "row_limit must be between 1 and 25,000; got 25,001.",
        ),
        (
            "batch_inspect_urls",
            {"urls": [f"https://example.com/{i}" for i in range(51)]},
            "urls must have between 1 and 50 items; got 51.",
        ),
        (
            "query_discover_analytics",
            {"dimensions": ["device"]},
            "dimensions[0] must be 'date', 'page' or 'country'; got 'device'.",
        ),
        (
            "query_search_analytics",
            {"filters": [{"dimension": "query"}]},
            "filters[0].expression is required.",
        ),
        ("query_search_appearance", {"appearance": ""}, "appearance must not be empty."),
    ],
)
async def test_schema_errors_are_plain_words(
    client: Client, tool: str, arguments: dict[str, Any], expected: str
) -> None:
    message = await call_error(client, tool, site_url="sc-domain:example.com", **arguments)
    assert message == f"Error executing tool {tool}: {expected}"


async def test_missing_argument_is_named(client: Client) -> None:
    message = await call_error(client, "query_search_analytics")
    assert message == "Error executing tool query_search_analytics: site_url is required."


# --- Notes --------------------------------------------------------------------------------


async def test_fresh_data_is_flagged_without_a_date_grouping(api: ApiStub, client: Client) -> None:
    api.on("POST", QUERY, {"rows": []})

    async def notes(**arguments: Any) -> str:
        result = await call(
            client, "query_search_analytics", site_url="sc-domain:example.com", **arguments
        )
        return " ".join(result["notes"])

    assert "This range includes 2026-10-12 to 2026-10-14" in await notes()
    assert "still being processed" not in await notes(end_date="2026-10-11")
    assert "This range includes" not in await notes(dimensions=["date"])
    assert "This range includes" not in await notes(data_state="final")


async def test_page_url_as_site_url_resolves_with_a_warning(api: ApiStub, client: Client) -> None:
    api.on("POST", QUERY, {"rows": []})
    result = await call(client, "query_search_analytics", site_url="https://example.com/calendar")
    assert result["site_url"] == "sc-domain:example.com"
    assert any("whole property" in n for n in result["notes"])
