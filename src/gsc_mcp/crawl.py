"""Reading what an indexing audit fetches: sitemaps, and the signals on a live page that
decide whether Google may index it (robots directives and the canonical URL)."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Literal
from urllib.parse import urljoin
from xml.etree import ElementTree

from gsc_mcp.web import FetchError

if TYPE_CHECKING:
    import httpx2

# Sitemaps never need a DTD; refusing one rules out entity-expansion attacks.
_DTD = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
_LINK_CANONICAL = re.compile(r'<([^>]+)>\s*;[^,]*\brel\s*=\s*"?canonical"?', re.IGNORECASE)
# X-Robots-Tag directives that take a value after a colon, unlike a "googlebot:" prefix.
_VALUE_DIRECTIVES = frozenset(
    {"unavailable_after", "max-snippet", "max-image-preview", "max-video-preview"}
)


@dataclass(slots=True)
class Sitemap:
    kind: Literal["urlset", "sitemapindex"]
    locations: list[str] = field(default_factory=list[str])


def parse_sitemap(data: bytes) -> Sitemap:
    """Parse an XML sitemap or sitemap index, or a plain-text sitemap (one URL per line)."""
    stripped = data.lstrip()
    if not stripped.startswith(b"<"):
        lines = stripped.decode("utf-8", "replace").splitlines()
        return Sitemap("urlset", [u.strip() for u in lines if u.strip().startswith("http")])
    if _DTD.search(data):
        raise FetchError("The sitemap declares a DTD or entities, so it was not parsed.")
    kind: Literal["urlset", "sitemapindex"] | None = None
    locations: list[str] = []
    try:
        # Safe here: DTDs are refused above, so there are no entities to expand.
        for event, element in ElementTree.iterparse(io.BytesIO(data), events=("start", "end")):  # noqa: S314
            local = element.tag.rsplit("}", 1)[-1]
            if event == "start" and kind is None:
                if local == "urlset":
                    kind = "urlset"
                elif local == "sitemapindex":
                    kind = "sitemapindex"
                else:
                    raise FetchError(
                        f"Not a sitemap: the root element is <{local}>. RSS and Atom feeds "
                        "are not read."
                    )
            elif event == "end" and local == "loc" and element.text and element.text.strip():
                locations.append(element.text.strip())
            elif event == "end" and local in ("url", "sitemap"):
                element.clear()
    except ElementTree.ParseError as exc:
        raise FetchError(f"The sitemap is not valid XML: {exc}") from exc
    if kind is None:
        raise FetchError("The sitemap is empty.")
    return Sitemap(kind, locations)


@dataclass(slots=True)
class PageSignals:
    noindex: bool = False
    noindex_source: str | None = None
    canonical: str | None = None


def page_signals(body: bytes, headers: httpx2.Headers, url: str) -> PageSignals:
    """Robots directives for Google and the declared canonical, from headers and HTML head."""
    signals = PageSignals()
    for value in headers.get_list("x-robots-tag"):
        if _header_noindex(value):
            signals.noindex, signals.noindex_source = True, "X-Robots-Tag header"
    for value in headers.get_list("link"):
        match = _LINK_CANONICAL.search(value)
        if match:
            signals.canonical = urljoin(url, match[1].strip())
    content_type = headers.get("content-type", "")
    if body and ("html" in content_type or not content_type):
        head = _HeadParser()
        head.feed(body.decode(_charset(content_type), "replace"))
        for name, content in head.robots:
            if not signals.noindex and _noindex(content):
                signals.noindex, signals.noindex_source = True, f"meta {name} tag"
        if head.canonical and not signals.canonical:
            signals.canonical = urljoin(url, head.canonical.strip())
    return signals


def _noindex(directives: str) -> bool:
    return any(d.strip().lower() in ("noindex", "none") for d in directives.split(","))


def _header_noindex(value: str) -> bool:
    agent, sep, rest = value.partition(":")
    if sep and agent.strip().lower() not in _VALUE_DIRECTIVES and " " not in agent.strip():
        # "googlebot: noindex" applies to Google; "otherbot: noindex" does not.
        return agent.strip().lower() == "googlebot" and _noindex(rest)
    return _noindex(value)


def _charset(content_type: str) -> str:
    match = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
    if match:
        try:
            "".encode(match[1])
        except LookupError:
            return "utf-8"
        return match[1]
    return "utf-8"


class _HeadParser(HTMLParser):
    """Collects robots meta tags and the canonical link from the head; stops at the body."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.robots: list[tuple[str, str]] = []
        self.canonical: str | None = None
        self._done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._done:
            return
        values = {name.lower(): value or "" for name, value in attrs}
        if tag == "meta" and values.get("name", "").lower() in ("robots", "googlebot"):
            self.robots.append((values["name"].lower(), values.get("content", "")))
        elif tag == "link" and "canonical" in values.get("rel", "").lower().split():
            self.canonical = self.canonical or values.get("href") or None
        elif tag == "body":
            self._done = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "head":
            self._done = True
