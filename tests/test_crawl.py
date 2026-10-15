from __future__ import annotations

import httpx2
import pytest

from gsc_mcp.crawl import page_signals, parse_sitemap
from gsc_mcp.web import FetchError

URLSET = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc> https://example.com/a </loc><lastmod>2026-10-01</lastmod></url>
  <url><loc>https://example.com/b?x=1&amp;y=2</loc></url>
</urlset>"""
INDEX = b"""<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/pages.xml</loc></sitemap>
</sitemapindex>"""


def test_parses_urlsets_and_indexes() -> None:
    urlset = parse_sitemap(URLSET)
    assert (urlset.kind, urlset.locations) == (
        "urlset",
        ["https://example.com/a", "https://example.com/b?x=1&y=2"],
    )
    index = parse_sitemap(INDEX)
    assert (index.kind, index.locations) == ("sitemapindex", ["https://example.com/pages.xml"])


def test_parses_text_sitemaps() -> None:
    text = b"https://example.com/a\n\nnot a url\r\nhttps://example.com/b\n"
    assert parse_sitemap(text).locations == ["https://example.com/a", "https://example.com/b"]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b'<!DOCTYPE x [<!ENTITY a "aaaa">]><urlset>&a;</urlset>', "DTD"),
        (b"<rss><channel/></rss>", "RSS"),
        (b"<urlset><url>", "not valid XML"),
    ],
)
def test_refuses_what_is_not_a_safe_sitemap(data: bytes, message: str) -> None:
    with pytest.raises(FetchError, match=message):
        parse_sitemap(data)


def html(head: str, *, headers: dict[str, str] | None = None) -> tuple[bytes, httpx2.Headers]:
    page = f"<html><head>{head}</head><body><meta name='robots' content='noindex'></body></html>"
    return page.encode(), httpx2.Headers({"content-type": "text/html", **(headers or {})})


@pytest.mark.parametrize(
    ("head", "headers", "noindex", "source"),
    [
        ("<meta name='robots' content='index, follow'>", None, False, None),
        ("<meta name='ROBOTS' content='NOINDEX, follow'>", None, True, "meta robots tag"),
        ("<meta name='googlebot' content='none'>", None, True, "meta googlebot tag"),
        ("", {"x-robots-tag": "noindex"}, True, "X-Robots-Tag header"),
        ("", {"x-robots-tag": "googlebot: noindex"}, True, "X-Robots-Tag header"),
        ("", {"x-robots-tag": "otherbot: noindex"}, False, None),
        ("", {"x-robots-tag": "unavailable_after: 25 Jun 2030 15:00:00 PST"}, False, None),
    ],
)
def test_reads_robots_directives_for_google(
    head: str, headers: dict[str, str] | None, noindex: bool, source: str | None
) -> None:
    # The body's robots meta is ignored: only the head counts.
    body, parsed = html(head, headers=headers)
    signals = page_signals(body, parsed, "https://example.com/a")
    assert (signals.noindex, signals.noindex_source) == (noindex, source)


def test_reads_the_canonical_from_the_head_or_link_header() -> None:
    body, headers = html("<link rel='canonical' href='/b'>")
    assert page_signals(body, headers, "https://example.com/a").canonical == "https://example.com/b"
    body, headers = html(
        "",
        headers={"link": '</font.woff2>; rel=preload, <https://example.com/c>; rel="canonical"'},
    )
    assert page_signals(body, headers, "https://example.com/a").canonical == "https://example.com/c"
