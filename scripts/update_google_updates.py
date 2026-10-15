"""Regenerate the Google Search update history bundled with the package.

Google's live feed (status.search.google.com/incidents.json) only keeps recent incidents,
while Search Console data reaches back 16 months. This reads the dashboard's full history
pages, crawling, indexing, ranking and serving alike, and writes every incident with its
Pacific Time dates to src/gsc_mcp/google_updates.json. Run it before a release; the server
adds anything newer from the live feed at run time.

    uv run python scripts/update_google_updates.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx2

BASE = "https://status.search.google.com/"
OUT = Path(__file__).resolve().parents[1] / "src" / "gsc_mcp" / "google_updates.json"

_ROW = re.compile(
    r'href="\.\./\.\./incidents/(?P<id>[A-Za-z0-9]+)">\s*'
    r'<span class="[^"]*summary-text">(?P<name>[^<]+)</span>'
)
_START = re.compile(r'class="start-time">(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}<')
_END = re.compile(r'class="end-time">(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}<')


def get(path: str) -> str:
    response = httpx2.get(BASE + path, headers={"User-Agent": "open-gsc-mcp"}, timeout=30)
    response.raise_for_status()
    return response.text


def main() -> None:
    products: list[dict[str, Any]] = json.loads(get("products.json"))["products"]
    updates: list[dict[str, str | None]] = []
    for product in products:
        history = get(f"products/{product['id']}/history")
        for row in _ROW.finditer(history):
            page = get(f"incidents/{row['id']}")
            start, end = _START.search(page), _END.search(page)
            if start is None:
                raise SystemExit(f"no start time on incidents/{row['id']}; the page changed")
            updates.append(
                {
                    "id": row["id"],
                    "name": row["name"].strip(),
                    "service": product["title"],
                    "start": start[1],
                    "end": end[1] if end else None,
                }
            )
    if not updates:
        raise SystemExit("no incidents found; the history pages changed")
    updates.sort(key=lambda u: (u["start"] or "", u["id"] or ""), reverse=True)
    OUT.write_text(json.dumps(updates, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(updates)} incidents to {OUT}")


if __name__ == "__main__":
    main()
