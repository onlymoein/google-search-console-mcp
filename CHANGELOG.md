# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `explain_change`: why traffic rose or fell between two periods. It splits the change by
  page, query, country, device and search appearance and names the slices that account
  for most of it; splits a change in clicks into the part from impressions and the part
  from click-through rate; and lists Google's search updates and incidents in or near the
  periods, from a bundled history back to 2021 plus Google's live feed.
- `audit_indexing`: a full indexing audit rebuilt from the property's sitemaps, since the
  API does not expose the Page indexing report's URL lists. It inspects every sitemap URL
  within the 2,000-a-day quota, saving progress so the audit carries on across calls and
  days; groups URLs by index status with likely fixes; and flags canonical conflicts,
  indexed pages with no impressions in 90 days, and pages shown in search but missing from
  the sitemaps. With `fetch_pages` it also checks each live page (status, redirects,
  noindex, canonical) against what Google last saw.
- `open-gsc-mcp audit SITE`: runs a full day's audit from a terminal or cron, where no
  client timeout applies.
- `GSC_DATA_DIR`: where audits keep their progress.

### Changed

- The server now reads Google's public update feed, and indexing audits read the
  property's own sitemaps and pages. Site fetches stay inside the property and never reach
  hosts on private or local addresses.
- The `weekly_report` prompt starts from `explain_change`, and `indexing_audit` audits every
  sitemap URL instead of a sample of top pages.
- The README's client configurations run `open-gsc-mcp@latest`. Without `@latest`, uvx
  keeps the version it cached the first time, so users never received new releases.

## [1.2.0] - 2026-10-15

### Added

- `query_google_news_analytics` and `query_news_tab_analytics`, which replace
  `query_news_analytics`. Each schema offers only what its source supports: Google News has
  no query dimension and no `byProperty`, and the News tab has no `byNewsShowcasePanel`.

### Deprecated

- `query_news_analytics`. Calls to it still work and go to the new tool its `source`
  selects, but it is no longer listed, so models use the new names. It will be removed in
  2.0.0. If your client allowlists tools by name, add the two new ones.

### Changed

- A page URL as `site_url` now resolves to the property that contains it, with a note that
  the results cover the whole property. It used to be refused.
- `query_image_analytics` and `query_video_analytics` no longer offer
  `byNewsShowcasePanel`, which Google only accepts for Discover and Google News.
- With `byNewsShowcasePanel`, the Discover and Google News tools group by date unless told
  otherwise, since that aggregation cannot group by page.
- The server no longer advertises a `resources` capability; it has no resources.

### Fixed

- `compare_periods` ranked the many keys with no clicks in either period in arbitrary order,
  so its biggest gainers and losers (and the weekly report built on them) were noise. Ties
  now fall back to the change in impressions, then current impressions.
- `compare_periods` reported `change: null` for every key missing from one period. When that
  period returned fewer rows than `fetch_limit`, its data is complete and the key had no
  traffic there, so the change is now computed against zeros. The cut-off warning appears
  only when a period actually hit the limit.
- Arguments the input schema rejects (`row_limit` out of range, more than 50 URLs, a
  dimension a surface does not have) are explained in plain words instead of pydantic's
  report.
- Results whose range reaches the last ~3 days now say that those days are still changing
  when not grouped by date, since Google only reports `first_incomplete_date` for date
  groupings.
- `byNewsShowcasePanel` errors say exactly what to change, including the filter it needs.
- `auth logout` no longer claims to have revoked a login it could not revoke: it says
  whether Google revoked it, had nothing to revoke, or could not be reached.
- A future end date was described as silently returning no rows. Google actually ignores
  the days after today; only a range wholly in the future comes back empty. Such end dates
  are still refused, with an accurate message.

## [1.1.0] - 2026-10-14

### Added

- Surface tools that only accept what each surface supports: `query_discover_analytics`,
  `query_image_analytics`, `query_video_analytics` and `query_news_analytics` (Google News
  or the News tab).
- `query_search_appearance`: step two of the appearance query, breaking one appearance down
  by query, page, country, device or date.

### Fixed

- Requests Google rejects are now caught before the call: Discover grouped or filtered by
  device, `byProperty` aggregation for Discover and Google News, and `searchAppearance`
  filters other than an exact name with `equals`. Appearance names are uppercased, since
  Google matches them case-sensitively.

## [1.0.0] - 2026-10-14

First release.

### Added

- Search analytics for every search type (web, image, video, news, Discover, Google News)
  with filters, data states, aggregation and pagination.
- Hourly analytics for the last ~10 days, with incomplete-hour metadata.
- Search appearance listing (step one of Google's two-step appearance query).
- Period comparison with totals and per-key changes.
- URL inspection, single and concurrent batch (up to 50 URLs) with progress reporting.
- Sitemaps: list and get; submit and delete behind `GSC_ENABLE_WRITES`.
- Properties: list and get; add and delete behind `GSC_ENABLE_WRITES`.
- Guards for API combinations Google rejects or silently answers with no rows, checked
  against the live API by `scripts/probe_compat.py`.
- Site resolution: bare domains and URLs map to the exact property.
- OAuth desktop login (`auth login`, `auth status`, `auth logout`) and service accounts,
  with read-only scope unless writes are enabled.
- Workflow prompts: weekly report, indexing audit, content opportunities.

[Unreleased]: https://github.com/onlymoein/google-search-console-mcp/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/onlymoein/google-search-console-mcp/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/onlymoein/google-search-console-mcp/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/onlymoein/google-search-console-mcp/releases/tag/v1.0.0
