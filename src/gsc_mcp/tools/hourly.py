"""Hourly Search Analytics: dataState=hourly_all with the HOUR dimension, set for you."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from pydantic import Field

from gsc_mcp.dates import HOURLY_DAYS
from gsc_mcp.guards import build_query
from gsc_mcp.models import AnalyticsResult, DimensionFilter, HourlyDimension, SearchType
from gsc_mcp.tools._common import (
    LARGE_RESULT,
    READ_ONLY,
    Deps,
    RowLimit,
    SiteUrl,
    StartRow,
    analytics_result,
    date_defaults,
)

if TYPE_CHECKING:
    from mcp.server import MCPServer

DEFAULT_HOURLY_DAYS = 3


def register(mcp: MCPServer, deps: Deps) -> None:
    @mcp.tool(title="Query hourly search analytics", annotations=READ_ONLY, meta=LARGE_RESULT)
    async def query_hourly_analytics(
        site_url: SiteUrl,
        start_date: Annotated[
            str | None,
            Field(
                description=f"First day, YYYY-MM-DD in Pacific Time, within the last "
                f"{HOURLY_DAYS} days. Defaults to {DEFAULT_HOURLY_DAYS - 1} days ago."
            ),
        ] = None,
        end_date: Annotated[
            str | None,
            Field(description="Last day, YYYY-MM-DD in Pacific Time. Defaults to today."),
        ] = None,
        dimensions: Annotated[
            list[HourlyDimension] | None,
            Field(description="Extra grouping besides the hour. Omit for hourly totals."),
        ] = None,
        search_type: SearchType = "web",
        filters: Annotated[
            list[DimensionFilter] | None, Field(description="Filters, combined with AND.")
        ] = None,
        row_limit: RowLimit = 100,
        start_row: StartRow = 0,
    ) -> AnalyticsResult:
        """Search performance broken down by hour (Pacific Time) for the last ~10 days. Use it
        to spot same-day spikes or compare today with the same day last week. The most
        recent hours are incomplete; first_incomplete_hour says where that starts."""
        today = deps.today()
        start, end = date_defaults(start_date, end_date, today, days=DEFAULT_HOURLY_DAYS)
        query = build_query(
            start_date=start,
            end_date=end,
            today=today,
            dimensions=dimensions or [],
            search_type=search_type,
            data_state="hourly_all",
            filters=filters or [],
            row_limit=row_limit,
            start_row=start_row,
            hourly=True,
        )
        resolution = await deps.sites.resolve(site_url)
        data = await deps.client.query_search_analytics(resolution.site_url, query.body())
        return analytics_result(resolution, query, data, today=today)
