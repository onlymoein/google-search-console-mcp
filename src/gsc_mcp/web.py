"""HTTP beyond the Search Console API: Google's update feed, and an indexing audit's reads of
the property's own sitemaps and pages.

Site fetches are fenced. Every URL, including each redirect hop, must be inside the
property, and its host must resolve only to public addresses: a domain property covers
every subdomain, and one of them may point into a private network. Bodies are capped, and
nothing fetched is ever executed or followed beyond the fence.
"""

from __future__ import annotations

import ipaddress
import socket
import zlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlsplit

import anyio
import httpx2

from gsc_mcp import __version__
from gsc_mcp.errors import GSCError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

USER_AGENT = f"open-gsc-mcp/{__version__} (+https://github.com/onlymoein/google-search-console-mcp)"
MAX_REDIRECTS = 5
PAGE_BYTES = 512 * 1024  # robots meta and canonical tags live in the head
SITEMAP_BYTES = 50 * 1024 * 1024  # Google's limit for one sitemap, uncompressed
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_GZIP_MAGIC = b"\x1f\x8b"


class FetchError(GSCError):
    """A URL could not be fetched, or was not fetched because it is outside the fence."""


@dataclass(slots=True)
class Fetched:
    url: str
    final_url: str
    status: int
    redirects: list[str] = field(default_factory=list[str])
    headers: httpx2.Headers = field(default_factory=httpx2.Headers)
    body: bytes = b""
    truncated: bool = False
    left_property: bool = False


async def system_resolve(host: str, port: int) -> list[str]:
    infos = await anyio.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


class WebClient:
    def __init__(
        self,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        resolve: Callable[[str, int], Awaitable[list[str]]] = system_resolve,
        timeout: float = 20.0,
    ) -> None:
        self._transport = transport
        self._resolve = resolve
        self._timeout = timeout
        self._http: httpx2.AsyncClient | None = None

    async def get_json(self, url: str) -> Any:
        """GET a fixed, trusted JSON endpoint (Google's update feed)."""
        try:
            response = await self._client().get(url)
            response.raise_for_status()
            return response.json()
        except (httpx2.HTTPError, ValueError) as exc:
            raise FetchError(f"Could not read {url}: {exc}") from exc

    async def fetch(self, url: str, *, allowed: Callable[[str], bool], max_bytes: int) -> Fetched:
        """GET a URL of the property, following redirects only while they stay inside it.

        A redirect that leaves the property is reported (left_property) rather than followed.
        """
        current = url
        redirects: list[str] = []
        for _ in range(MAX_REDIRECTS + 1):
            if not allowed(current):
                raise FetchError(f"{current} is outside the property, so it was not fetched.")
            await self._check_public(current)
            try:
                async with self._client().stream("GET", current) as response:
                    location = response.headers.get("location")
                    if response.status_code in _REDIRECTS and location:
                        target = urljoin(current, location)
                        redirects.append(target)
                        if not allowed(target):
                            return Fetched(
                                url=url,
                                final_url=target,
                                status=response.status_code,
                                redirects=redirects,
                                headers=response.headers,
                                left_property=True,
                            )
                        current = target
                        continue
                    body, truncated = await _read(response, max_bytes)
                    return Fetched(
                        url=url,
                        final_url=current,
                        status=response.status_code,
                        redirects=redirects,
                        headers=response.headers,
                        body=body,
                        truncated=truncated,
                    )
            except httpx2.HTTPError as exc:
                raise FetchError(f"Could not fetch {current}: {exc}") from exc
        raise FetchError(f"{url} redirects more than {MAX_REDIRECTS} times.")

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def _client(self) -> httpx2.AsyncClient:
        if self._http is None:
            self._http = httpx2.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
                follow_redirects=False,
                headers={"User-Agent": USER_AGENT},
            )
        return self._http

    async def _check_public(self, url: str) -> None:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port or (443 if parts.scheme == "https" else 80)
        try:
            addresses = await self._resolve(host, port)
        except OSError as exc:
            raise FetchError(f"Could not resolve {host}: {exc}") from exc
        private = [a for a in addresses if not ipaddress.ip_address(a.split("%")[0]).is_global]
        if not addresses or private:
            raise FetchError(
                f"{host} resolves to a private or local address, so it was not fetched."
            )


def gunzip(data: bytes, *, max_bytes: int = SITEMAP_BYTES) -> bytes:
    """Decompress a .gz sitemap, refusing one that inflates past Google's 50 MB limit."""
    if not data.startswith(_GZIP_MAGIC):
        return data
    inflater = zlib.decompressobj(wbits=31)
    try:
        out = inflater.decompress(data, max_bytes)
    except zlib.error as exc:
        raise FetchError(f"The sitemap is not valid gzip: {exc}") from exc
    if inflater.unconsumed_tail:
        raise FetchError("The sitemap is larger than 50 MB uncompressed, Google's limit.")
    return out


async def _read(response: httpx2.Response, max_bytes: int) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        take = chunk[: max_bytes - size]
        chunks.append(take)
        size += len(take)
        if size >= max_bytes:
            return b"".join(chunks), True
    return b"".join(chunks), False
