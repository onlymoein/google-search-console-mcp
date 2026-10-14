"""Search appearance: Google's two-step pattern, one tool per step.

searchAppearance cannot share a query with any other dimension. So step one lists the
appearances a property had, and step two breaks one of them down by query, page, country,
device or date, using an exact-name filter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from pydantic import Field

from gsc_mcp.errors import GuardError
from gsc_mcp.guards import NO_QUERY_TYPES
from gsc_mcp.models import AnalyticsResult, BreakdownDimension, DimensionFilter, SearchType
from gsc_mcp.tools._common import (
    LARGE_RESULT,
    READ_ONLY,
    DataStateParam,
    Deps,
    EndDate,
    RowLimit,
    SiteUrl,
    StartDate,
    StartRow,
    run_analytics,
)

if TYPE_CHECKING:
    from mcp.server import MCPServer


def register(mcp: MCPServer, deps: Deps) -> None:
    @mcp.tool(title="List search appearances", annotations=READ_ONLY)
    async def list_search_appearances(
        site_url: SiteUrl,
        start_date: StartDate = None,
        end_date: EndDate = None,
        search_type: SearchType = "web",
        data_state: DataStateParam = "all",
    ) -> AnalyticsResult:
        """Step one: list the search appearances (rich results and other result features,
        such as VIDEO or REVIEW_SNIPPET) the property had, with clicks and impressions.
        Then call query_search_appearance with one of the names to break it down."""
        return await run_analytics(
            deps,
            site_url=site_url,
            start_date=start_date,
            end_date=end_date,
            dimensions=["searchAppearance"],
            search_type=search_type,
            data_state=data_state,
            row_limit=1000,
            extra_notes=[
                "Next step: query_search_appearance with one of these names. Names are "
                "specific to the search_type."
            ],
        )

    @mcp.tool(title="Query one search appearance", annotations=READ_ONLY, meta=LARGE_RESULT)
    async def query_search_appearance(
        site_url: SiteUrl,
        appearance: Annotated[
            str,
            Field(
                min_length=1,
                description="Exact appearance name from list_search_appearances, such as "
                "VIDEO or REVIEW_SNIPPET.",
            ),
        ],
        start_date: StartDate = None,
        end_date: EndDate = None,
        dimensions: Annotated[
            list[BreakdownDimension] | None,
            Field(
                description="Break the appearance down by these. Omit for ['query'] (['page'] "
                "for Discover and Google News)."
            ),
        ] = None,
        search_type: SearchType = "web",
        filters: Annotated[
            list[DimensionFilter] | None,
            Field(description="Extra filters, combined with AND with the appearance."),
        ] = None,
        data_state: DataStateParam = "all",
        row_limit: RowLimit = 100,
        start_row: StartRow = 0,
    ) -> AnalyticsResult:
        """Step two: performance of one search appearance, broken down by query, page,
        country, device or date. Get the appearance name from list_search_appearances."""
        extra = filters or []
        if any(f.dimension == "searchAppearance" for f in extra):
            raise GuardError("Pass the appearance as `appearance`, not as a filter.")
        if dimensions is None:
            dimensions = ["page"] if search_type in NO_QUERY_TYPES else ["query"]
        appearance_filter = DimensionFilter(
            dimension="searchAppearance", operator="equals", expression=appearance
        )
        return await run_analytics(
            deps,
            site_url=site_url,
            start_date=start_date,
            end_date=end_date,
            dimensions=dimensions,
            search_type=search_type,
            filters=[appearance_filter, *extra],
            data_state=data_state,
            row_limit=row_limit,
            start_row=start_row,
        )
