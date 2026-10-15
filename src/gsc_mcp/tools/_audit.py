"""Full indexing audit: every URL in a property's sitemaps, inspected within Google's daily
quota, with progress kept between runs so a large site is covered over several days.

The Search Console API does not expose the Page indexing report's URL lists. This rebuilds
them: read the sitemaps, inspect each URL, group the results by Google's coverage state,
and flag indexed pages that never appear in search. It can also fetch each page to set what
it serves now beside what Google last saw. Used by the audit_indexing tool and the
`open-gsc-mcp audit` command.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import anyio

from gsc_mcp.client import INSPECTIONS_PER_DAY
from gsc_mcp.crawl import Sitemap, page_signals, parse_sitemap
from gsc_mcp.dates import FRESH_DAYS
from gsc_mcp.errors import GSCError, GuardError, QuotaError
from gsc_mcp.guards import build_query
from gsc_mcp.models import (
    AuditIssue,
    AuditUrl,
    CoverageCount,
    IndexingAudit,
    LiveCheck,
    SitemapSource,
)
from gsc_mcp.sites import same_url
from gsc_mcp.tools.inspection import to_result
from gsc_mcp.web import PAGE_BYTES, SITEMAP_BYTES, FetchError, gunzip

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from gsc_mcp.sites import Property
    from gsc_mcp.store import AuditRow
    from gsc_mcp.tools._common import Deps

logger = logging.getLogger(__name__)

INSPECT_CONCURRENCY = 10  # Google allows 600 a minute; one inspection takes seconds
LIVE_CONCURRENCY = 4
RECHECK_DAYS = 14
SITEMAP_TTL = timedelta(days=1)
SITEMAP_CONCURRENCY = 4
MAX_SITEMAPS = 500
MAX_URLS = 200_000
MAX_INDEX_DEPTH = 3
PAGE_DAYS = 90
MAX_PAGE_ROWS = 100_000
PAGE_SIZE = 100

COVERAGE_FIXES = (
    (
        "crawled - currently not indexed",
        "Google crawled the page and chose not to index it, usually because it judged the "
        "content thin, duplicated or low in value. Improve or merge it, and link to it from "
        "strong pages.",
    ),
    (
        "discovered - currently not indexed",
        "Google knows the URL but has not crawled it yet; common on new or large sites. "
        "Strengthen internal links to it and keep the sitemap to pages worth indexing.",
    ),
    (
        "unknown to google",
        "Google has not seen this URL. Check that the sitemap is submitted and that other "
        "pages link to it.",
    ),
    (
        "noindex",
        "The page asks not to be indexed. Remove the noindex if it should appear in search; "
        "otherwise take it out of the sitemap.",
    ),
    (
        "robots.txt",
        "robots.txt stops Google from crawling it. Allow it if it should be indexed; "
        "otherwise take it out of the sitemap.",
    ),
    (
        "alternate page with proper canonical",
        "Fine if intended: the page names another URL as canonical. List that URL in the "
        "sitemap instead.",
    ),
    (
        "duplicate",
        "Google treats the page as a duplicate and indexes another URL. Check its canonical "
        "tag, or merge the pages.",
    ),
    (
        "redirect",
        "Sitemaps should list final URLs. Replace each redirecting URL with its target.",
    ),
    (
        "not found (404)",
        "The URL returns 404. Take it out of the sitemap, or restore or redirect the page.",
    ),
    (
        "soft 404",
        "The page looks empty or like an error page to Google. Give it real content, or "
        "return 404 or 410.",
    ),
    ("server error", "Google got a 5xx error. Check the server's stability and logs."),
    ("unauthorized", "The page needs a login (401), so Google cannot index it."),
    ("forbidden", "The server refused Google (403). Check firewall and bot-blocking rules."),
)
DEFAULT_FIX = (
    "See Google's help for this Page indexing status: "
    "https://support.google.com/webmasters/answer/7440203"
)


@dataclass(slots=True)
class _Issue:
    title: str
    fix: str
    rank: int
    urls: list[str] = field(default_factory=list[str])


class Auditor:
    """One audit run for one property. Every result is saved as soon as it arrives, so an
    interrupted run loses nothing."""

    def __init__(self, deps: Deps, prop: Property) -> None:
        self.deps = deps
        self.prop = prop
        self.site = prop.site_url
        self.store = deps.store
        self.today = deps.today()
        self.notes: list[str] = []
        self.inspected_now = 0
        self.live_checked = 0

    @staticmethod
    def now() -> str:
        return datetime.now(UTC).isoformat(timespec="seconds")

    # --- Sitemaps ---------------------------------------------------------------------

    async def sync_sitemaps(self, sitemap_url: str | None = None, *, force: bool = False) -> None:
        """Read the sitemaps into the URL list, at most once a day unless asked."""
        read_at, sources = self.store.sitemaps(self.site)
        roots = {s["url"] for s in sources if s.get("root")}
        stale = read_at is None or (
            datetime.now(UTC) - datetime.fromisoformat(read_at) > SITEMAP_TTL
        )
        if not (force or stale or (sitemap_url and sitemap_url not in roots)):
            return
        if sitemap_url:
            starts = [sitemap_url]
        else:
            submitted = await self.deps.client.list_sitemaps(self.site)
            starts = [str(s["path"]) for s in submitted if s.get("path")]
            if not starts:
                raise GuardError(
                    f"No sitemaps are submitted for {self.site}, so there is no URL list to "
                    "audit. Pass sitemap_url, or submit a sitemap in Search Console."
                )
        urls, sources = await self._read_sitemaps(starts)
        if len(urls) > MAX_URLS:
            self.notes.append(f"Only the first {MAX_URLS:,} sitemap URLs are audited.")
        self.store.replace_urls(
            self.site, list(urls.items())[:MAX_URLS], sources, read_at=self.now()
        )

    async def _read_sitemaps(
        self, starts: list[str]
    ) -> tuple[dict[str, str], list[dict[str, object]]]:
        """Read sitemaps and the sitemaps their indexes list, level by level. Returns each
        page URL with the sitemap it came from, and what was read from each sitemap."""
        urls: dict[str, str] = {}
        sources: list[dict[str, object]] = []
        seen: set[str] = set()
        outside: list[str] = []
        limiter = anyio.Semaphore(SITEMAP_CONCURRENCY)

        async def read(url: str, root: bool, children: list[tuple[str, bool]]) -> None:
            async with limiter:
                try:
                    sitemap = await self._fetch_sitemap(url)
                except FetchError as exc:
                    sources.append({"url": url, "urls": 0, "error": str(exc), "root": root})
                    return
            inside = 0
            for loc in sitemap.locations:
                if not self.prop.contains(loc):
                    outside.append(loc)
                elif sitemap.kind == "sitemapindex":
                    children.append((loc, False))
                else:
                    urls.setdefault(loc, url)
                    inside += 1
            sources.append({"url": url, "urls": inside, "error": None, "root": root})

        level = [(u, True) for u in dict.fromkeys(starts)]
        for depth in range(MAX_INDEX_DEPTH + 1):
            children: list[tuple[str, bool]] = []
            batch = [(u, r) for u, r in level if u not in seen][: MAX_SITEMAPS - len(seen)]
            seen.update(u for u, _ in batch)
            async with anyio.create_task_group() as group:
                for url, root in batch:
                    group.start_soon(read, url, root, children)
            level = children
            if not level:
                break
            if depth == MAX_INDEX_DEPTH:
                self.notes.append("Sitemap indexes nested deeper than 3 levels were not read.")
        if len(seen) >= MAX_SITEMAPS:
            self.notes.append(f"Only the first {MAX_SITEMAPS} sitemaps were read.")
        if outside:
            self.notes.append(
                f"{len(outside):,} sitemap entries point outside {self.site} and were left "
                f"out, for example {outside[0]}."
            )
        return urls, sources

    async def _fetch_sitemap(self, url: str) -> Sitemap:
        fetched = await self.deps.web.fetch(
            url, allowed=self.prop.contains, max_bytes=SITEMAP_BYTES
        )
        if fetched.left_property:
            raise FetchError(f"It redirects outside the property, to {fetched.final_url}.")
        if fetched.status != 200:
            raise FetchError(f"HTTP {fetched.status}.")
        if fetched.truncated:
            raise FetchError("It is larger than 50 MB, Google's limit.")
        return parse_sitemap(gunzip(fetched.body))

    # --- Pages with impressions ------------------------------------------------------------

    def impressions_period(self) -> tuple[date, date]:
        end = self.today - timedelta(days=FRESH_DAYS)
        return end - timedelta(days=PAGE_DAYS - 1), end

    async def sync_pages(self) -> None:
        """Every page with web search impressions in the last 90 days of final data, once a
        day: the sitemap URLs missing from it never appeared in search."""
        if self.store.pages_day(self.site) == self.today.isoformat():
            return
        start, end = self.impressions_period()
        pages: dict[str, tuple[int, int]] = {}
        start_row = 0
        while start_row < MAX_PAGE_ROWS:
            query = build_query(
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                today=self.today,
                dimensions=["page"],
                search_type="web",
                data_state="final",
                row_limit=25_000,
                start_row=start_row,
            )
            data = await self.deps.client.query_search_analytics(self.site, query.body())
            rows = data.get("rows", [])
            for row in rows:
                keys = row.get("keys") or [""]
                pages[str(keys[0])] = (int(row.get("clicks", 0)), int(row.get("impressions", 0)))
            if len(rows) < 25_000:
                break
            start_row += len(rows)
        self.store.replace_pages(self.site, self.today.isoformat(), pages)

    # --- Inspection -------------------------------------------------------------------

    async def inspect(
        self, limit: int, progress: Callable[[int, int, str], Awaitable[None]] | None = None
    ) -> None:
        """Inspect up to `limit` URLs: never-inspected ones first, then results older than
        two weeks. Stops cleanly when Google's daily quota runs out."""
        day = self.today.isoformat()
        used, exhausted = self.store.quota(self.site, day)
        budget = min(limit, INSPECTIONS_PER_DAY - used)
        if exhausted or budget <= 0:
            return
        stale = (datetime.now(UTC) - timedelta(days=RECHECK_DAYS)).isoformat()
        urls = self.store.to_inspect(self.site, budget, stale_before=stale)
        limiter = anyio.Semaphore(INSPECT_CONCURRENCY)
        quota_hit = anyio.Event()
        done = 0

        async def run(url: str) -> None:
            nonlocal done
            async with limiter:
                if quota_hit.is_set():
                    return
                try:
                    data = await self.deps.client.inspect_url(self.site, url)
                    result = to_result(url, self.site, data).model_dump()
                    self.store.save_inspection(self.site, url, at=self.now(), inspection=result)
                    self.store.count_inspection(self.site, day)
                    self.inspected_now += 1
                except QuotaError:
                    quota_hit.set()
                    self.store.mark_exhausted(self.site, day)
                    return
                except GSCError as exc:
                    self.store.save_inspection(
                        self.site, url, at=self.now(), inspection=None, error=str(exc)
                    )
                    self.store.count_inspection(self.site, day)
            done += 1
            if progress is not None:
                await progress(done, len(urls), url)

        async with anyio.create_task_group() as group:
            for url in urls:
                group.start_soon(run, url)

    # --- Live checks ------------------------------------------------------------------

    async def check_live(self, limit: int) -> None:
        """Fetch inspected pages directly, URLs Google does not index first."""
        limiter = anyio.Semaphore(LIVE_CONCURRENCY)

        async def run(url: str) -> None:
            async with limiter:
                live = await self._live(url)
            self.store.save_live(self.site, url, at=self.now(), live=live.model_dump())
            self.live_checked += 1

        async with anyio.create_task_group() as group:
            for url in self.store.to_check_live(self.site, limit):
                group.start_soon(run, url)

    async def _live(self, url: str) -> LiveCheck:
        try:
            fetched = await self.deps.web.fetch(
                url, allowed=self.prop.contains, max_bytes=PAGE_BYTES
            )
        except FetchError as exc:
            return LiveCheck(error=str(exc))
        except Exception:
            logger.exception("Unexpected error fetching %s", url)
            return LiveCheck(error="Unexpected error; see the server log.")
        signals = page_signals(fetched.body, fetched.headers, fetched.final_url)
        return LiveCheck(
            status=fetched.status,
            final_url=fetched.final_url,
            redirects=fetched.redirects,
            left_property=fetched.left_property,
            noindex=signals.noindex,
            noindex_source=signals.noindex_source,
            canonical=signals.canonical,
        )

    # --- Report -----------------------------------------------------------------------

    def report(
        self, *, issue: str | None = None, start: int = 0, examples: int = 5
    ) -> IndexingAudit:
        rows = self.store.rows(self.site)
        pages = self.store.pages(self.site)
        have_pages = self.store.pages_day(self.site) is not None
        _, sources = self.store.sitemaps(self.site)
        issues = self._issues(rows, pages, have_pages)
        inspected = sum(1 for r in rows if r.inspected_at)
        coverage = Counter(
            (r.coverage_state or ("Inspection failed" if r.error else "Unknown"), r.verdict)
            for r in rows
            if r.inspected_at
        )
        used, exhausted = self.store.quota(self.site, self.today.isoformat())

        listed: list[AuditUrl] = []
        next_start = None
        if issue is not None:
            if issue not in issues:
                known = ", ".join(issues) or "none yet"
                raise GuardError(f"No issue {issue!r} in this audit. Current issues: {known}.")
            by_url = {r.url: r for r in rows}
            urls = issues[issue].urls
            for url in urls[start : start + PAGE_SIZE]:
                listed.append(self._audit_url(url, by_url.get(url), pages, have_pages))
            if start + PAGE_SIZE < len(urls):
                next_start = start + PAGE_SIZE

        return IndexingAudit(
            site_url=self.site,
            sitemaps=[
                SitemapSource(url=str(s["url"]), urls=int(s["urls"]), error=s.get("error"))
                for s in sources
            ],
            urls_in_sitemaps=len(rows),
            inspected=inspected,
            not_yet_inspected=len(rows) - inspected,
            inspected_now=self.inspected_now,
            live_checked=self.live_checked,
            quota_used_today=used,
            quota_exhausted_today=exhausted,
            coverage=[
                CoverageCount(coverage_state=state, verdict=verdict, count=count)
                for (state, verdict), count in coverage.most_common()
            ],
            issues=[
                AuditIssue(
                    key=key,
                    title=found.title,
                    count=len(found.urls),
                    fix=found.fix,
                    examples=found.urls[:examples],
                )
                for key, found in sorted(issues.items(), key=lambda i: (i[1].rank, -len(i[1].urls)))
            ],
            issue=issue,
            urls=listed,
            next_start=next_start,
            notes=self.notes
            + self._report_notes(len(rows), inspected, exhausted, have_pages, rows),
        )

    def _issues(
        self, rows: list[AuditRow], pages: dict[str, tuple[int, int]], have_pages: bool
    ) -> dict[str, _Issue]:
        issues: dict[str, _Issue] = {}

        def add(key: str, title: str, fix: str, rank: int, url: str) -> None:
            issues.setdefault(key, _Issue(title=title, fix=fix, rank=rank)).urls.append(url)

        for row in rows:
            ins = row.inspection or {}
            live = row.live or {}
            coverage = row.coverage_state or ""
            if row.error:
                add(
                    "inspection_failed",
                    "Inspection failed",
                    "Retry later; the errors say why.",
                    9,
                    row.url,
                )
            if ins and row.verdict != "PASS":
                add(
                    f"not_indexed:{_slug(coverage)}",
                    f"Not indexed: {coverage}",
                    _fix(coverage),
                    0,
                    row.url,
                )
            if ins.get("canonical_mismatch"):
                add(
                    "canonical_mismatch",
                    "Google chose a different canonical than the page declares",
                    "Google indexes another URL for this content. Make the canonical tag, "
                    "internal links and sitemap agree on one URL.",
                    5,
                    row.url,
                )
            if ins and row.verdict == "PASS" and have_pages and row.url not in pages:
                add(
                    "indexed_no_impressions",
                    f"Indexed, but no web search impressions in {PAGE_DAYS} days",
                    "Google indexed the page but never showed it. Check that it targets "
                    "searches people make, and that it is not a near-duplicate.",
                    6,
                    row.url,
                )
            if not live:
                continue
            if live.get("error"):
                add(
                    "live_unreachable",
                    "The page could not be fetched now",
                    "See each URL's error.",
                    8,
                    row.url,
                )
                continue
            status = live.get("status") or 0
            if live.get("left_property") or live.get("redirects"):
                add(
                    "live_redirects",
                    "The sitemap lists a URL that redirects",
                    "Sitemaps should list final URLs. Replace each with its redirect target.",
                    3,
                    row.url,
                )
            elif status >= 400:
                add(
                    "live_error_status",
                    "The page returns an error now",
                    "Restore the page or take it out of the sitemap.",
                    2,
                    row.url,
                )
            if live.get("noindex") and row.verdict == "PASS":
                add(
                    "live_noindex_indexed",
                    "Indexed, but the page now says noindex",
                    "It will drop out of Google at the next crawl. Remove the noindex if "
                    "that is not intended.",
                    1,
                    row.url,
                )
            if not live.get("noindex") and status == 200 and "noindex" in coverage.lower():
                add(
                    "noindex_removed",
                    "Google saw noindex, but the page no longer has it",
                    "Request indexing for it in Search Console's URL Inspection.",
                    1,
                    row.url,
                )
            canonical = live.get("canonical")
            final = live.get("final_url") or row.url
            if canonical and not same_url(canonical, final) and not live.get("noindex"):
                add(
                    "live_canonical_elsewhere",
                    "The page's canonical tag points to another URL",
                    "List the canonical URL in the sitemap instead, or fix the tag if it is wrong.",
                    4,
                    row.url,
                )
        if have_pages:
            listed = {r.url for r in rows}
            missing = sorted(
                (u for u in pages if u not in listed and self.prop.contains(u)),
                key=lambda u: -pages[u][1],
            )
            for url in missing:
                add(
                    "in_search_not_in_sitemaps",
                    "Shown in search but missing from the sitemaps",
                    "Add these pages to a sitemap if they should be indexed, or redirect "
                    "them if they are old or duplicate URLs.",
                    7,
                    url,
                )
        return issues

    def _audit_url(
        self,
        url: str,
        row: AuditRow | None,
        pages: dict[str, tuple[int, int]],
        have_pages: bool,
    ) -> AuditUrl:
        impressions = pages.get(url, (0, 0))[1] if have_pages else None
        if row is None:
            return AuditUrl(url=url, impressions=impressions)
        ins = row.inspection or {}
        return AuditUrl(
            url=url,
            verdict=row.verdict,
            coverage_state=row.coverage_state,
            last_crawl_time=ins.get("last_crawl_time"),
            google_canonical=ins.get("google_canonical"),
            user_canonical=ins.get("user_canonical"),
            impressions=impressions,
            inspected_at=row.inspected_at,
            error=row.error,
            live=LiveCheck.model_validate(row.live) if row.live else None,
        )

    def _report_notes(
        self,
        total: int,
        inspected: int,
        exhausted: bool,
        have_pages: bool,
        rows: list[AuditRow],
    ) -> list[str]:
        notes: list[str] = []
        remaining = total - inspected
        if remaining:
            used, _ = self.store.quota(self.site, self.today.isoformat())
            left_today = 0 if exhausted else max(INSPECTIONS_PER_DAY - used, 0)
            if remaining <= left_today:
                when = "today's quota covers them"
            else:
                days = -(-(remaining - left_today) // INSPECTIONS_PER_DAY)
                when = f"at Google's 2,000 a day that takes {days} more day{'s' * (days > 1)}"
            notes.append(
                f"{remaining:,} of {total:,} sitemap URLs are not inspected yet; {when}. Call "
                "again to continue, or run `open-gsc-mcp audit` in a terminal to use a full "
                "day's quota in one go."
            )
        if exhausted:
            notes.append(
                "Google's inspection quota for this property is used up for today (Pacific "
                "Time); the audit continues tomorrow."
            )
        if have_pages:
            start, end = self.impressions_period()
            notes.append(
                f"Impressions are web search impressions from {start} to {end}. Sitemap URLs "
                "are matched exactly to the page URLs Google reports, so http and https, www "
                "and trailing slashes count as different URLs."
            )
        if inspected and not any(r.live for r in rows):
            notes.append(
                "Pass fetch_pages=true to also fetch each page and compare its status code, "
                "redirects, noindex and canonical with what Google saw."
            )
        return notes


def _slug(text: str) -> str:
    """An issue key the model can type back: Google's states contain curly quotes."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "unknown"


def _fix(coverage: str) -> str:
    lowered = coverage.lower()
    for needle, fix in COVERAGE_FIXES:
        if needle in lowered:
            return fix
    return DEFAULT_FIX
