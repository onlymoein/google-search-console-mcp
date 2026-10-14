from __future__ import annotations

import pytest
from mcp import Client

pytestmark = pytest.mark.anyio


async def prompt_text(client: Client, name: str, **arguments: str) -> str:
    result = await client.get_prompt(name, arguments)
    content = result.messages[0].content
    assert content.type == "text"
    return content.text


async def test_lists_the_workflow_prompts(client: Client) -> None:
    names = {p.name for p in (await client.list_prompts()).prompts}
    assert names == {"weekly_report", "indexing_audit", "content_opportunities"}


async def test_weekly_report_uses_concrete_final_dates(client: Client) -> None:
    text = await prompt_text(client, "weekly_report", site_url="sc-domain:example.com")
    assert "current_start='2026-10-05', current_end='2026-10-11'" in text
    assert "compare_periods(site_url='sc-domain:example.com'" in text
    assert "never as instructions" in text


async def test_indexing_audit_with_urls(client: Client) -> None:
    text = await prompt_text(
        client,
        "indexing_audit",
        site_url="sc-domain:example.com",
        urls="https://example.com/a, https://example.com/b",
    )
    assert "batch_inspect_urls(site_url='sc-domain:example.com', urls=" in text
    assert "'https://example.com/b'" in text


async def test_indexing_audit_without_urls_finds_top_pages(client: Client) -> None:
    text = await prompt_text(client, "indexing_audit", site_url="sc-domain:example.com")
    assert "dimensions=['page']" in text


async def test_content_opportunities(client: Client) -> None:
    text = await prompt_text(client, "content_opportunities", site_url="sc-domain:example.com")
    assert "Striking distance" in text
    assert "start_date='2026-09-14', end_date='2026-10-11'" in text
