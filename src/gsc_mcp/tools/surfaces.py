"""One tool per search surface: Discover, Google Images, video search, Google News and the
News tab of Search.

Each tool exposes only what its surface supports, so the schema itself keeps the model away
from requests Google would reject. Discover, for example, offers no query, device or
position, and these rules were checked against the live API (scripts/probe_compat.py).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field

from gsc_mcp.errors import GuardError
from gsc_mcp.models import (
    AnalyticsResult,
    Dimension,
    DimensionFilter,
    DiscoverDimension,
    DiscoverFilter,
    GoogleNewsDimension,
    GoogleNewsFilter,
    PageAggregation,
    SearchAggregation,
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
SearchAggregationParam = Annotated[
    SearchAggregation,
    Field(
        description="'auto' is right almost always. byProperty cannot be used with page "
        "grouping or filters."
    ),
]
ShowcaseAggregationParam = Annotated[
    PageAggregation,
    Field(
        description="'auto' aggregates by page. byNewsShowcasePanel counts News Showcase "
        "panels instead: it needs the filter searchAppearance equals NEWS_SHOWCASE and cannot "
        "group or filter by page, so dimensions default to ['date'] with it."
    ),
]


def news_alias(arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """1.1.0's query_news_analytics(source=...), split by source into two tools in 1.2.0."""
    forwarded = dict(arguments)
    source = forwarded.pop("source", "google_news")
    if source == "google_news":
        return "query_google_news_analytics", forwarded
    if source == "news_tab":
        return "query_news_tab_analytics", forwarded
    raise GuardError(f"source must be 'google_news' or 'news_tab'; got {source!r}.")


def page_default(aggregation_type: str) -> list[str]:
    """Page is the natural grouping here, except for byNewsShowcasePanel, which forbids it."""
    return ["date"] if aggregation_type == "byNewsShowcasePanel" else ["page"]


def register(mcp: MCPServer, deps: Deps) -> None:
    @mcp.tool(title="Query Discover performance", annotations=READ_ONLY, meta=LARGE_RESULT)
    async def query_discover_analytics(
        site_url: SiteUrl,
        start_date: StartDate = None,
        end_date: EndDate = None,
        dimensions: Annotated[
            list[DiscoverDimension] | None,
            Field(
                description="Group rows by these. Omit for ['page'] (['date'] with "
                "byNewsShowcasePanel); [] for totals."
            ),
        ] = None,
        filters: Annotated[
            list[DiscoverFilter] | None, Field(description="Filters, combined with AND.")
        ] = None,
        data_state: DataStateParam = "all",
        aggregation_type: ShowcaseAggregationParam = "auto",
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
            dimensions=page_default(aggregation_type) if dimensions is None else dimensions,
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
        aggregation_type: SearchAggregationParam = "auto",
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
        aggregation_type: SearchAggregationParam = "auto",
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

    @mcp.tool(title="Query Google News performance", annotations=READ_ONLY, meta=LARGE_RESULT)
    async def query_google_news_analytics(
        site_url: SiteUrl,
        start_date: StartDate = None,
        end_date: EndDate = None,
        dimensions: Annotated[
            list[GoogleNewsDimension] | None,
            Field(
                description="Group rows by these. Omit for ['page'] (['date'] with "
                "byNewsShowcasePanel); [] for totals."
            ),
        ] = None,
        filters: Annotated[
            list[GoogleNewsFilter] | None, Field(description="Filters, combined with AND.")
        ] = None,
        data_state: DataStateParam = "all",
        aggregation_type: ShowcaseAggregationParam = "auto",
        row_limit: RowLimit = 100,
        start_row: StartRow = 0,
    ) -> AnalyticsResult:
        """Google News performance (news.google.com and the Google News app): clicks,
        impressions and CTR by page, country, device or date. Google News reports no
        queries and no average position. For the News tab in Google Search, use
        query_news_tab_analytics."""
        return await run_analytics(
            deps,
            site_url=site_url,
            start_date=start_date,
            end_date=end_date,
            dimensions=page_default(aggregation_type) if dimensions is None else dimensions,
            search_type="googleNews",
            filters=[f.general() for f in filters or []],
            data_state=data_state,
            aggregation_type=aggregation_type,
            row_limit=row_limit,
            start_row=start_row,
        )

    @mcp.tool(title="Query News tab performance", annotations=READ_ONLY, meta=LARGE_RESULT)
    async def query_news_tab_analytics(
        site_url: SiteUrl,
        start_date: StartDate = None,
        end_date: EndDate = None,
        dimensions: SearchDimensions = None,
        filters: Filters = None,
        data_state: DataStateParam = "all",
        aggregation_type: SearchAggregationParam = "auto",
        row_limit: RowLimit = 100,
        start_row: StartRow = 0,
    ) -> AnalyticsResult:
        """Performance in the News tab of Google Search: clicks, impressions, CTR and
        average position, by query, page, country, device or date. For news.google.com and
        the Google News app, use query_google_news_analytics."""
        return await run_analytics(
            deps,
            site_url=site_url,
            start_date=start_date,
            end_date=end_date,
            dimensions=["query"] if dimensions is None else dimensions,
            search_type="news",
            filters=filters or [],
            data_state=data_state,
            aggregation_type=aggregation_type,
            row_limit=row_limit,
            start_row=start_row,
        )
