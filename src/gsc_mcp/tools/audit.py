"""The indexing audit tool: every sitemap URL, inspected over as many days as it takes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from gsc_mcp.models import IndexingAudit
from gsc_mcp.tools._audit import Auditor
from gsc_mcp.tools._common import LARGE_RESULT, Deps, SiteUrl

if TYPE_CHECKING:
    from mcp.server import MCPServer

# Changes nothing in Search Console or on the site, but reads the site's own sitemaps and,
# if asked, its pages: the open world beyond Google's API.
AUDIT = ToolAnnotations(read_only_hint=True, open_world_hint=True)
MAX_PER_CALL = 500
DEFAULT_LIVE = 50


def register(mcp: MCPServer, deps: Deps) -> None:
    @mcp.tool(title="Audit indexing", annotations=AUDIT, meta=LARGE_RESULT)
    async def audit_indexing(
        site_url: SiteUrl,
        ctx: Context,
        inspect: Annotated[
            int,
            Field(
                ge=0,
                le=MAX_PER_CALL,
                description="URLs to inspect in this call. Each takes a few seconds and they "
                "run 10 at a time, so 50 is about 30 seconds. 0 only reports.",
            ),
        ] = 50,
        fetch_pages: Annotated[
            bool,
            Field(
                description="Also fetch inspected pages directly (only URLs inside the "
                "property), as many as inspect or 50 if it is 0, to compare their status "
                "code, redirects, noindex and canonical tag now with what Google last saw."
            ),
        ] = False,
        sitemap_url: Annotated[
            str | None,
            Field(
                description="Audit this sitemap (or sitemap index) instead of every sitemap "
                "submitted for the property."
            ),
        ] = None,
        restart: Annotated[
            bool,
            Field(description="Forget this property's saved audit and start over."),
        ] = False,
        issue: Annotated[
            str | None,
            Field(
                description="List every URL with this issue key (from issues[].key), "
                "100 at a time. Nothing new is inspected."
            ),
        ] = None,
        start: Annotated[int, Field(ge=0, description="Offset into the issue's URLs.")] = 0,
        examples: Annotated[
            int, Field(ge=0, le=50, description="Example URLs shown per issue.")
        ] = 5,
    ) -> IndexingAudit:
        """Full indexing audit of a property, rebuilt from its sitemaps because Google's API
        does not expose the Page indexing report's URL lists. Each call inspects the next
        URLs (new ones first, then any checked more than 14 days ago) and reports every URL
        inspected so far: grouped by Google's index status with likely fixes, plus canonical
        conflicts, indexed pages with no search impressions in 90 days, and pages shown in
        search but missing from the sitemaps. Progress is saved locally, so call again to
        continue, today or on later days; Google allows 2,000 inspections per property per
        day."""
        resolution = await deps.sites.resolve(site_url)
        prop = await deps.sites.property_for(resolution.site_url)
        auditor = Auditor(deps, prop)
        if issue is not None:
            return auditor.report(issue=issue, start=start, examples=examples)
        if restart:
            deps.store.reset(prop.site_url)
        await auditor.sync_sitemaps(sitemap_url, force=restart)
        await auditor.sync_pages()

        async def progress(done: int, total: int, url: str) -> None:
            await ctx.report_progress(done, total, f"Inspected {url}")

        await auditor.inspect(inspect, progress)
        if fetch_pages:
            await auditor.check_live(inspect or DEFAULT_LIVE)
        if resolution.note:
            auditor.notes.insert(0, resolution.note)
        return auditor.report(examples=examples)
