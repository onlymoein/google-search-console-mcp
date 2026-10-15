"""MCP tools, one module per area of the Search Console API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from gsc_mcp.tools import (
    analytics,
    appearances,
    audit,
    compare,
    explain,
    hourly,
    inspection,
    sitemaps,
    sites,
    surfaces,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from mcp.server import MCPServer

    from gsc_mcp.tools._common import Deps

MODULES = (
    sites,
    analytics,
    surfaces,
    hourly,
    appearances,
    compare,
    explain,
    inspection,
    audit,
    sitemaps,
)

# Renamed tools: the old name maps its arguments onto the current tool, so callers written
# for an earlier release keep working. Not listed in tools/list, so models only see the
# current names. Remove each in the next major release.
DEPRECATED_ALIASES: dict[str, Callable[[dict[str, Any]], tuple[str, dict[str, Any]]]] = {
    "query_news_analytics": surfaces.news_alias,  # 1.2.0
}


def register_all(mcp: MCPServer, deps: Deps) -> None:
    for module in MODULES:
        module.register(mcp, deps)
