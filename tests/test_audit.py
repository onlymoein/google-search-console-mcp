"""audit_indexing end to end against a small fake site."""

from __future__ import annotations

import gzip
import json
from typing import TYPE_CHECKING, Any

import httpx2
import pytest

from tests.conftest import DOMAIN_PATH, INSPECT, QUERY, ApiStub, WebStub, call, call_error

if TYPE_CHECKING:
    from mcp import Client

pytestmark = pytest.mark.anyio

SITE = "sc-domain:example.com"
INDEX = "https://example.com/sitemap_index.xml"
PAGES = "https://example.com/pages.xml.gz"
A, B, C, D = (f"https://example.com/{p}" for p in "abcd")
NOT_IN_SITEMAP = "https://example.com/x"

# What Google's URL Inspection says about each URL.
INSPECTIONS = {
    A: ("PASS", "Submitted and indexed", A, A),
    B: ("NEUTRAL", "Excluded by \u2018noindex\u2019 tag", None, B),
    C: ("NEUTRAL", "Crawled - currently not indexed", None, C),
    D: ("PASS", "Submitted and indexed", "https://example.com/other", D),
}


def page(robots: str = "index, follow") -> httpx2.Response:
    html = f"<html><head><meta name='robots' content='{robots}'></head><body>hi</body></html>"
    return httpx2.Response(200, text=html, headers={"content-type": "text/html"})


def inspection(request: httpx2.Request) -> httpx2.Response:
    url = json.loads(request.content)["inspectionUrl"]
    verdict, coverage, google, user = INSPECTIONS[url]
    index = {"verdict": verdict, "coverageState": coverage, "userCanonical": user}
    if google:
        index["googleCanonical"] = google
    return httpx2.Response(200, json={"inspectionResult": {"indexStatusResult": index}})


def urlset(*urls: str) -> bytes:
    entries = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    return (
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>'.encode()
    )


@pytest.fixture(autouse=True)
def site(api: ApiStub, web: WebStub) -> None:
    api.on("GET", f"{DOMAIN_PATH}/sitemaps", {"sitemap": [{"path": INDEX}]})
    api.on("POST", INSPECT, inspection)
    api.on(
        "POST",
        QUERY,
        {
            "rows": [
                {"keys": [A], "clicks": 10, "impressions": 500},
                {"keys": [NOT_IN_SITEMAP], "clicks": 1, "impressions": 20},
            ]
        },
    )
    web.on(
        INDEX,
        httpx2.Response(
            200,
            text=f"<sitemapindex><sitemap><loc>{PAGES}</loc></sitemap>"
            "<sitemap><loc>https://cdn.other.org/s.xml</loc></sitemap></sitemapindex>",
        ),
    )
    web.on(
        PAGES,
        httpx2.Response(200, content=gzip.compress(urlset(A, B, C, D, "https://other.org/z"))),
    )
    web.on(A, page("noindex"))  # indexed, but now says noindex
    web.on(B, page())  # Google saw noindex; it is gone now
    web.on(C, httpx2.Response(301, headers={"location": "/c/"}))
    web.on(f"{C}/", page())
    web.on(D, httpx2.Response(404))


def issues(result: dict[str, Any]) -> dict[str, list[str]]:
    return {i["key"]: i["examples"] for i in result["issues"]}


async def test_audits_in_steps_and_reports_every_issue(
    api: ApiStub, web: WebStub, client: Client
) -> None:
    first = await call(client, "audit_indexing", site_url=SITE, inspect=2)
    assert (first["urls_in_sitemaps"], first["inspected"], first["inspected_now"]) == (4, 2, 2)
    assert first["not_yet_inspected"] == 2
    assert {s["url"]: s["urls"] for s in first["sitemaps"]} == {INDEX: 0, PAGES: 4}
    assert any("2 sitemap entries point outside" in n for n in first["notes"])
    assert any("today's quota covers them" in n for n in first["notes"])

    second = await call(client, "audit_indexing", site_url=SITE, fetch_pages=True)
    assert (second["inspected"], second["inspected_now"], second["live_checked"]) == (4, 2, 4)
    assert second["quota_used_today"] == 4
    assert issues(second) == {
        "not_indexed:excluded-by-noindex-tag": [B],
        "not_indexed:crawled-currently-not-indexed": [C],
        "live_noindex_indexed": [A],
        "noindex_removed": [B],
        "live_error_status": [D],
        "live_redirects": [C],
        "canonical_mismatch": [D],
        "indexed_no_impressions": [D],
        "in_search_not_in_sitemaps": [NOT_IN_SITEMAP],
    }
    # Most severe first: index states (largest first, ties in sitemap order), then what
    # changed since Google's crawl.
    assert [i["key"] for i in second["issues"][:3]] == [
        "not_indexed:excluded-by-noindex-tag",
        "not_indexed:crawled-currently-not-indexed",
        "live_noindex_indexed",
    ]
    assert len(api.bodies("index:inspect")) == 4
    # The sitemaps are read once a day, and nothing outside the property is fetched.
    assert web.fetched().count(INDEX) == 1
    assert not [u for u in web.fetched() if "other.org" in u]


async def test_lists_one_issues_urls(client: Client) -> None:
    await call(client, "audit_indexing", site_url=SITE, fetch_pages=True)
    listed = await call(
        client, "audit_indexing", site_url=SITE, issue="not_indexed:crawled-currently-not-indexed"
    )
    assert listed["inspected_now"] == 0
    [url] = listed["urls"]
    assert (url["url"], url["coverage_state"], url["impressions"]) == (
        C,
        "Crawled - currently not indexed",
        0,
    )
    assert url["live"]["redirects"] == [f"{C}/"]
    message = await call_error(client, "audit_indexing", site_url=SITE, issue="nope")
    assert "Current issues:" in message


async def test_stops_for_the_day_when_the_quota_runs_out(api: ApiStub, client: Client) -> None:
    api.on("POST", INSPECT, httpx2.Response(429, json={"error": {"code": 429, "message": "Quota"}}))
    first = await call(client, "audit_indexing", site_url=SITE)
    assert first["quota_exhausted_today"]
    assert any("used up for today" in n for n in first["notes"])
    before = len(api.bodies("index:inspect"))
    await call(client, "audit_indexing", site_url=SITE)
    assert len(api.bodies("index:inspect")) == before


async def test_restart_forgets_the_saved_audit(api: ApiStub, web: WebStub, client: Client) -> None:
    await call(client, "audit_indexing", site_url=SITE)
    restarted = await call(client, "audit_indexing", site_url=SITE, restart=True, inspect=0)
    assert (restarted["inspected"], restarted["urls_in_sitemaps"]) == (0, 4)
    assert web.fetched().count(INDEX) == 2


async def test_never_fetches_private_hosts(web: WebStub, client: Client) -> None:
    web.private.add("example.com")
    result = await call(client, "audit_indexing", site_url=SITE)
    assert result["urls_in_sitemaps"] == 0
    assert "private or local address" in (result["sitemaps"][0]["error"] or "")
    assert web.requests == []


async def test_needs_a_sitemap(api: ApiStub, client: Client) -> None:
    api.on("GET", f"{DOMAIN_PATH}/sitemaps", {"sitemap": []})
    message = await call_error(client, "audit_indexing", site_url=SITE)
    assert "No sitemaps are submitted" in message
    result = await call(client, "audit_indexing", site_url=SITE, sitemap_url=PAGES, inspect=0)
    assert result["urls_in_sitemaps"] == 4
