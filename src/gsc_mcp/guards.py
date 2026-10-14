"""Validation that runs before any Search Analytics call.

Each rule exists because Google either rejects the request with a terse 400 or, worse,
accepts it and silently returns no rows: an invalid regex, a two-letter country code, an
unknown device, hourly data older than ten days and future dates all come back as an empty
200. `scripts/probe_compat.py` re-checks every rule against the live API.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from gsc_mcp.dates import HISTORY_MONTHS, HOURLY_DAYS, months_before, parse_date, span_days
from gsc_mcp.errors import GuardError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from gsc_mcp.models import DimensionFilter

MAX_ROW_LIMIT = 25_000
NO_QUERY_TYPES = frozenset({"discover", "googleNews"})
NO_DEVICE_TYPES = frozenset({"discover"})
REGEX_OPERATORS = frozenset({"includingRegex", "excludingRegex"})
NEGATIVE_OPERATORS = frozenset({"notEquals", "notContains"})
DEVICES = frozenset({"DESKTOP", "MOBILE", "TABLET"})
_COUNTRY = re.compile(r"^[A-Za-z]{3}$")

# Constructs Python's `re` accepts but RE2 (Google's regex engine) does not.
_RE2_UNSUPPORTED = (
    (re.compile(r"\(\?<?[=!]"), "lookahead/lookbehind"),
    (re.compile(r"\\[1-9]|\\k<|\(\?P="), "backreferences"),
    (re.compile(r"\(\?>"), "atomic groups"),
    (re.compile(r"(?<!\\)[*+?}]\+"), "possessive quantifiers"),
    (re.compile(r"\(\?\("), "conditional groups"),
    (re.compile(r"(?<!\\)\\Z"), "\\Z (use \\z or $)"),
)
# Quoted literals (\Q...\E) are plain text to RE2, so they are set aside before any check.
_QUOTED = re.compile(r"\\Q.*?(?:\\E|$)", re.S)
# Inline flags: RE2 knows i, m, s and U only (Python adds a, L, u and x).
_FLAG_GROUP = re.compile(r"\(\?([A-Za-z-]+)[:)]")
_RE2_FLAGS = frozenset("imsU-")
# RE2-only syntax that Python's `re` rejects, rewritten to an equivalent Python can compile
# so the syntax check does not refuse valid Google patterns.
_RE2_TO_PYTHON = (
    (re.compile(r"\\[pP](\{[^}]*\}|[A-Za-z])"), "x"),  # Unicode classes: \pL, \p{Greek}
    (re.compile(r"\[:\^?[a-z]+:\]"), "x"),  # POSIX classes: [[:alpha:]]
    (re.compile(r"(?<!\\)\\z"), "$"),  # end of text
    (re.compile(r"\\C"), "."),  # any byte
    (re.compile(r"\(\?[imsU-]+\)"), ""),  # inline flags, which Python only allows first
    (re.compile(r"\(\?[imsU-]+:"), "(?:"),  # scoped flags; Python has no U
)


@dataclass(frozen=True, slots=True)
class AnalyticsQuery:
    """A validated Search Analytics request."""

    start: date
    end: date
    dimensions: tuple[str, ...]
    search_type: str
    data_state: str
    aggregation_type: str
    filters: tuple[DimensionFilter, ...] = ()
    row_limit: int = 100
    start_row: int = 0
    notes: tuple[str, ...] = ()

    def body(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "startDate": self.start.isoformat(),
            "endDate": self.end.isoformat(),
            "dimensions": list(self.dimensions),
            "type": self.search_type,
            "dataState": self.data_state,
            "aggregationType": self.aggregation_type,
            "rowLimit": self.row_limit,
            "startRow": self.start_row,
        }
        if self.filters:
            body["dimensionFilterGroups"] = [
                {
                    "groupType": "and",
                    "filters": [
                        {
                            "dimension": f.dimension,
                            "operator": f.operator,
                            "expression": f.expression,
                        }
                        for f in self.filters
                    ],
                }
            ]
        return body


def build_query(
    *,
    start_date: str,
    end_date: str,
    today: date,
    dimensions: Sequence[str],
    search_type: str,
    data_state: str,
    aggregation_type: str = "auto",
    filters: Sequence[DimensionFilter] = (),
    row_limit: int = 100,
    start_row: int = 0,
    hourly: bool = False,
) -> AnalyticsQuery:
    start, end, notes = check_dates(start_date, end_date, today=today, hourly=hourly)
    dims = check_dimensions(dimensions, search_type=search_type, hourly=hourly)
    normalized = check_filters(filters, search_type=search_type)
    check_aggregation(aggregation_type, dims, normalized, search_type=search_type)
    check_paging(row_limit, start_row)
    return AnalyticsQuery(
        start=start,
        end=end,
        dimensions=dims,
        search_type=search_type,
        data_state="hourly_all" if hourly else data_state,
        aggregation_type=aggregation_type,
        filters=tuple(normalized),
        row_limit=row_limit,
        start_row=start_row,
        notes=tuple(notes),
    )


def check_dates(
    start_date: str, end_date: str, *, today: date, hourly: bool = False
) -> tuple[date, date, list[str]]:
    """Validate a date range; returns it, possibly clamped, with notes on any adjustment."""
    start = parse_date(start_date, field="start_date")
    end = parse_date(end_date, field="end_date")
    if start > end:
        raise GuardError(f"start_date {start} is after end_date {end}.")
    if end > today:
        raise GuardError(
            f"end_date {end} is in the future. Search Console dates are in Pacific Time, "
            f"where today is {today}; Google silently returns no rows for future dates."
        )
    if hourly:
        earliest = today - timedelta(days=HOURLY_DAYS)
        if start < earliest:
            raise GuardError(
                f"Hourly data only covers about the last {HOURLY_DAYS} days (from {earliest}, "
                f"Pacific Time); Google silently returns no rows before that. Use a later "
                "start_date, or query_search_analytics for daily data."
            )
        return start, end, []
    earliest = months_before(today, HISTORY_MONTHS)
    if end < earliest:
        raise GuardError(
            f"Search Console keeps {HISTORY_MONTHS} months of data; nothing exists before "
            f"{earliest}, so a range ending {end} has no data."
        )
    if start < earliest:
        return (
            earliest,
            end,
            [
                f"Search Console keeps {HISTORY_MONTHS} months of data, so start_date was moved "
                f"from {start} to {earliest}."
            ],
        )
    return start, end, []


def check_dimensions(
    dimensions: Sequence[str], *, search_type: str, hourly: bool = False
) -> tuple[str, ...]:
    dims = tuple(dimensions)
    duplicates = sorted({d for d in dims if dims.count(d) > 1})
    if duplicates:
        raise GuardError(f"Dimensions repeat: {', '.join(duplicates)}. List each one once.")
    if "searchAppearance" in dims and len(dims) > 1:
        # docs: "Search appearance is not available as a column along with any other dimensions."
        raise GuardError(
            "searchAppearance cannot be combined with other dimensions. First call "
            "list_search_appearances to see which appearances exist, then query with a "
            "searchAppearance filter and the other dimensions you need."
        )
    if "device" in dims and search_type in NO_DEVICE_TYPES:
        raise GuardError(
            "The Discover report has no device breakdown, so 'device' cannot be a dimension."
        )
    if "query" in dims and search_type in NO_QUERY_TYPES:
        raise GuardError(
            f"The {search_type} report has no query data, so 'query' cannot be a dimension. "
            "Group by page, country, device or date instead."
        )
    if hourly:
        if "date" in dims:
            raise GuardError(
                "Hourly data cannot also be grouped by date; each hour already has one."
            )
        return ("hour", *dims)
    return dims


def check_filters(filters: Sequence[DimensionFilter], *, search_type: str) -> list[DimensionFilter]:
    normalized: list[DimensionFilter] = []
    for f in filters:
        expression = f.expression.strip()
        if not expression:
            raise GuardError(f"The {f.dimension} filter has an empty expression.")
        if f.dimension == "query" and search_type in NO_QUERY_TYPES:
            raise GuardError(
                f"The {search_type} report has no query data, so it cannot be filtered by query."
            )
        if f.dimension == "device" and search_type in NO_DEVICE_TYPES:
            raise GuardError(
                "The Discover report has no device data, so it cannot be filtered by device."
            )
        if f.dimension == "searchAppearance":
            if f.operator in NEGATIVE_OPERATORS:
                raise GuardError(
                    f"{f.operator} on searchAppearance is broken in Google's API: it returns "
                    "only the rows you meant to exclude (a confirmed bug). Query the "
                    "appearances you want with equals instead, or query without the filter "
                    "and subtract."
                )
            if f.operator != "equals":
                raise GuardError(
                    "searchAppearance filters need equals and an exact appearance name such "
                    "as VIDEO (Google rejects partial names and patterns). Call "
                    "list_search_appearances to see the names."
                )
            # Google's appearance names are uppercase and matched exactly.
            expression = expression.upper()
        elif f.operator in REGEX_OPERATORS:
            check_re2(expression)
        if f.dimension == "country" and f.operator in ("equals", "notEquals"):
            if not _COUNTRY.match(expression):
                raise GuardError(
                    f"country filters use 3-letter ISO 3166-1 alpha-3 codes such as 'usa', "
                    f"'gbr' or 'deu'; got {expression!r}. Google silently returns no rows "
                    "for other codes."
                )
            expression = expression.lower()
        if f.dimension == "device" and f.operator in ("equals", "notEquals"):
            if expression.upper() not in DEVICES:
                raise GuardError(
                    f"device must be DESKTOP, MOBILE or TABLET; got {expression!r}. Google "
                    "silently returns no rows for other values."
                )
            expression = expression.upper()
        normalized.append(f.model_copy(update={"expression": expression}))
    return normalized


def check_aggregation(
    aggregation_type: str,
    dimensions: Sequence[str],
    filters: Sequence[DimensionFilter],
    *,
    search_type: str,
) -> None:
    uses_page = "page" in dimensions or any(f.dimension == "page" for f in filters)
    if aggregation_type == "byProperty" and search_type in NO_QUERY_TYPES:
        raise GuardError(
            f"The {search_type} report is always aggregated by page; byProperty is not "
            "available. Use 'auto'."
        )
    if aggregation_type == "byProperty" and uses_page:
        # docs: "If you group or filter by page, you cannot aggregate by property."
        raise GuardError(
            "byProperty aggregation cannot be used when grouping or filtering by page. "
            "Use 'auto' (which aggregates by page here)."
        )
    if aggregation_type == "byNewsShowcasePanel":
        if search_type not in NO_QUERY_TYPES:
            raise GuardError(
                "byNewsShowcasePanel only applies to search_type discover or googleNews."
            )
        if uses_page:
            raise GuardError(
                "byNewsShowcasePanel cannot be used when grouping or filtering by page."
            )
        showcase = [
            f
            for f in filters
            if f.dimension == "searchAppearance"
            and f.operator == "equals"
            and f.expression.upper() == "NEWS_SHOWCASE"
        ]
        if not showcase:
            raise GuardError(
                "byNewsShowcasePanel requires a searchAppearance equals NEWS_SHOWCASE filter."
            )


def check_paging(row_limit: int, start_row: int) -> None:
    if not 1 <= row_limit <= MAX_ROW_LIMIT:
        raise GuardError(f"row_limit must be between 1 and {MAX_ROW_LIMIT:,}; got {row_limit}.")
    if start_row < 0:
        raise GuardError(f"start_row must be 0 or more; got {start_row}.")


def check_re2(pattern: str) -> None:
    """Google answers an invalid or non-RE2 regex with zero rows, not an error."""
    unquoted = _QUOTED.sub("x", pattern)
    for regex, name in _RE2_UNSUPPORTED:
        if regex.search(unquoted):
            raise GuardError(
                f"The regex {pattern!r} uses {name}, which Google's RE2 engine does not "
                "support; Google would silently return no rows."
            )
    for flags in _FLAG_GROUP.findall(unquoted):
        if not set(flags) <= _RE2_FLAGS:
            raise GuardError(
                f"The regex {pattern!r} uses the inline flags {flags!r}; RE2 only supports "
                "i, m, s and U. Google would silently return no rows."
            )
    translated = unquoted
    for regex, replacement in _RE2_TO_PYTHON:
        translated = regex.sub(replacement, translated)
    try:
        re.compile(translated)
    except re.error as exc:
        raise GuardError(
            f"The regex {pattern!r} is invalid ({exc}); Google would silently return no rows."
        ) from None


def check_equal_periods(current: tuple[date, date], previous: tuple[date, date]) -> None:
    if span_days(*current) != span_days(*previous):
        raise GuardError(
            f"Periods must be the same length to compare: current is {span_days(*current)} "
            f"days, previous is {span_days(*previous)} days."
        )
    if previous[1] >= current[0] and previous[0] <= current[1]:
        raise GuardError("The previous period overlaps the current one.")
