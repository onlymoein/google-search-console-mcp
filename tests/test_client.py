from __future__ import annotations

import httpx2
import pytest

from gsc_mcp.client import SearchConsoleClient
from gsc_mcp.errors import ApiError, QuotaError
from tests.conftest import ApiStub, FakeTokens

pytestmark = pytest.mark.anyio

QUERY = "webmasters/v3/sites/sc-domain%3Aexample.com/searchAnalytics/query"


def google_error(status: int, message: str, reason: str = "badRequest") -> httpx2.Response:
    return httpx2.Response(
        status,
        json={"error": {"code": status, "message": message, "errors": [{"reason": reason}]}},
    )


async def test_percent_encodes_site_urls(api: ApiStub, gsc: SearchConsoleClient) -> None:
    api.on("POST", QUERY, {"rows": []})
    api.on(
        "GET",
        "webmasters/v3/sites/https%3A%2F%2Fwww.example.com%2F/sitemaps/"
        "https%3A%2F%2Fwww.example.com%2Fsitemap.xml",
        {"path": "https://www.example.com/sitemap.xml"},
    )
    await gsc.query_search_analytics("sc-domain:example.com", {"startDate": "2026-10-01"})
    sitemap = await gsc.get_sitemap(
        "https://www.example.com/", "https://www.example.com/sitemap.xml"
    )
    assert sitemap["path"] == "https://www.example.com/sitemap.xml"
    assert api.requests[0].headers["Authorization"] == "Bearer test-token"


async def test_retries_server_errors(api: ApiStub, gsc: SearchConsoleClient) -> None:
    api.on("POST", QUERY, httpx2.Response(503), httpx2.Response(500), {"rows": [{"keys": ["a"]}]})
    data = await gsc.query_search_analytics("sc-domain:example.com", {})
    assert data == {"rows": [{"keys": ["a"]}]}
    assert len(api.requests) == 3


async def test_quota_error_after_retries(api: ApiStub, gsc: SearchConsoleClient) -> None:
    api.on("POST", QUERY, google_error(429, "Quota exceeded", "rateLimitExceeded"))
    with pytest.raises(QuotaError, match="Quota exceeded") as raised:
        await gsc.query_search_analytics("sc-domain:example.com", {})
    assert raised.value.status == 429
    assert raised.value.reason == "rateLimitExceeded"
    assert len(api.requests) == 4  # first try + 3 retries


async def test_refreshes_token_once_on_401(
    api: ApiStub, gsc: SearchConsoleClient, tokens: FakeTokens
) -> None:
    api.on("POST", QUERY, google_error(401, "expired"), {"rows": []})
    assert await gsc.query_search_analytics("sc-domain:example.com", {}) == {"rows": []}
    assert tokens.invalidated == 1


async def test_does_not_retry_client_errors(api: ApiStub, gsc: SearchConsoleClient) -> None:
    api.on("POST", QUERY, google_error(400, "Request contains an invalid argument."))
    with pytest.raises(ApiError, match="invalid argument") as raised:
        await gsc.query_search_analytics("sc-domain:example.com", {})
    assert raised.value.status == 400
    assert len(api.requests) == 1


async def test_permission_error_explains_service_accounts(
    api: ApiStub, gsc: SearchConsoleClient
) -> None:
    api.on("POST", QUERY, google_error(403, "User does not have sufficient permission"))
    with pytest.raises(ApiError, match="added as a user on the property"):
        await gsc.query_search_analytics("sc-domain:example.com", {})


async def test_non_json_error_body(api: ApiStub, gsc: SearchConsoleClient) -> None:
    api.on("POST", QUERY, httpx2.Response(418, text="teapot"))
    with pytest.raises(ApiError, match="teapot"):
        await gsc.query_search_analytics("sc-domain:example.com", {})


async def test_retries_then_reports_network_failures(tokens: FakeTokens) -> None:
    attempts = 0

    def fail(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        raise httpx2.ConnectError("connection refused", request=request)

    client = SearchConsoleClient(
        tokens, transport=httpx2.MockTransport(fail), backoff_base=0, backoff_cap=0
    )
    with pytest.raises(ApiError, match="Could not reach") as raised:
        await client.list_sites()
    assert raised.value.status == 0
    assert attempts == 4


async def test_empty_success_body(api: ApiStub, gsc: SearchConsoleClient) -> None:
    api.on(
        "PUT",
        "webmasters/v3/sites/sc-domain%3Aexample.com/sitemaps/https%3A%2F%2Fexample.com%2Fs.xml",
        httpx2.Response(204),
    )
    await gsc.submit_sitemap("sc-domain:example.com", "https://example.com/s.xml")


async def test_inspect_sends_the_documented_body(api: ApiStub, gsc: SearchConsoleClient) -> None:
    api.on("POST", "v1/urlInspection/index:inspect", {"inspectionResult": {"x": 1}})
    result = await gsc.inspect_url("sc-domain:example.com", "https://example.com/a", "de-DE")
    assert result == {"x": 1}
    assert api.bodies("index:inspect") == [
        {
            "inspectionUrl": "https://example.com/a",
            "siteUrl": "sc-domain:example.com",
            "languageCode": "de-DE",
        }
    ]


async def test_non_json_success_is_explained(api: ApiStub, gsc: SearchConsoleClient) -> None:
    api.on("POST", QUERY, httpx2.Response(200, text="<html>Sign in to Wi-Fi</html>"))
    with pytest.raises(ApiError, match="captive portal"):
        await gsc.query_search_analytics("sc-domain:example.com", {})


async def test_long_retry_after_fails_fast(api: ApiStub, gsc: SearchConsoleClient) -> None:
    api.on("POST", QUERY, httpx2.Response(503, headers={"Retry-After": "120"}))
    with pytest.raises(ApiError, match="HTTP 503"):
        await gsc.query_search_analytics("sc-domain:example.com", {})
    assert len(api.requests) == 1
