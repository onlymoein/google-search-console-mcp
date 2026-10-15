"""Credentials: OAuth desktop flow (primary) or a service account (headless).

The MCP server never opens a browser. Signing in is a separate, explicit step
(`open-gsc-mcp auth login`) run in a terminal; the server only loads and refreshes what
that step saved. A browser flow started from inside a stdio server races the client's
startup timeout and has nowhere sensible to print to.
"""

# google-auth and google-auth-oauthlib ship without complete type hints.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

import anyio
import anyio.to_thread
import requests
from google.auth.exceptions import GoogleAuthError, RefreshError
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials

from gsc_mcp.errors import AuthError, ConfigError

if TYPE_CHECKING:
    from google.auth.credentials import Credentials

    from gsc_mcp.config import Settings

logger = logging.getLogger(__name__)

READONLY_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
FULL_SCOPE = "https://www.googleapis.com/auth/webmasters"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

LOGIN_HINT = "open-gsc-mcp auth login --client-file /absolute/path/to/client_secret.json"


def scopes_for(*, write: bool) -> list[str]:
    """Least privilege: read-only unless write tools are enabled."""
    return [FULL_SCOPE] if write else [READONLY_SCOPE]


def has_required_scope(granted: list[str], *, write: bool) -> bool:
    if FULL_SCOPE in granted:
        return True
    return not write and READONLY_SCOPE in granted


class TokenSource(Protocol):
    """Supplies bearer tokens to the API client."""

    async def token(self) -> str: ...

    async def invalidate(self) -> None: ...


class GoogleTokenSource:
    """Loads credentials on first use and refreshes them when they expire."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._credentials: Credentials | None = None
        self._lock = anyio.Lock()

    async def token(self) -> str:
        async with self._lock:
            if self._credentials is None:
                self._credentials = await anyio.to_thread.run_sync(self._load)
            credentials = self._credentials
            if not credentials.valid:
                try:
                    await anyio.to_thread.run_sync(self._refresh, credentials)
                except AuthError:
                    # Reload from disk next time, so a fresh `auth login` takes effect
                    # without restarting the MCP client.
                    self._credentials = None
                    raise
            token: Any = credentials.token
            if not isinstance(token, str):  # pragma: no cover - defensive
                raise AuthError("Google returned credentials without an access token.")
            return token

    async def invalidate(self) -> None:
        """Force a refresh on the next call, e.g. after a 401."""
        async with self._lock:
            if self._credentials is not None:
                self._credentials.token = None

    def _load(self) -> Credentials:
        if self._settings.service_account_file is not None:
            return load_service_account(
                self._settings.service_account_file, write=self._settings.enable_writes
            )
        return load_user_credentials(
            self._settings.resolved_token_file, write=self._settings.enable_writes
        )

    def _refresh(self, credentials: Credentials) -> None:
        try:
            credentials.refresh(Request())
        except RefreshError as exc:
            raise explain_refresh_error(
                exc, service_account=self._settings.service_account_file
            ) from exc
        except (GoogleAuthError, requests.RequestException) as exc:
            raise AuthError(f"Could not refresh Google credentials: {exc}") from exc


def load_service_account(path: Path, *, write: bool) -> Credentials:
    if not path.is_file():
        raise AuthError(
            f"GSC_SERVICE_ACCOUNT_FILE points to {path}, which does not exist or is not a file."
        )
    try:
        return service_account.Credentials.from_service_account_file(
            str(path), scopes=scopes_for(write=write)
        )
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise AuthError(
            f"{path} is not a valid service account key file ({exc}). Download a JSON key "
            "from Google Cloud > IAM & Admin > Service Accounts > Keys."
        ) from exc


def load_user_credentials(path: Path, *, write: bool) -> UserCredentials:
    if not path.is_file():
        raise AuthError(
            "Not signed in to Google Search Console. In a terminal, run:\n"
            f"  {LOGIN_HINT}\n"
            "or set GSC_SERVICE_ACCOUNT_FILE to use a service account."
        )
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
        credentials = UserCredentials.from_authorized_user_info(info)
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise AuthError(
            f"The saved login at {path} is unreadable ({exc}). Sign in again:\n  {LOGIN_HINT}"
        ) from exc
    granted = list(credentials.scopes or [])
    if not has_required_scope(granted, write=write):
        if write:
            raise AuthError(
                "GSC_ENABLE_WRITES is on, but the saved login only grants read-only access. "
                f"Sign in again with write access:\n  {LOGIN_HINT} --write"
            )
        raise AuthError(
            f"The saved login has no Search Console scope. Sign in again:\n  {LOGIN_HINT}"
        )
    return credentials


def explain_refresh_error(exc: RefreshError, *, service_account: Path | None) -> AuthError:
    """Turn a refresh failure into a message that says what to do about it."""
    if service_account is not None:
        return AuthError(
            f"Google rejected the service account key ({exc}). The key may have been deleted "
            "or disabled in Google Cloud."
        )
    if "invalid_grant" in str(exc):
        return AuthError(
            "Your Google login has expired or was revoked (invalid_grant). If your OAuth "
            "consent screen is in 'Testing' status, Google expires logins after 7 days: set "
            "it to 'In production' in Google Cloud > Google Auth Platform > Audience. "
            f"Then sign in again:\n  {LOGIN_HINT}"
        )
    return AuthError(f"Could not refresh your Google login ({exc}). Sign in again:\n  {LOGIN_HINT}")


# --- CLI-side operations --------------------------------------------------------------


def login(
    client_file: Path, token_file: Path, *, write: bool, open_browser: bool = True
) -> UserCredentials:
    """Run the OAuth desktop flow in the terminal and save the refresh token."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not client_file.is_file():
        raise ConfigError(f"OAuth client file {client_file} does not exist or is not a file.")
    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_file), scopes=scopes_for(write=write)
        )
    except ValueError as exc:
        raise ConfigError(
            f"{client_file} is not an OAuth client file ({exc}). In Google Cloud, create an "
            "OAuth client ID of type 'Desktop app' and download its JSON."
        ) from exc
    try:
        credentials = flow.run_local_server(
            host="localhost",
            port=0,
            open_browser=open_browser,
            authorization_prompt_message="Open this URL in a browser to sign in:\n{url}",
            success_message="Signed in. You can close this tab and return to the terminal.",
            timeout_seconds=300,
            prompt="consent",
        )
    except Exception as exc:  # oauthlib and wsgiref raise a variety of types here
        raise AuthError(
            f"Sign-in did not complete ({type(exc).__name__}: {exc}). Consent may have been "
            "denied or the 5-minute window may have passed; run the command again."
        ) from exc
    if not isinstance(credentials, UserCredentials):
        raise AuthError("The OAuth flow returned an unexpected credential type.")
    if not credentials.refresh_token:
        raise AuthError(
            "Google did not return a refresh token. Remove this app's access at "
            "https://myaccount.google.com/permissions and sign in again."
        )
    write_private_file(token_file, credentials.to_json())
    return credentials


