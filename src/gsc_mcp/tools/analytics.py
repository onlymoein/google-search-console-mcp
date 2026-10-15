"""Search Analytics: the general query tool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from pydantic import Field

from gsc_mcp.models import AggregationType, AnalyticsResult, Dimension, DimensionFilter, SearchType
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
Aggregation = Annotated[
    AggregationType,
    Field(
        description="'auto' is right almost always. byProperty cannot be used with page "
        "grouping or filters, nor with Discover or Google News."
    ),
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
        data_state: DataStateParam = "all",
        aggregation_type: Aggregation = "auto",
        row_limit: RowLimit = 100,
        start_row: StartRow = 0,
    ) -> AnalyticsResult:
        """Clicks, impressions, CTR and average position from the Performance report, grouped
        by dimensions and filtered. Rows are sorted by clicks. Invalid combinations are
        rejected with an explanation before Google is called. For Discover, Images, video,
        Google News or the News tab, the matching query_*_analytics tool offers only what
        that surface supports."""
        return await run_analytics(
            deps,
            site_url=site_url,
            start_date=start_date,
            end_date=end_date,
            dimensions=["query"] if dimensions is None else dimensions,
            search_type=search_type,
            filters=filters or [],
            data_state=data_state,
            aggregation_type=aggregation_type,
            row_limit=row_limit,
            start_row=start_row,
        )
