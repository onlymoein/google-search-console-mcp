"""Sitemaps: list and get; submit and delete when writes are enabled."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field

from gsc_mcp.errors import GuardError
from gsc_mcp.models import SitemapContent, SitemapInfo, SitemapList, WriteResult
from gsc_mcp.tools._common import DESTRUCTIVE, READ_ONLY, WRITE, Deps, SiteUrl

if TYPE_CHECKING:
    from mcp.server import MCPServer

    from gsc_mcp.sites import Property

ExactSiteUrl = Annotated[
    str, Field(description="The property's exact site_url, as listed by list_sites.")
]
SitemapUrl = Annotated[
    str, Field(description="Full URL of the sitemap, e.g. 'https://www.example.com/sitemap.xml'.")
]


def register(mcp: MCPServer, deps: Deps) -> None:
    async def checked(site_url: str, sitemap_url: str, *, exact: bool = False) -> Property:
        if exact:
            prop = await deps.sites.exact(site_url)
        else:
            resolution = await deps.sites.resolve(site_url)
            prop = await deps.sites.property_for(resolution.site_url)
        if not prop.contains(sitemap_url):
            raise GuardError(
                f"{sitemap_url!r} is not inside the property {prop.site_url!r}. A sitemap must "
                "be hosted within the property it is submitted to."
            )
        return prop

    @mcp.tool(title="List sitemaps", annotations=READ_ONLY)
    async def list_sitemaps(
        site_url: SiteUrl,
        sitemap_index: Annotated[
            str | None,
            Field(description="Only list the sitemaps inside this sitemap index URL."),
        ] = None,
    ) -> SitemapList:
        """List submitted sitemaps with their status: last submitted and downloaded, errors,
        warnings, whether processing is pending, and URL counts by content type."""
        resolution = await deps.sites.resolve(site_url)
        entries = await deps.client.list_sitemaps(resolution.site_url, sitemap_index)
        return SitemapList(site_url=resolution.site_url, sitemaps=[to_info(e) for e in entries])

    @mcp.tool(title="Get a sitemap", annotations=READ_ONLY)
    async def get_sitemap(site_url: SiteUrl, sitemap_url: SitemapUrl) -> SitemapInfo:
        """Status of one submitted sitemap."""
        prop = await checked(site_url, sitemap_url)
        return to_info(await deps.client.get_sitemap(prop.site_url, sitemap_url.strip()))

    if not deps.settings.enable_writes:
        return

    @mcp.tool(title="Submit a sitemap", annotations=WRITE)
    async def submit_sitemap(site_url: ExactSiteUrl, sitemap_url: SitemapUrl) -> WriteResult:
        """Submit (or resubmit) a sitemap so Google fetches it. Safe to repeat."""
        prop = await checked(site_url, sitemap_url, exact=True)
        await deps.client.submit_sitemap(prop.site_url, sitemap_url.strip())
        return WriteResult(
            action="submit_sitemap",
            site_url=prop.site_url,
            target=sitemap_url.strip(),
            done=True,
            notes=[
                "Google processes submitted sitemaps asynchronously; check back with get_sitemap."
            ],
        )

    @mcp.tool(title="Delete a sitemap", annotations=DESTRUCTIVE)
    async def delete_sitemap(site_url: ExactSiteUrl, sitemap_url: SitemapUrl) -> WriteResult:
        """Remove a sitemap from Search Console. Google may still know the URLs in it, and
        the file itself is not touched. Confirm with the user first."""
        prop = await checked(site_url, sitemap_url, exact=True)
        await deps.client.delete_sitemap(prop.site_url, sitemap_url.strip())
        return WriteResult(
            action="delete_sitemap", site_url=prop.site_url, target=sitemap_url.strip(), done=True
        )


def to_info(entry: dict[str, Any]) -> SitemapInfo:
    return SitemapInfo(
        path=str(entry.get("path", "")),
        type=entry.get("type"),
        is_index=bool(entry.get("isSitemapsIndex", False)),
        is_pending=bool(entry.get("isPending", False)),
        last_submitted=entry.get("lastSubmitted"),
        last_downloaded=entry.get("lastDownloaded"),
        # The API sends int64 counts as strings.
        warnings=int(entry.get("warnings", 0) or 0),
        errors=int(entry.get("errors", 0) or 0),
        # `indexed` is deprecated by Google and always unreliable, so it is left out.
        contents=[
            SitemapContent(type=str(c.get("type", "")), submitted=int(c.get("submitted", 0) or 0))
            for c in entry.get("contents", [])
        ],
    )
