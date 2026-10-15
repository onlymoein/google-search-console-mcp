from __future__ import annotations

from pathlib import Path

import httpx2
import pytest

from gsc_mcp import __version__, auth, server
from gsc_mcp.cli import main
from gsc_mcp.client import SearchConsoleClient
from gsc_mcp.config import Settings
from gsc_mcp.tools._common import Deps
from gsc_mcp.web import WebClient
from tests.conftest import DOMAIN_PATH, INSPECT, QUERY, TODAY, ApiStub, WebStub


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    for name in (
        "GSC_OAUTH_CLIENT_FILE",
        "GSC_SERVICE_ACCOUNT_FILE",
        "GSC_ENABLE_WRITES",
        "GSC_LOG_LEVEL",
    ):
        monkeypatch.delenv(name, raising=False)
    token = tmp_path / "token.json"
    monkeypatch.setenv("GSC_TOKEN_FILE", str(token))
    return token


def run(*argv: str) -> int:
    with pytest.raises(SystemExit) as exited:
        main(list(argv))
    code = exited.value.code
    return code if isinstance(code, int) else 1


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert run("--version") == 0
    assert __version__ in capsys.readouterr().out


def test_status_without_login(capsys: pytest.CaptureFixture[str], clean_env: Path) -> None:
    assert run("auth", "status") == 1
    out = capsys.readouterr().out
    assert "Mode:        oauth" in out
    assert f"{clean_env} (missing)" in out
    assert "auth login" in out


def test_login_needs_a_client_file(capsys: pytest.CaptureFixture[str]) -> None:
    assert run("auth", "login") == 2
    assert "--client-file" in capsys.readouterr().err


def test_login_rejects_a_missing_client_file(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert run("auth", "login", "--client-file", str(tmp_path / "nope.json")) == 1
    assert "does not exist" in capsys.readouterr().err


def test_login_is_pointless_with_a_service_account(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("GSC_SERVICE_ACCOUNT_FILE", str(tmp_path / "key.json"))
    assert run("auth", "login") == 1
    assert "service account" in capsys.readouterr().err


def test_logout_without_login(capsys: pytest.CaptureFixture[str]) -> None:
    assert run("auth", "logout") == 0
    assert "No saved login" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("revoked", "revoked the login at Google"),
        ("no_token", "Nothing to revoke: it held no refresh token"),
        ("rejected", "Nothing to revoke: Google had already expired or revoked it"),
        ("unreachable", "Google could not be reached"),
    ],
)
def test_logout_says_whether_it_revoked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], outcome: str, expected: str
) -> None:
    def logout(token_file: Path) -> str:
        return outcome

    monkeypatch.setattr(auth, "logout", logout)
    assert run("auth", "logout") == 0
    out = capsys.readouterr().out
    assert expected in out
    assert ("revoked the login" in out) == (outcome == "revoked")


def test_bad_configuration_is_reported_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GSC_SERVICE_ACCOUNT_FILE", "relative/key.json")
    assert run("auth", "status") == 1
    assert capsys.readouterr().err.startswith("error: GSC_SERVICE_ACCOUNT_FILE must be an absolute")


def test_auth_without_a_subcommand_is_an_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert run("auth") == 2
    assert "login, status or logout" in capsys.readouterr().err


def test_status_with_a_missing_service_account_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("GSC_SERVICE_ACCOUNT_FILE", str(tmp_path / "missing.json"))
    assert run("auth", "status") == 1
    out = capsys.readouterr().out
    assert "GSC_SERVICE_ACCOUNT_FILE" in out
    assert "auth login" not in out


def test_audit_runs_a_full_day_from_the_terminal(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    api: ApiStub,
    web: WebStub,
    gsc: SearchConsoleClient,
) -> None:
    sitemap = "https://example.com/sitemap.xml"
    api.on("GET", f"{DOMAIN_PATH}/sitemaps", {"sitemap": [{"path": sitemap}]})
    api.on("POST", QUERY, {"rows": []})
    api.on(
        "POST",
        INSPECT,
        {
            "inspectionResult": {
                "indexStatusResult": {"verdict": "NEUTRAL", "coverageState": "Crawled"}
            }
        },
    )
    web.on(sitemap, httpx2.Response(200, text="https://example.com/a\nhttps://example.com/b"))
    monkeypatch.setenv("GSC_DATA_DIR", str(tmp_path / "data"))
    real = server.make_deps

    def make_deps(settings: Settings) -> Deps:
        fetcher = WebClient(transport=httpx2.MockTransport(web.handle), resolve=web.resolve)
        return real(settings, client=gsc, web=fetcher, today=lambda: TODAY)

    monkeypatch.setattr(server, "make_deps", make_deps)
    assert run("audit", "sc-domain:example.com") == 0
    out = capsys.readouterr().out
    assert "2 URLs in the sitemaps, 2 inspected (2 just now)" in out
    assert "Not indexed: Crawled" in out
    assert f"Saved in {tmp_path / 'data' / 'audits.sqlite3'}" in out
