from __future__ import annotations

from datetime import date

import httpx2
import pytest

from gsc_mcp.updates import FEED_URL, GoogleUpdates, bundled, from_feed
from gsc_mcp.web import WebClient
from tests.conftest import WebStub

pytestmark = pytest.mark.anyio

PREVIOUS = (date(2026, 7, 19), date(2026, 8, 15))
CURRENT = (date(2026, 8, 16), date(2026, 9, 12))


def incident(id_: str, name: str, begin: str, end: str | None) -> dict[str, object]:
    return {
        "id": id_,
        "external_desc": name,
        "begin": begin,
        "end": end,
        "service_name": "Ranking",
    }


def test_bundled_history_covers_search_consoles_16_months() -> None:
    history = bundled()
    assert len({u.id for u in history}) == len(history)
    assert min(u.start for u in history) < date(2025, 6, 1)
    assert all(u.end is None or u.end >= u.start for u in history)


def test_feed_times_become_pacific_days() -> None:
    # 03:00 UTC on Aug 18 is still Aug 17 in California.
    [update] = from_feed([incident("x", "Late update", "2026-08-18T03:00:00+00:00", None)])
    assert (update.start, update.end) == (date(2026, 8, 17), None)
    assert from_feed([{"id": "broken"}]) == []


async def test_lists_updates_near_the_periods_with_their_timing(web: WebStub) -> None:
    web.on(
        FEED_URL,
        httpx2.Response(
            200,
            json=[
                incident("new", "Fresh update", "2026-09-01T16:00:00+00:00", None),
                incident(
                    "old", "Old update", "2025-01-01T16:00:00+00:00", "2025-01-05T16:00:00+00:00"
                ),
            ],
        ),
    )
    updates = GoogleUpdates(WebClient(transport=httpx2.MockTransport(web.handle)))
    found, note = await updates.between(PREVIOUS, CURRENT)
    timing = {u.name: u.timing for u in found}
    assert timing["Fresh update"] == "current"
    assert timing["August 2026 spam update"] == "current"  # from the bundled history
    assert "Old update" not in timing
    assert note is None
    assert [u.start for u in found] == sorted((u.start for u in found), reverse=True)
    fresh = next(u for u in found if u.name == "Fresh update")
    assert (fresh.end, fresh.url) == (None, "https://status.search.google.com/incidents/new")


async def test_falls_back_to_the_bundled_history_when_the_feed_fails(web: WebStub) -> None:
    web.on(FEED_URL, httpx2.Response(503))
    updates = GoogleUpdates(WebClient(transport=httpx2.MockTransport(web.handle)))
    found, note = await updates.between(PREVIOUS, CURRENT)
    assert "August 2026 spam update" in {u.name for u in found}
    assert note is not None
    assert "could not be read" in note


async def test_caches_the_feed(web: WebStub) -> None:
    web.on(FEED_URL, httpx2.Response(200, json=[]))
    updates = GoogleUpdates(WebClient(transport=httpx2.MockTransport(web.handle)))
    await updates.between(PREVIOUS, CURRENT)
    await updates.between(PREVIOUS, CURRENT)
    assert web.fetched() == [FEED_URL]
