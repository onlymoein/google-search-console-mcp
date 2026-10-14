"""URL Inspection: index status for one URL, or a bounded concurrent batch."""

from __future__ import annotations

import logging
from collections import Counter
from typing import TYPE_CHECKING, Annotated, Any

import anyio
from mcp.server.mcpserver import Context
from pydantic import Field

from gsc_mcp.errors import GSCError, GuardError, QuotaError
from gsc_mcp.models import (
    BatchInspectionResult,
    BatchItem,
    InspectionResult,
    RichResultIssue,
    RichResultItem,
)
from gsc_mcp.tools._common import READ_ONLY, Deps, SiteUrl

if TYPE_CHECKING:
    from mcp.server import MCPServer

    from gsc_mcp.sites import Property

logger = logging.getLogger(__name__)

MAX_BATCH = 50
CONCURRENCY = 5
PER_URL_TIMEOUT = 120.0
QUOTA_NOTE = "URL Inspection quota: 2,000 inspections per day and 600 per minute per property."

LanguageCode = Annotated[
    str, Field(description="BCP-47 language for issue messages, e.g. 'en-US' or 'de-DE'.")
]


def register(mcp: MCPServer, deps: Deps) -> None:
    async def inspect_one(prop: Property, url: str, language_code: str) -> InspectionResult:
        if not prop.contains(url):
            raise GuardError(
                f"{url!r} is not inside the property {prop.site_url!r}. URL Inspection only "
                "works on URLs that belong to the property; check list_sites for the right one."
            )
        data = await deps.client.inspect_url(prop.site_url, url, language_code)
        return to_result(url, prop.site_url, data)

    @mcp.tool(title="Inspect a URL", annotations=READ_ONLY)
    async def inspect_url(
        site_url: SiteUrl,
        url: Annotated[str, Field(description="Full URL of the page, inside the property.")],
        language_code: LanguageCode = "en-US",
    ) -> InspectionResult:
        """Google's index status for one URL: whether it is indexed and why not, last crawl,
        canonical chosen by Google versus declared, robots.txt and fetch state, sitemaps and
        rich results. This is Google's indexed view, not a live test of the page."""
        resolution = await deps.sites.resolve(site_url)
        prop = await deps.sites.property_for(resolution.site_url)
        return await inspect_one(prop, url.strip(), language_code)

    @mcp.tool(title="Inspect many URLs", annotations=READ_ONLY)
    async def batch_inspect_urls(
        site_url: SiteUrl,
        urls: Annotated[
            list[str],
            Field(min_length=1, max_length=MAX_BATCH, description=f"1-{MAX_BATCH} page URLs."),
        ],
        ctx: Context,
        language_code: LanguageCode = "en-US",
    ) -> BatchInspectionResult:
        """Inspect up to 50 URLs concurrently and summarize their index verdicts. Failures
        are reported per URL; if the daily quota runs out, the remaining URLs are skipped
        and listed so you can retry them later."""
        resolution = await deps.sites.resolve(site_url)
        prop = await deps.sites.property_for(resolution.site_url)
        unique = list(dict.fromkeys(u.strip() for u in urls if u.strip()))
        items: dict[str, BatchItem] = {}
        limiter = anyio.Semaphore(CONCURRENCY)
        progress_lock = anyio.Lock()
        quota_hit = anyio.Event()
        quota_failed: set[str] = set()
        done = 0

        async def run(url: str) -> None:
            nonlocal done
            async with limiter:
                if quota_hit.is_set():
                    items[url] = BatchItem(
                        url=url, status="skipped", error="Not inspected: quota exhausted."
                    )
                else:
                    try:
                        with anyio.fail_after(PER_URL_TIMEOUT):
                            result = await inspect_one(prop, url, language_code)
                        items[url] = BatchItem(url=url, status="ok", result=result)
                    except QuotaError as exc:
                        quota_hit.set()
                        quota_failed.add(url)
                        items[url] = BatchItem(url=url, status="error", error=str(exc))
                    except GSCError as exc:
                        items[url] = BatchItem(url=url, status="error", error=str(exc))
                    except TimeoutError:
                        items[url] = BatchItem(
                            url=url,
                            status="error",
                            error=f"Timed out after {PER_URL_TIMEOUT:.0f}s.",
                        )
                    except Exception:
                        # One bad URL must not cost the rest of the batch its results.
                        logger.exception("Unexpected error inspecting %s", url)
                        items[url] = BatchItem(
                            url=url, status="error", error="Unexpected error; see the server log."
                        )
            async with progress_lock:
                done += 1
                await ctx.report_progress(done, len(unique), f"Inspected {url}")

        async with anyio.create_task_group() as group:
            for url in unique:
                group.start_soon(run, url)

        ordered = [items[u] for u in unique]
        verdicts = Counter(
            (i.result.verdict or "UNKNOWN") for i in ordered if i.status == "ok" and i.result
        )
        notes = [QUOTA_NOTE]
        if quota_hit.is_set():
            retry = [i.url for i in ordered if i.status == "skipped" or i.url in quota_failed]
            notes.append(
                f"Quota ran out; {len(retry)} URL(s) were not inspected. Retry them later: "
                + ", ".join(retry)
            )
        if len(unique) < len(urls):
            notes.append(f"{len(urls) - len(unique)} duplicate or empty URL(s) were ignored.")
        return BatchInspectionResult(
            site_url=prop.site_url, items=ordered, verdict_counts=dict(verdicts), notes=notes
        )


def to_result(url: str, site_url: str, data: dict[str, Any]) -> InspectionResult:
    index: dict[str, Any] = data.get("indexStatusResult") or {}
    rich: dict[str, Any] = data.get("richResultsResult") or {}
    amp: dict[str, Any] = data.get("ampResult") or {}
    google_canonical = index.get("googleCanonical")
    user_canonical = index.get("userCanonical")
    mismatch = google_canonical != user_canonical if google_canonical and user_canonical else None
    items = [
        RichResultItem(
            type=str(detected.get("richResultType", "")),
            issues=[
                RichResultIssue(
                    severity=str(issue.get("severity", "")),
                    message=str(issue.get("issueMessage", "")),
                )
                for item in detected.get("items", [])
                for issue in item.get("issues", [])
            ],
        )
        for detected in rich.get("detectedItems", [])
    ]
    return InspectionResult(
        url=url,
        site_url=site_url,
        verdict=index.get("verdict"),
        coverage_state=index.get("coverageState"),
        indexing_state=index.get("indexingState"),
        page_fetch_state=index.get("pageFetchState"),
        robots_txt_state=index.get("robotsTxtState"),
        last_crawl_time=index.get("lastCrawlTime"),
        crawled_as=index.get("crawledAs"),
        google_canonical=google_canonical,
        user_canonical=user_canonical,
        canonical_mismatch=mismatch,
        sitemaps=list(index.get("sitemap", [])),
        referring_urls=list(index.get("referringUrls", [])),
        rich_results_verdict=rich.get("verdict"),
        rich_results=items,
        amp_verdict=amp.get("verdict"),
        inspection_link=data.get("inspectionResultLink"),
    )
