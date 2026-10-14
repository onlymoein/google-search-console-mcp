# Contributing

Thanks for helping. Bug reports, fixes and new tools are all welcome. For anything larger
than a small fix, please open an issue first so we can agree on the approach.

## Setup

```sh
uv sync
uv run pytest
```

Before opening a pull request, run everything CI runs:

```sh
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest
```

## Layout

```
src/gsc_mcp/
  cli.py        command line: serve, auth login/status/logout
  server.py     builds the MCPServer and registers tools and prompts
  config.py     settings from environment variables
  auth.py       OAuth desktop flow and service accounts
  client.py     async Search Console API client (httpx2): retries, error mapping
  guards.py     request validation, one rule per known API trap
  sites.py      resolves what the model calls a site to an exact property
  models.py     tool input and output types (the output schemas)
  prompts.py    workflow prompts
  tools/        one module per area; each has register(mcp, deps)
scripts/
  probe_compat.py   checks the guard rules against the live API
  smoke_stdio.py    runs the built server over stdio (used in CI)
tests/              unit tests, in-memory MCP tests, opt-in live tests
```

## Adding a tool

1. Put it in the matching module under `src/gsc_mcp/tools/`, inside `register()`.
2. Type every parameter with `Annotated[..., Field(description=...)]`; the description is
   what the model reads.
3. Return a Pydantic model from `models.py`. It becomes the tool's output schema.
4. Raise `GuardError` (or another `GSCError`) for anything the model can fix. Its message
   reaches the model, so say what was wrong and what to do instead.
5. Set annotations: `READ_ONLY` for reads. Writes use `WRITE` or `DESTRUCTIVE` and are only
   registered when `deps.settings.enable_writes` is true.
6. Test it through the in-memory client in `tests/test_tools.py`, with the API stubbed.

## Guard rules

A guard exists only when Google rejects a request or silently returns wrong or empty data.
When you add or change one, add a case to `scripts/probe_compat.py`, run it against a
property with traffic, and include the output in your pull request.

## Commits and pull requests

- One logical change per pull request, with tests.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/):
  `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `ci:`, `chore:`.
- Add a line under **Unreleased** in `CHANGELOG.md` for anything users would notice.
- Tool names, parameters and output fields are public API: changing them is a breaking
  change.

## Releasing (maintainers)

1. Update the version in `pyproject.toml` and `server.json` (both places in it), and move
   the **Unreleased** changelog entries under the new version.
2. Commit, then tag `vX.Y.Z` and push the tag. The release workflow checks that the
   versions match the tag, tests, builds, publishes to PyPI with trusted publishing and
   attestations, publishes to the MCP Registry and creates the GitHub release.

One-time setup: on PyPI, add a trusted publisher for project `open-gsc-mcp`, owner
`onlymoein`, repository `google-search-console-mcp`, workflow `release.yml` and environment
`pypi`; then create the `pypi` environment in the GitHub repository settings.
