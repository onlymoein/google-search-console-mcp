from __future__ import annotations

import gzip

import httpx2
import pytest

from gsc_mcp.sites import Property
from gsc_mcp.web import PAGE_BYTES, FetchError, WebClient, gunzip
from tests.conftest import WebStub

pytestmark = pytest.mark.anyio

PROP = Property("sc-domain:example.com", "siteOwner")


def client(web: WebStub) -> WebClient:
    return WebClient(transport=httpx2.MockTransport(web.handle), resolve=web.resolve)


async def test_follows_redirects_inside_the_property(web: WebStub) -> None:
    web.on("https://example.com/a", httpx2.Response(301, headers={"location": "/b"}))
    web.on("https://example.com/b", httpx2.Response(200, text="hello"))
    fetched = await client(web).fetch(
        "https://example.com/a", allowed=PROP.contains, max_bytes=PAGE_BYTES
    )
    assert (fetched.status, fetched.final_url, fetched.body) == (
        200,
        "https://example.com/b",
        b"hello",
    )
    assert fetched.redirects == ["https://example.com/b"]


async def test_reports_but_does_not_follow_a_redirect_out_of_the_property(web: WebStub) -> None:
    web.on(
        "https://example.com/a", httpx2.Response(302, headers={"location": "https://evil.test/"})
    )
    fetched = await client(web).fetch(
        "https://example.com/a", allowed=PROP.contains, max_bytes=PAGE_BYTES
    )
    assert fetched.left_property
    assert fetched.final_url == "https://evil.test/"
    assert web.fetched() == ["https://example.com/a"]


async def test_refuses_urls_outside_the_property(web: WebStub) -> None:
    with pytest.raises(FetchError, match="outside the property"):
        await client(web).fetch("https://other.org/", allowed=PROP.contains, max_bytes=10)
    assert web.requests == []


async def test_refuses_hosts_on_private_addresses(web: WebStub) -> None:
    # A domain property covers every subdomain, and one may point into a private network.
    web.private.add("intranet.example.com")
    with pytest.raises(FetchError, match="private or local address"):
        await client(web).fetch(
            "https://intranet.example.com/", allowed=PROP.contains, max_bytes=10
        )
    assert web.requests == []


async def test_stops_redirect_loops(web: WebStub) -> None:
    web.on("https://example.com/a", httpx2.Response(301, headers={"location": "/a"}))
    with pytest.raises(FetchError, match="redirects more than"):
        await client(web).fetch("https://example.com/a", allowed=PROP.contains, max_bytes=10)


async def test_caps_the_body(web: WebStub) -> None:
    web.on("https://example.com/big", httpx2.Response(200, content=b"x" * 100))
    fetched = await client(web).fetch(
        "https://example.com/big", allowed=PROP.contains, max_bytes=10
    )
    assert (fetched.body, fetched.truncated) == (b"x" * 10, True)


def test_gunzip_passes_plain_data_through_and_refuses_bombs() -> None:
    assert gunzip(b"<urlset/>") == b"<urlset/>"
    assert gunzip(gzip.compress(b"<urlset/>")) == b"<urlset/>"
    with pytest.raises(FetchError, match="50 MB"):
        gunzip(gzip.compress(b"\0" * 2000), max_bytes=1000)
