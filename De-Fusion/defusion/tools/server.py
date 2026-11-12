"""Report on the De-Fusion runtime, poll jobs, restart the add-in and run Fusion text commands."""
import asyncio
import datetime
import logging
import sys
import threading
import time
import uuid
from pathlib import Path

import defusion
from defusion import auth, config, dispatch
from defusion import server as _server
from defusion.actions import action
from defusion.context import Ctx
from defusion.errors import INVALID_PARAMS, NOT_FOUND, UNSUPPORTED, ActionError

log = logging.getLogger("defusion")

_STARTED = time.time()
_STARTED_MONO = time.monotonic()
_INSTANCE = uuid.uuid4().hex[:12]


def _fusion_version():
    try:
        import adsk.core

        return adsk.core.Application.get().version
    except Exception:
        return None


_FUSION_VERSION = _fusion_version()
_PENDING = None
RESTART_DELAY_S = 0.75


def _iso(epoch):
    if epoch is None:
        return None
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@action("server", access="read", main_thread=False)
def status(ctx: Ctx) -> dict:
    """Report De-Fusion and Fusion versions, port, auth, config, profile, queue and uptime."""
    port = config.settings().port
    return {
        "server": {
            "name": "De-Fusion",
            "version": defusion.__version__,
            "state": _server.state(),
            "started": _iso(_STARTED),
            "uptime_s": round(time.monotonic() - _STARTED_MONO, 1),
            "instance": _INSTANCE,
        },
        "fusion": {"version": _FUSION_VERSION},
        "http": {"port": port, "url": "http://127.0.0.1:%d/mcp" % port, "auth": auth.token() is not None},
        "config": {
            "path": str(config.CONFIG_PATH),
            "exists": config.CONFIG_PATH.is_file(),
            "error": config.load_error(),
        },
        "profile": {"items": list(ctx.profile_items), "allowed_actions": len(ctx.allowed)},
        "dispatcher": {"queue_depth": dispatch.queue_depth(), "jobs_running": dispatch.running_count()},
    }


@action("server", access="read", main_thread=False)
def job_status(ctx: Ctx, job_id: str) -> dict:
    """Return the state and, when finished, the result or error of a timed-out call's job."""
    job = dispatch.get_job(job_id)
    if job is None:
        raise ActionError(
            NOT_FOUND,
            "unknown job %s" % job_id,
            hints=["only the last 100 jobs are kept", "jobs do not survive server.restart_addin"],
        )
    end = job["finished"] if job["finished"] is not None else time.time()
    out = {
        "job_id": job_id,
        "state": job["state"],
        "started": _iso(job["started"]),
        "finished": _iso(job["finished"]),
        "elapsed_s": round(end - job["started"], 1),
    }
    if job["state"] == "done":
        out["result"] = job["result"]
    elif job["state"] == "failed":
        out["error"] = job["error"]
    return out


# --- restart_addin ------------------------------------------------------------------------------


def _locate(scripts):
    """This add-in's Script: by AddIns link path, by resolved path, then by name."""
    path = Path(defusion.__file__)
    attempts = (
        lambda: scripts.itemByPath(str(path.parent.parent)),
        lambda: scripts.itemByPath(str(path.resolve().parent.parent)),
        lambda: scripts.itemsByName("De-Fusion")[0],
    )
    for attempt in attempts:
        try:
            found = attempt()
        except Exception:
            continue
        if found is not None:
            return found
    return None


def _make_handler():
    import adsk.core

    class _RestartHandler(adsk.core.CustomEventHandler):
        def notify(self, args):
            _restart()

    return _RestartHandler()


def _step(what, fn):
    try:
        fn()
    except Exception:
        log.exception("restart_addin: %s failed", what)


def _restart():
    pending = _PENDING
    pending["fired"].set()
    if pending["done"]:
        return
    pending["done"] = True

    def purge():
        for name in [m for m in sys.modules if m == "defusion" or m.startswith("defusion.")]:
            del sys.modules[name]

    _step("script.stop", pending["script"].stop)
    _step("module purge", purge)
    _step("unregisterCustomEvent", lambda: pending["app"].unregisterCustomEvent(pending["event_id"]))
    _step("script.run", pending["script"].run)


def _fire():
    global _PENDING
    pending = _PENDING
    try:
        for _ in range(50):
            pending["app"].fireCustomEvent(pending["event_id"])
            if pending["fired"].wait(0.1):
                return
        log.error("restart event never delivered")
    except Exception:
        log.exception("restart_addin: firing the restart event failed")
    _PENDING = None


def _scheduled(script, already):
    return {
        "scheduled": True,
        "already_scheduled": already,
        "delay_ms": int(RESTART_DELAY_S * 1000),
        "addin": script.name,
        "instance": _INSTANCE,
        "hint": "poll server.status until server.instance differs",
    }


