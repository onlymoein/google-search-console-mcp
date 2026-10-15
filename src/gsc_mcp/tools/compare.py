"""Period comparison: two equal-length periods, joined on dimension values."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field

from gsc_mcp.guards import NO_QUERY_TYPES, build_query
from gsc_mcp.models import ComparedRow, ComparisonResult, Dimension, DimensionFilter, SearchType
from gsc_mcp.tools._common import READ_ONLY, Deps, SiteUrl
from gsc_mcp.tools._comparison import (
    CurrentEnd,
    CurrentStart,
    FinalByDefault,
    PreviousEnd,
    PreviousStart,
    fetch_all,
    join,
    resolve_periods,
    totals,
    zero,
)

if TYPE_CHECKING:
    from mcp.server import MCPServer

    from gsc_mcp.guards import AnalyticsQuery
    from gsc_mcp.tools._comparison import Period

SortBy = Literal["clicks_change", "impressions_change", "clicks", "impressions"]


def register(mcp: MCPServer, deps: Deps) -> None:
    @mcp.tool(title="Compare two periods", annotations=READ_ONLY)
    async def compare_periods(
        site_url: SiteUrl,
        current_start: CurrentStart = None,
        current_end: CurrentEnd = None,
        previous_start: PreviousStart = None,
        previous_end: PreviousEnd = None,
        dimensions: Annotated[
            list[Dimension] | None,
            Field(
                description="Compare per value of these. Omit for ['query']; [] for totals only."
            ),
        ] = None,
        search_type: SearchType = "web",
        filters: Annotated[
            list[DimensionFilter] | None, Field(description="Filters, combined with AND.")
        ] = None,
        data_state: FinalByDefault = "final",
        sort_by: Annotated[
            SortBy,
            Field(
                description="clicks_change (default) and impressions_change rank the biggest "
                "gains and losses first; ties fall back to the other metric's change, then "
                "current impressions. clicks and impressions rank by the current period."
            ),
        ] = "clicks_change",
        row_limit: Annotated[int, Field(ge=1, le=1000, description="Rows to return.")] = 50,
        fetch_limit: Annotated[
            int,
            Field(ge=1, le=25_000, description="Top rows fetched per period before joining."),
        ] = 1000,
    ) -> ComparisonResult:
        """Compare search performance between two periods of equal length: totals plus a
        per-key breakdown with absolute and percent changes. Position change is negative
        when ranking improved."""
        today = deps.today()
        current, previous = resolve_periods(
            current_start, current_end, previous_start, previous_end, today=today
        )
        dims: list[str] = ["query"] if dimensions is None else list(dimensions)

        def make(period: Period, group_by: list[str], limit: int) -> AnalyticsQuery:
            return build_query(
                start_date=period[0].isoformat(),
                end_date=period[1].isoformat(),
                today=today,
                dimensions=group_by,
                search_type=search_type,
                data_state=data_state,
                filters=filters or [],
                row_limit=limit,
            )

        queries = {
            "current_total": make(current, [], 1),
            "previous_total": make(previous, [], 1),
        }
        if dims:
            queries["current_rows"] = make(current, dims, fetch_limit)
            queries["previous_rows"] = make(previous, dims, fetch_limit)
        resolution = await deps.sites.resolve(site_url)
        results = await fetch_all(deps, resolution.site_url, queries)

        with_position = search_type not in NO_QUERY_TYPES
        total = totals(results["current_total"], results["previous_total"], with_position)
        rows: list[ComparedRow] = []
        cut_off: list[str] = []
        if dims:
            rows, cut_off = join(
                dims,
                results["current_rows"],
                results["previous_rows"],
                fetch_limit=fetch_limit,
                with_position=with_position,
            )
            rows.sort(key=lambda r: _sort_key(r, sort_by), reverse=True)

        notes: list[str] = []
        if resolution.note:
            notes.append(resolution.note)
        if cut_off:
            notes.append(
                f"The {' and '.join(cut_off)} period returned the full fetch_limit of "
                f"{fetch_limit:,} rows, so a key missing from it may just rank below that "
                "cut-off; those rows have change null. Raise fetch_limit (up to 25,000) to "
                "join more."
            )
        if data_state == "all":
            notes.append(
                "data_state='all' includes fresh data that is still being processed; recent "
                "days in the current period may be undercounted."
            )
        if "query" in dims:
            notes.append(
                "Rare (anonymized) queries are omitted from rows but counted in totals, so a "
                "query shown with zeros in one period may have had a few impressions there."
            )

        return ComparisonResult(
            site_url=resolution.site_url,
            search_type=search_type,
            data_state=data_state,
            current_period=(current[0].isoformat(), current[1].isoformat()),
            previous_period=(previous[0].isoformat(), previous[1].isoformat()),
            dimensions=dims,
            totals=total,
            rows=rows[:row_limit],
            notes=notes,
        )


def _sort_key(row: ComparedRow, sort_by: SortBy) -> tuple[int, ...]:
    """Largest first. Most keys have no clicks in either period, so ties fall back to the
    other metric and then current impressions instead of landing in arbitrary order."""
    current = row.current
    if sort_by in ("clicks", "impressions"):
        if current is None:
            return (-1, -1)
        pair = (current.clicks, current.impressions)
        return pair if sort_by == "clicks" else pair[::-1]
    if row.change is not None:
        clicks, impressions = abs(row.change.clicks), abs(row.change.impressions)
    else:
        # Missing from a period that hit fetch_limit: rank by the traffic on the known side.
        side = row.current or row.previous or zero()
        clicks, impressions = side.clicks, side.impressions
    first, second = (clicks, impressions) if sort_by == "clicks_change" else (impressions, clicks)
    return (first, second, current.impressions if current else 0)
