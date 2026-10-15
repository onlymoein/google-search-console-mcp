"""Period comparison: two equal-length periods, joined on dimension values."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Annotated, Any, Literal

import anyio
from pydantic import Field

from gsc_mcp.dates import FRESH_DAYS, HISTORY_MONTHS, parse_date, span_days
from gsc_mcp.errors import GSCError, GuardError
from gsc_mcp.guards import NO_QUERY_TYPES, build_query, check_dates, check_equal_periods
from gsc_mcp.models import (
    ComparedRow,
    ComparisonResult,
    DataState,
    Dimension,
    DimensionFilter,
    MetricChange,
    MetricSet,
    SearchType,
)
from gsc_mcp.tools._common import READ_ONLY, Deps, SiteUrl

if TYPE_CHECKING:
    from mcp.server import MCPServer

    from gsc_mcp.guards import AnalyticsQuery

SortBy = Literal["clicks_change", "impressions_change", "clicks", "impressions"]
DEFAULT_DAYS = 28


def register(mcp: MCPServer, deps: Deps) -> None:
    @mcp.tool(title="Compare two periods", annotations=READ_ONLY)
    async def compare_periods(
        site_url: SiteUrl,
        current_start: Annotated[
            str | None,
            Field(description="First day of the period to analyze, YYYY-MM-DD (Pacific Time)."),
        ] = None,
        current_end: Annotated[
            str | None,
            Field(
                description="Last day of the period to analyze. Defaults to 3 days ago, the "
                "last day that is usually final."
            ),
        ] = None,
        previous_start: Annotated[
            str | None,
            Field(description="First day of the baseline. Defaults to the period just before."),
        ] = None,
        previous_end: Annotated[
            str | None, Field(description="Last day of the baseline; same length as current.")
        ] = None,
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
        data_state: Annotated[
            DataState,
            Field(
                description="Defaults to 'final': fresh, still-changing data in the current "
                "period would read as a decline against a complete baseline."
            ),
        ] = "final",
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
        current, previous = _periods(
            current_start, current_end, previous_start, previous_end, today=today
        )
        dims: list[str] = ["query"] if dimensions is None else list(dimensions)

        def make(period: tuple[date, date], group_by: list[str], limit: int) -> AnalyticsQuery:
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
        results: dict[str, dict[str, Any]] = {}
        failures: list[GSCError] = []

        async def fetch(name: str, query: AnalyticsQuery) -> None:
            try:
                results[name] = await deps.client.query_search_analytics(
                    resolution.site_url, query.body()
                )
            except GSCError as exc:
                # Collected rather than raised: a task group would wrap it in an
                # ExceptionGroup and the model would lose the explanation.
                failures.append(exc)

        async with anyio.create_task_group() as group:
            for name, query in queries.items():
                group.start_soon(fetch, name, query)
        if failures:
            raise failures[0]

        with_position = search_type not in NO_QUERY_TYPES
        totals = _compare(
            {},
            _first(results["current_total"], with_position),
            _first(results["previous_total"], with_position),
        ) or ComparedRow(keys={}, current=_zero(), previous=_zero())

        rows: list[ComparedRow] = []
        cut_off: list[str] = []
        if dims:
            cur_data, prev_data = results["current_rows"], results["previous_rows"]
            cur = _by_key(dims, cur_data, with_position)
            prev = _by_key(dims, prev_data, with_position)
            # A period that returned fewer rows than fetch_limit is complete, so a key
            # missing from it had no traffic there. One that hit the limit may have cut it.
            cur_complete = len(cur_data.get("rows", [])) < fetch_limit
            prev_complete = len(prev_data.get("rows", [])) < fetch_limit
            cut_off = [
                label
                for label, complete in (("current", cur_complete), ("previous", prev_complete))
                if not complete
            ]
            for key in cur.keys() | prev.keys():
                row = _compare(
                    dict(zip(dims, key, strict=True)),
                    cur[key] if key in cur else _zero() if cur_complete else None,
                    prev[key] if key in prev else _zero() if prev_complete else None,
                )
                if row is not None:
                    rows.append(row)
            # Sorting is stable: keys alphabetically first, so full ties keep a fixed order.
            rows.sort(key=lambda r: tuple(r.keys.values()))
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
            totals=totals,
            rows=rows[:row_limit],
            notes=notes,
        )


def _periods(
    current_start: str | None,
    current_end: str | None,
    previous_start: str | None,
    previous_end: str | None,
    *,
    today: date,
) -> tuple[tuple[date, date], tuple[date, date]]:
    cur_end = (
        parse_date(current_end, field="current_end")
        if current_end
        else today - timedelta(days=FRESH_DAYS)
    )
    cur_start = (
        parse_date(current_start, field="current_start")
        if current_start
        else cur_end - timedelta(days=DEFAULT_DAYS - 1)
    )
    if not current_end and cur_start > cur_end:
        raise GuardError(
            f"current_end defaults to {cur_end} (the last day that is usually final), which "
            f"is before current_start {cur_start}. Pass current_end explicitly."
        )
    current = _checked(cur_start, cur_end, today=today, label="current")
    length = timedelta(days=span_days(*current) - 1)
    if previous_start:
        prev_start = parse_date(previous_start, field="previous_start")
        prev_end = (
            parse_date(previous_end, field="previous_end") if previous_end else prev_start + length
        )
    elif previous_end:
        prev_end = parse_date(previous_end, field="previous_end")
        prev_start = prev_end - length
    else:
        prev_end = current[0] - timedelta(days=1)
        prev_start = prev_end - length
    previous = _checked(prev_start, prev_end, today=today, label="previous")
    check_equal_periods(current, previous)
    return current, previous


def _checked(start: date, end: date, *, today: date, label: str) -> tuple[date, date]:
    checked_start, checked_end, clamped = check_dates(
        start.isoformat(), end.isoformat(), today=today
    )
    if clamped:
        raise GuardError(
            f"The {label} period starts before the {HISTORY_MONTHS} months of data Search "
            "Console keeps; choose shorter or later periods."
        )
    return checked_start, checked_end


def _metrics(row: dict[str, Any], with_position: bool) -> MetricSet:
    return MetricSet(
        clicks=int(row.get("clicks", 0)),
        impressions=int(row.get("impressions", 0)),
        ctr=round(float(row.get("ctr", 0.0)), 4),
        position=round(float(row["position"]), 2) if with_position and "position" in row else None,
    )


def _zero() -> MetricSet:
    return MetricSet(clicks=0, impressions=0, ctr=0.0)


def _first(data: dict[str, Any], with_position: bool) -> MetricSet:
    rows: list[dict[str, Any]] = data.get("rows", [])
    return _metrics(rows[0], with_position) if rows else _zero()


def _by_key(
    dims: list[str], data: dict[str, Any], with_position: bool
) -> dict[tuple[str, ...], MetricSet]:
    return {
        tuple(row.get("keys", [])): _metrics(row, with_position)
        for row in data.get("rows", [])
        if len(row.get("keys", [])) == len(dims)
    }


def _pct(current: int, previous: int) -> float | None:
    return round((current - previous) / previous * 100, 1) if previous else None


def _compare(
    keys: dict[str, str], current: MetricSet | None, previous: MetricSet | None
) -> ComparedRow | None:
    if current is None and previous is None:
        return None
    change = None
    if current is not None and previous is not None:
        position = None
        if current.position is not None and previous.position is not None:
            position = round(current.position - previous.position, 2)
        change = MetricChange(
            clicks=current.clicks - previous.clicks,
            impressions=current.impressions - previous.impressions,
            ctr=round(current.ctr - previous.ctr, 4),
            position=position,
            clicks_pct=_pct(current.clicks, previous.clicks),
            impressions_pct=_pct(current.impressions, previous.impressions),
        )
    return ComparedRow(keys=keys, current=current, previous=previous, change=change)


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
        side = row.current or row.previous or _zero()
        clicks, impressions = side.clicks, side.impressions
    first, second = (clicks, impressions) if sort_by == "clicks_change" else (impressions, clicks)
    return (first, second, current.impressions if current else 0)
