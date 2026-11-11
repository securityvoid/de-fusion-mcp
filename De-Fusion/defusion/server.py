"""MCP server construction, HTTP app, uvicorn thread, start/stop (design § 3, § 6.4-6.5)."""
import asyncio
import importlib
import logging
import os
import socket
import threading
import time
from typing import Any

import anyio.to_thread
import uvicorn
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS
from mcp.server.transport_security import TransportSecuritySettings

from . import __version__, actions, auth, config, dispatch, profiles
from . import log as dlog
from .units import OutputUnits

log = logging.getLogger("defusion")

INSTRUCTIONS = (
    "De-Fusion drives Autodesk Fusion. Each tool takes an `action` plus `params`: call it as "
    '{"action": "<name>", "params": {...}}. Call `action="help"` (optionally with params '
    '{"action": "<name>"}) to get the allowed actions or one action\'s parameter schema. Lengths and '
    'angles accept unit expressions such as "2 in" or "45 deg"; a top-level `units` object '
    "({length, angle}) sets the units of the returned values."
)

ALWAYS_ALLOWED = ("server.status", "server.job_status")

_state = {"status": "stopped"}
_run = {"server": None, "thread": None, "port": None}


def state():
    """``starting``, ``running`` or ``failed`` while the server is up; ``stopped`` before start() and after stop()."""
    return _state["status"]


# --- profile resolution -------------------------------------------------------------------------------


class _Profiles:
    """The allowed set per request: the query's profile or the default, or the config-error set (§ 6.1, § 6.4)."""

    MAX_CACHED = 256

    def __init__(self, cfg, registry, error):
        self.cfg, self.registry, self.error = cfg, registry, error
        self.merged = profiles.merge(profiles.BUILTIN, cfg.profiles)
        self._cache = {}

    def for_query(self, qs):
        """``(allowed "tool.action" frozenset, expanded items)`` for a raw query string; raises InvalidProfile."""
        hit = self._cache.get(qs)
        if hit is None:
            hit = self._resolve(profiles.parse_query(qs) or self.cfg.default_profile)
            if len(self._cache) >= self.MAX_CACHED:
                self._cache.clear()
            self._cache[qs] = hit
        return hit

    def _resolve(self, items):
        if self.error:
            return frozenset(ALWAYS_ALLOWED), []
        allowed = profiles.resolve(items, self.merged, self.registry)
        keys = {"%s.%s" % t for t in allowed}
        keys.update(ALWAYS_ALLOWED)
        expanded = [("-" if n else "") + s for n, s in profiles.expand(items, self.merged)]
        return frozenset(keys), expanded


def _query(ctx):
    request = getattr(ctx, "request", None)
    return request.url.query if request is not None else ""


def _profile_middleware(prof):
    """SDK ServerMiddleware: rejects an invalid ``?profile=`` on every request, filters ``tools/list`` (§ 6.5)."""

    async def middleware(ctx, call_next):
        try:
            allowed, _ = prof.for_query(_query(ctx))
        except profiles.InvalidProfile as exc:
            raise MCPError(INVALID_PARAMS, str(exc)) from None
        result = await call_next(ctx)
        if ctx.method == "tools/list":
            result["tools"] = _filter_tools(result["tools"], allowed)
        return result

    return middleware


def _filter_tools(tools, allowed):
    """Rewrite the wire-form (dict) tool list: drop tools with no allowed action, narrow the rest."""
    out = []
    for tool in tools:
        built = actions.build_tool(tool["name"], allowed)
        if built is not None:
            out.append({**tool, "description": built["description"], "inputSchema": built["inputSchema"],
                        "annotations": built["annotations"]})
    return out


# --- app ----------------------------------------------------------------------------------------------------------


def _tool_function(tool, prof, output_units):
    async def fn(action: Any, params: Any = None, units: Any = None, ctx: Context = None) -> Any:
        args = {"action": action}
        if params is not None:
            args["params"] = params
        if units is not None:
            args["units"] = units
        allowed, items = prof.for_query(_query(ctx.request_context))
        return await anyio.to_thread.run_sync(
            lambda: actions.call(tool, args, allowed, items, output_units, denied_note=prof.error)
        )

    fn.__name__ = tool
    return fn


