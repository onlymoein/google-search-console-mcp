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
