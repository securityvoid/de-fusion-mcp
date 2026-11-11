import asyncio
import socket
import sys
import threading
import time
import types as pytypes

import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

from defusion import profiles, server
from defusion.actions import action
from defusion.config import Config


@pytest.fixture
def acts(registry, fake_design, dispatcher):
    def add(tool, name, access):
        def f(ctx) -> dict:
            """x."""
            return {"ran": "%s.%s" % (tool, name)}
        f.__name__ = name
        action(tool, access=access)(f)

    for t, n, a in [("model", "list", "read"), ("model", "extrude", "write"), ("model", "purge", "destructive"),
                    ("cam", "setup", "write"), ("script", "run", "write"),
                    ("server", "status", "read"), ("server", "job_status", "read")]:
        add(t, n, a)
    return registry


class Harness:
    def __init__(self, cfg, registry, error=None):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cfg.port = s.getsockname()[1]
        self.port = cfg.port
        app = server.build_app(cfg, registry, error=error)
        self.uv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.uv.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        end = time.time() + 5
        while not self.uv.started and time.time() < end:
            time.sleep(0.02)
        return self

    def __exit__(self, *a):
        self.uv.should_exit = True
        self.thread.join(5)

    def run(self, coro_fn, query=""):
        async def main():
            async with streamable_http_client("http://127.0.0.1:%d/mcp%s" % (self.port, query)) as (r, w, *_):
                async with ClientSession(r, w) as cs:
                    await cs.initialize()
                    return await coro_fn(cs)
        try:
            return asyncio.run(main())
        except BaseExceptionGroup as group:  # the client wraps the JSON-RPC error raised in its task group
            leaf = group
            while isinstance(leaf, BaseExceptionGroup):
                leaf = leaf.exceptions[0]
            raise leaf from None


@pytest.fixture
def harness(acts):
    made = []

    def make(cfg=None, error=None):
        h = Harness(cfg or Config(), acts, error)
        made.append(h)
        return h.__enter__()

    yield make
    for h in made:
        h.__exit__()


def listing(h, query=""):
    async def f(cs):
        return {t.name: t for t in (await cs.list_tools()).tools}
    return h.run(f, query)


def names(tool):
    return tool.input_schema["properties"]["action"]["enum"]


def call(h, tool, action_, query="", params=None):
    async def f(cs):
        r = await cs.call_tool(tool, {"action": action_, "params": params or {}})
        return r.structured_content
    return h.run(f, query)


def test_default_profile_hides_script(harness):
    tools = listing(harness())
    assert set(tools) == {"model", "cam", "server"}  # default ["all", "-script"]
    assert names(tools["model"]) == ["help", "extrude", "list", "purge"]
    assert call(harness(), "script", "run")["error"]["code"] == "NOT_ALLOWED"


def test_readonly_profile_filters_list_and_annotations(harness):
    h = harness()
    tools = listing(h, "?profile=readonly")
    assert set(tools) == {"model", "server"}
    assert names(tools["model"]) == ["help", "list"]
    a = tools["model"].annotations
    assert (a.read_only_hint, a.destructive_hint, a.idempotent_hint, a.open_world_hint) == (True, False, False, False)
    assert "Actions: list." in tools["model"].description
    assert call(h, "model", "extrude", "?profile=readonly")["error"]["code"] == "NOT_ALLOWED"
    r = call(h, "model", "list", "?profile=readonly")
    assert r["result"] == {"ran": "model.list"}, r


def test_annotations_follow_allowed_actions(harness):
    h = harness()
    full = listing(h)["model"].annotations
    assert (full.read_only_hint, full.destructive_hint) == (False, True)
    w = listing(h, "?profile=model.extrude")["model"].annotations
    assert (w.read_only_hint, w.destructive_hint) == (False, False)


def test_status_tools_always_listed(harness):
    h = harness()
    tools = listing(h, "?profile=cam")
    assert set(tools) == {"cam", "server"} and names(tools["server"]) == ["help", "job_status", "status"]
    assert call(h, "server", "status", "?profile=cam")["ok"] is True


def test_profile_composition_from_query(harness):
    h = harness()
    assert names(listing(h, "?profile=all,-write,-destructive")["model"]) == ["help", "list"]
    assert set(listing(h, "?profile=model.extrude,-model.extrude,cam")) == {"cam", "server"}
    assert names(listing(h, "?profile=readonly%2Ccam")["cam"]) == ["help", "setup"]


def test_user_profile_from_config(harness):
    h = harness(Config(profiles={"mine": ["model.list"]}))
    assert names(listing(h, "?profile=mine")["model"]) == ["help", "list"]


def test_default_profile_from_config(harness):
    h = harness(Config(default_profile=["readonly"]))
    assert set(listing(h)) == {"model", "server"}


@pytest.mark.parametrize("query,item", [
    ("?profile=nosuch", "nosuch"), ("?profile=", ""), ("?profile=a&profile=b", "profile=a&profile=b"),
    ("?profile=model.nosuch*", "model.nosuch*"), ("?profile=all,,cam", "all,,cam"), ("?profile=-readonly", "-readonly"),
])
def test_invalid_profile_fails_every_request(harness, query, item):
    h = harness()
    for fn in (lambda cs: cs.list_tools(), lambda cs: cs.call_tool("model", {"action": "list"})):
        with pytest.raises(MCPError) as e:
            h.run(fn, query)
        assert e.value.code == -32602
        assert e.value.message.startswith("invalid profile item '%s': " % item)
        assert e.value.message.endswith("; valid selectors are in design § 6.3")


def test_invalid_profile_fails_initialize_too(harness):
    h = harness()

    async def main():
        async with streamable_http_client("http://127.0.0.1:%d/mcp?profile=nosuch" % h.port) as (r, w, *_):
            async with ClientSession(r, w) as cs:
                await cs.initialize()

    with pytest.raises(Exception):
        asyncio.run(main())


def test_resolution_cached_per_query_string(harness, monkeypatch):
    h = harness()
    n = []
    real = profiles.resolve
    monkeypatch.setattr(profiles, "resolve", lambda *a: n.append(1) or real(*a))
    for _ in range(3):
        listing(h, "?profile=readonly")
    assert len(n) == 1
    listing(h, "?profile=cam")
    assert len(n) == 2
    listing(h, "?x=1&profile=readonly")  # a different query string is a different cache key
    assert len(n) == 3


def test_config_error_leaves_only_status_tools(harness):
    h = harness(error="invalid config /x/config.toml: port must be an integer 1024-65535")
    tools = listing(h)
    assert set(tools) == {"server"} and names(tools["server"]) == ["help", "job_status", "status"]
    assert set(listing(h, "?profile=full")) == {"server"}
    r = call(h, "model", "extrude")
    assert r["error"]["code"] == "NOT_ALLOWED" and "port must be an integer" in r["error"]["message"]
    assert call(h, "server", "status")["ok"] is True


def test_allowed_set_and_items_reach_the_action(acts, harness):
    seen = {}

    @action("model", access="read")
    def probe(ctx) -> dict:
        """Probe."""
        seen.update(allowed=ctx.allowed, items=ctx.profile_items)
        return {}

    h = harness()
    call(h, "model", "probe", "?profile=readonly,-server.status")
    assert seen["allowed"] == {"model.list", "model.probe", "server.status", "server.job_status"}
    assert seen["items"] == ["-write", "-destructive", "-server.status"]
