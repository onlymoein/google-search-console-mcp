# google-auth ships without complete type hints.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
import requests
from google.auth.exceptions import RefreshError

from gsc_mcp import auth
from gsc_mcp.auth import FULL_SCOPE, READONLY_SCOPE
from gsc_mcp.config import Settings
from gsc_mcp.errors import AuthError


def token_json(scopes: list[str]) -> str:
    return json.dumps(
        {
            "token": "access",
            "refresh_token": "refresh",
            "client_id": "id.apps.googleusercontent.com",
            "client_secret": "secret",
            "token_uri": "https://oauth2.googleapis.com/token",
            "scopes": scopes,
        }
    )


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    return tmp_path / "token.json"


def test_scopes_are_least_privilege() -> None:
    assert auth.scopes_for(write=False) == [READONLY_SCOPE]
    assert auth.scopes_for(write=True) == [FULL_SCOPE]


def test_missing_login_says_how_to_sign_in(token_file: Path) -> None:
    with pytest.raises(AuthError, match="auth login --client-file"):
        auth.load_user_credentials(token_file, write=False)


@pytest.mark.parametrize(
    ("scopes", "write", "ok"),
    [
        ([READONLY_SCOPE], False, True),
        ([FULL_SCOPE], False, True),
        ([FULL_SCOPE], True, True),
        ([READONLY_SCOPE], True, False),
        (["https://www.googleapis.com/auth/userinfo.email"], False, False),
    ],
)
def test_scope_check(token_file: Path, scopes: list[str], write: bool, ok: bool) -> None:
    token_file.write_text(token_json(scopes))
    if ok:
        assert auth.load_user_credentials(token_file, write=write).refresh_token == "refresh"
    else:
        with pytest.raises(AuthError, match="Sign in again"):
            auth.load_user_credentials(token_file, write=write)


def test_read_only_login_with_writes_enabled_asks_for_write(token_file: Path) -> None:
    token_file.write_text(token_json([READONLY_SCOPE]))
    with pytest.raises(AuthError, match="--write"):
        auth.load_user_credentials(token_file, write=True)


def test_unreadable_login(token_file: Path) -> None:
    token_file.write_text("{not json")
    with pytest.raises(AuthError, match="unreadable"):
        auth.load_user_credentials(token_file, write=False)


def test_missing_service_account_file(tmp_path: Path) -> None:
    with pytest.raises(AuthError, match="does not exist"):
        auth.load_service_account(tmp_path / "missing.json", write=False)


def test_invalid_service_account_file(tmp_path: Path) -> None:
    key = tmp_path / "key.json"
    key.write_text(json.dumps({"type": "service_account"}))
    with pytest.raises(AuthError, match="not a valid service account key"):
        auth.load_service_account(key, write=False)


def test_invalid_grant_explains_testing_mode() -> None:
    error = auth.explain_refresh_error(
        RefreshError("invalid_grant: Token has been expired"), service_account=None
    )
    assert "7 days" in str(error)
    assert "In production" in str(error)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_saved_login_is_private(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "token.json"
    auth.write_private_file(target, "{}")
    assert target.read_text() == "{}"
    assert os.stat(target).st_mode & 0o777 == 0o600
    assert [p.name for p in target.parent.iterdir()] == ["token.json"]


def test_describe(tmp_path: Path, token_file: Path) -> None:
    token_file.write_text(token_json([FULL_SCOPE]))
    status = auth.describe(Settings(token_file=token_file))
    assert (status.mode, status.exists, status.scopes) == ("oauth", True, [FULL_SCOPE])

    key = tmp_path / "key.json"
    status = auth.describe(Settings(service_account_file=key, enable_writes=True))
    assert (status.mode, status.exists, status.scopes) == ("service_account", False, [FULL_SCOPE])


def revoke_answering(monkeypatch: pytest.MonkeyPatch, status: int | None) -> list[dict[str, Any]]:
    """Stub Google's revoke endpoint; status None means Google cannot be reached."""
    posted: list[dict[str, Any]] = []

    def fake_post(url: str, data: dict[str, Any], timeout: int) -> requests.Response:
        posted.append(data)
        if status is None:
            raise requests.ConnectionError("offline")
        response = requests.Response()
        response.status_code = status
        return response

    monkeypatch.setattr(auth.requests, "post", fake_post)
    return posted


def test_logout_revokes_and_deletes(monkeypatch: pytest.MonkeyPatch, token_file: Path) -> None:
    posted = revoke_answering(monkeypatch, 200)
    token_file.write_text(token_json([READONLY_SCOPE]))
    assert auth.logout(token_file) == "revoked"
    assert not token_file.exists()
    assert posted == [{"token": "refresh"}]
    assert auth.logout(token_file) is None


@pytest.mark.parametrize(
    ("saved", "status", "outcome"),
    [
        (token_json([READONLY_SCOPE]), 400, "rejected"),
        (token_json([READONLY_SCOPE]), None, "unreachable"),
        (json.dumps({"token": "access"}), 200, "no_token"),
        ("not json", 200, "no_token"),
    ],
)
def test_logout_reports_what_happened_and_always_deletes(
    monkeypatch: pytest.MonkeyPatch, token_file: Path, saved: str, status: int | None, outcome: str
) -> None:
    posted = revoke_answering(monkeypatch, status)
    token_file.write_text(saved)
    assert auth.logout(token_file) == outcome
    assert not token_file.exists()
    assert bool(posted) == (outcome != "no_token")


@pytest.mark.anyio
async def test_token_source_loads_lazily_and_reports_missing_login(token_file: Path) -> None:
    source = auth.GoogleTokenSource(Settings(token_file=token_file))
    with pytest.raises(AuthError, match="Not signed in"):
        await source.token()


@pytest.mark.anyio
async def test_token_source_rereads_the_login_after_a_failed_refresh(
    monkeypatch: pytest.MonkeyPatch, token_file: Path
) -> None:
    def revoked(self: object, request: object) -> None:
        raise RefreshError("invalid_grant: Token has been expired or revoked.")

    monkeypatch.setattr(auth.UserCredentials, "refresh", revoked)
    expired = json.loads(token_json([READONLY_SCOPE]))
    expired["expiry"] = "2000-01-01T00:00:00Z"
    token_file.write_text(json.dumps(expired))
    source = auth.GoogleTokenSource(Settings(token_file=token_file))
    with pytest.raises(AuthError, match="invalid_grant"):
        await source.token()

    fresh = json.loads(token_json([READONLY_SCOPE]))
    fresh["token"] = "new-access"
    fresh["expiry"] = "2099-01-01T00:00:00Z"
    token_file.write_text(json.dumps(fresh))  # what `auth login` does
    assert await source.token() == "new-access"
