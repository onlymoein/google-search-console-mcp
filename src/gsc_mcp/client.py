"""A small async client for the Search Console API.

The API is eleven endpoints, so this talks REST directly over `httpx2` (already a
dependency of the MCP SDK) instead of using `google-api-python-client`, which is in
maintenance mode and built on `httplib2`, which is not thread-safe. Being async-native is
what lets batch URL inspection run concurrently without sharing a non-thread-safe transport.
"""

from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote

import anyio
import httpx2

from gsc_mcp import __version__
from gsc_mcp.errors import ApiError, QuotaError

if TYPE_CHECKING:
    from gsc_mcp.auth import TokenSource

logger = logging.getLogger(__name__)

BASE_URL = "https://searchconsole.googleapis.com/"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

JSON = dict[str, Any]


def encode(segment: str) -> str:
    """Percent-encode a path segment. `sc-domain:` colons and URL slashes must be escaped."""
    return quote(segment, safe="")


class SearchConsoleClient:
    def __init__(
        self,
        tokens: TokenSource,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        timeout: float = 60.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        backoff_cap: float = 8.0,
        max_wait: float = 30.0,
    ) -> None:
        self._tokens = tokens
        self._transport = transport
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._max_wait = max_wait  # a longer Retry-After fails fast instead of blocking the tool
        self._http: httpx2.AsyncClient | None = None

    # --- Sites -----------------------------------------------------------------------

    async def list_sites(self) -> list[JSON]:
        data = await self._request("GET", "webmasters/v3/sites")
        return cast("list[JSON]", data.get("siteEntry", []))

    async def get_site(self, site_url: str) -> JSON:
        return await self._request("GET", f"webmasters/v3/sites/{encode(site_url)}")

    async def add_site(self, site_url: str) -> None:
        await self._request("PUT", f"webmasters/v3/sites/{encode(site_url)}")

    async def delete_site(self, site_url: str) -> None:
        await self._request("DELETE", f"webmasters/v3/sites/{encode(site_url)}")

    # --- Search Analytics -------------------------------------------------------------

    async def query_search_analytics(self, site_url: str, body: JSON) -> JSON:
        path = f"webmasters/v3/sites/{encode(site_url)}/searchAnalytics/query"
        return await self._request("POST", path, json=body)

    # --- Sitemaps ---------------------------------------------------------------------

    async def list_sitemaps(self, site_url: str, sitemap_index: str | None = None) -> list[JSON]:
        params = {"sitemapIndex": sitemap_index} if sitemap_index else None
        path = f"webmasters/v3/sites/{encode(site_url)}/sitemaps"
        data = await self._request("GET", path, params=params)
        return cast("list[JSON]", data.get("sitemap", []))

    async def get_sitemap(self, site_url: str, feedpath: str) -> JSON:
        path = f"webmasters/v3/sites/{encode(site_url)}/sitemaps/{encode(feedpath)}"
        return await self._request("GET", path)

    async def submit_sitemap(self, site_url: str, feedpath: str) -> None:
        path = f"webmasters/v3/sites/{encode(site_url)}/sitemaps/{encode(feedpath)}"
        await self._request("PUT", path)

    async def delete_sitemap(self, site_url: str, feedpath: str) -> None:
        path = f"webmasters/v3/sites/{encode(site_url)}/sitemaps/{encode(feedpath)}"
        await self._request("DELETE", path)

    # --- URL Inspection ---------------------------------------------------------------

    async def inspect_url(self, site_url: str, url: str, language_code: str = "en-US") -> JSON:
        body = {"inspectionUrl": url, "siteUrl": site_url, "languageCode": language_code}
        data = await self._request("POST", "v1/urlInspection/index:inspect", json=body)
        return cast("JSON", data.get("inspectionResult", {}))

    # --- Plumbing ---------------------------------------------------------------------

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def _client(self) -> httpx2.AsyncClient:
        if self._http is None:
            self._http = httpx2.AsyncClient(
                base_url=BASE_URL,
                timeout=self._timeout,
                transport=self._transport,
                headers={"User-Agent": f"open-gsc-mcp/{__version__}"},
            )
        return self._http

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: JSON | None = None,
        params: dict[str, str] | None = None,
    ) -> JSON:
        refreshed = False
        attempt = 0
        while True:
            headers = {"Authorization": f"Bearer {await self._tokens.token()}"}
            try:
                response = await self._client().request(
                    method, path, json=json, params=params, headers=headers
                )
            except httpx2.TransportError as exc:
                if attempt < self._max_retries:
                    attempt += 1
                    await self._sleep(attempt, None)
                    continue
                raise ApiError(
                    f"Could not reach the Search Console API ({type(exc).__name__}: {exc}).",
                    status=0,
                ) from exc

            if response.status_code == 401 and not refreshed:
                refreshed = True
                await self._tokens.invalidate()
                continue
            if response.status_code in RETRY_STATUSES and attempt < self._max_retries:
                wait = _retry_after(response)
                if wait is None or wait <= self._max_wait:
                    attempt += 1
                    await self._sleep(attempt, wait)
                    continue
            if response.is_success:
                return _json_body(response)
            raise _api_error(response)

    async def _sleep(self, attempt: int, retry_after: float | None) -> None:
        delay = min(self._backoff_cap, self._backoff_base * 2 ** (attempt - 1))
        delay *= 0.5 + random.random() / 2  # noqa: S311 - jitter, not crypto
        if retry_after is not None:
            delay = max(delay, retry_after)
        logger.debug("Retrying Search Console request in %.2fs (attempt %d)", delay, attempt)
        await anyio.sleep(delay)


