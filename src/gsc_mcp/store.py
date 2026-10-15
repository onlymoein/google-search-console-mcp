"""Local storage for indexing audits, so one property's audit can span several days.

It keeps the URL list read from the property's sitemaps, each URL's latest inspection and
live check, the inspections used per day, and which pages had search impressions. One
SQLite file in the user data directory (GSC_DATA_DIR). It holds URLs and index states of
the user's own properties, nothing secret, but it is still created owner-only.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from gsc_mcp.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

SCHEMA_VERSION = 1
_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_urls (
    site_url TEXT NOT NULL,
    url TEXT NOT NULL,
    sitemap TEXT,
    in_sitemap INTEGER NOT NULL DEFAULT 1,
    position INTEGER NOT NULL DEFAULT 0,
    inspected_at TEXT,
    verdict TEXT,
    coverage_state TEXT,
    inspection TEXT,
    error TEXT,
    live TEXT,
    live_at TEXT,
    PRIMARY KEY (site_url, url)
);
CREATE TABLE IF NOT EXISTS audit_sites (
    site_url TEXT PRIMARY KEY,
    sitemaps_read_at TEXT,
    sitemaps TEXT,
    pages_day TEXT,
    quota_day TEXT,
    quota_used INTEGER NOT NULL DEFAULT 0,
    quota_exhausted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS audit_pages (
    site_url TEXT NOT NULL,
    url TEXT NOT NULL,
    clicks INTEGER NOT NULL,
    impressions INTEGER NOT NULL,
    PRIMARY KEY (site_url, url)
);
"""


@dataclass(frozen=True, slots=True)
class AuditRow:
    url: str
    sitemap: str | None
    inspected_at: str | None
    verdict: str | None
    coverage_state: str | None
    inspection: dict[str, Any] | None
    error: str | None
    live: dict[str, Any] | None
    live_at: str | None


