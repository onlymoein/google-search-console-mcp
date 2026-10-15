"""Shared fixtures: a scripted Search Console API behind httpx2.MockTransport."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import httpx2
import pytest
from mcp import Client
from mcp.server import MCPServer

from gsc_mcp.client import SearchConsoleClient
from gsc_mcp.config import Settings
from gsc_mcp.server import build_server
from gsc_mcp.web import WebClient

TODAY = date(2026, 10, 14)
DOMAIN_PATH = "webmasters/v3/sites/sc-domain%3Aexample.com"
QUERY = f"{DOMAIN_PATH}/searchAnalytics/query"
INSPECT = "v1/urlInspection/index:inspect"
SITES = [
    {"siteUrl": "sc-domain:example.com", "permissionLevel": "siteOwner"},
    {"siteUrl": "https://www.example.com/", "permissionLevel": "siteFullUser"},
]

Handler = Callable[[httpx2.Request], httpx2.Response]
Reply = httpx2.Response | Handler | dict[str, Any]


class FakeTokens:
    def __init__(self) -> None:
        self.invalidated = 0

    async def token(self) -> str:
        return "test-token"

    async def invalidate(self) -> None:
        self.invalidated += 1


@dataclass
class ApiStub:
    """Answers requests from a queue of replies per route; records every request."""

    routes: dict[str, list[Reply]] = field(default_factory=dict[str, list[Reply]])
    requests: list[httpx2.Request] = field(default_factory=list[httpx2.Request])

    def on(self, method: str, path: str, *replies: Reply) -> None:
        """Queue replies for `METHOD path`; the last reply repeats once the queue drains."""
        self.routes[f"{method} {path}"] = list(replies)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        key = f"{request.method} {request.url.raw_path.decode().split('?')[0].lstrip('/')}"
        queue = self.routes.get(key)
        if not queue:
            return httpx2.Response(404, json={"error": {"code": 404, "message": f"no stub: {key}"}})
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, httpx2.Response):
            return reply
        if isinstance(reply, dict):
            return httpx2.Response(200, json=reply)
        return reply(request)

    def bodies(self, path_suffix: str) -> list[dict[str, Any]]:
        return [
            json.loads(r.content)
            for r in self.requests
            if r.url.raw_path.decode().endswith(path_suffix) and r.content
        ]


@dataclass
class WebStub:
    """Answers site and feed requests by full URL; unknown URLs get a 404. Hosts resolve to
    a public address unless listed in `private`."""

    routes: dict[str, Reply] = field(default_factory=dict[str, Reply])
    requests: list[httpx2.Request] = field(default_factory=list[httpx2.Request])
    private: set[str] = field(default_factory=set[str])

    def on(self, url: str, reply: Reply) -> None:
        self.routes[url] = reply

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        reply = self.routes.get(str(request.url))
        if reply is None:
            return httpx2.Response(404, text="not found")
        if isinstance(reply, httpx2.Response):
            return reply
        if isinstance(reply, dict):
            return httpx2.Response(200, json=reply)
        return reply(request)

    async def resolve(self, host: str, port: int) -> list[str]:
        return ["10.0.0.7"] if host in self.private else ["93.184.216.34"]

    def fetched(self) -> list[str]:
        return [str(r.url) for r in self.requests]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def api() -> ApiStub:
    stub = ApiStub()
    stub.on("GET", "webmasters/v3/sites", {"siteEntry": SITES})
    return stub


@pytest.fixture
def tokens() -> FakeTokens:
    return FakeTokens()


@pytest.fixture
def gsc(api: ApiStub, tokens: FakeTokens) -> SearchConsoleClient:
    return SearchConsoleClient(
        tokens, transport=httpx2.MockTransport(api.handle), backoff_base=0, backoff_cap=0
    )


@pytest.fixture
def web() -> WebStub:
    return WebStub()


@pytest.fixture
def make_server(gsc: SearchConsoleClient, web: WebStub, tmp_path: Path) -> Callable[..., MCPServer]:
    def make(*, enable_writes: bool = False) -> MCPServer:
        return build_server(
            Settings(enable_writes=enable_writes, data_dir=tmp_path / "data"),
            client=gsc,
            web=WebClient(transport=httpx2.MockTransport(web.handle), resolve=web.resolve),
            today=lambda: TODAY,
        )

    return make


@pytest.fixture
async def client(make_server: Callable[..., MCPServer]) -> AsyncIterator[Client]:
    async with Client(make_server(), raise_exceptions=True) as connected:
        yield connected


@pytest.fixture
async def write_client(make_server: Callable[..., MCPServer]) -> AsyncIterator[Client]:
    async with Client(make_server(enable_writes=True), raise_exceptions=True) as connected:
        yield connected


async def call(client: Client, name: str, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool(name, arguments)
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content


async def call_error(client: Client, name: str, **arguments: Any) -> str:
    result = await client.call_tool(name, arguments)
    assert result.is_error
    text = result.content[0]
    assert text.type == "text"
    return text.text
