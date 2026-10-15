# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through this repository's
[Security tab](https://github.com/onlymoein/google-search-console-mcp/security/advisories/new)
("Report a vulnerability"). Do not open a public issue.

Include what you found, how to reproduce it, and its impact. You can expect an
acknowledgement within a week. Fixes for confirmed issues are released as soon as they are
ready, and reporters are credited unless they prefer otherwise.

## Supported versions

Only the latest release receives security fixes.

## Scope

In scope: this server's code, for example leaking credentials or tokens, writing outside the
intended files, running write tools while they are disabled, or command execution.

Out of scope: Google's API and its behavior, vulnerabilities in dependencies that have no
impact on this project (report those upstream), and issues that require an attacker who
already controls your machine or your Google account.

## How credentials are handled

- OAuth logins are saved to your user config directory with owner-only permissions and are
  never logged. `open-gsc-mcp auth logout` revokes and deletes them.
- Service account keys are read from the path you configure and are never copied.
- Logins request the full Search Console scope, which the write tools need, unless you
  sign in with `--read-only` or set `GSC_ENABLE_WRITES=0`.
- The server connects to `searchconsole.googleapis.com`, Google's OAuth endpoints and
  Google's public update feed. Indexing audits also fetch the property's own sitemaps and
  pages, never hosts that resolve to private or local addresses.
