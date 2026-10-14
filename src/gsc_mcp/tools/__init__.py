"""MCP tools, one module per area of the Search Console API."""

from __future__ import annotations

from typing import TYPE_CHECKING

from gsc_mcp.tools import (
    analytics,
    appearances,
    compare,
    hourly,
    inspection,
    sitemaps,
    sites,
    surfaces,
)

if TYPE_CHECKING:
    from mcp.server import MCPServer

    from gsc_mcp.tools._common import Deps

MODULES = (sites, analytics, surfaces, hourly, appearances, compare, inspection, sitemaps)


def register_all(mcp: MCPServer, deps: Deps) -> None:
    for module in MODULES:
        module.register(mcp, deps)
