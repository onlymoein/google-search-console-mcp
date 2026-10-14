"""Local MCP server for the Google Search Console API."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("open-gsc-mcp")
except PackageNotFoundError:  # pragma: no cover - running from a source tree without install
    __version__ = "0.0.0"
