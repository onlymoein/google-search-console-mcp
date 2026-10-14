"""Resolve what the model calls a site to the exact property string Google expects.

The API is strict: `sc-domain:example.com`, `https://example.com/` and
`https://www.example.com/` are three different properties, and the wrong one returns an
empty result or a 403 rather than a helpful error. Resolution happens against the
account's real property list, so the model never has to guess the format.

Read tools resolve loosely (a bare domain finds its property). Write tools demand the exact
property, so a retried or sloppy delete can never land on a broader property.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import anyio

from gsc_mcp.errors import GuardError, SiteNotFoundError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

DOMAIN_PREFIX = "sc-domain:"
_DEFAULT_PORTS = {"http": 80, "https": 443}


@dataclass(frozen=True, slots=True)
class ParsedUrl:
    scheme: str
    host: str
    port: int
    path: str


def parse_url(url: str) -> ParsedUrl | None:
    """Normalize an http(s) URL for comparison; None if it is not a usable web URL."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.hostname:
        return None
    return ParsedUrl(
        scheme=scheme,
        host=normalize_host(parts.hostname),
        port=port or _DEFAULT_PORTS[scheme],
        path=parts.path or "/",
    )


def normalize_host(host: str) -> str:
    """Lowercase, drop a trailing dot, and use the ASCII (punycode) form of IDN hosts."""
    host = host.lower().rstrip(".")
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        return host


@dataclass(frozen=True, slots=True)
class Property:
    site_url: str
    permission_level: str

    @property
    def is_domain(self) -> bool:
        return self.site_url.startswith(DOMAIN_PREFIX)

    @property
    def domain(self) -> str:
        """For `sc-domain:` properties, the domain; for URL-prefix ones, the host."""
        if self.is_domain:
            return normalize_host(self.site_url.removeprefix(DOMAIN_PREFIX))
        parsed = parse_url(self.site_url)
        return parsed.host if parsed else ""

    @property
    def is_root_prefix(self) -> bool:
        parsed = parse_url(self.site_url)
        return not self.is_domain and parsed is not None and parsed.path == "/"

    def contains(self, url: str) -> bool:
        """Whether a page or sitemap URL belongs to this property."""
        target = parse_url(url)
        if target is None:
            return False
        if self.is_domain:
            return target.host == self.domain or target.host.endswith("." + self.domain)
        prefix = parse_url(self.site_url)
        return (
            prefix is not None
            and (target.scheme, target.host, target.port)
            == (prefix.scheme, prefix.host, prefix.port)
            and target.path.startswith(prefix.path)
        )


@dataclass(frozen=True, slots=True)
class Resolution:
    site_url: str
    requested: str

    @property
    def changed(self) -> bool:
        return self.site_url != self.requested