class AuditStore:
    """Opened on first use, so a server that never audits never creates the file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: sqlite3.Connection | None = None

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # --- The URL list ---------------------------------------------------------------

    def sitemaps(self, site: str) -> tuple[str | None, list[dict[str, Any]]]:
        """When the sitemaps were last read, and what was read from each."""
        row = self._site(site)
        if row is None or row["sitemaps_read_at"] is None:
            return None, []
        return row["sitemaps_read_at"], json.loads(row["sitemaps"] or "[]")

    def replace_urls(
        self,
        site: str,
        urls: Iterable[tuple[str, str]],
        sources: list[dict[str, Any]],
        *,
        read_at: str,
    ) -> None:
        """Make (url, sitemap) pairs the property's URL list, keeping known URLs' results."""
        db = self._db()
        with db:
            self._ensure_site(site)
            db.execute("UPDATE audit_urls SET in_sitemap = 0 WHERE site_url = ?", (site,))
            db.executemany(
                "INSERT INTO audit_urls (site_url, url, sitemap, position) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (site_url, url) DO UPDATE SET in_sitemap = 1, "
                "sitemap = excluded.sitemap, position = excluded.position",
                ((site, url, sitemap, i) for i, (url, sitemap) in enumerate(urls)),
            )
            db.execute(
                "UPDATE audit_sites SET sitemaps_read_at = ?, sitemaps = ? WHERE site_url = ?",
                (read_at, json.dumps(sources), site),
            )

    def rows(self, site: str) -> list[AuditRow]:
        """Every URL currently in the property's sitemaps, in sitemap order."""
        cursor = self._db().execute(
            "SELECT url, sitemap, inspected_at, verdict, coverage_state, inspection, error, "
            "live, live_at FROM audit_urls WHERE site_url = ? AND in_sitemap = 1 "
            "ORDER BY position",
            (site,),
        )
        return [
            AuditRow(
                url=r["url"],
                sitemap=r["sitemap"],
                inspected_at=r["inspected_at"],
                verdict=r["verdict"],
                coverage_state=r["coverage_state"],
                inspection=json.loads(r["inspection"]) if r["inspection"] else None,
                error=r["error"],
                live=json.loads(r["live"]) if r["live"] else None,
                live_at=r["live_at"],
            )
            for r in cursor
        ]

    def to_inspect(self, site: str, limit: int, *, stale_before: str) -> list[str]:
        """Never-inspected URLs first, in sitemap order; then results older than stale_before."""
        cursor = self._db().execute(
            "SELECT url FROM audit_urls WHERE site_url = ? AND in_sitemap = 1 "
            "AND (inspected_at IS NULL OR inspected_at < ?) "
            "ORDER BY inspected_at IS NOT NULL, inspected_at, position LIMIT ?",
            (site, stale_before, limit),
        )
        return [r["url"] for r in cursor]

    def to_check_live(self, site: str, limit: int) -> list[str]:
        """Inspected URLs whose live check is missing or older than their inspection,
        URLs Google does not index first."""
        cursor = self._db().execute(
            "SELECT url FROM audit_urls WHERE site_url = ? AND in_sitemap = 1 "
            "AND inspected_at IS NOT NULL AND (live_at IS NULL OR live_at < inspected_at) "
            "ORDER BY verdict = 'PASS', position LIMIT ?",
            (site, limit),
        )
        return [r["url"] for r in cursor]

    def save_inspection(
        self,
        site: str,
        url: str,
        *,
        at: str,
        inspection: dict[str, Any] | None,
        error: str | None = None,
    ) -> None:
        verdict = inspection.get("verdict") if inspection else None
        coverage = inspection.get("coverage_state") if inspection else None
        with self._db() as db:
            db.execute(
                "UPDATE audit_urls SET inspected_at = ?, verdict = ?, coverage_state = ?, "
                "inspection = ?, error = ? WHERE site_url = ? AND url = ?",
                (
                    at,
                    verdict,
                    coverage,
                    json.dumps(inspection) if inspection else None,
                    error,
                    site,
                    url,
                ),
            )

    def save_live(self, site: str, url: str, *, at: str, live: dict[str, Any]) -> None:
        with self._db() as db:
            db.execute(
                "UPDATE audit_urls SET live = ?, live_at = ? WHERE site_url = ? AND url = ?",
                (json.dumps(live), at, site, url),
            )

    def reset(self, site: str) -> None:
        """Forget everything about the property's audit, to start again from the sitemaps."""
        with self._db() as db:
            for table in ("audit_urls", "audit_pages", "audit_sites"):
                db.execute(f"DELETE FROM {table} WHERE site_url = ?", (site,))  # noqa: S608

    # --- Inspection quota -------------------------------------------------------------

    def quota(self, site: str, day: str) -> tuple[int, bool]:
        """Inspections this server used for the property on `day`, and whether Google
        reported the quota exhausted that day."""
        row = self._site(site)
        if row is None or row["quota_day"] != day:
            return 0, False
        return row["quota_used"], bool(row["quota_exhausted"])

    def count_inspection(self, site: str, day: str) -> None:
        self._set_quota(site, day, used=self.quota(site, day)[0] + 1)

    def mark_exhausted(self, site: str, day: str) -> None:
        self._set_quota(site, day, exhausted=True)

    def _set_quota(
        self, site: str, day: str, *, used: int | None = None, exhausted: bool | None = None
    ) -> None:
        current_used, current_exhausted = self.quota(site, day)
        with self._db() as db:
            self._ensure_site(site)
            db.execute(
                "UPDATE audit_sites SET quota_day = ?, quota_used = ?, quota_exhausted = ? "
                "WHERE site_url = ?",
                (
                    day,
                    current_used if used is None else used,
                    int(current_exhausted if exhausted is None else exhausted),
                    site,
                ),
            )

    # --- Pages with impressions ---------------------------------------------------------

    def pages_day(self, site: str) -> str | None:
        row = self._site(site)
        return row["pages_day"] if row is not None else None

    def replace_pages(self, site: str, day: str, pages: dict[str, tuple[int, int]]) -> None:
        with self._db() as db:
            self._ensure_site(site)
            db.execute("DELETE FROM audit_pages WHERE site_url = ?", (site,))
            db.executemany(
                "INSERT INTO audit_pages (site_url, url, clicks, impressions) VALUES (?, ?, ?, ?)",
                ((site, url, c, i) for url, (c, i) in pages.items()),
            )
            db.execute("UPDATE audit_sites SET pages_day = ? WHERE site_url = ?", (day, site))

    def pages(self, site: str) -> dict[str, tuple[int, int]]:
        cursor = self._db().execute(
            "SELECT url, clicks, impressions FROM audit_pages WHERE site_url = ?", (site,)
        )
        return {r["url"]: (r["clicks"], r["impressions"]) for r in cursor}

    # --- Plumbing ---------------------------------------------------------------------

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fresh = not self.path.exists()
            conn = sqlite3.connect(self.path)
            if fresh:
                os.chmod(self.path, 0o600)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                conn.close()
                raise ConfigError(
                    f"{self.path} was written by a newer open-gsc-mcp; upgrade to use it."
                )
            conn.executescript(_SCHEMA)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self._conn = conn
        return self._conn

    def _site(self, site: str) -> sqlite3.Row | None:
        cursor = self._db().execute("SELECT * FROM audit_sites WHERE site_url = ?", (site,))
        row: sqlite3.Row | None = cursor.fetchone()
        return row

    def _ensure_site(self, site: str) -> None:
        self._db().execute(
            "INSERT INTO audit_sites (site_url) VALUES (?) ON CONFLICT DO NOTHING", (site,)
        )
