from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from gsc_mcp.dates import months_before, parse_date
from gsc_mcp.errors import GuardError
from gsc_mcp.guards import AnalyticsQuery, build_query, check_equal_periods, check_re2
from gsc_mcp.models import DimensionFilter

TODAY = date(2026, 10, 14)


def query(**overrides: Any) -> AnalyticsQuery:
    params: dict[str, Any] = {
        "start_date": "2026-10-01",
        "end_date": "2026-10-10",
        "today": TODAY,
        "dimensions": ["query"],
        "search_type": "web",
        "data_state": "all",
    }
    params.update(overrides)
    return build_query(**params)


def flt(dimension: str, operator: str, expression: str) -> DimensionFilter:
    return DimensionFilter.model_validate(
        {"dimension": dimension, "operator": operator, "expression": expression}
    )


def test_body_matches_the_api_shape() -> None:
    q = query(filters=[flt("page", "contains", "/blog/")], row_limit=50, start_row=100)
    assert q.body() == {
        "startDate": "2026-10-01",
        "endDate": "2026-10-10",
        "dimensions": ["query"],
        "type": "web",
        "dataState": "all",
        "aggregationType": "auto",
        "rowLimit": 50,
        "startRow": 100,
        "dimensionFilterGroups": [
            {
                "groupType": "and",
                "filters": [{"dimension": "page", "operator": "contains", "expression": "/blog/"}],
            }
        ],
    }


# Every rejected case below was checked against the live API by scripts/probe_compat.py:
# Google either answers 400 or silently returns zero rows.
REJECTED: list[tuple[str, dict[str, Any], str]] = [
    ("appearance + date", {"dimensions": ["searchAppearance", "date"]}, "cannot be combined"),
    ("appearance + query", {"dimensions": ["searchAppearance", "query"]}, "cannot be combined"),
    ("duplicate dims", {"dimensions": ["query", "query"]}, "repeat"),
    ("discover query", {"search_type": "discover"}, "no query data"),
    ("googleNews query", {"search_type": "googleNews"}, "no query data"),
    (
        "discover query filter",
        {
            "search_type": "discover",
            "dimensions": ["page"],
            "filters": [flt("query", "contains", "a")],
        },
        "no query data",
    ),
    ("byProperty + page", {"dimensions": ["page"], "aggregation_type": "byProperty"}, "byProperty"),
    (
        "byProperty + page filter",
        {"aggregation_type": "byProperty", "filters": [flt("page", "contains", "/")]},
        "byProperty",
    ),
    (
        "showcase on web",
        {"dimensions": ["date"], "aggregation_type": "byNewsShowcasePanel"},
        "discover",
    ),
    (
        "showcase by page",
        {
            "search_type": "discover",
            "dimensions": ["page"],
            "aggregation_type": "byNewsShowcasePanel",
            "filters": [flt("searchAppearance", "equals", "NEWS_SHOWCASE")],
        },
        "group by date or country",
    ),
    (
        "showcase without filter",
        {
            "search_type": "discover",
            "dimensions": ["date"],
            "aggregation_type": "byNewsShowcasePanel",
        },
        "NEWS_SHOWCASE",
    ),
    ("lookahead", {"filters": [flt("query", "includingRegex", "(?=a)a")]}, "lookahead"),
    ("backreference", {"filters": [flt("query", "includingRegex", "(a)\\1")]}, "backreferences"),
    ("invalid regex", {"filters": [flt("query", "includingRegex", "(")]}, "invalid"),
    ("appearance notEquals", {"filters": [flt("searchAppearance", "notEquals", "VIDEO")]}, "bug"),
    ("appearance notContains", {"filters": [flt("searchAppearance", "notContains", "V")]}, "bug"),
    ("alpha-2 country", {"filters": [flt("country", "equals", "us")]}, "alpha-3"),
    ("unknown device", {"filters": [flt("device", "equals", "PHONE")]}, "DESKTOP"),
    ("future end", {"end_date": "2026-10-20"}, "future"),
    ("start after end", {"start_date": "2026-10-11"}, "after"),
    ("bad date", {"start_date": "10/01/2026"}, "YYYY-MM-DD"),
    ("before history", {"start_date": "2024-01-01", "end_date": "2024-02-01"}, "nothing exists"),
    ("row limit", {"row_limit": 25_001}, "row_limit"),
    ("discover by device", {"search_type": "discover", "dimensions": ["device"]}, "no device"),
    (
        "discover device filter",
        {
            "search_type": "discover",
            "dimensions": ["page"],
            "filters": [flt("device", "equals", "MOBILE")],
        },
        "no device",
    ),
    (
        "discover byProperty",
        {"search_type": "discover", "dimensions": ["date"], "aggregation_type": "byProperty"},
        "by page",
    ),
    (
        "googleNews byProperty",
        {"search_type": "googleNews", "dimensions": ["date"], "aggregation_type": "byProperty"},
        "by page",
    ),
    (
        "appearance contains",
        {"filters": [flt("searchAppearance", "contains", "VID")]},
        "exact appearance name",
    ),
    (
        "appearance regex",
        {"filters": [flt("searchAppearance", "includingRegex", "VID.*")]},
        "exact appearance name",
    ),
    ("negative start row", {"start_row": -1}, "start_row"),
]


