"""One tool per search surface: Discover, Google Images, video search and news.

Each tool exposes only what its surface supports, so the schema itself keeps the model away
from requests Google would reject. Discover, for example, offers no query, device or
position, and these rules were checked against the live API (scripts/probe_compat.py).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field

from gsc_mcp.models import (
    AggregationType,
    AnalyticsResult,
    BreakdownDimension,
    Dimension,
    DimensionFilter,
    DiscoverDimension,
    DiscoverFilter,
)
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

Filters = Annotated[list[DimensionFilter] | None, Field(description="Filters, combined with AND.")]
SearchDimensions = Annotated[
    list[Dimension] | None,
    Field(
        description="Group rows by these. Omit for ['query']; [] for totals. "
        "searchAppearance must be the only dimension."
    ),
]
PageAggregation = Annotated[
    Literal["auto", "byPage", "byNewsShowcasePanel"],
    Field(
        description="'auto' aggregates by page here. byNewsShowcasePanel needs a "
        "searchAppearance equals NEWS_SHOWCASE filter."
    ),
]


def register(mcp: MCPServer, deps: Deps) -> None:
    @mcp.tool(title="Query Discover performance", annotations=READ_ONLY, meta=LARGE_RESULT)
    async def query_discover_analytics(
        site_url: SiteUrl,
        start_date: StartDate = None,
        end_date: EndDate = None,
        dimensions: Annotated[
            list[DiscoverDimension] | None,
            Field(description="Group rows by these. Omit for ['page']; [] for totals."),
        ] = None,
        filters: Annotated[
            list[DiscoverFilter] | None, Field(description="Filters, combined with AND.")
        ] = None,
        data_state: DataStateParam = "all",
        aggregation_type: PageAggregation = "auto",
        row_limit: RowLimit = 100,
        start_row: StartRow = 0,
    ) -> AnalyticsResult:
        """Google Discover performance: clicks, impressions and CTR by page, country or date.
        Discover reports no queries, no average position and no device breakdown, and has
        data only for sites that appear in Discover feeds."""
        return await run_analytics(
            deps,
            site_url=site_url,
            start_date=start_date,
            end_date=end_date,
            dimensions=["page"] if dimensions is None else dimensions,
            search_type="discover",
            filters=[f.general() for f in filters or []],
            data_state=data_state,
            aggregation_type=aggregation_type,
            row_limit=row_limit,
            start_row=start_row,
        )

    @mcp.tool(title="Query Google Images performance", annotations=READ_ONLY, meta=LARGE_RESULT)
    async def query_image_analytics(
        site_url: SiteUrl,
        start_date: StartDate = None,
        end_date: EndDate = None,
        dimensions: SearchDimensions = None,
        filters: Filters = None,
        data_state: DataStateParam = "all",
        aggregation_type: AggregationType = "auto",
        row_limit: RowLimit = 100,
        start_row: StartRow = 0,
    ) -> AnalyticsResult:
        """Performance in Google Images and the Images tab of Search: clicks, impressions,
        CTR and average position within image results, by query, page, country, device
        or date."""
        return await run_analytics(
            deps,
            site_url=site_url,
            start_date=start_date,
            end_date=end_date,
            dimensions=["query"] if dimensions is None else dimensions,
            search_type="image",
            filters=filters or [],
            data_state=data_state,
            aggregation_type=aggregation_type,
            row_limit=row_limit,
            start_row=start_row,
        )

    @mcp.tool(title="Query video search performance", annotations=READ_ONLY, meta=LARGE_RESULT)
    async def query_video_analytics(
        site_url: SiteUrl,
        start_date: StartDate = None,
        end_date: EndDate = None,
        dimensions: SearchDimensions = None,
        filters: Filters = None,
        data_state: DataStateParam = "all",
        aggregation_type: AggregationType = "auto",
        row_limit: RowLimit = 100,
        start_row: StartRow = 0,
    ) -> AnalyticsResult:
        """Performance in the Videos tab of Google Search: clicks, impressions, CTR and
        average position, by query, page, country, device or date. Video results shown on
        the main web results are counted under web search, not here."""
        return await run_analytics(
            deps,
            site_url=site_url,
            start_date=start_date,
            end_date=end_date,
            dimensions=["query"] if dimensions is None else dimensions,
            search_type="video",
            filters=filters or [],
            data_state=data_state,
            aggregation_type=aggregation_type,
            row_limit=row_limit,
            start_row=start_row,
        )

    @mcp.tool(title="Query news performance", annotations=READ_ONLY, meta=LARGE_RESULT)
    async def query_news_analytics(
        site_url: SiteUrl,
        source: Annotated[
            Literal["google_news", "news_tab"],
            Field(
                description="'google_news': news.google.com and the Google News app (no "
                "queries, no position). 'news_tab': the News tab in Google Search."
            ),
        ] = "google_news",
        start_date: StartDate = None,
        end_date: EndDate = None,
        dimensions: Annotated[
            list[BreakdownDimension] | None,
            Field(
                description="Group rows by these. Omit for ['page'] (google_news) or "
                "['query'] (news_tab); [] for totals. google_news has no query."
            ),
        ] = None,
        filters: Filters = None,
        data_state: DataStateParam = "all",
        aggregation_type: Annotated[
            AggregationType,
            Field(
                description="'auto' is right almost always. google_news is aggregated by "
                "page (no byProperty); byNewsShowcasePanel needs a searchAppearance equals "
                "NEWS_SHOWCASE filter and google_news."
            ),
        ] = "auto",
        row_limit: RowLimit = 100,
        start_row: StartRow = 0,
    ) -> AnalyticsResult:
        """News performance: Google News (news.google.com and the app) or the News tab in
        Google Search. Clicks, impressions and CTR by page, country, device or date; the
        News tab also has queries and average position."""
        google_news = source == "google_news"
        if dimensions is None:
            dimensions = ["page"] if google_news else ["query"]
        return await run_analytics(
            deps,
            site_url=site_url,
            start_date=start_date,
            end_date=end_date,
            dimensions=dimensions,
            search_type="googleNews" if google_news else "news",
            filters=filters or [],
            data_state=data_state,
            aggregation_type=aggregation_type,
            row_limit=row_limit,
            start_row=start_row,
        )