class SiteResolver:
    """Caches the property list briefly; refetches once on a miss in case it just changed."""

    def __init__(
        self,
        fetch: Callable[[], Awaitable[list[Property]]],
        *,
        ttl_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch
        self._ttl = ttl_seconds
        self._clock = clock
        self._cached: list[Property] | None = None
        self._fetched_at = 0.0
        self._lock = anyio.Lock()

    async def properties(self, *, refresh: bool = False) -> list[Property]:
        async with self._lock:
            stale = self._clock() - self._fetched_at > self._ttl
            if refresh or self._cached is None or stale:
                self._cached = await self._fetch()
                self._fetched_at = self._clock()
            return self._cached

    def invalidate(self) -> None:
        self._cached = None

    async def resolve(self, requested: str) -> Resolution:
        """Loose resolution for read tools: a bare domain or URL finds its property."""
        properties = await self.properties()
        try:
            return Resolution(match(requested, properties).site_url, requested)
        except SiteNotFoundError:
            properties = await self.properties(refresh=True)
            return Resolution(match(requested, properties).site_url, requested)

    async def exact(self, requested: str) -> Property:
        """Strict resolution for write tools: only the property's own site_url is accepted."""
        properties = await self.properties(refresh=True)
        found = exact_match(requested, properties)
        if found is not None:
            return found
        try:
            suggestion = f" Did you mean {match(requested, properties).site_url!r}?"
        except GuardError:
            suggestion = " Call list_sites to see the exact site_url values."
        raise GuardError(
            f"Write tools need a property's exact site_url, and {requested!r} is not one."
            f"{suggestion}"
        )

    async def property_for(self, site_url: str) -> Property:
        for prop in await self.properties():
            if prop.site_url == site_url:
                return prop
        raise SiteNotFoundError(f"{site_url!r} is not a property this account can access.")


def exact_match(requested: str, properties: list[Property]) -> Property | None:
    """The property whose site_url this is, allowing only case and a missing trailing slash."""
    value = requested.strip()
    for candidate in (value, _with_trailing_slash(value)):
        for prop in properties:
            if prop.site_url == candidate:
                return prop
    lowered = value.lower()
    for prop in properties:
        if prop.site_url.lower() in (lowered, _with_trailing_slash(lowered)):
            return prop
    return None


def match(requested: str, properties: list[Property]) -> Property:
    """Pick the property `requested` refers to, or explain why there is no single answer."""
    value = requested.strip()
    if not value:
        raise GuardError("site_url is empty. Call list_sites to see the available properties.")
    exact = exact_match(value, properties)
    if exact is not None:
        return exact
    if value.lower().startswith(DOMAIN_PREFIX):
        raise _not_found(value, properties)

    url = value if "://" in value else f"https://{value}"
    parsed = parse_url(url)
    if parsed is None:
        raise _not_found(value, properties)
    has_path = parsed.path != "/" or "?" in value

    if "://" in value or has_path:
        # A specific URL that is not itself a property: don't silently widen or narrow the
        # scope. Name the properties that contain it so the model can choose and filter.
        containing = [p for p in properties if p.contains(url)]
        if len(containing) == 1 and not has_path:
            return containing[0]
        hint = ""
        if containing and has_path:
            hint = (
                " It is a URL inside a property, not a property itself: pass one of these as "
                "site_url and filter by page instead."
            )
        raise _not_found(value, containing or properties, extra=hint)

    host = parsed.host
    domains = [
        p for p in properties if p.is_domain and (host == p.domain or host.endswith("." + p.domain))
    ]
    if domains:
        # The most specific domain property wins: shop.example.com over example.com.
        return max(domains, key=lambda p: len(p.domain))

    # Only whole-host URL-prefix properties; a subfolder property is never a silent match.
    prefixes = [p for p in properties if p.is_root_prefix and p.domain == host]
    if len(prefixes) == 1:
        return prefixes[0]
    if len(prefixes) > 1:
        raise GuardError(
            f"{requested!r} matches several properties: "
            + ", ".join(repr(p.site_url) for p in prefixes)
            + ". Pass the exact site_url."
        )
    related = [p for p in properties if p.domain == host]
    raise _not_found(value, related or properties)


def _not_found(value: str, candidates: list[Property], *, extra: str = "") -> SiteNotFoundError:
    if not candidates:
        return SiteNotFoundError(
            f"{value!r} is not available: this account cannot see any Search Console "
            "properties. A service account must be added as a user on the property in "
            "Search Console > Settings > Users and permissions."
        )
    listed = ", ".join(repr(p.site_url) for p in candidates[:20])
    more = f" (and {len(candidates) - 20} more)" if len(candidates) > 20 else ""
    return SiteNotFoundError(
        f"{value!r} is not a property this account can access.{extra} "
        f"Candidates: {listed}{more}. Domain properties look like 'sc-domain:example.com'; "
        "URL-prefix properties look like 'https://www.example.com/'."
    )


def _with_trailing_slash(value: str) -> str:
    if value.startswith(DOMAIN_PREFIX) or value.endswith("/") or "://" not in value:
        return value
    return value + "/"
