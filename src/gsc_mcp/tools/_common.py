"""Shared pieces for the tool modules: dependencies, parameter types and row formatting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Annotated, Any

from mcp.types import ToolAnnotations
from pydantic import Field

from gsc_mcp.dates import FRESH_DAYS, parse_date, today_pacific
from gsc_mcp.guards import NO_QUERY_TYPES, build_query
from gsc_mcp.models import AnalyticsResult, DataState, Row

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from gsc_mcp.client import SearchConsoleClient
    from gsc_mcp.config import Settings
    from gsc_mcp.guards import AnalyticsQuery
    from gsc_mcp.models import DimensionFilter
    from gsc_mcp.sites import Resolution, SiteResolver


@dataclass(slots=True)
class Deps:
    client: SearchConsoleClient
    sites: SiteResolver
    settings: Settings
    today: Callable[[], date] = today_pacific


READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False
)
# Claude Code spills results over ~25k tokens to a file; allow larger tables inline.
LARGE_RESULT = {"anthropic/maxResultSizeChars": 200_000}

SiteUrl = Annotated[
    str,
    Field(
        description="The property, as listed by list_sites: 'sc-domain:example.com' for a "
        "domain property or 'https://www.example.com/' for a URL-prefix property. A bare "
        "domain such as 'example.com', or any URL on the site, is resolved to the property "
        "that contains it."
    ),
]
StartDate = Annotated[
    str | None,
    Field(description="First day, YYYY-MM-DD in Pacific Time. Defaults to 28 days ago."),
]
EndDate = Annotated[
    str | None,
    Field(description="Last day (inclusive), YYYY-MM-DD in Pacific Time. Defaults to today."),
]
DataStateParam = Annotated[
    DataState,
    Field(
        description="'all' includes fresh data that may still change and matches the Search "
        "Console UI; 'final' is finalized data only (lags 2-3 days)."
    ),
]
RowLimit = Annotated[int, Field(ge=1, le=25_000, description="Rows to return (1-25,000).")]
StartRow = Annotated[int, Field(ge=0, description="Zero-based row offset, for paging.")]

DEFAULT_DAYS = 28


def date_defaults(
    start_date: str | None, end_date: str | None, today: date, days: int = DEFAULT_DAYS
) -> tuple[str, str]:
    """Fill in a missing end (today) and start (`days` days ending at end)."""
    end = parse_date(end_date, field="end_date") if end_date else today
    if start_date:
        return start_date, end.isoformat()
    return (end - timedelta(days=days - 1)).isoformat(), end.isoformat()


async def run_analytics(
    deps: Deps,
    *,
    site_url: str,
    start_date: str | None,
    end_date: str | None,
    dimensions: Sequence[str],
    search_type: str,
    filters: Sequence[DimensionFilter] = (),
    data_state: str = "all",
    aggregation_type: str = "auto",
    row_limit: int = 100,
    start_row: int = 0,
    hourly: bool = False,
    default_days: int = DEFAULT_DAYS,
    extra_notes: list[str] | None = None,
) -> AnalyticsResult:
    """Validate, resolve the site, query Search Analytics and shape the result."""
    today = deps.today()
    start, end = date_defaults(start_date, end_date, today, days=default_days)
    query = build_query(
        start_date=start,
        end_date=end,
        today=today,
        dimensions=dimensions,
        search_type=search_type,
        data_state=data_state,
        aggregation_type=aggregation_type,
        filters=filters,
        row_limit=row_limit,
        start_row=start_row,
        hourly=hourly,
    )
    resolution = await deps.sites.resolve(site_url)
    data = await deps.client.query_search_analytics(resolution.site_url, query.body())
    return analytics_result(resolution, query, data, today=today, extra_notes=extra_notes)


def format_row(dimensions: tuple[str, ...], row: dict[str, Any], search_type: str) -> Row:
    out: Row = dict(zip(dimensions, row.get("keys", []), strict=False))
    out["clicks"] = int(row.get("clicks", 0))
    out["impressions"] = int(row.get("impressions", 0))
    out["ctr"] = round(float(row.get("ctr", 0.0)), 4)
    if search_type not in NO_QUERY_TYPES and "position" in row:
        out["position"] = round(float(row["position"]), 2)
    return out


def analytics_result(
    resolution: Resolution,
    query: AnalyticsQuery,
    data: dict[str, Any],
    *,
    today: date,
    extra_notes: list[str] | None = None,
) -> AnalyticsResult:
    rows = [format_row(query.dimensions, r, query.search_type) for r in data.get("rows", [])]
    metadata: dict[str, Any] = data.get("metadata") or {}
    # The API returns camelCase here although the reference documents snake_case.
    incomplete_date = metadata.get("firstIncompleteDate") or metadata.get("first_incomplete_date")
    incomplete_hour = metadata.get("firstIncompleteHour") or metadata.get("first_incomplete_hour")
    next_start_row = query.start_row + len(rows) if len(rows) == query.row_limit else None

    notes: list[str] = []
    if resolution.note:
        notes.append(resolution.note)
    notes.extend(query.notes)
    if not rows:
        notes.append(
            "No rows matched. Check the date range, filters and search_type; new properties "
            "and low-traffic pages often have no data."
        )
    if "query" in query.dimensions:
        notes.append(
            "Google omits rare (anonymized) queries from query rows, so rows sum to less "
            "than the property total."
        )
    if next_start_row is not None:
        notes.append(f"More rows may exist: call again with start_row={next_start_row}.")
        if {"query", "page"} & set(query.dimensions):
            notes.append(
                "Google exposes at most 50,000 rows per day per search type. For complete "
                "page or query data over a long range, query one day at a time."
            )
    if incomplete_date:
        notes.append(f"Data from {incomplete_date} onward is still being processed and may change.")
    if incomplete_hour:
        notes.append(f"Data from {incomplete_hour} onward is still being processed and may change.")
    first_fresh = today - timedelta(days=FRESH_DAYS - 1)
    if query.data_state == "all" and query.end >= first_fresh and "date" not in query.dimensions:
        # Google reports first_incomplete_date only when grouping by date, so say it here.
        notes.append(
            f"This range includes {max(query.start, first_fresh)} to {query.end}, which is "
            "usually still being processed, so these numbers may still change. Add 'date' "
            "to dimensions to see where incomplete data starts, or use data_state='final'."
        )
    if query.data_state == "final" and query.end >= first_fresh:
        notes.append(
            "data_state='final' leaves out the last few days, which are not final yet. "
            "Use data_state='all' to include them."
        )
    if query.search_type in NO_QUERY_TYPES:
        notes.append(f"The {query.search_type} report has no average position.")
    notes.extend(extra_notes or [])

    return AnalyticsResult(
        site_url=resolution.site_url,
        search_type=query.search_type,
        start_date=query.start.isoformat(),
        end_date=query.end.isoformat(),
        data_state=query.data_state,
        dimensions=list(query.dimensions),
        rows=rows,
        row_count=len(rows),
        next_start_row=next_start_row,
        first_incomplete_date=incomplete_date,
        first_incomplete_hour=incomplete_hour,
        aggregation_type=data.get("responseAggregationType"),
        notes=notes,
        request=query.body(),
    )
