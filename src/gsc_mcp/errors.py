"""Errors whose message is written for the model (and the person behind it) to act on.

Every expected failure subclasses `ToolError`, so the MCP SDK hands the message to the
model as an `is_error` tool result. Anything else is a bug: the SDK logs the traceback
and the model only learns that the call failed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.server.mcpserver.exceptions import ToolError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import ValidationError
    from pydantic_core import ErrorDetails


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


_TYPE_NAMES = {
    "int_type": "a whole number",
    "int_parsing": "a whole number",
    "int_from_float": "a whole number",
    "float_type": "a number",
    "float_parsing": "a number",
    "string_type": "text",
    "bool_type": "true or false",
    "bool_parsing": "true or false",
    "list_type": "a list",
    "dict_type": "an object",
    "model_type": "an object",
    "model_attributes_type": "an object",
}


def explain_arguments(error: ValidationError, properties: Mapping[str, Any]) -> str:
    """Rewrite pydantic's report on rejected tool arguments as plain sentences.

    `properties` is the tool's input schema, which knows both ends of a range where
    pydantic names only the end that was crossed.
    """
    return " ".join(_explain(e, properties) for e in error.errors(include_url=False))


def _explain(error: ErrorDetails, properties: Mapping[str, Any]) -> str:
    loc = error["loc"]
    name = _field_name(loc)
    kind = error["type"]
    ctx: dict[str, Any] = dict(error.get("ctx") or {})
    got = _show(error["input"])
    schema: Mapping[str, Any] = properties.get(str(loc[0]), {}) if len(loc) == 1 else {}
    if kind == "missing":
        return f"{name} is required."
    if kind in ("greater_than_equal", "less_than_equal", "greater_than", "less_than"):
        low = schema.get("minimum", ctx.get("ge"))
        high = schema.get("maximum", ctx.get("le"))
        return f"{name} must be {_span(low, high, ctx)}; got {got}."
    if kind in ("too_short", "too_long"):
        low = schema.get("minItems", ctx.get("min_length"))
        high = schema.get("maxItems", ctx.get("max_length"))
        return f"{name} must have {_span(low, high, ctx)} items; got {ctx.get('actual_length')}."
    if kind == "string_too_short" and ctx.get("min_length") == 1:
        return f"{name} must not be empty."
    if kind == "literal_error":
        return f"{name} must be {ctx['expected']}; got {got}."
    if kind in _TYPE_NAMES:
        return f"{name} must be {_TYPE_NAMES[kind]}; got {got}."
    message = error["msg"]
    if message.startswith("Input "):
        return f"{name} {message.removeprefix('Input ')}; got {got}."
    return f"{name}: {message}."


def _field_name(loc: tuple[int | str, ...]) -> str:
    name = ""
    for part in loc:
        if isinstance(part, int):
            name += f"[{part}]"
        else:
            name += f".{part}" if name else part
    return name or "arguments"


def _span(low: object, high: object, ctx: Mapping[str, Any]) -> str:
    if low is not None and high is not None:
        return f"between {_show(low)} and {_show(high)}"
    if low is not None:
        return f"at least {_show(low)}"
    if high is not None:
        return f"at most {_show(high)}"
    if "gt" in ctx:
        return f"more than {_show(ctx['gt'])}"
    return f"less than {_show(ctx.get('lt'))}"


def _show(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value:,}"
    text = repr(value)
    return text if len(text) <= 80 else text[:77] + "..."
