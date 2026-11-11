"""In-process MCP surface: the real start() path with fakes, driven by the SDK client and raw HTTP."""
import asyncio
import json
import socket
import sys
import threading
import time
import types as pytypes
import urllib.error
import urllib.request

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.shared.exceptions import MCPError
from mcp.server.transport_security import DEFAULT_MAX_REQUEST_BODY_SIZE

from adsk import core
from defusion import auth, config, server
from defusion.actions import action

RAN = []


@pytest.fixture
def surface(tmp_path, monkeypatch, registry, fake_design, fake_app):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.delenv("DEFUSION_TOKEN", raising=False)
    monkeypatch.setitem(sys.modules, "defusion.tools", pytypes.ModuleType("defusion.tools"))
    RAN.clear()

    @action("model", access="write")
    def make(ctx) -> dict:
        """Make a body."""
        RAN.append("model.make")
        ctx.component().add_body("B")
        return {"made": True}

    @action("model", access="read")
    def peek(ctx) -> dict:
        """Peek."""
        RAN.append("model.peek")
        return {"bodies": len(list(ctx.component().bRepBodies))}

    @action("model", access="destructive")
    def purge(ctx) -> dict:
        """Purge."""
        RAN.append("model.purge")
        return {}

    @action("script", access="write")
    def run(ctx) -> dict:
        """Run a script."""
        RAN.append("script.run")
        return {}

    @action("server", access="read")
    def status(ctx) -> dict:
        """Status."""
        return {"config": {"error": config.load_error()}, "state": server.state()}

    @action("server", access="read")
    def job_status(ctx) -> dict:
        """Job status."""
        return {}

    def write(text):
        (tmp_path / "config.toml").write_text("port = %d\n%s" % (port, text))

    write("")
    ns = pytypes.SimpleNamespace(port=port, write=write, app=fake_app, dir=tmp_path)

    def start():
        core.reset_calls()
        server.start()
        end = time.time() + 5
        while time.time() < end and server.state() != "running":
            time.sleep(0.02)
        assert server.state() == "running"

    ns.start = start
    yield ns
    server.stop()
    auth.set_token(None)
    (tmp_path / "config.toml").unlink()
    config.load()


def session(port, query="", token=None, headers=None):
    hdrs = dict(headers or {})
    if token:
        hdrs["Authorization"] = "Bearer " + token

    def run(fn):
        async def main():
            client = create_mcp_http_client(headers=hdrs)
            async with streamable_http_client("http://127.0.0.1:%d/mcp%s" % (port, query), http_client=client) as (r, w, *_):
                async with ClientSession(r, w) as cs:
                    init = await cs.initialize()
                    return await fn(cs, init)
        try:
            return asyncio.run(main())
        except BaseExceptionGroup as group:
            leaf = group
            while isinstance(leaf, BaseExceptionGroup):
                leaf = leaf.exceptions[0]
            raise leaf from None

    return run


def call_tool(port, tool, act, query="", token=None, params=None):
    async def fn(cs, init):
        r = await cs.call_tool(tool, {"action": act, "params": params or {}})
        return r.structured_content
    return session(port, query, token)(fn)


def raw(port, method="POST", path="/mcp", body=None, headers=None):
    data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=data, method=method, headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def post_status(port, body):
    """POST with httpx so a response sent before the whole body is read is still seen."""
    async def main():
        client = create_mcp_http_client(headers={"Content-Type": "application/json",
                                                 "Accept": "application/json, text/event-stream"})
        async with client:
            return (await client.post("http://127.0.0.1:%d/mcp" % port, content=body)).status_code
    return asyncio.run(main())


INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}


