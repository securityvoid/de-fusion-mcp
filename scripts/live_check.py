"""Drive the running De-Fusion add-in over /mcp and run each tool set's live scenarios (design § 10).

    DEFUSION_TOKEN=... python scripts/live_check.py [--profile P] [--port N] [--only tool[.action]]
"""
import argparse
import asyncio
import contextlib
import importlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT / "De-Fusion", ROOT / "De-Fusion" / "lib"):  # lib last: only a fallback for missing packages
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

try:  # tool modules import adsk to register; outside Fusion the scenario loader only needs the names
    import adsk  # noqa: F401
except ImportError:
    sys.path.append(str(ROOT / "tests" / "fakes"))

from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402
from mcp.shared._httpx_utils import create_mcp_http_client  # noqa: E402

DEFAULT_PORT = 8642
EXIT_CODES = "Exit codes: 0 all scenarios pass, 1 any failure, 2 server unreachable."
_harness = {"port": None, "token": None}


def configure(port, token):
    """Set the port and bearer token every open_session() uses."""
    _harness["port"], _harness["token"] = port, token


def default_port():
    """The ``port`` of the config file, 8642 when unset or unreadable."""
    import tomllib

    from defusion import config

    try:
        return int(tomllib.loads(config.CONFIG_PATH.read_text(encoding="utf-8")).get("port", DEFAULT_PORT))
    except (OSError, ValueError):
        return DEFAULT_PORT


def _leaf(exc):
    while isinstance(exc, BaseExceptionGroup):
        exc = exc.exceptions[0]
    return exc


@contextlib.asynccontextmanager
async def open_session(profile=None):
    """An initialized SDK ClientSession on the harness port and token, with ``?profile=`` when given."""
    port = _harness["port"] or default_port()
    headers = {"Authorization": "Bearer " + _harness["token"]} if _harness["token"] else {}
    url = "http://127.0.0.1:%d/mcp%s" % (port, "?profile=" + profile if profile else "")
    client = create_mcp_http_client(headers=headers)
    async with streamable_http_client(url, http_client=client) as (read, write, *_):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def call(session, tool, action, params, units=None):
    """tools/call; returns the parsed envelope (``ok`` true or false)."""
    args = {"action": action, "params": params}
    if units is not None:
        args["units"] = units
    result = await session.call_tool(tool, args)
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


async def _ok(session, tool, action, params):
    env = await call(session, tool, action, params)
    if not env["ok"]:
        raise RuntimeError("%s.%s failed: %s" % (tool, action, env["error"]["message"]))
    return env["result"]


async def box(session, w, d, h, name=None):
    """A w x d x h box on XY from the origin as a new body; returns the body Ref."""
    sk = await _ok(session, "sketch", "create", {"plane": "XY"})
    ref = sk["sketch"]
    await _ok(session, "sketch", "rectangle", {"sketch": ref, "mode": "two_point", "p1": [0, 0], "p2": [w, d]})
    feat = await _ok(session, "model", "extrude", {"profile": {"sketch": ref, "index": 0}, "distance": h,
                                                   "operation": "new_body"})
    body = feat["bodies"][0]
    if name:
        await _ok(session, "model", "rename", {"target": body, "name": name})
    return body


def load_scenarios(only):
    """LIVE_SCENARIOS of each tool module, filtered by ``only`` (``tool`` or ``tool.action``)."""
    tool, _, action = (only or "").partition(".")
    out = []
    for name in importlib.import_module("defusion.tools").TOOL_MODULES:
        if tool and not action and name != tool:
            continue
        module = importlib.import_module("defusion.tools." + name)
        for scenario in getattr(module, "LIVE_SCENARIOS", []):
            if not action or scenario[1] == only:
                out.append(scenario)
    return out


LIVE_PREFIX = "defusion-live-"


async def freeze_leftovers(session):
    """Make every still-editable ``defusion-live-*`` file in the active project's root read-only; returns False on a failure."""
    env = await call(session, "document", "list_projects", {})
    active = [p for p in env["result"]["items"] if p["active"]] if env["ok"] else []
    if not active:
        return True
    ok = True
    offset = 0
    while offset is not None:
        env = await call(session, "document", "list_folders", {"folder": active[0]["name"], "offset": offset, "limit": 100})
        if not env["ok"]:
            return False
        for item in env["result"]["items"]:
            if item["kind"] != "file" or not item["name"].startswith(LIVE_PREFIX):
                continue
            props = await call(session, "document", "datafile_get", {"file": item["id"]})
            if not props["ok"] or props["result"]["read_only"]:
                continue
            opened = await call(session, "document", "open", {"file": item["id"]})
            if opened["ok"]:
                fixed = await call(session, "document", "set_read_only", {"read_only": True, "document": opened["result"]["id"]})
                await call(session, "document", "close", {"document": opened["result"]["id"], "confirm": True})
            else:
                fixed = opened
            print("%s editable leftover %s" % ("FIXED" if fixed["ok"] else "FAIL", item["path"]))
            ok = ok and fixed["ok"]
        offset = env["result"]["next_offset"]
    return ok


async def run(profile, only):
    scenarios = load_scenarios(only)
    failed = False
    async with contextlib.AsyncExitStack() as stack:
        try:
            full = await stack.enter_async_context(open_session("full"))
        except (Exception, BaseExceptionGroup) as exc:
            leaf = _leaf(exc)
            raise ConnectionError(str(leaf) or type(leaf).__name__) from None
        await _ok(full, "document", "create", {})
        try:
            async with open_session(profile) as session:
                for name, tag, fn in scenarios:
                    try:
                        await fn(session)
                    except Exception as exc:
                        failed = True
                        print("FAIL %s (%s): %s" % (name, tag, exc or type(exc).__name__))
                    else:
                        print("PASS %s (%s)" % (name, tag))
        finally:
            if not await freeze_leftovers(full):
                failed = True
            env = await call(full, "document", "close", {"confirm": True})
            if not env["ok"]:
                failed = True
                print("FAIL scratch document close: %s" % env["error"]["message"], file=sys.stderr)
    if only and not scenarios:
        print("no live scenarios match --only %s" % only, file=sys.stderr)
        failed = True
    return 1 if failed else 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if any(a == "--token" or a.startswith("--token=") for a in argv):
        print("--token is not accepted; set the DEFUSION_TOKEN environment variable", file=sys.stderr)
        return 2
    ap = argparse.ArgumentParser(prog="live_check.py", description="Run De-Fusion live scenarios against the running add-in.",
                                 epilog="The bearer token is read from the DEFUSION_TOKEN environment variable only.\n" + EXIT_CODES,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="profile of the scenario session (default: the server default)")
    ap.add_argument("--port", type=int, help="server port (default: the config port, 8642)")
    ap.add_argument("--only", metavar="tool[.action]", help="run one tool module or the scenarios tagged tool.action")
    args = ap.parse_args(argv)
    configure(args.port or default_port(), os.environ.get("DEFUSION_TOKEN") or None)
    try:
        return asyncio.run(run(args.profile, args.only))
    except ConnectionError as exc:
        print("server unreachable on port %d: %s" % (_harness["port"], exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
