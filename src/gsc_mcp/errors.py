"""Errors whose message is written for the model (and the person behind it) to act on.

Every expected failure subclasses `ToolError`, so the MCP SDK hands the message to the
model as an `is_error` tool result. Anything else is a bug: the SDK logs the traceback
and the model only learns that the call failed.
"""

from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError


class GSCError(ToolError):
    """Base class for every error this server raises on purpose."""


class ConfigError(GSCError):
    """An environment variable or path is missing or malformed."""


class AuthError(GSCError):
    """Credentials are missing, expired, revoked or lack a required scope."""


class GuardError(GSCError):
    """The request would be rejected by Google, or silently return wrong data."""


class SiteNotFoundError(GuardError):
    """The site_url does not match a property this account can access."""


class ApiError(GSCError):
    """Google returned an error after retries were exhausted."""

    def __init__(self, message: str, *, status: int, reason: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason


class QuotaError(ApiError):
    """Google answered 429: a per-minute or per-day quota is used up."""