def CALL(tool="model", act="make"):
    return {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": {"action": act}}}


def test_initialize_reports_name_and_instructions(surface):
    surface.start()
    init = session(surface.port)(lambda cs, init: _ret(init))
    assert init.server_info.name == "De-Fusion" and init.instructions == server.INSTRUCTIONS


async def _ret(x):
    return x


def test_no_resources_or_prompts(surface):
    surface.start()

    async def fn(cs, init):
        out = {}
        for name, call in (("resources", cs.list_resources), ("prompts", cs.list_prompts)):
            try:
                r = await call()
                out[name] = getattr(r, name)
            except MCPError as e:
                out[name] = e.code
        return out

    out = session(surface.port)(fn)
    assert out["resources"] in ([], -32601) and out["prompts"] in ([], -32601)


def test_no_sse_route(surface):
    surface.start()
    assert raw(surface.port, "GET", "/sse")[0] == 404
    assert raw(surface.port, "GET", "/messages/")[0] in (404, 405)


def test_listens_on_loopback_only(surface):
    surface.start()
    assert server._run["server"].servers[0].sockets[0].getsockname()[0] == "127.0.0.1"


def test_tools_list_and_call_default_and_profile(surface):
    surface.start()

    async def lst(cs, init):
        return {t.name: t for t in (await cs.list_tools()).tools}

    full = session(surface.port)(lst)
    assert set(full) == {"model", "server"}
    ro = session(surface.port, "?profile=readonly")(lst)
    assert ro["model"].input_schema["properties"]["action"]["enum"] == ["help", "peek"]
    assert ro["model"].annotations.read_only_hint is True
    assert call_tool(surface.port, "model", "make")["ok"] is True
    denied = call_tool(surface.port, "model", "make", "?profile=readonly")
    assert denied["ok"] is False and denied["error"]["code"] == "NOT_ALLOWED"
    assert RAN == ["model.make"]
    with pytest.raises(MCPError) as e:
        call_tool(surface.port, "model", "peek", "?profile=nosuch")
    assert "invalid profile item 'nosuch'" in e.value.message


def test_all_fake_adsk_calls_on_dispatcher_thread(surface):
    surface.start()
    core.reset_calls()
    assert call_tool(surface.port, "model", "make")["ok"] is True
    assert call_tool(surface.port, "model", "peek")["result"] == {"bodies": 1}
    assert core.CALLS and core.calling_threads() == {surface.app.main_thread_id}
    assert set(core.FIRE_THREADS) - {surface.app.main_thread_id}  # fired from request/timer threads only
    assert threading.get_ident() not in core.calling_threads()


def test_token_required_when_set(surface):
    surface.write('token = "s3cret"\n')
    surface.start()
    assert raw(surface.port, body=INIT)[0] == 401
    status, body = raw(surface.port, body=INIT, headers={"Authorization": "Bearer wrong"})
    assert status == 401 and json.loads(body)["error"] == "unauthorized"
    assert raw(surface.port, "GET", "/sse")[0] == 401
    assert call_tool(surface.port, "model", "peek", token="s3cret")["ok"] is True
    with pytest.raises(Exception):
        call_tool(surface.port, "model", "peek")


def test_no_token_means_no_auth(surface):
    surface.start()
    assert raw(surface.port, body=INIT)[0] == 200


def test_foreign_host_rejected(surface):
    surface.start()
    status, _ = raw(surface.port, body=INIT, headers={"Host": "evil.example"})
    assert status in (400, 403, 421)


def test_foreign_origin_rejected_and_runs_no_action(surface):
    surface.start()
    host = {"Host": "127.0.0.1:%d" % surface.port}
    status, _ = raw(surface.port, body=CALL(), headers={**host, "Origin": "http://evil.example"})
    assert status in (400, 403) and RAN == []
    status, body = raw(surface.port, body=CALL(), headers={**host, "Origin": "http://localhost:%d" % surface.port})
    assert status == 200 and RAN == ["model.make"]


def test_localhost_host_allowed(surface):
    surface.start()
    assert raw(surface.port, body=INIT, headers={"Host": "localhost:%d" % surface.port})[0] == 200


def test_body_one_byte_over_default_limit_rejected(surface):
    surface.start()
    base = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {"pad": ""}}).encode()
    limit = DEFAULT_MAX_REQUEST_BODY_SIZE
    over = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {"pad": "x" * (limit - len(base) + 1)}}).encode()
    assert len(over) == limit + 1
    assert post_status(surface.port, over) == 413
    at = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {"pad": "x" * (limit - len(base))}}).encode()
    assert len(at) == limit and post_status(surface.port, at) != 413


def _port_8642_in_use():
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", 8642)) == 0


# a config error falls back to port 8642; run only when it is free on this machine
@pytest.mark.skipif(_port_8642_in_use(), reason="port 8642 is in use here")
@pytest.mark.parametrize("text", ["port = = 1\n", '[profiles]\nbad = ["model.nosuch*"]\n', "log_level = 5\n"])
def test_config_error_blocks_everything_but_status(surface, text, monkeypatch):
    (surface.dir / "config.toml").write_text(text)
    monkeypatch.setattr(config, "DEFAULT_PORT", config.DEFAULT_PORT)
    core.reset_calls()
    server.start()
    end = time.time() + 5
    while time.time() < end and server.state() != "running":
        time.sleep(0.02)
    assert server.state() == "running" and config.load_error()
    denied = call_tool(8642, "model", "make")
    assert denied["error"]["code"] == "NOT_ALLOWED" and config.load_error().split(":")[0] in denied["error"]["message"]
    assert RAN == []
    status = call_tool(8642, "server", "status")
    assert status["ok"] is True and status["result"]["config"]["error"] == config.load_error()

    async def lst(cs, init):
        return {t.name for t in (await cs.list_tools()).tools}

    assert session(8642)(lst) == {"server"}

