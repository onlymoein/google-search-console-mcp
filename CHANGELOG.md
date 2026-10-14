# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/onlymoein/google-search-console-mcp/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/onlymoein/google-search-console-mcp/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/onlymoein/google-search-console-mcp/releases/tag/v1.0.0
