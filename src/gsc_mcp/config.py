"""Settings read from environment variables.

Paths are expanded (`~`, `$VAR`) and must be absolute afterwards: MCP clients launch the
server from an unpredictable working directory, so a relative path would resolve somewhere
the user never intended.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from platformdirs import user_config_path, user_data_path

from gsc_mcp.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Mapping

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

ENV_OAUTH_CLIENT_FILE = "GSC_OAUTH_CLIENT_FILE"
ENV_SERVICE_ACCOUNT_FILE = "GSC_SERVICE_ACCOUNT_FILE"
ENV_TOKEN_FILE = "GSC_TOKEN_FILE"  # noqa: S105 - an env var name, not a secret
ENV_ENABLE_WRITES = "GSC_ENABLE_WRITES"
ENV_LOG_LEVEL = "GSC_LOG_LEVEL"
ENV_DATA_DIR = "GSC_DATA_DIR"

APP_NAME = "open-gsc-mcp"
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def default_token_file() -> Path:
    return user_config_path(APP_NAME, appauthor=False) / "token.json"


def default_data_dir() -> Path:
    return user_data_path(APP_NAME, appauthor=False)


@dataclass(frozen=True, slots=True)
class Settings:
    oauth_client_file: Path | None = None
    service_account_file: Path | None = None
    token_file: Path | None = None
    enable_writes: bool = True
    log_level: LogLevel = "INFO"
    data_dir: Path | None = None

    @property
    def resolved_token_file(self) -> Path:
        return self.token_file or default_token_file()

    @property
    def resolved_data_dir(self) -> Path:
        """Where indexing audits keep their progress."""
        return self.data_dir or default_data_dir()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        return cls(
            oauth_client_file=_path(env, ENV_OAUTH_CLIENT_FILE),
            service_account_file=_path(env, ENV_SERVICE_ACCOUNT_FILE),
            token_file=_path(env, ENV_TOKEN_FILE),
            enable_writes=_flag(env, ENV_ENABLE_WRITES, default=True),
            log_level=_log_level(env),
            data_dir=_path(env, ENV_DATA_DIR),
        )


def expand_path(raw: str, *, source: str) -> Path:
    """Expand `~` and environment variables; reject anything that is still relative."""
    expanded = Path(os.path.expandvars(os.path.expanduser(raw.strip())))
    if not expanded.is_absolute():
        raise ConfigError(
            f"{source} must be an absolute path, got {raw!r}. MCP clients start the server "
            "from an arbitrary working directory, so relative paths are not accepted."
        )
    return expanded


def _path(env: Mapping[str, str], name: str) -> Path | None:
    raw = env.get(name, "").strip()
    return expand_path(raw, source=name) if raw else None


def _flag(env: Mapping[str, str], name: str, *, default: bool) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ConfigError(f"{name} must be one of 1/0, true/false, yes/no, on/off; got {raw!r}.")


def _log_level(env: Mapping[str, str]) -> LogLevel:
    raw = env.get(ENV_LOG_LEVEL, "INFO").strip().upper() or "INFO"
    if raw not in _LOG_LEVELS:
        raise ConfigError(f"{ENV_LOG_LEVEL} must be one of {', '.join(_LOG_LEVELS)}; got {raw!r}.")
    return raw
