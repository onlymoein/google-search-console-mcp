## What and why

<!-- What does this change, and what problem does it solve? Link the issue. -->

## Checklist

- [ ] Tests cover the change, and `uv run pytest` passes
- [ ] `uv run ruff check . && uv run ruff format --check . && uv run pyright` pass
- [ ] `CHANGELOG.md` has an entry under Unreleased (for user-visible changes)
- [ ] For guard changes: `scripts/probe_compat.py` output against a live property is included
