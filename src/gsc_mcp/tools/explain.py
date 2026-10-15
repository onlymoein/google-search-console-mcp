"""Why did traffic change? Split the change between two periods by page, query, country,
device and search appearance, say which slices account for most of it, and set Google's
updates beside it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field

from gsc_mcp.guards import NO_DEVICE_TYPES, NO_QUERY_TYPES, build_query
from gsc_mcp.models import (
    Breakdown,
    ChangeEffects,
    ChangeExplanation,
    DimensionFilter,
    Mover,
    SearchType,
)
from gsc_mcp.tools._common import READ_ONLY, Deps, SiteUrl
from gsc_mcp.tools._comparison import (
    CurrentEnd,
    CurrentStart,
    FinalByDefault,
    PreviousEnd,
    PreviousStart,
    fetch_all,
    join,
    pct,
    resolve_periods,
    totals,
)

if TYPE_CHECKING:
    from mcp.server import MCPServer

    from gsc_mcp.guards import AnalyticsQuery
    from gsc_mcp.models import ComparedRow, GoogleUpdate, MetricSet
    from gsc_mcp.tools._comparison import Period

Metric = Literal["clicks", "impressions"]
BreakdownDimension = Literal["page", "query", "country", "device", "searchAppearance"]
BREAKDOWNS: tuple[BreakdownDimension, ...] = (
    "page",
    "query",
    "country",
    "device",
    "searchAppearance",
)
LABELS = {
    "page": ("page", "pages"),
    "query": ("query", "queries"),
    "country": ("country", "countries"),
    "device": ("device", "devices"),
    "searchAppearance": ("search appearance", "search appearances"),
}
FEW_CLICKS = 30
TIMING_WORDS = {
    "current": "during the current period",
    "previous": "during the previous period",
    "both": "across both periods",
    "between": "between the two periods",
    "before": "shortly before both periods",
}


def register(mcp: MCPServer, deps: Deps) -> None:
    @mcp.tool(title="Explain a change in traffic", annotations=READ_ONLY)
    async def explain_change(
        site_url: SiteUrl,
        current_start: CurrentStart = None,
        current_end: CurrentEnd = None,
        previous_start: PreviousStart = None,
        previous_end: PreviousEnd = None,
        metric: Annotated[
            Metric, Field(description="The metric whose change to explain.")
        ] = "clicks",
        search_type: SearchType = "web",
        breakdowns: Annotated[
            list[BreakdownDimension] | None,
            Field(
                description="Dimensions to split the change by. Omit for every one the "
                "search type has: page, query, country, device and searchAppearance."
            ),
        ] = None,
        filters: Annotated[
            list[DimensionFilter] | None,
            Field(description="Explain the change within this slice only; combined with AND."),
        ] = None,
        data_state: FinalByDefault = "final",
        top: Annotated[
            int, Field(ge=1, le=20, description="Movers to list per breakdown and direction.")
        ] = 5,
        fetch_limit: Annotated[
            int,
            Field(ge=1, le=25_000, description="Top rows fetched per period and breakdown."),
        ] = 5000,
    ) -> ChangeExplanation:
        """Answer "why did traffic go up or down?" between two periods of equal length.
        Splits the change by page, query, country, device and search appearance and says
        which slices account for most of it; separates clicks lost to fewer impressions
        from clicks lost to a lower CTR; and lists Google updates that ran in or near the
        periods. Defaults compare the last 28 days of final data with the 28 before."""
        today = deps.today()
        current, previous = resolve_periods(
            current_start, current_end, previous_start, previous_end, today=today
        )
        dims = _available(search_type) if breakdowns is None else list(dict.fromkeys(breakdowns))

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

        queries = {"current": make(current, [], 1), "previous": make(previous, [], 1)}
        for dim in dims:
            queries[f"current:{dim}"] = make(current, [dim], fetch_limit)
            queries[f"previous:{dim}"] = make(previous, [dim], fetch_limit)
        resolution = await deps.sites.resolve(site_url)
        results = await fetch_all(deps, resolution.site_url, queries)
        updates, feed_note = await deps.updates.between(previous, current)

        with_position = search_type not in NO_QUERY_TYPES
        total = totals(results["current"], results["previous"], with_position)
        cur_total, prev_total = _value(total.current, metric), _value(total.previous, metric)
        change = cur_total - prev_total
        breakdown_list: list[Breakdown] = []
        cut_off_dims: list[str] = []
        for dim in dims:
            rows, cut_off = join(
                [dim],
                results[f"current:{dim}"],
                results[f"previous:{dim}"],
                fetch_limit=fetch_limit,
                with_position=with_position,
            )
            if cut_off:
                cut_off_dims.append(dim)
            breakdown_list.append(
                _breakdown(dim, rows, metric, change, top=top, complete=not cut_off)
            )

        effects = _effects(total) if metric == "clicks" else None
        summary = _headline(metric, cur_total, prev_total, current, previous)
        summary += _effect_lines(effects, total)
        summary += [line for b in breakdown_list if (line := _breakdown_line(b, metric))]
        summary += _update_lines(updates)

        notes: list[str] = []
        if resolution.note:
            notes.append(resolution.note)
        if metric == "clicks" and max(cur_total, prev_total) < FEW_CLICKS:
            notes.append(
                "There are few clicks in either period, so the split is noisy; "
                "metric='impressions' may say more."
            )
        if cut_off_dims:
            notes.append(
                f"These breakdowns hit fetch_limit ({fetch_limit:,} rows) in a period: "
                f"{', '.join(cut_off_dims)}. Slices past the cut-off are in unattributed; "
                "raise fetch_limit to cover them."
            )
        if "query" in dims:
            notes.append(
                "Google leaves rare (anonymized) queries out of query rows, so part of the "
                "change is usually unattributed by query."
            )
        if metric == "impressions" and "page" in dims:
            notes.append(
                "Page rows count an impression for each of the site's URLs in a result, so "
                "they can add up to more than the property total."
            )
        if data_state == "all":
            notes.append(
                "data_state='all' includes fresh data that is still being processed; recent "
                "days in the current period may be undercounted."
            )
        if feed_note:
            notes.append(feed_note)

        return ChangeExplanation(
            site_url=resolution.site_url,
            search_type=search_type,
            data_state=data_state,
            metric=metric,
            current_period=(current[0].isoformat(), current[1].isoformat()),
            previous_period=(previous[0].isoformat(), previous[1].isoformat()),
            totals=total,
            effects=effects,
            breakdowns=breakdown_list,
            google_updates=updates,
            summary=summary,
            notes=notes,
            requests=[q.body() for q in queries.values()],
        )


def _available(search_type: str) -> list[str]:
    return [
        d
        for d in BREAKDOWNS
        if not (d == "query" and search_type in NO_QUERY_TYPES)
        and not (d == "device" and search_type in NO_DEVICE_TYPES)
    ]


def _value(metrics: MetricSet | None, metric: Metric) -> int:
    if metrics is None:
        return 0
    return metrics.clicks if metric == "clicks" else metrics.impressions


def _breakdown(
    dim: str, rows: list[ComparedRow], metric: Metric, change: int, *, top: int, complete: bool
) -> Breakdown:
    known = [
        (row.keys[dim], _value(row.current, metric), _value(row.previous, metric))
        for row in rows
        if row.current is not None and row.previous is not None
    ]
    gains = sum(cur - prev for _, cur, prev in known if cur > prev)
    losses = sum(cur - prev for _, cur, prev in known if cur < prev)
    previous_sum = sum(prev for _, _, prev in known)
    # The side to explain: the direction of the total change, or the larger side if flat.
    falling = change < 0 or (change == 0 and -losses > gains)
    side_total, other_total = (losses, gains) if falling else (gains, losses)

    def movers(rising: bool, limit: int, side: int) -> list[Mover]:
        picked = [k for k in known if (k[1] > k[2]) == rising and k[1] != k[2]]
        picked.sort(key=lambda k: (-abs(k[1] - k[2]), -k[2], k[0]))
        return [
            Mover(
                key=key,
                previous=prev,
                current=cur,
                change=cur - prev,
                change_pct=pct(cur, prev),
                share_pct=round((cur - prev) / side * 100, 1),
                previous_share_pct=round(prev / previous_sum * 100, 1) if previous_sum else None,
            )
            for key, cur, prev in picked[:limit]
        ]

    main = movers(not falling, top, side_total)
    against = movers(falling, top, other_total)
    top3 = sum(m.change for m in main[:3])
    return Breakdown(
        dimension=dim,
        gains=gains,
        losses=losses,
        net=gains + losses,
        unattributed=change - (gains + losses),
        top3_share_pct=round(top3 / side_total * 100, 1) if side_total else None,
        complete=complete,
        movers=main + against,
    )


def _effects(total: ComparedRow) -> ChangeEffects | None:
    """Clicks = impressions x CTR, so a change in clicks splits exactly into the part from
    impressions (at the previous CTR) and the part from CTR (on current impressions)."""
    cur, prev = total.current, total.previous
    if cur is None or prev is None or prev.impressions == 0:
        return None
    from_impressions = round((cur.impressions - prev.impressions) * prev.clicks / prev.impressions)
    return ChangeEffects(
        impressions=from_impressions, ctr=(cur.clicks - prev.clicks) - from_impressions
    )


def _headline(metric: Metric, cur: int, prev: int, current: Period, previous: Period) -> list[str]:
    periods = f"{current[0]} to {current[1]} against {previous[0]} to {previous[1]}"
    name = metric.capitalize()
    if cur == prev:
        return [f"{name} did not change: {cur:,} in both periods ({periods})."]
    direction = "rose" if cur > prev else "fell"
    change_pct = pct(cur, prev)
    percent = f" ({change_pct:+.1f}%)" if change_pct is not None else ""
    return [
        f"{name} {direction} by {abs(cur - prev):,}{percent}, from {prev:,} to {cur:,} ({periods})."
    ]


def _effect_lines(effects: ChangeEffects | None, total: ComparedRow) -> list[str]:
    lines: list[str] = []
    if effects is not None and (effects.impressions or effects.ctr):
        parts: list[str] = []
        if effects.impressions:
            more = "more" if effects.impressions > 0 else "fewer"
            parts.append(f"{effects.impressions:+,} clicks from {more} impressions")
        if effects.ctr:
            higher = "a higher" if effects.ctr > 0 else "a lower"
            parts.append(f"{effects.ctr:+,} from {higher} click-through rate")
        lines.append("That splits into " + " and ".join(parts) + ".")
    before = total.previous.position if total.previous else None
    after = total.current.position if total.current else None
    if before is not None and after is not None and abs(after - before) >= 0.1:
        moved = "improved" if after < before else "worsened"
        lines.append(
            f"Average position {moved} from {before:.1f} to {after:.1f} (lower is better)."
        )
    return lines


def _breakdown_line(b: Breakdown, metric: Metric) -> str | None:
    if not b.movers or b.top3_share_pct is None:
        return None
    # movers lists the side that explains the total change first.
    falling = b.movers[0].change < 0
    side = [m for m in b.movers if (m.change < 0) == falling][:3]
    singular, plural = LABELS[b.dimension]
    word = f"{'lost' if falling else 'gained'} {metric}"
    lead = side[0]
    before = (
        f"; {lead.key} had {lead.previous_share_pct:.0f}% of {metric} before"
        if lead.previous_share_pct is not None
        else ""
    )
    if len(side) == 1:
        return (
            f"By {singular}, {lead.key} ({lead.change:+,}) makes up {lead.share_pct:.0f}% of "
            f"the {word}{before}."
        )
    leaders = ", ".join(f"{m.key} ({m.change:+,})" for m in side)
    return (
        f"By {singular}, the top {len(side)} {plural} make up {b.top3_share_pct:.0f}% of the "
        f"{word}: {leaders}{before}."
    )


def _update_lines(updates: list[GoogleUpdate]) -> list[str]:
    relevant = [u for u in updates if u.timing != "before"][:4]
    if not relevant:
        return []
    lines = [
        f"Google's {u.name} ran {u.start} to {u.end or 'now (ongoing)'}, {TIMING_WORDS[u.timing]}."
        for u in relevant
    ]
    lines.append(
        "Timing alone does not show that an update caused the change; compare its dates "
        "with daily data (query_search_analytics with dimensions ['date'])."
    )
    return lines
