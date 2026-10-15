"""Launch the server as a real stdio subprocess and check it answers.

Used in CI against the built wheel, so packaging mistakes (a missing module, a broken entry
point, stray stdout output) fail the build. Needs no Google credentials: listing tools
never calls the API.

    uv build
    uv run python scripts/smoke_stdio.py uvx --from dist/<wheel>.whl open-gsc-mcp
"""

from __future__ import annotations

import sys

import anyio
from mcp import Client, StdioServerParameters

EXPECTED = {
    "list_sites",
    "get_site",
    "query_search_analytics",
    "query_hourly_analytics",
    "list_search_appearances",
    "query_search_appearance",
    "query_discover_analytics",
    "query_image_analytics",
    "query_video_analytics",
    "query_google_news_analytics",
    "query_news_tab_analytics",
    "compare_periods",
    "explain_change",
    "inspect_url",
    "audit_indexing",
    "batch_inspect_urls",
    "list_sitemaps",
    "get_sitemap",
    # Write tools, on by default.
    "add_site",
    "delete_site",
    "submit_sitemap",
    "delete_sitemap",
}


async def main(command: list[str]) -> None:
    params = StdioServerParameters(command=command[0], args=command[1:])
    with anyio.fail_after(60):
        async with Client(params) as client:
            tools = {t.name for t in (await client.list_tools()).tools}
            prompts = {p.name for p in (await client.list_prompts()).prompts}
    if tools != EXPECTED:
        raise SystemExit(f"unexpected tools: {sorted(tools ^ EXPECTED)}")
    print(f"ok: {len(tools)} tools, {len(prompts)} prompts over stdio")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: smoke_stdio.py <command> [args...]")
    anyio.run(main, sys.argv[1:])
