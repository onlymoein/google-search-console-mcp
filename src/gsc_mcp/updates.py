"""Google Search updates and incidents, to set beside a change in traffic.

Two sources. A history bundled with the package (scripts/update_google_updates.py writes
it from status.search.google.com before each release), and Google's live feed, which adds
anything newer. Dates are Pacific Time days, like Search Console's own.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from importlib import resources
from typing import TYPE_CHECKING, Any, cast

from gsc_mcp.dates import PACIFIC
from gsc_mcp.models import GoogleUpdate, UpdateTiming
from gsc_mcp.web import FetchError

if TYPE_CHECKING:
    from collections.abc import Callable

    from gsc_mcp.web import WebClient

logger = logging.getLogger(__name__)

FEED_URL = "https://status.search.google.com/incidents.json"
INCIDENT_URL = "https://status.search.google.com/incidents/{id}"
CACHE_SECONDS = 6 * 3600
# An update that finished shortly before the comparison can still be settling during it.
LEAD_DAYS = 14


@dataclass(frozen=True, slots=True)
class Update:
    id: str
    name: str
    service: str
    start: date
    end: date | None


def bundled() -> list[Update]:
    raw = resources.files("gsc_mcp").joinpath("google_updates.json").read_text("utf-8")
    return [
        Update(
            id=item["id"],
            name=item["name"],
            service=item["service"],
            start=date.fromisoformat(item["start"]),
            end=date.fromisoformat(item["end"]) if item.get("end") else None,
        )
        for item in json.loads(raw)
    ]


def from_feed(feed: list[dict[str, Any]]) -> list[Update]:
    """Incidents from Google's live feed. Its timestamps are UTC; Search Console's days are
    Pacific Time, so each is converted."""
    updates: list[Update] = []
    for item in feed:
        try:
            updates.append(
                Update(
                    id=str(item["id"]),
                    name=str(item["external_desc"]).strip(),
                    service=str(item.get("service_name") or "Search"),
                    start=_pacific_day(item["begin"]),
                    end=_pacific_day(item["end"]) if item.get("end") else None,
                )
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("Skipping a malformed entry in Google's update feed: %r", item)
    return updates


class GoogleUpdates:
    """The bundled history merged with Google's live feed, which is cached for a while."""

    def __init__(self, web: WebClient, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._web = web
        self._clock = clock
        self._feed: list[Update] | None = None
        self._fetched_at = 0.0

    async def between(
        self, previous: tuple[date, date], current: tuple[date, date]
    ) -> tuple[list[GoogleUpdate], str | None]:
        """Updates overlapping the two periods (or ending shortly before), newest first,
        and a note if the live feed could not be read."""
        note = None
        try:
            feed = await self._live()
        except FetchError as exc:
            feed = []
            note = (
                f"Google's live update feed could not be read ({exc}), so updates after this "
                "version was released may be missing."
            )
        merged = {u.id: u for u in bundled()}
        merged.update({u.id: u for u in feed})
        window_start = min(previous[0], current[0]) - timedelta(days=LEAD_DAYS)
        window_end = max(previous[1], current[1])
        found = [
            GoogleUpdate(
                name=u.name,
                service=u.service,
                start=u.start.isoformat(),
                end=u.end.isoformat() if u.end else None,
                timing=_timing(u, previous, current),
                url=INCIDENT_URL.format(id=u.id),
            )
            for u in merged.values()
            if u.start <= window_end and (u.end or window_end) >= window_start
        ]
        found.sort(key=lambda g: g.start, reverse=True)
        return found, note

    async def _live(self) -> list[Update]:
        if self._feed is None or self._clock() - self._fetched_at > CACHE_SECONDS:
            data = await self._web.get_json(FEED_URL)
            if not isinstance(data, list):
                raise FetchError("the feed is not a list of incidents")
            items = cast("list[object]", data)
            self._feed = from_feed(
                [cast("dict[str, Any]", i) for i in items if isinstance(i, dict)]
            )
            self._fetched_at = self._clock()
        return self._feed


def _timing(
    update: Update, previous: tuple[date, date], current: tuple[date, date]
) -> UpdateTiming:
    end = update.end or date.max

    def overlaps(period: tuple[date, date]) -> bool:
        return update.start <= period[1] and end >= period[0]

    in_previous, in_current = overlaps(previous), overlaps(current)
    if in_previous and in_current:
        return "both"
    if in_current:
        return "current"
    if in_previous:
        return "previous"
    first = min(previous[0], current[0])
    return "before" if end < first else "between"


def _pacific_day(timestamp: str) -> date:
    return datetime.fromisoformat(timestamp).astimezone(PACIFIC).date()