def build_app(config_, registry, *, error=None, token=None):
    """The ASGI app: stateless streamable HTTP at ``/mcp``, one SDK tool per De-Fusion tool, optional bearer auth."""
    port = config_.port
    prof = _Profiles(config_, registry, error)
    output_units = OutputUnits.for_call(None, config_.units)
    mcp = MCPServer(
        name="De-Fusion", version=__version__, instructions=INSTRUCTIONS, middleware=[_profile_middleware(prof)]
    )
    tools = sorted({a.tool for a in registry})
    for tool in tools:
        mcp.add_tool(
            _tool_function(tool, prof, output_units), name=tool, description=tool, structured_output=False
        )
    security = TransportSecuritySettings(
        allowed_hosts=["127.0.0.1:%d" % port, "localhost:%d" % port],
        allowed_origins=["http://127.0.0.1:%d" % port, "http://localhost:%d" % port],
    )
    app = mcp.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True, host="127.0.0.1",
        transport_security=security,
    )
    return auth.BearerAuth(app, token) if token else app


# --- start / stop -----------------------------------------------------------------------------------------------


def _check_selectors(cfg, registry):
    """Resolve default_profile and every [profiles] entry; the first failure is returned as text."""
    merged = profiles.merge(profiles.BUILTIN, cfg.profiles)
    try:
        profiles.resolve(cfg.default_profile, merged, registry)
        for name in cfg.profiles:
            profiles.resolve([name], merged, registry)
    except profiles.InvalidProfile as exc:
        return "invalid config %s: %s" % (config.CONFIG_PATH, exc)
    return None


def _port_open(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def start():
    """Load config, register the dispatcher, import the tools and start uvicorn on a daemon thread.

    Returns without waiting for the listener; ``state()`` reports ``starting``, ``running`` or ``failed``."""
    if _run["thread"] is not None:
        return
    _state["status"] = "starting"
    cfg, error = config.load()
    dlog.setup(config.CONFIG_DIR, cfg.log_level)
    token = None
    try:
        token = auth.token_from(cfg, os.environ)
    except ValueError as exc:
        error = error or str(exc)
        config.record_error(error)
    auth.set_token(token)
    dispatch.register()
    importlib.import_module("defusion.tools")
    registry = actions.registry()
    if not error:
        error = _check_selectors(cfg, registry)
        if error:
            config.record_error(error)
    app = build_app(cfg, registry, error=error, token=token)
    uv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=cfg.port, log_level="warning", lifespan="on"))
    try:
        sock = uv.config.bind_socket()
        sock.set_inheritable(False)  # uvicorn leaves it inheritable; Fusion's child processes would hold the port
    except (OSError, SystemExit):  # uvicorn's bind_socket exits the process on a bind error
        log.error("Port %d in use; set port in %s and restart Fusion", cfg.port, config.CONFIG_PATH)
        _state["status"] = "failed"
        return
    thread = threading.Thread(target=_serve, args=(uv, sock), name="defusion-uvicorn", daemon=True)
    _run.update(server=uv, thread=thread, port=cfg.port)
    thread.start()


def _serve(uv, sock):
    async def main():
        task = asyncio.ensure_future(uv.serve(sockets=[sock]))
        while not uv.started and not task.done():
            await asyncio.sleep(0.01)
        if uv.started:
            _state["status"] = "running"
        await task

    try:
        asyncio.run(main())
    except BaseException:  # uvicorn exits with SystemExit on startup failure
        log.exception("server thread failed")
        _state["status"] = "failed"
    finally:
        sock.close()


def stop():
    """Stop uvicorn, wait for the port to be released, fail queued items and release the dispatcher."""
    uv, thread, port = _run["server"], _run["thread"], _run["port"]
    if uv is not None:
        uv.should_exit = True
        thread.join(5)
        deadline = time.monotonic() + 5
        while _port_open(port) and time.monotonic() < deadline:
            time.sleep(0.05)
    _run.update(server=None, thread=None, port=None)
    dispatch.fail_queued()
    dispatch.unregister()
    dispatch.clear_jobs()
    _state["status"] = "stopped"