@pytest.mark.parametrize(
    ("overrides", "message"), [c[1:] for c in REJECTED], ids=[c[0] for c in REJECTED]
)
def test_rejects(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(GuardError, match=message):
        query(**overrides)


ACCEPTED: list[tuple[str, dict[str, Any]]] = [
    ("appearance alone", {"dimensions": ["searchAppearance"]}),
    ("appearance filter + query", {"filters": [flt("searchAppearance", "equals", "VIDEO")]}),
    ("discover by page", {"search_type": "discover", "dimensions": ["page"]}),
    ("googleNews by device", {"search_type": "googleNews", "dimensions": ["device"]}),
    (
        "regex on country",
        {"dimensions": ["country"], "filters": [flt("country", "includingRegex", "^u")]},
    ),
    ("RE2 unicode class", {"filters": [flt("query", "includingRegex", "\\pL+")]}),
    (
        "two filters on one dimension",
        {"filters": [flt("query", "contains", "a"), flt("query", "contains", "b")]},
    ),
    ("totals", {"dimensions": []}),
    (
        "news showcase",
        {
            "search_type": "googleNews",
            "dimensions": ["date"],
            "aggregation_type": "byNewsShowcasePanel",
            "filters": [flt("searchAppearance", "equals", "NEWS_SHOWCASE")],
        },
    ),
]


@pytest.mark.parametrize("overrides", [c[1] for c in ACCEPTED], ids=[c[0] for c in ACCEPTED])
def test_accepts(overrides: dict[str, Any]) -> None:
    query(**overrides)


def test_showcase_lists_every_fix_at_once() -> None:
    with pytest.raises(GuardError) as raised:
        query(
            search_type="googleNews",
            dimensions=["page"],
            aggregation_type="byNewsShowcasePanel",
            filters=[flt("page", "contains", "/news/")],
        )
    message = str(raised.value)
    assert "group by date or country" in message
    assert "remove the page filter" in message
    assert '"expression": "NEWS_SHOWCASE"' in message


def test_normalizes_country_and_device() -> None:
    q = query(filters=[flt("country", "equals", "USA"), flt("device", "notEquals", "mobile")])
    assert [f.expression for f in q.filters] == ["usa", "MOBILE"]


def test_clamps_start_to_sixteen_months_with_a_note() -> None:
    q = query(start_date="2025-01-01")
    assert q.start == months_before(TODAY, 16) == date(2025, 6, 14)
    assert "moved from 2025-01-01" in q.notes[0]


def test_hourly_forces_state_and_hour_dimension() -> None:
    q = query(start_date="2026-10-12", end_date="2026-10-14", dimensions=["page"], hourly=True)
    assert q.data_state == "hourly_all"
    assert q.dimensions == ("hour", "page")


def test_hourly_rejects_old_dates_and_date_dimension() -> None:
    with pytest.raises(GuardError, match="last 10 days"):
        query(start_date="2026-10-01", end_date="2026-10-02", dimensions=[], hourly=True)
    with pytest.raises(GuardError, match="grouped by date"):
        query(start_date="2026-10-12", end_date="2026-10-13", dimensions=["date"], hourly=True)


def test_re2_check_allows_ordinary_patterns() -> None:
    check_re2("^(best|top) [a-z]+ shoes?$")
    check_re2("[[:alpha:]]+")


def test_equal_periods() -> None:
    check_equal_periods(
        (date(2026, 10, 8), date(2026, 10, 14)), (date(2026, 10, 1), date(2026, 10, 7))
    )
    with pytest.raises(GuardError, match="same length"):
        check_equal_periods(
            (date(2026, 10, 8), date(2026, 10, 14)), (date(2026, 10, 1), date(2026, 10, 6))
        )
    with pytest.raises(GuardError, match="overlaps"):
        check_equal_periods(
            (date(2026, 10, 8), date(2026, 10, 14)), (date(2026, 10, 7), date(2026, 10, 13))
        )


@pytest.mark.parametrize(
    ("day", "months", "expected"),
    [
        (date(2026, 10, 14), 16, date(2025, 6, 14)),
        (date(2026, 3, 31), 1, date(2026, 2, 28)),
        (date(2024, 3, 31), 1, date(2024, 2, 29)),
        (date(2026, 1, 15), 13, date(2024, 12, 15)),
    ],
)
def test_months_before(day: date, months: int, expected: date) -> None:
    assert months_before(day, months) == expected


def test_parse_date_names_the_field() -> None:
    with pytest.raises(GuardError, match="end_date"):
        parse_date("yesterday", field="end_date")


@pytest.mark.parametrize(
    "pattern", ["a.*\\z", "\\Qc++\\E", "shoes(?i)SALE", "(?i:boots)", "\\pL{2,}"]
)
def test_re2_syntax_python_lacks_is_accepted(pattern: str) -> None:
    check_re2(pattern)


@pytest.mark.parametrize(
    ("pattern", "message"),
    [("a.*\\Z", "\\\\Z"), ("(?x)a b", "inline flags"), ("(a)?(?(1)b|c)", "conditional")],
)
def test_python_only_syntax_is_rejected(pattern: str, message: str) -> None:
    with pytest.raises(GuardError, match=message):
        check_re2(pattern)


def test_rejects_blank_filter_expression() -> None:
    with pytest.raises(GuardError, match="empty expression"):
        query(filters=[flt("query", "contains", "   ")])


def test_appearance_names_are_uppercased() -> None:
    q = query(filters=[flt("searchAppearance", "equals", "review_snippet")])
    assert q.filters[0].expression == "REVIEW_SNIPPET"