@action("server", access="write", undo_group=False, edits_document="none")
def restart_addin(ctx: Ctx) -> dict:
    """Restart De-Fusion about 750 ms after replying; reloads its code and config.

    The HTTP server is down for a few seconds. Poll server.status until server.instance changes.
    Calls in flight at restart end with a transport error and an unknown outcome; job ids are lost."""
    global _PENDING
    scripts = getattr(ctx.app, "scripts", None)
    if scripts is None:
        raise ActionError(UNSUPPORTED, "this Fusion build has no Application.scripts")
    script = _locate(scripts)
    if script is None:
        raise ActionError(NOT_FOUND, "De-Fusion not found in app.scripts")
    if _PENDING is not None:
        return _scheduled(script, True)
    event_id = "defusion.restart.%s" % uuid.uuid4().hex
    event = ctx.app.registerCustomEvent(event_id)
    handler = _make_handler()
    event.add(handler)
    _PENDING = {
        "event_id": event_id, "event": event, "handler": handler, "script": script, "app": ctx.app,
        "fired": threading.Event(), "done": False,
    }
    timer = threading.Timer(RESTART_DELAY_S, _fire)
    timer.daemon = True
    timer.start()
    return _scheduled(script, False)


# --- run_text_command -----------------------------------------------------------------------------


@action("server", access="destructive", undo_group=False)
def run_text_command(ctx: Ctx, command: str) -> dict:
    """Run one Fusion text command (app.executeTextCommand) and return its output.

    Text commands are unrestricted and can change or discard data; no undo group is opened."""
    if command.strip() == "":
        raise ActionError(INVALID_PARAMS, "command must not be empty")
    return {"command": command, "output": ctx.text_command(command)}


# --- live scenarios (design § 8.2) ---------------------------------------------------------------


from .live_common import _lc  # noqa: E402


async def _status_fields(session):
    r = await _lc().call(session, "server", "status", {})
    assert r["ok"], r
    res = r["result"]
    assert res["server"]["version"] == defusion.__version__
    assert isinstance(res["server"]["uptime_s"], float) and isinstance(res["server"]["instance"], str)
    assert isinstance(res["fusion"]["version"], str)
    assert res["http"]["url"] == "http://127.0.0.1:%d/mcp" % res["http"]["port"]
    assert isinstance(res["http"]["auth"], bool) and isinstance(res["config"]["exists"], bool)
    assert res["config"]["error"] is None
    assert isinstance(res["profile"]["allowed_actions"], int)
    assert isinstance(res["dispatcher"]["queue_depth"], int) and isinstance(res["dispatcher"]["jobs_running"], int)


async def _status_profile(session):
    lc = _lc()
    counts = {}
    for name in ("readonly", "full"):
        async with lc.open_session(name) as s:
            r = await lc.call(s, "server", "status", {})
            assert r["ok"], r
            counts[name] = r["result"]["profile"]["allowed_actions"]
    assert counts["readonly"] < counts["full"], counts


async def _job_unknown(session):
    r = await _lc().call(session, "server", "job_status", {"job_id": "nope"})
    assert not r["ok"] and r["error"]["code"] == "NOT_FOUND", r


async def _text_command(session):
    lc = _lc()
    for cmd in ('PTransaction.Start "De-Fusion live"', "PTransaction.Abort"):
        r = await lc.call(session, "server", "run_text_command", {"command": cmd, "confirm": True})
        assert r["ok"], r
        assert r["result"]["command"] == cmd and isinstance(r["result"]["output"], str), r


async def _live_restart(session):
    lc = _lc()
    before = (await lc.call(session, "server", "status", {}))["result"]["server"]
    r = await lc.call(session, "server", "restart_addin", {})
    assert r["ok"] and r["result"]["scheduled"] is True, r
    end = time.time() + 30
    while time.time() < end:
        await asyncio.sleep(0.5)
        try:  # a request that fails while the server is down ends its session, so poll on a fresh one
            async with lc.open_session() as probe:
                now = (await lc.call(probe, "server", "status", {}))["result"]["server"]
        except (Exception, BaseExceptionGroup):
            continue  # the server is down while the add-in reloads
        if now["instance"] != before["instance"]:
            assert now["started"] > before["started"], (before, now)
            return
    raise AssertionError("status did not answer with a new instance within 30 s")


LIVE_SCENARIOS = [
    ("status_fields", "server.status", _status_fields),
    ("status_profile", "server.status", _status_profile),
    ("job_unknown", "server.job_status", _job_unknown),
    ("text_command", "server.run_text_command", _text_command),
    ("restart", "server.restart_addin", _live_restart),  # runs last
]
