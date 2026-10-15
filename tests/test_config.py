from __future__ import annotations

from pathlib import Path

import pytest

from gsc_mcp.config import Settings, default_data_dir, default_token_file
from gsc_mcp.errors import ConfigError


def test_defaults() -> None:
    settings = Settings.from_env({})
    assert settings == Settings()
    assert settings.resolved_token_file == default_token_file()
    assert settings.resolved_data_dir == default_data_dir()


def test_reads_every_variable(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {
            "GSC_OAUTH_CLIENT_FILE": str(tmp_path / "client.json"),
            "GSC_SERVICE_ACCOUNT_FILE": str(tmp_path / "sa.json"),
            "GSC_TOKEN_FILE": str(tmp_path / "token.json"),
            "GSC_ENABLE_WRITES": "true",
            "GSC_LOG_LEVEL": "debug",
            "GSC_DATA_DIR": str(tmp_path / "data"),
        }
    )
    assert settings.oauth_client_file == tmp_path / "client.json"
    assert settings.service_account_file == tmp_path / "sa.json"
    assert settings.resolved_token_file == tmp_path / "token.json"
    assert settings.enable_writes is True
    assert settings.log_level == "DEBUG"
    assert settings.resolved_data_dir == tmp_path / "data"


def test_expands_home_and_variables(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # what expanduser reads on Windows
    monkeypatch.setenv("KEYS", str(tmp_path / "keys"))
    settings = Settings.from_env(
        {"GSC_TOKEN_FILE": "~/token.json", "GSC_SERVICE_ACCOUNT_FILE": "$KEYS/sa.json"}
    )
    assert settings.token_file == tmp_path / "token.json"
    assert settings.service_account_file == tmp_path / "keys" / "sa.json"


def test_rejects_relative_paths() -> None:
    with pytest.raises(ConfigError, match="GSC_SERVICE_ACCOUNT_FILE must be an absolute path"):
        Settings.from_env({"GSC_SERVICE_ACCOUNT_FILE": "keys/sa.json"})


@pytest.mark.parametrize(
    ("raw", "expected"), [("1", True), ("ON", True), ("0", False), ("", False)]
)
def test_flags(raw: str, expected: bool) -> None:
    assert Settings.from_env({"GSC_ENABLE_WRITES": raw}).enable_writes is expected


def test_rejects_unclear_flag() -> None:
    with pytest.raises(ConfigError, match="GSC_ENABLE_WRITES"):
        Settings.from_env({"GSC_ENABLE_WRITES": "maybe"})


def test_rejects_unknown_log_level() -> None:
    with pytest.raises(ConfigError, match="GSC_LOG_LEVEL"):
        Settings.from_env({"GSC_LOG_LEVEL": "LOUD"})
