"""Command line: run the server (default) or manage the Google login.

open-gsc-mcp                      run the MCP server over stdio
open-gsc-mcp auth login           sign in with the OAuth desktop flow
open-gsc-mcp auth status          show which credentials are used and test them
open-gsc-mcp auth logout          revoke and delete the saved login
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

import anyio

from gsc_mcp import __version__, auth
from gsc_mcp.auth import FULL_SCOPE, GoogleTokenSource
from gsc_mcp.client import SearchConsoleClient
from gsc_mcp.config import ENV_OAUTH_CLIENT_FILE, Settings, expand_path
from gsc_mcp.errors import GSCError

if TYPE_CHECKING:
    from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> None:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_env()
        if args.command in (None, "serve"):
            from gsc_mcp.server import build_server

            build_server(settings).run("stdio")
        elif args.auth_command == "login":
            sys.exit(_login(settings, args))
        elif args.auth_command == "status":
            sys.exit(_status(settings))
        elif args.auth_command == "logout":
            sys.exit(_logout(settings))
        else:
            parser.error("auth needs a subcommand: login, status or logout")
    except GSCError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="open-gsc-mcp",
        description="Local MCP server for the Google Search Console API.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("serve", help="run the MCP server over stdio (the default)")
    auth_parser = commands.add_parser("auth", help="manage the Google login")
    auth_commands = auth_parser.add_subparsers(dest="auth_command")

    login = auth_commands.add_parser("login", help="sign in with the OAuth desktop flow")
    login.add_argument(
        "--client-file",
        help=f"OAuth 'Desktop app' client JSON (absolute path; default: ${ENV_OAUTH_CLIENT_FILE})",
    )
    login.add_argument(
        "--write",
        action="store_true",
        help="also grant write access (needed for GSC_ENABLE_WRITES=1)",
    )
    login.add_argument(
        "--no-browser",
        action="store_true",
        help="print the sign-in URL instead of opening a browser",
    )
    auth_commands.add_parser("status", help="show which credentials are used and test them")
    auth_commands.add_parser("logout", help="revoke and delete the saved login")
    return parser


def _login(settings: Settings, args: argparse.Namespace) -> int:
    if settings.service_account_file is not None:
        print(
            "GSC_SERVICE_ACCOUNT_FILE is set, so the server uses the service account and "
            "ignores any OAuth login. Unset it to use OAuth.",
            file=sys.stderr,
        )
        return 1
    if args.client_file:
        client_file = expand_path(args.client_file, source="--client-file")
    elif settings.oauth_client_file is not None:
        client_file = settings.oauth_client_file
    else:
        print(
            f"error: pass --client-file or set {ENV_OAUTH_CLIENT_FILE} to the OAuth client JSON "
            "downloaded from Google Cloud (type: Desktop app).",
            file=sys.stderr,
        )
        return 2
    write = args.write or settings.enable_writes
    token_file = settings.resolved_token_file
    auth.login(client_file, token_file, write=write, open_browser=not args.no_browser)
    access = "read and write" if write else "read-only"
    print(f"Signed in ({access}). Saved to {token_file}")
    return _check_access(settings)


def _status(settings: Settings) -> int:
    status = auth.describe(settings)
    print(f"Mode:        {status.mode}")
    print(f"Credentials: {status.credential_path} ({'found' if status.exists else 'missing'})")
    if status.scopes:
        print(f"Access:      {'read and write' if FULL_SCOPE in status.scopes else 'read-only'}")
    print(f"Writes:      {'enabled' if status.writes_enabled else 'disabled'} (GSC_ENABLE_WRITES)")
    if not status.exists:
        if status.mode == "service_account":
            print("\nThe service account key file does not exist. Check GSC_SERVICE_ACCOUNT_FILE.")
        else:
            print(f"\nNot signed in. Run: {auth.LOGIN_HINT}")
        return 1
    return _check_access(settings)


def _logout(settings: Settings) -> int:
    token_file = settings.resolved_token_file
    outcome = auth.logout(token_file)
    if outcome is None:
        print(f"No saved login at {token_file}")
    elif outcome == "revoked":
        print(f"Signed out: revoked the login at Google and deleted {token_file}")
    elif outcome == "unreachable":
        print(
            f"Signed out: deleted {token_file}, but Google could not be reached to revoke "
            "the login. To revoke it, remove this app at https://myaccount.google.com/permissions"
        )
    else:
        reason = (
            "it held no refresh token"
            if outcome == "no_token"
            else "Google had already expired or revoked it"
        )
        print(f"Signed out: deleted {token_file}. Nothing to revoke: {reason}.")
    return 0


def _check_access(settings: Settings) -> int:
    async def run() -> int:
        client = SearchConsoleClient(GoogleTokenSource(settings))
        try:
            sites = await client.list_sites()
        finally:
            await client.aclose()
        print(f"\nConnected. {len(sites)} propert{'y' if len(sites) == 1 else 'ies'} available:")
        for site in sites:
            print(f"  {site.get('siteUrl')}  ({site.get('permissionLevel')})")
        return 0

    return anyio.run(run)