def _retry_after(response: httpx2.Response) -> float | None:
    """Seconds from a numeric Retry-After header; None if absent or not a number."""
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _json_body(response: httpx2.Response) -> JSON:
    if not response.content:
        return {}
    try:
        data: Any = response.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise ApiError(
            "Search Console answered with something that is not a JSON object (HTTP "
            f"{response.status_code}, {response.headers.get('Content-Type', 'no content type')}). "
            "A proxy, firewall or captive portal may be intercepting requests.",
            status=response.status_code,
        )
    return cast("JSON", data)


def _api_error(response: httpx2.Response) -> ApiError:
    status = response.status_code
    message, reason = _google_error(response)
    if status == 429:
        return QuotaError(
            "Google's Search Console quota is used up for now (HTTP 429). Limits: Search "
            "Analytics 1,200 queries/minute per site and per user; URL Inspection 2,000/day "
            f"and 600/minute per site. Google said: {message}",
            status=status,
            reason=reason,
        )
    if status == 401:
        hint = "Google rejected the credentials. Run `open-gsc-mcp auth status` to check them."
    elif status == 403:
        hint = (
            "This account has no permission for that resource. Check list_sites for the "
            "properties and permission levels this account has; a service account must be "
            "added as a user on the property in Search Console."
        )
    elif status == 404:
        hint = "Google could not find that resource."
    elif status >= 500:
        hint = "Search Console had a server error and retries did not help; try again later."
    else:
        hint = "Google rejected the request."
    return ApiError(f"{hint} (HTTP {status}) Google said: {message}", status=status, reason=reason)


def _google_error(response: httpx2.Response) -> tuple[str, str | None]:
    """Pull the human message and reason out of Google's error envelope."""
    try:
        payload: Any = response.json()
    except ValueError:
        return (response.text.strip()[:500] or response.reason_phrase, None)
    if not isinstance(payload, dict):
        return (str(cast("object", payload))[:500], None)
    error: Any = cast("JSON", payload).get("error")
    if not isinstance(error, dict):
        return (str(cast("object", payload))[:500], None)
    error = cast("JSON", error)
    message = str(error.get("message") or response.reason_phrase)
    reason: str | None = None
    details: Any = error.get("errors")
    if isinstance(details, list) and details:
        first: Any = cast("list[Any]", details)[0]
        if isinstance(first, dict):
            reason = cast("JSON", first).get("reason")
    return message, reason or cast("str | None", error.get("status"))
