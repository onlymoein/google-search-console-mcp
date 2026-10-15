"""Shared by the tools that compare two periods: default periods, concurrent fetching, and
joining each period's rows on their dimension values."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Annotated, Any

import anyio
from pydantic import Field

from gsc_mcp.dates import FRESH_DAYS, HISTORY_MONTHS, parse_date, span_days
from gsc_mcp.errors import GSCError, GuardError
from gsc_mcp.guards import check_dates, check_equal_periods
from gsc_mcp.models import ComparedRow, DataState, MetricChange, MetricSet

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from gsc_mcp.guards import AnalyticsQuery
    from gsc_mcp.tools._common import Deps

Period = tuple[date, date]

CurrentStart = Annotated[
    str | None,
    Field(description="First day of the period to analyze, YYYY-MM-DD (Pacific Time)."),
]
CurrentEnd = Annotated[
    str | None,
    Field(
        description="Last day of the period to analyze. Defaults to 3 days ago, the last day "
        "that is usually final."
    ),
]
PreviousStart = Annotated[
    str | None,
    Field(description="First day of the baseline. Defaults to the period just before."),
]
PreviousEnd = Annotated[
    str | None, Field(description="Last day of the baseline; same length as current.")
]
FinalByDefault = Annotated[
    DataState,
    Field(
        description="Defaults to 'final': fresh, still-changing data in the current period "
        "would read as a decline against a complete baseline."
    ),
]
DEFAULT_DAYS = 28
# Search Analytics allows 1,200 queries a minute; a few at a time is plenty and polite.
CONCURRENCY = 4


def resolve_periods(
    current_start: str | None,
    current_end: str | None,
    previous_start: str | None,
    previous_end: str | None,
    *,
    today: date,
) -> tuple[Period, Period]:
    """Fill in defaults: the current period ends on the last usually-final day and lasts
    28 days; the previous period is the same length and ends the day before it starts."""
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


def _checked(start: date, end: date, *, today: date, label: str) -> Period:
    checked_start, checked_end, clamped = check_dates(
        start.isoformat(), end.isoformat(), today=today
    )
    if clamped:
        raise GuardError(
            f"The {label} period starts before the {HISTORY_MONTHS} months of data Search "
            "Console keeps; choose shorter or later periods."
        )
    return checked_start, checked_end


async def fetch_all(
    deps: Deps, site_url: str, queries: Mapping[str, AnalyticsQuery]
) -> dict[str, dict[str, Any]]:
    """Run the queries concurrently; raise the first failure with its explanation intact."""
    results: dict[str, dict[str, Any]] = {}
    failures: list[GSCError] = []
    limiter = anyio.Semaphore(CONCURRENCY)

    async def fetch(name: str, query: AnalyticsQuery) -> None:
        async with limiter:
            try:
                results[name] = await deps.client.query_search_analytics(site_url, query.body())
            except GSCError as exc:
                # Collected rather than raised: a task group would wrap it in an
                # ExceptionGroup and the model would lose the explanation.
                failures.append(exc)

    async with anyio.create_task_group() as group:
        for name, query in queries.items():
            group.start_soon(fetch, name, query)
    if failures:
        raise failures[0]
    return results


def metrics(row: Mapping[str, Any], with_position: bool) -> MetricSet:
    return MetricSet(
        clicks=int(row.get("clicks", 0)),
        impressions=int(row.get("impressions", 0)),
        ctr=round(float(row.get("ctr", 0.0)), 4),
        position=round(float(row["position"]), 2) if with_position and "position" in row else None,
    )


def zero() -> MetricSet:
    return MetricSet(clicks=0, impressions=0, ctr=0.0)


def totals(
    current: Mapping[str, Any], previous: Mapping[str, Any], with_position: bool
) -> ComparedRow:
    """Compare two dimensionless (property total) responses."""
    return compare_row({}, _first(current, with_position), _first(previous, with_position)) or (
        ComparedRow(keys={}, current=zero(), previous=zero())
    )


def join(
    dims: Sequence[str],
    current: Mapping[str, Any],
    previous: Mapping[str, Any],
    *,
    fetch_limit: int,
    with_position: bool,
) -> tuple[list[ComparedRow], list[str]]:
    """Join two periods' rows on their keys. Returns the rows and which periods ("current",
    "previous") hit fetch_limit, so a key missing from them may just rank below the cut-off.

    A period that returned fewer rows than fetch_limit is complete, so a key missing from it
    had no traffic there and counts as zero.
    """
    cur = _by_key(dims, current, with_position)
    prev = _by_key(dims, previous, with_position)
    cur_complete = len(current.get("rows", [])) < fetch_limit
    prev_complete = len(previous.get("rows", [])) < fetch_limit
    cut_off = [
        label
        for label, complete in (("current", cur_complete), ("previous", prev_complete))
        if not complete
    ]
    rows: list[ComparedRow] = []
    for key in cur.keys() | prev.keys():
        row = compare_row(
            dict(zip(dims, key, strict=True)),
            cur[key] if key in cur else zero() if cur_complete else None,
            prev[key] if key in prev else zero() if prev_complete else None,
        )
        if row is not None:
            rows.append(row)
    # Keys alphabetically, so callers' stable sorts leave full ties in a fixed order.
    rows.sort(key=lambda r: tuple(r.keys.values()))
    return rows, cut_off


def compare_row(
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
            clicks_pct=pct(current.clicks, previous.clicks),
            impressions_pct=pct(current.impressions, previous.impressions),
        )
    return ComparedRow(keys=keys, current=current, previous=previous, change=change)


def pct(current: float, previous: float) -> float | None:
    return round((current - previous) / previous * 100, 1) if previous else None


def _first(data: Mapping[str, Any], with_position: bool) -> MetricSet:
    rows: list[dict[str, Any]] = data.get("rows", [])
    return metrics(rows[0], with_position) if rows else zero()


def _by_key(
    dims: Sequence[str], data: Mapping[str, Any], with_position: bool
) -> dict[tuple[str, ...], MetricSet]:
    return {
        tuple(row.get("keys", [])): metrics(row, with_position)
        for row in data.get("rows", [])
        if len(row.get("keys", [])) == len(dims)
    }
