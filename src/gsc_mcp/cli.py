"""Command line: run the server (default) or manage the Google login.

open-gsc-mcp                      run the MCP server over stdio
open-gsc-mcp auth login           sign in with the OAuth desktop flow
open-gsc-mcp auth status          show which credentials are used and test them
open-gsc-mcp auth logout          revoke and delete the saved login
open-gsc-mcp audit SITE           run an indexing audit, up to a full day's inspections
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

import anyio

from gsc_mcp import __version__, auth
from gsc_mcp.auth import GoogleTokenSource, can_write
from gsc_mcp.client import INSPECTIONS_PER_DAY, SearchConsoleClient
from gsc_mcp.config import ENV_OAUTH_CLIENT_FILE, Settings, expand_path
from gsc_mcp.errors import GSCError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from gsc_mcp.models import IndexingAudit


def main(argv: Sequence[str] | None = None) -> None:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_env()
        if args.command in (None, "serve"):
            from gsc_mcp.server import build_server

            build_server(settings).run("stdio")
        elif args.command == "audit":
            sys.exit(_audit(settings, args))
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
    access = login.add_mutually_exclusive_group()
    access.add_argument(
        "--read-only",
        action="store_true",
        help="grant read access only; the tools that make changes will then be refused",
    )
    # Full access became the default in 1.4.0; --write is still accepted.
    access.add_argument("--write", action="store_true", help=argparse.SUPPRESS)
    login.add_argument(
        "--no-browser",
        action="store_true",
        help="print the sign-in URL instead of opening a browser",
    )
    auth_commands.add_parser("status", help="show which credentials are used and test them")
    auth_commands.add_parser("logout", help="revoke and delete the saved login")

    audit = commands.add_parser(
        "audit",
        help="run an indexing audit, up to a full day's inspections",
        description="Inspect the property's sitemap URLs and save the results, continuing "
        "where the last run stopped. Your assistant reads them with audit_indexing.",
    )
    audit.add_argument("site_url", help="the property, e.g. sc-domain:example.com")
    audit.add_argument(
        "--inspect",
        type=_inspect_count,
        default=INSPECTIONS_PER_DAY,
        help=f"URLs to inspect (default {INSPECTIONS_PER_DAY:,}, Google's daily limit)",
    )
    audit.add_argument(
        "--fetch-pages",
        action="store_true",
        help="also fetch the inspected pages to compare them with what Google saw",
    )
    audit.add_argument("--sitemap", help="audit this sitemap instead of every submitted one")
    audit.add_argument("--restart", action="store_true", help="forget the saved audit first")
    return parser


def _inspect_count(raw: str) -> int:
    value = int(raw)
    if not 0 <= value <= INSPECTIONS_PER_DAY:
        raise argparse.ArgumentTypeError(f"must be between 0 and {INSPECTIONS_PER_DAY:,}")
    return value


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
    write = args.write or (settings.enable_writes and not args.read_only)
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
        print(f"Access:      {'read and write' if can_write(status.scopes) else 'read-only'}")
    writes = "on" if status.writes_enabled else "off"
    print(f"Write tools: {writes} (GSC_ENABLE_WRITES)")
    if status.writes_enabled and status.scopes and not can_write(status.scopes):
        print(
            "\nThis login can only read, so Google will refuse changes. To allow them, sign "
            f"in again: {auth.LOGIN_HINT}"
        )
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


def _audit(settings: Settings, args: argparse.Namespace) -> int:
    from gsc_mcp.server import close_deps, make_deps
    from gsc_mcp.tools._audit import Auditor

    async def run() -> int:
        deps = make_deps(settings)
        try:
            resolution = await deps.sites.resolve(args.site_url)
            prop = await deps.sites.property_for(resolution.site_url)
            auditor = Auditor(deps, prop)
            if args.restart:
                deps.store.reset(prop.site_url)
            print(f"Reading the sitemaps of {prop.site_url}...", file=sys.stderr)
            await auditor.sync_sitemaps(args.sitemap, force=args.restart)
            await auditor.sync_pages()

            async def progress(done: int, total: int, url: str) -> None:
                print(f"\rInspected {done:,} of {total:,}", end="", file=sys.stderr, flush=True)

            await auditor.inspect(args.inspect, progress)
            print(file=sys.stderr)
            if args.fetch_pages:
                print("Fetching the pages...", file=sys.stderr)
                await auditor.check_live(args.inspect or INSPECTIONS_PER_DAY)
            _print_audit(auditor.report(examples=3))
            print(f"\nSaved in {deps.store.path}")
            return 0
        finally:
            await close_deps(deps)

    return anyio.run(run)


def _print_audit(report: IndexingAudit) -> None:
    print(
        f"\n{report.site_url}: {report.urls_in_sitemaps:,} URLs in the sitemaps, "
        f"{report.inspected:,} inspected ({report.inspected_now:,} just now)."
    )
    if report.coverage:
        print("\nIndex status:")
        for group in report.coverage:
            print(f"  {group.count:>6,}  {group.coverage_state}")
    if report.issues:
        print("\nIssues:")
        for issue in report.issues:
            print(f"  {issue.count:>6,}  {issue.title}")
            for url in issue.examples:
                print(f"          {url}")
    for note in report.notes:
        print(f"\n{note}")


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
