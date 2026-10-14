# Google Search Console MCP Server

[![CI](https://github.com/onlymoein/google-search-console-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/onlymoein/google-search-console-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/open-gsc-mcp?label=PyPI)](https://pypi.org/project/open-gsc-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/open-gsc-mcp?label=Python)](https://pypi.org/project/open-gsc-mcp/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

<!-- mcp-name: io.github.onlymoein/google-search-console-mcp -->

A local [Model Context Protocol](https://modelcontextprotocol.io) server that connects AI
assistants (Claude Code, Claude Desktop, Cursor and any other MCP client) to the
[Google Search Console API](https://developers.google.com/webmaster-tools). Ask about clicks,
queries, pages, indexing and sitemaps in plain language; the server turns that into correct
API calls and returns structured data.

It runs on your machine, talks only to Google, and stores nothing but your own login.

> **Unofficial.** This project is not affiliated with or endorsed by Google. You use your own
> Google Cloud credentials and are responsible for your usage under Google's terms.

## Why this one

- **It catches the API's traps.** Google answers many bad requests with an empty result
  instead of an error: an invalid regex, a two-letter country code, hourly data older than
  ten days, a future date. Other combinations fail with a terse 400. Every one of these is
  checked before the call and explained in plain words. The rules are
  [verified against the live API](scripts/probe_compat.py), not copied from blog posts.
- **Read-only by default.** Write tools do not exist unless you turn them on, and the login
  only asks Google for read-only access unless you need more.
- **Structured output.** Every tool declares an output schema, rows come back as named
  fields, and every analytics result includes the exact request sent to Google.
- **Full API coverage**: search analytics (all search types, filters, data states,
  pagination), hourly data, search appearances, URL inspection (single and concurrent
  batch), sitemaps and properties, plus a dedicated tool for each search surface (Discover,
  Images, video, news) that only accepts what that surface supports.
- **Built on the current stack**: MCP Python SDK 2.x (protocol 2026-07-28 and older clients
  alike) and an async client, so batch inspection runs concurrently.

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) and a Google account
with access to at least one Search Console property.

1. **Create an OAuth client** in Google Cloud (about five minutes, once):
   1. Open [console.cloud.google.com](https://console.cloud.google.com/) and create or pick a
      project.
   2. **APIs & Services > Library**: enable the **Google Search Console API**.
   3. **Google Auth Platform > Branding**: fill in an app name and your email.
   4. **Google Auth Platform > Audience**: choose **External**, add your Google account as a
      test user, then click **Publish app** so the status is **In production**.
      *Skip this and Google expires your login every 7 days.*
   5. **Google Auth Platform > Clients > Create client**: type **Desktop app**. Download the
      JSON and keep it outside any repository, for example
      `~/.config/open-gsc-mcp/client_secret.json`.

2. **Sign in** (opens a browser once; the server itself never does):

   ```sh
   uvx open-gsc-mcp auth login --client-file ~/.config/open-gsc-mcp/client_secret.json
   ```

   Google shows "Google hasn't verified this app" because the app is yours and unreviewed;
   choose **Advanced > Go to (your app)**. When it finishes, the command lists the
   properties it can see.

3. **Add the server to your client** (below), restart the client, and ask something like
   *"Which queries lost the most clicks this month on example.com?"*

## Client configuration

**Claude Code**

```sh
claude mcp add --scope user gsc -- uvx open-gsc-mcp
```

**Claude Desktop**: edit `claude_desktop_config.json`
(macOS: `~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`):

```json
{
  "mcpServers": {
    "gsc": {
      "command": "uvx",
      "args": ["open-gsc-mcp"]
    }
  }
}
```

If Claude Desktop reports that `uvx` cannot be found, use its absolute path
(`which uvx` on macOS and Linux, `where uvx` on Windows).

**Cursor**: add the same `mcpServers` block to `~/.cursor/mcp.json`.

To pass settings, add an `env` object to the server entry, or `-e NAME=value` to
`claude mcp add`. Paths must be absolute: clients start the server from an arbitrary
working directory.

## Service account (headless)

For servers, CI or shared machines, use a service account instead of a personal login:

1. In Google Cloud, **IAM & Admin > Service Accounts**: create one, then **Keys > Add key >
   JSON**. (Some organizations block key creation by policy.)
2. In Search Console, **Settings > Users and permissions > Add user**: add the service
   account's email to each property, with Full or Restricted permission.
3. Point the server at the key:

   ```sh
   claude mcp add --scope user gsc -e GSC_SERVICE_ACCOUNT_FILE=/abs/path/key.json -- uvx open-gsc-mcp
   ```

When `GSC_SERVICE_ACCOUNT_FILE` is set it takes precedence over any OAuth login.

## Tools

| Tool | What it does |
|---|---|
| `list_sites` | Properties this account can access, with exact `site_url` and permission level. |
| `get_site` | One property's type and permission level. |
| `query_search_analytics` | Clicks, impressions, CTR and position by query, page, country, device, date or search appearance; any search type; filters; `final` or `all` data; paging. |
| `query_hourly_analytics` | The same, by hour, for the last ~10 days. Reports where incomplete hours start. |
| `query_discover_analytics` | Google Discover by page, country or date. No query, device or position, so the tool doesn't offer them. |
| `query_image_analytics` | Google Images: queries, pages and positions within image results. |
| `query_video_analytics` | The Videos tab of Google Search. |
| `query_news_analytics` | Google News (news.google.com and the app), or the News tab in Search with `source="news_tab"`. |
| `list_search_appearances` | Rich results and other result features the property had: step one of Google's two-step appearance query. |
| `query_search_appearance` | Step two: one appearance broken down by query, page, country, device or date. |
| `compare_periods` | Two equal-length periods: totals and per-key changes, absolute and percent. |
| `inspect_url` | Index status, coverage, canonical (Google's versus yours), crawl, robots.txt and rich results for one URL. |
| `batch_inspect_urls` | Up to 50 URLs concurrently, with progress, per-URL errors and a verdict summary. Stops cleanly when the quota runs out. |
| `list_sitemaps`, `get_sitemap` | Submitted sitemaps with errors, warnings, pending state and URL counts. |

With `GSC_ENABLE_WRITES=1` four more tools appear: `submit_sitemap`, `delete_sitemap`,
`add_site` and `delete_site`. They are annotated as writes (deletes as destructive), so
well-behaved clients ask before running them.

Sites can be given as `sc-domain:example.com`, `https://www.example.com/`, or a bare
`example.com`, which is resolved against your real property list.

**Prompts**: `weekly_report`, `indexing_audit` and `content_opportunities` are ready-made
workflows with concrete dates and exact tool calls.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `GSC_SERVICE_ACCOUNT_FILE` | unset | Service account key; when set, it is used instead of OAuth. |
| `GSC_OAUTH_CLIENT_FILE` | unset | OAuth client JSON, so `auth login` needs no `--client-file`. |
| `GSC_TOKEN_FILE` | per-user config dir | Where `auth login` saves the login (mode 0600). |
| `GSC_ENABLE_WRITES` | `0` | Register the four write tools. Needs a login with write access: `auth login --write`. |
| `GSC_LOG_LEVEL` | `INFO` | `DEBUG` also logs every HTTP request. Logs go to stderr. |

Command line:

```sh
uvx open-gsc-mcp                   # run the MCP server over stdio (what clients do)
uvx open-gsc-mcp auth login        # sign in; --write for write access, --no-browser over SSH
uvx open-gsc-mcp auth status       # which credentials are used, and do they work
uvx open-gsc-mcp auth logout       # revoke and delete the saved login
```

## API behavior handled for you

| Google's behavior | What the server does |
|---|---|
| `searchAppearance` cannot be grouped with any other dimension (400). | Rejects it and points to the two-step pattern. |
| `notEquals`/`notContains` on `searchAppearance` return the rows you meant to exclude (a confirmed Google bug). | Rejects them with an explanation. |
| Discover and Google News have no query data (400) and no position; Discover has no device data (400). | Rejects `query` (and `device` for Discover); leaves position out of rows. The surface tools don't even offer them. |
| `searchAppearance` filters must be an exact, uppercase appearance name for that search type (anything else: 400). | Requires `equals`, uppercases the name, and offers a dedicated step-two tool. |
| `hour` needs `dataState=hourly_all` and cannot be combined with `date` (400). | A dedicated hourly tool sets both. |
| Hourly data older than ~10 days, a future end date, an invalid or non-RE2 regex, a two-letter country code or an unknown device all return **zero rows, no error**. | Rejects each before the call. |
| Dates are Pacific Time; 16 months of history. | Uses Pacific Time; clamps the start date and says so. |
| `byProperty` aggregation fails with page grouping or filters, and for Discover and Google News (400). | Rejects it. |
| Recent data is incomplete. | Reports `first_incomplete_date` / `first_incomplete_hour`; comparisons default to `final` data. |
| At most 25,000 rows per request, top rows only. | Defaults to 100 rows and returns `next_start_row` for paging. |
| URL Inspection: 2,000 per day and 600 per minute per property. | Batches run 5 at a time and stop cleanly on quota errors. |
| `siteUrl` must match the property string exactly. | Resolves bare domains and URLs against your property list. |

**Not available through Google's API**, so no server can provide them: the generative AI
(AI Overviews and AI Mode) report, platform properties (Instagram, TikTok, X, YouTube), the
branded query filter, Core Web Vitals, manual actions, links, and the Page indexing report's
URL lists.

## Troubleshooting

- **"Your Google login has expired or was revoked (invalid_grant)"**: the OAuth app is
  probably still in Testing status, which limits logins to 7 days. Publish it (Quick start,
  step 1.4) and run `auth login` again.
- **"Not signed in"**: run `uvx open-gsc-mcp auth login --client-file ...`, or set
  `GSC_SERVICE_ACCOUNT_FILE`.
- **No properties, or 403 for a service account**: add the service account's email as a user
  on the property in Search Console.
- **Empty results**: check the date range (Pacific Time; `final` data lags 2-3 days), the
  `search_type`, and filters. Each result's `notes` and `request` show what was asked.
- **Check the setup end to end**: `uvx open-gsc-mcp auth status`.

## Security and privacy

- Runs locally over stdio. No telemetry; nothing is sent anywhere except Google's API.
- The OAuth login is stored in your user config directory with owner-only permissions.
  `auth logout` revokes it at Google and deletes it.
- Logins request read-only access unless writes are enabled.
- Query and page strings come from searchers and other websites. The server tells the model
  to treat them as data, not instructions.

Report vulnerabilities privately; see [SECURITY.md](SECURITY.md).

## Development

```sh
git clone https://github.com/onlymoein/google-search-console-mcp
cd google-search-console-mcp
uv sync
uv run pytest                # unit and in-memory MCP tests, no network
uv run ruff check . && uv run ruff format --check . && uv run pyright
```

Live, read-only checks against your own property:

```sh
export GSC_SERVICE_ACCOUNT_FILE=/abs/path/key.json
GSC_TEST_SITE=sc-domain:example.com uv run pytest -m live
uv run python scripts/probe_compat.py sc-domain:example.com
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the project layout and how to add a tool.

## License

[MIT](LICENSE)
