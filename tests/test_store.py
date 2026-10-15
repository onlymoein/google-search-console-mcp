from __future__ import annotations

import sqlite3
import sys
from typing import TYPE_CHECKING

import pytest

from gsc_mcp.errors import ConfigError
from gsc_mcp.store import AuditStore

if TYPE_CHECKING:
    from pathlib import Path

SITE = "sc-domain:example.com"


@pytest.fixture
def store(tmp_path: Path) -> AuditStore:
    return AuditStore(tmp_path / "data" / "audits.sqlite3")


def urls(*names: str) -> list[tuple[str, str]]:
    return [(f"https://example.com/{n}", "https://example.com/sitemap.xml") for n in names]


def test_is_created_on_first_use_and_owner_only(store: AuditStore) -> None:
    assert not store.path.exists()
    store.replace_urls(SITE, urls("a"), [], read_at="2026-10-14T00:00:00+00:00")
    assert store.path.exists()
    if sys.platform != "win32":
        assert store.path.stat().st_mode & 0o777 == 0o600


def test_keeps_results_when_the_sitemaps_change(store: AuditStore) -> None:
    store.replace_urls(SITE, urls("a", "b"), [], read_at="t1")
    store.save_inspection(SITE, "https://example.com/a", at="t1", inspection={"verdict": "PASS"})
    store.replace_urls(SITE, urls("c", "a"), [{"url": "s"}], read_at="t2")
    rows = store.rows(SITE)
    assert [r.url for r in rows] == ["https://example.com/c", "https://example.com/a"]
    assert rows[1].verdict == "PASS"
    assert store.sitemaps(SITE) == ("t2", [{"url": "s"}])


def test_inspects_new_urls_first_then_stale_ones(store: AuditStore) -> None:
    store.replace_urls(SITE, urls("a", "b", "c"), [], read_at="t")
    store.save_inspection(SITE, "https://example.com/a", at="2026-09-01", inspection=None)
    store.save_inspection(SITE, "https://example.com/b", at="2026-10-10", inspection=None)
    assert store.to_inspect(SITE, 10, stale_before="2026-10-01") == [
        "https://example.com/c",
        "https://example.com/a",
    ]
    assert store.to_inspect(SITE, 1, stale_before="2026-10-01") == ["https://example.com/c"]


def test_quota_is_counted_per_day(store: AuditStore) -> None:
    store.count_inspection(SITE, "2026-10-14")
    store.count_inspection(SITE, "2026-10-14")
    store.mark_exhausted(SITE, "2026-10-14")
    assert store.quota(SITE, "2026-10-14") == (2, True)
    assert store.quota(SITE, "2026-10-15") == (0, False)


def test_reset_forgets_one_property(store: AuditStore) -> None:
    store.replace_urls(SITE, urls("a"), [], read_at="t")
    store.replace_urls("sc-domain:other.org", urls("b"), [], read_at="t")
    store.replace_pages(SITE, "2026-10-14", {"https://example.com/a": (1, 10)})
    store.reset(SITE)
    assert store.rows(SITE) == []
    assert store.pages(SITE) == {}
    assert len(store.rows("sc-domain:other.org")) == 1


def test_refuses_a_file_from_a_newer_version(store: AuditStore) -> None:
    store.path.parent.mkdir(parents=True)
    with sqlite3.connect(store.path) as conn:
        conn.execute("PRAGMA user_version = 99")
    with pytest.raises(ConfigError, match="newer open-gsc-mcp"):
        store.rows(SITE)
