"""Search Analytics: the general query tool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from pydantic import Field

from gsc_mcp.guards import build_query
from gsc_mcp.models import (
    AggregationType,
    AnalyticsResult,
    DataState,
    Dimension,
    DimensionFilter,
    SearchType,
)
from gsc_mcp.tools._common import (
    LARGE_RESULT,
    READ_ONLY,
    Deps,
    EndDate,
    RowLimit,
    SiteUrl,
    StartDate,
    StartRow,
    analytics_result,
    date_defaults,
)

if TYPE_CHECKING:
    from mcp.server import MCPServer

Dimensions = Annotated[
    list[Dimension] | None,
    Field(
        description="Group rows by these, in order. Omit for ['query']; pass [] for property "
        "totals. searchAppearance must be the only dimension (see list_search_appearances). "
        "Discover and Google News have no query dimension."
    ),
]
Filters = Annotated[
    list[DimensionFilter] | None,
    Field(description="Filters, combined with AND."),
]


def register(mcp: MCPServer, deps: Deps) -> None:
    @mcp.tool(title="Query search analytics", annotations=READ_ONLY, meta=LARGE_RESULT)
    async def query_search_analytics(
        site_url: SiteUrl,
        start_date: StartDate = None,
        end_date: EndDate = None,
        dimensions: Dimensions = None,
        search_type: Annotated[
            SearchType,
            Field(description="web, image, video, news (the News tab), discover or googleNews."),
        ] = "web",
        filters: Filters = None,
        data_state: Annotated[
            DataState,
            Field(
                description="'all' includes fresh data that may still change and matches the "
                "Search Console UI; 'final' is finalized data only (lags 2-3 days)."
            ),
        ] = "all",
        aggregation_type: Annotated[
            AggregationType,
            Field(
                description="'auto' is right almost always. byProperty cannot be used with "
                "page grouping or filters."
            ),
        ] = "auto",
        row_limit: RowLimit = 100,
        start_row: StartRow = 0,
    ) -> AnalyticsResult:
        """Clicks, impressions, CTR and average position from the Performance report, grouped
        by dimensions and filtered. Rows are sorted by clicks. Invalid combinations are
        rejected with an explanation before Google is called."""
        today = deps.today()
        start, end = date_defaults(start_date, end_date, today)
        query = build_query(
            start_date=start,
            end_date=end,
            today=today,
            dimensions=["query"] if dimensions is None else dimensions,
            search_type=search_type,
            data_state=data_state,
            aggregation_type=aggregation_type,
            filters=filters or [],
            row_limit=row_limit,
            start_row=start_row,
        )
        resolution = await deps.sites.resolve(site_url)
        data = await deps.client.query_search_analytics(resolution.site_url, query.body())
        return analytics_result(resolution, query, data, today=today)
