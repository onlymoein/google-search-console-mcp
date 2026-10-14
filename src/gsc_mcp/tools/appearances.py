"""Search appearance: step one of Google's two-step pattern.

searchAppearance cannot share a query with any other dimension. So: list the appearances
here, then break one down with query_search_analytics and a searchAppearance equals filter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gsc_mcp.guards import build_query
from gsc_mcp.models import AnalyticsResult, DataState, SearchType
from gsc_mcp.tools._common import (
    READ_ONLY,
    Deps,
    EndDate,
    SiteUrl,
    StartDate,
    analytics_result,
    date_defaults,
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
        data_state: DataState = "all",
    ) -> AnalyticsResult:
        """List the search appearances (rich results and other result features, such as
        VIDEO or REVIEW_SNIPPET) the property had, with their clicks and impressions. To
        break one down, call query_search_analytics with filters=[{"dimension":
        "searchAppearance", "operator": "equals", "expression": "<appearance>"}] and the
        dimensions you need."""
        today = deps.today()
        start, end = date_defaults(start_date, end_date, today)
        query = build_query(
            start_date=start,
            end_date=end,
            today=today,
            dimensions=["searchAppearance"],
            search_type=search_type,
            data_state=data_state,
            row_limit=1000,
        )
        resolution = await deps.sites.resolve(site_url)
        data = await deps.client.query_search_analytics(resolution.site_url, query.body())
        notes = [
            "Next step: query_search_analytics with a searchAppearance equals filter. Avoid "
            "notEquals/notContains on searchAppearance: Google's API inverts them (known bug)."
        ]
        return analytics_result(resolution, query, data, today=today, extra_notes=notes)
