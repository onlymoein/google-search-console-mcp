"""Tool input and output shapes. Return types double as the MCP output schema."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

SearchType = Literal["web", "image", "video", "news", "discover", "googleNews"]
Dimension = Literal["date", "query", "page", "country", "device", "searchAppearance"]
HourlyDimension = Literal["query", "page", "country", "device"]
BreakdownDimension = Literal["date", "query", "page", "country", "device"]
DiscoverDimension = Literal["date", "page", "country"]
GoogleNewsDimension = Literal["date", "page", "country", "device"]
FilterDimension = Literal["query", "page", "country", "device", "searchAppearance"]
Operator = Literal[
    "equals", "notEquals", "contains", "notContains", "includingRegex", "excludingRegex"
]
DataState = Literal["final", "all"]
AggregationType = Literal["auto", "byPage", "byProperty", "byNewsShowcasePanel"]
# Web, image, video and the News tab: News Showcase panels exist only in Discover and Google News.
SearchAggregation = Literal["auto", "byPage", "byProperty"]
# Discover and Google News: always aggregated by page, so byProperty does not apply.
PageAggregation = Literal["auto", "byPage", "byNewsShowcasePanel"]

Row = dict[str, str | int | float]


class _FilterFields(BaseModel):
    operator: Operator = "equals"
    expression: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "Value to match. country: ISO 3166-1 alpha-3 code such as 'usa'. "
                "device: DESKTOP, MOBILE or TABLET. searchAppearance: an exact name from "
                "list_search_appearances, with equals. Regex operators use RE2 syntax "
                "(no lookarounds or backreferences)."
            ),
        ),
    ]


class DimensionFilter(_FilterFields):
    """One filter. Multiple filters are combined with AND (the only grouping Google supports)."""

    dimension: FilterDimension


class DiscoverFilter(_FilterFields):
    """A Discover filter. Discover has no query or device data to filter on."""

    dimension: Literal["page", "country", "searchAppearance"]

    def general(self) -> DimensionFilter:
        return DimensionFilter.model_validate(self.model_dump())


class GoogleNewsFilter(_FilterFields):
    """A Google News filter. Google News has no query data to filter on."""

    dimension: Literal["page", "country", "device", "searchAppearance"]

    def general(self) -> DimensionFilter:
        return DimensionFilter.model_validate(self.model_dump())


class SiteInfo(BaseModel):
    site_url: str
    permission_level: str
    property_type: Literal["domain", "url_prefix"]


class SiteList(BaseModel):
    sites: list[SiteInfo]
    notes: list[str] = Field(default_factory=list[str])


class AnalyticsResult(BaseModel):
    site_url: str
    search_type: str
    start_date: str
    end_date: str
    data_state: str
    dimensions: list[str]
    rows: list[Row] = Field(
        description="One object per row: dimension values by name, then clicks, impressions, "
        "ctr (0-1) and position (absent for Discover and Google News)."
    )
    row_count: int
    next_start_row: int | None = Field(
        default=None, description="Pass as start_row to fetch the next page, if there is one."
    )
    first_incomplete_date: str | None = None
    first_incomplete_hour: str | None = None
    aggregation_type: str | None = None
    notes: list[str] = Field(default_factory=list[str])
    request: dict[str, Any] = Field(description="The exact request body sent to Google.")


class MetricSet(BaseModel):
    clicks: int
    impressions: int
    ctr: float
    position: float | None = None


class MetricChange(BaseModel):
    clicks: int
    impressions: int
    ctr: float
    position: float | None = None
    clicks_pct: float | None = None
    impressions_pct: float | None = None


class ComparedRow(BaseModel):
    keys: dict[str, str]
    current: MetricSet | None = Field(
        description="Zeros when the key had no traffic in the period. Null only when the "
        "period hit fetch_limit, so the key may just rank below the cut-off."
    )
    previous: MetricSet | None = Field(description="As for current.")
    change: MetricChange | None = Field(
        default=None,
        description="current minus previous. Null when either side is unknown because its "
        "period hit fetch_limit.",
    )


class ComparisonResult(BaseModel):
    site_url: str
    search_type: str
    data_state: str
    current_period: tuple[str, str]
    previous_period: tuple[str, str]
    dimensions: list[str]
    totals: ComparedRow
    rows: list[ComparedRow]
    notes: list[str] = Field(default_factory=list[str])


class RichResultIssue(BaseModel):
    severity: str
    message: str


class RichResultItem(BaseModel):
    type: str
    issues: list[RichResultIssue]


class InspectionResult(BaseModel):
    url: str
    site_url: str
    verdict: str | None = Field(default=None, description="PASS, PARTIAL, FAIL or NEUTRAL.")
    coverage_state: str | None = None
    indexing_state: str | None = None
    page_fetch_state: str | None = None
    robots_txt_state: str | None = None
    last_crawl_time: str | None = None
    crawled_as: str | None = None
    google_canonical: str | None = None
    user_canonical: str | None = None
    canonical_mismatch: bool | None = None
    sitemaps: list[str] = Field(default_factory=list[str])
    referring_urls: list[str] = Field(default_factory=list[str])
    rich_results_verdict: str | None = None
    rich_results: list[RichResultItem] = Field(default_factory=list[RichResultItem])
    amp_verdict: str | None = None
    inspection_link: str | None = None


class BatchItem(BaseModel):
    url: str
    status: Literal["ok", "error", "skipped"]
    result: InspectionResult | None = None
    error: str | None = None


class BatchInspectionResult(BaseModel):
    site_url: str
    items: list[BatchItem]
    verdict_counts: dict[str, int]
    notes: list[str] = Field(default_factory=list[str])


class SitemapContent(BaseModel):
    type: str
    submitted: int


class SitemapInfo(BaseModel):
    path: str
    type: str | None = None
    is_index: bool = False
    is_pending: bool = False
    last_submitted: str | None = None
    last_downloaded: str | None = None
    warnings: int = 0
    errors: int = 0
    contents: list[SitemapContent] = Field(default_factory=list[SitemapContent])


class SitemapList(BaseModel):
    site_url: str
    sitemaps: list[SitemapInfo]


class WriteResult(BaseModel):
    action: str
    site_url: str
    target: str
    done: bool
    notes: list[str] = Field(default_factory=list[str])


# --- Explaining a change ------------------------------------------------------------------


UpdateTiming = Literal["current", "previous", "both", "between", "before"]


class GoogleUpdate(BaseModel):
    name: str
    service: str = Field(description="Ranking, Indexing, Crawling or Serving.")
    start: str
    end: str | None = Field(description="Last day of the rollout; null while it is ongoing.")
    timing: UpdateTiming = Field(
        description="When it ran: during the current or previous period, across both, "
        "between them, or in the two weeks before them."
    )
    url: str


class Mover(BaseModel):
    key: str
    previous: int
    current: int
    change: int
    change_pct: float | None = Field(description="Null when previous is 0.")
    share_pct: float = Field(
        description="This slice's share of all losses in the breakdown (of all gains, for a gain)."
    )
    previous_share_pct: float | None = Field(
        description="This slice's share of the breakdown's previous-period total. A slice "
        "whose share of the loss is far above this lost disproportionately."
    )


class Breakdown(BaseModel):
    dimension: str
    gains: int = Field(description="Sum of all rising slices.")
    losses: int = Field(description="Sum of all falling slices (negative).")
    net: int = Field(description="gains + losses.")
    unattributed: int = Field(
        description="The total change minus net: change these rows do not cover, such as "
        "anonymized queries, results without a search appearance, or rows past fetch_limit."
    )
    top3_share_pct: float | None = Field(
        description="How much of the losses (or gains, if traffic rose) the top 3 slices make up."
    )
    complete: bool = Field(description="False if a period hit fetch_limit.")
    movers: list[Mover] = Field(
        description="The biggest movers in the direction of the total change, then the "
        "biggest movers against it."
    )


class ChangeEffects(BaseModel):
    """Splits a change in clicks exactly into its two causes."""

    impressions: int = Field(
        description="Clicks gained or lost because impressions changed (at the previous CTR)."
    )
    ctr: int = Field(
        description="Clicks gained or lost because CTR changed (on the current impressions)."
    )


class ChangeExplanation(BaseModel):
    site_url: str
    search_type: str
    data_state: str
    metric: str
    current_period: tuple[str, str]
    previous_period: tuple[str, str]
    totals: ComparedRow
    effects: ChangeEffects | None = None
    breakdowns: list[Breakdown]
    google_updates: list[GoogleUpdate] = Field(
        description="Google updates and incidents in or near the periods. Timing alone does "
        "not show that one caused the change."
    )
    summary: list[str] = Field(description="The findings in plain sentences.")
    notes: list[str] = Field(default_factory=list[str])
    requests: list[dict[str, Any]] = Field(description="The exact request bodies sent to Google.")


# --- Indexing audit -------------------------------------------------------------------------


class SitemapSource(BaseModel):
    url: str
    urls: int
    error: str | None = None


class LiveCheck(BaseModel):
    """What the page serves now, fetched directly, to set beside what Google last saw."""

    status: int | None = None
    final_url: str | None = None
    redirects: list[str] = Field(default_factory=list[str])
    left_property: bool = False
    noindex: bool = False
    noindex_source: str | None = None
    canonical: str | None = None
    error: str | None = None


class AuditUrl(BaseModel):
    url: str
    verdict: str | None = None
    coverage_state: str | None = None
    last_crawl_time: str | None = None
    google_canonical: str | None = None
    user_canonical: str | None = None
    impressions: int | None = Field(
        default=None, description="Web search impressions in the last 90 days of final data."
    )
    inspected_at: str | None = None
    error: str | None = None
    live: LiveCheck | None = None


class CoverageCount(BaseModel):
    coverage_state: str
    verdict: str | None
    count: int


class AuditIssue(BaseModel):
    key: str = Field(description="Pass as `issue` to list every URL with this issue.")
    title: str
    count: int
    fix: str
    examples: list[str]


class IndexingAudit(BaseModel):
    site_url: str
    sitemaps: list[SitemapSource]
    urls_in_sitemaps: int
    inspected: int
    not_yet_inspected: int
    inspected_now: int
    live_checked: int
    quota_used_today: int = Field(
        description="Inspections this server used for the property today (Pacific Time). "
        "Other tools and clients share Google's 2,000 a day."
    )
    quota_exhausted_today: bool
    coverage: list[CoverageCount]
    issues: list[AuditIssue]
    issue: str | None = Field(default=None, description="The issue listed in urls, if any.")
    urls: list[AuditUrl] = Field(default_factory=list[AuditUrl])
    next_start: int | None = Field(
        default=None, description="Pass as start to list the next page of the issue's URLs."
    )
    notes: list[str] = Field(default_factory=list[str])
