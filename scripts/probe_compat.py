"""Check the Search Analytics compatibility rules in guards.py against the live API.

Google does not document every invalid combination, so the guards encode beliefs. This
script sends each combination straight to Google (bypassing the guards) and reports which
beliefs no longer hold. Read-only: it only calls searchAnalytics.query.

Each case expects one of three outcomes: "accept" (200, any rows), "reject" (an HTTP
error) or "empty" (200 with zero rows, which is how Google answers many bad requests). Run
it against a property with recent traffic, or the "empty" cases prove nothing.

    export GSC_SERVICE_ACCOUNT_FILE=/abs/key.json
    uv run python scripts/probe_compat.py sc-domain:example.com

Exit status is 1 if any observed result differs from the expectation.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

import anyio

from gsc_mcp.auth import GoogleTokenSource
from gsc_mcp.client import SearchConsoleClient
from gsc_mcp.config import Settings
from gsc_mcp.dates import months_before, today_pacific
from gsc_mcp.errors import ApiError


@dataclass(frozen=True)
class Case:
    name: str
    expect: Literal["accept", "reject", "empty"]
    body: dict[str, Any]


def cases(days_ago: Any) -> list[Case]:
    week = {"startDate": days_ago(9), "endDate": days_ago(3)}
    recent = {"startDate": days_ago(3), "endDate": days_ago(1)}

    def q(**extra: Any) -> dict[str, Any]:
        return {**week, "rowLimit": 5, **extra}

    def f(dimension: str, operator: str, expression: str) -> dict[str, Any]:
        return {
            "filters": [{"dimension": dimension, "operator": operator, "expression": expression}]
        }

    def regex(pattern: str) -> dict[str, Any]:
        """Queries matching `pattern`; the property must have queries containing 'a'."""
        return q(
            dimensions=["query"], dimensionFilterGroups=[f("query", "includingRegex", pattern)]
        )

    hourly = {"startDate": days_ago(2), "endDate": days_ago(1), "dataState": "hourly_all"}
    query = {"dimensions": ["query"]}
    return [
        Case("searchAppearance alone", "accept", q(dimensions=["searchAppearance"])),
        Case("searchAppearance + date", "reject", q(dimensions=["searchAppearance", "date"])),
        Case("searchAppearance + query", "reject", q(dimensions=["searchAppearance", "query"])),
        Case("searchAppearance + device", "reject", q(dimensions=["searchAppearance", "device"])),
        Case(
            "appearance filter + query",
            "accept",
            q(**query, dimensionFilterGroups=[f("searchAppearance", "equals", "VIDEO")]),
        ),
        Case("discover by page", "accept", q(type="discover", dimensions=["page"])),
        Case("discover by query", "reject", q(type="discover", **query)),
        Case("googleNews by query", "reject", q(type="googleNews", **query)),
        Case(
            "discover query filter",
            "reject",
            q(
                type="discover",
                dimensions=["page"],
                dimensionFilterGroups=[f("query", "contains", "a")],
            ),
        ),
        Case("hour with final", "reject", {**recent, "dimensions": ["hour"], "dataState": "final"}),
        Case("hour with hourly_all", "accept", {**hourly, "dimensions": ["hour"]}),
        Case("hour + query", "accept", {**hourly, "dimensions": ["hour", "query"], "rowLimit": 5}),
        Case("hour + date", "reject", {**hourly, "dimensions": ["hour", "date"]}),
        Case(
            "hour 12 days ago",
            "empty",
            {
                "startDate": days_ago(12),
                "endDate": days_ago(11),
                "dataState": "hourly_all",
                "dimensions": ["hour"],
            },
        ),
        Case("byProperty + page", "reject", q(dimensions=["page"], aggregationType="byProperty")),
        Case(
            "byProperty + page filter",
            "reject",
            q(
                **query,
                aggregationType="byProperty",
                dimensionFilterGroups=[f("page", "contains", "/")],
            ),
        ),
        Case(
            "showcase panel on web",
            "reject",
            q(dimensions=["date"], aggregationType="byNewsShowcasePanel"),
        ),
        Case(
            "regex on query",
            "accept",
            q(**query, dimensionFilterGroups=[f("query", "includingRegex", "a|e")]),
        ),
        Case(
            "regex on country",
            "accept",
            q(
                dimensions=["country"],
                dimensionFilterGroups=[f("country", "includingRegex", "^[a-z]{3}$")],
            ),
        ),
        Case(
            "RE2 unicode class",
            "accept",
            q(**query, dimensionFilterGroups=[f("query", "includingRegex", "\\pL")]),
        ),
        Case(
            "lookahead regex",
            "empty",
            q(**query, dimensionFilterGroups=[f("query", "includingRegex", "(?=a)a")]),
        ),
        Case(
            "backreference regex",
            "empty",
            q(**query, dimensionFilterGroups=[f("query", "includingRegex", "(a)\\1")]),
        ),
        Case(
            "invalid regex",
            "empty",
            q(**query, dimensionFilterGroups=[f("query", "includingRegex", "(")]),
        ),
        Case("discover by device", "reject", q(type="discover", dimensions=["device"])),
        Case(
            "discover device filter",
            "reject",
            q(
                type="discover",
                dimensions=["date"],
                dimensionFilterGroups=[f("device", "equals", "MOBILE")],
            ),
        ),
        Case("googleNews by device", "accept", q(type="googleNews", dimensions=["device"])),
        Case(
            "discover byProperty",
            "reject",
            q(type="discover", dimensions=["date"], aggregationType="byProperty"),
        ),
        Case(
            "googleNews byProperty",
            "reject",
            q(type="googleNews", dimensions=["date"], aggregationType="byProperty"),
        ),
        Case(
            "appearance lowercase name",
            "reject",
            q(
                dimensions=["query"],
                dimensionFilterGroups=[f("searchAppearance", "equals", "video")],
            ),
        ),
        Case(
            "appearance partial contains",
            "reject",
            q(
                dimensions=["query"],
                dimensionFilterGroups=[f("searchAppearance", "contains", "VID")],
            ),
        ),
        Case("RE2 end of text \\z", "accept", regex("a.*\\z")),
        Case("RE2 quoted \\Q..\\E", "accept", regex("\\Qa\\E")),
        Case("RE2 flag mid-pattern", "accept", regex("a(?i)R")),
        Case("\\Z (not RE2)", "empty", regex("a.*\\Z")),
        Case("verbose flag (not RE2)", "empty", regex("(?x)a")),
        Case(
            "two filters, same dimension",
            "accept",
            q(
                **query,
                dimensionFilterGroups=[
                    {
                        "filters": [
                            {"dimension": "query", "operator": "contains", "expression": "a"},
                            {"dimension": "query", "operator": "contains", "expression": "e"},
                        ]
                    }
                ],
            ),
        ),
        Case(
            "country alpha-2",
            "empty",
            q(dimensions=["country"], dimensionFilterGroups=[f("country", "equals", "us")]),
        ),
        Case(
            "unknown device",
            "empty",
            q(dimensions=["device"], dimensionFilterGroups=[f("device", "equals", "PHONE")]),
        ),
        Case(
            "range in the future",
            "empty",
            {"startDate": days_ago(-1), "endDate": days_ago(-3), "dimensions": ["date"]},
        ),
        Case("rowLimit 25001", "reject", q(**query, rowLimit=25001)),
    ]


async def run(site_url: str) -> int:
    settings = Settings.from_env()
    client = SearchConsoleClient(GoogleTokenSource(settings), max_retries=1)
    today = today_pacific()

    def days_ago(n: int) -> str:
        return (today - timedelta(days=n)).isoformat()

    mismatches = 0
    try:
        for case in cases(days_ago):
            try:
                data = await client.query_search_analytics(site_url, case.body)
                rows = len(data.get("rows", []))
                observed, detail = ("accept" if rows else "empty"), f"{rows} rows"
            except ApiError as exc:
                observed = "reject"
                detail = f"HTTP {exc.status}: {str(exc).split('Google said: ')[-1][:90]}"
            matched = observed == case.expect or (case.expect == "accept" and observed == "empty")
            mismatches += not matched
            flag = "ok" if matched else "MISMATCH"
            print(f"{flag:8} {case.name:28} expect {case.expect:6} | {detail}")
        earliest = months_before(today, 16)
        try:
            body = {
                "startDate": (earliest - timedelta(days=30)).isoformat(),
                "endDate": earliest.isoformat(),
            }
            data = await client.query_search_analytics(site_url, body)
            print(f"info     {'beyond 16 months':28} accepted, {len(data.get('rows', []))} rows")
        except ApiError as exc:
            print(f"info     {'beyond 16 months':28} rejected HTTP {exc.status}")
    finally:
        await client.aclose()
    print(f"\n{mismatches} mismatch(es)")
    return 1 if mismatches else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("site_url", help="exact property, e.g. sc-domain:example.com")
    args = parser.parse_args()
    sys.exit(anyio.run(run, args.site_url))


if __name__ == "__main__":
    main()