RevokeOutcome = Literal["revoked", "no_token", "rejected", "unreachable"]


def logout(token_file: Path) -> RevokeOutcome | None:
    """Revoke the saved login at Google and delete the file; None if there was no file.

    The file is deleted whatever Google answers: the user asked for it to be gone, and a
    login Google will not revoke has usually expired or been revoked already.
    """
    if not token_file.is_file():
        return None
    outcome = _revoke(token_file)
    token_file.unlink()
    return outcome


def _revoke(token_file: Path) -> RevokeOutcome:
    try:
        saved = json.loads(token_file.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return "no_token"
    refresh_token = saved.get("refresh_token") if isinstance(saved, dict) else None
    if not refresh_token:
        return "no_token"
    try:
        response = requests.post(REVOKE_URL, data={"token": refresh_token}, timeout=10)
    except requests.RequestException:
        return "unreachable"
    # Google answers 400 invalid_token for a login that already expired or was revoked.
    return "revoked" if response.ok else "rejected"


@dataclass(slots=True)
class AuthStatus:
    mode: str
    credential_path: Path
    exists: bool
    scopes: list[str] = field(default_factory=list[str])
    writes_enabled: bool = False


def describe(settings: Settings) -> AuthStatus:
    """What the server would use, without touching the network."""
    if settings.service_account_file is not None:
        path = settings.service_account_file
        return AuthStatus(
            mode="service_account",
            credential_path=path,
            exists=path.is_file(),
            scopes=scopes_for(write=settings.enable_writes),
            writes_enabled=settings.enable_writes,
        )
    path = settings.resolved_token_file
    scopes: list[str] = []
    if path.is_file():
        with contextlib.suppress(ValueError, OSError):
            scopes = list(json.loads(path.read_text(encoding="utf-8")).get("scopes") or [])
    return AuthStatus(
        mode="oauth",
        credential_path=path,
        exists=path.is_file(),
        scopes=scopes,
        writes_enabled=settings.enable_writes,
    )


def write_private_file(path: Path, text: str) -> None:
    """Atomically write `text` to `path`, readable only by the current user."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
