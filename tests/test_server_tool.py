"""The server tool (docs/specs/server/design.md § 8.1), with the fake adsk."""
import importlib
import socket
import sys
import threading
import time
import types as pytypes
from pathlib import Path

import pytest
from adsk import core

from defusion import actions, auth, config, dispatch, server
from defusion.errors import ActionError

from test_mcp_surface import call_tool, session
from helpers import wait_for

TOOLS_DIR = Path(__file__).resolve().parent.parent / "De-Fusion" / "defusion" / "tools"
READ = frozenset({"server.status", "server.job_status"})
ALL = frozenset({"server.status", "server.job_status", "server.restart_addin", "server.run_text_command"})


@pytest.fixture
def srv(registry, monkeypatch):
    """A fresh import of defusion.tools.server in the isolated registry (no other tool modules)."""
    pkg = pytypes.ModuleType("defusion.tools")
    pkg.__path__ = [str(TOOLS_DIR)]
    monkeypatch.setitem(sys.modules, "defusion.tools", pkg)
    monkeypatch.delitem(sys.modules, "defusion.tools.server", raising=False)
    mod = importlib.import_module("defusion.tools.server")
    yield mod
    sys.modules.pop("defusion.tools.server", None)


def call(act, params=None, allowed=ALL, items=("all",)):
    r = actions.call("server", {"action": act, "params": params or {}}, allowed, list(items))
    return r.structured_content


# --- status ----------------------------------------------------------------------------------


def test_status_fields(srv):
    r = call("status")
    assert r["ok"], r
    res = r["result"]
    import defusion

    assert res["server"]["version"] == defusion.__version__
    assert res["server"]["name"] == "De-Fusion"
    assert res["server"]["started"].endswith("Z") and len(res["server"]["started"]) == 20
    assert isinstance(res["server"]["uptime_s"], float) and len(res["server"]["instance"]) == 12
    assert res["http"]["port"] == config.settings().port
    assert res["http"]["url"] == "http://127.0.0.1:%d/mcp" % config.settings().port
    assert res["http"]["auth"] is False
    assert res["config"]["path"] == str(config.CONFIG_PATH)
    assert res["profile"] == {"items": ["all"], "allowed_actions": 4}
    assert res["dispatcher"] == {"queue_depth": 0, "jobs_running": 0}


def test_status_reports_auth(srv):
    auth.set_token("t0k")
    try:
        assert call("status")["result"]["http"]["auth"] is True
    finally:
        auth.set_token(None)


def test_status_instance_changes_per_load(srv):
    first = call("status")["result"]["server"]["instance"]
    sys.modules.pop("defusion.tools.server")
    with actions.isolated_registry():
        second = importlib.import_module("defusion.tools.server")._INSTANCE
    assert first != second


def test_status_runs_without_main_thread(srv, dispatcher, fake_app):
    fake_app.hold()
    t = threading.Thread(target=lambda: dispatcher.run(lambda: 1, timeout=0.5), daemon=True)
    t.start()
    assert wait_for(lambda: dispatch.queue_depth() == 1)
    core.reset_calls()
    start = time.monotonic()
    r = call("status")
    assert time.monotonic() - start < 1
    assert r["result"]["dispatcher"]["queue_depth"] == 1
    assert not [c for c in core.CALLS if c[0].startswith("Application.")]
    fake_app.release()
    t.join(2)


@pytest.fixture
def broken_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setitem(config._state, "config", config.settings())
    monkeypatch.setitem(config._state, "error", None)
    (tmp_path / "config.toml").write_text("port = [broken\n")
    config.load()


def test_status_config_error_blocks_writes(srv, broken_config):
    err = call("status", allowed=READ)["result"]["config"]
    assert err["exists"] is True and err["error"]
    denied = call("restart_addin", allowed=frozenset(server.ALWAYS_ALLOWED))
    assert not denied["ok"] and denied["error"]["code"] == "NOT_ALLOWED"


# --- job_status ------------------------------------------------------------------------------


def test_job_status_unknown(srv):
    r = call("job_status", {"job_id": "nope"})
    assert not r["ok"] and r["error"]["code"] == "NOT_FOUND"


def test_job_status_running_done_failed(srv, dispatcher):
    def slow(value):
        def fn():
            time.sleep(0.3)
            return value
        return fn

    out = dispatcher.run(slow({"n": 1}), timeout=0.05, label="x.y")
    jid = out["job_id"]
    running = call("job_status", {"job_id": jid})["result"]
    assert running["state"] == "running" and running["finished"] is None
    assert "result" not in running and "error" not in running
    assert wait_for(lambda: dispatch.get_job(jid)["state"] == "done")
    done = call("job_status", {"job_id": jid})["result"]
    assert done["result"] == {"n": 1} and done["finished"].endswith("Z") and done["elapsed_s"] >= 0

    big = dispatcher.run(slow("x" * 65537), timeout=0.05, label="x.y")["job_id"]
    assert wait_for(lambda: dispatch.get_job(big)["state"] == "failed")
    failed = call("job_status", {"job_id": big})["result"]
    assert failed["error"]["code"] == "RESULT_TOO_LARGE" and "result" not in failed

    def boom():
        time.sleep(0.3)
        raise ActionError("NOT_FOUND", "gone")

    bid = dispatcher.run(boom, timeout=0.05, label="x.y")["job_id"]
    assert wait_for(lambda: dispatch.get_job(bid)["state"] == "failed")
    assert call("job_status", {"job_id": bid})["result"]["error"]["code"] == "NOT_FOUND"


# --- profile (REQ-SRV-007) -------------------------------------------------------------------


def test_profile_cam_sees_only_read_actions(srv, tmp_path, monkeypatch, fake_design, fake_app):
    @actions.action("cam", access="read")
    def cam_peek(ctx) -> dict:
        """Peek."""
        return {}

    @actions.action("script", access="write")
    def script_run(ctx) -> dict:
        """Run."""
        return {}

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.delenv("DEFUSION_TOKEN", raising=False)
    (tmp_path / "config.toml").write_text("port = %d\n" % port)
    server.start()
    try:
        assert wait_for(lambda: server.state() == "running", 5)

        async def lst(cs, init):
            return {t.name: t for t in (await cs.list_tools()).tools}

        tools = session(port, "?profile=cam")(lst)
        assert tools["server"].input_schema["properties"]["action"]["enum"] == ["help", "job_status", "status"]  # core sorts by name
        denied = call_tool(port, "server", "restart_addin", "?profile=cam")
        assert denied["error"]["code"] == "NOT_ALLOWED"
        full = call_tool(port, "server", "status", "?profile=all")["result"]["profile"]["allowed_actions"]
        cam = call_tool(port, "server", "status", "?profile=cam")["result"]["profile"]["allowed_actions"]
        assert cam < full
    finally:
        server.stop()
        auth.set_token(None)
        (tmp_path / "config.toml").unlink()
        config.load()


# --- restart_addin ---------------------------------------------------------------------------


class FakeScript:
    name = "De-Fusion"

    def __init__(self, order, stop_raises=False):
        self.order, self.stop_raises = order, stop_raises

    def stop(self):
        self.order.append("stop")
        if self.stop_raises:
            raise RuntimeError("stop failed")

    def run(self):
        self.order.append("run")


class FakeScripts:
    def __init__(self, script, by_path=True):
        self.script, self.by_path, self.paths = script, by_path, []

    def itemByPath(self, path):
        self.paths.append(path)
        return self.script if self.by_path else None

    def itemsByName(self, name):
        return [self.script] if name == "De-Fusion" else []


class ImmediateTimer:
    created = []

    def __init__(self, delay, fn):
        self.delay, self.fn, self.daemon = delay, fn, False
        ImmediateTimer.created.append(self)

    def start(self):
        threading.Thread(target=self.fn, daemon=True).start()


@pytest.fixture
def restart_env(srv, dispatcher, fake_app, monkeypatch):
    saved = {m: v for m, v in sys.modules.items() if m == "defusion" or m.startswith("defusion.")}
    order = []
    ImmediateTimer.created.clear()
    monkeypatch.setattr(srv, "threading", pytypes.SimpleNamespace(Timer=ImmediateTimer, Event=threading.Event))
    monkeypatch.setitem(sys.modules, "defusion.zz_test", pytypes.ModuleType("defusion.zz_test"))
    real_unregister = fake_app.unregisterCustomEvent

    def unregister(event_id):
        order.append("unregister")
        return real_unregister(event_id)

    fake_app.unregisterCustomEvent = unregister
    yield srv, fake_app, order
    for name in [m for m in sys.modules if m == "defusion" or m.startswith("defusion.")]:
        del sys.modules[name]
    sys.modules.update(saved)  # the restart purges defusion; put the test session's modules back


def test_restart_unsupported_without_scripts(restart_env):
    srv, app, order = restart_env
    r = call("restart_addin")
    assert r["error"]["code"] == "UNSUPPORTED" and not ImmediateTimer.created


def test_restart_not_found(restart_env):
    srv, app, order = restart_env
    app.scripts = FakeScripts(None)
    app.scripts.itemsByName = lambda name: []
    r = call("restart_addin")
    assert r["error"]["code"] == "NOT_FOUND" and not ImmediateTimer.created and srv._PENDING is None


def test_restart_locates_by_name_when_path_fails(restart_env):
    srv, app, order = restart_env
    app.scripts = FakeScripts(FakeScript(order), by_path=False)
    r = call("restart_addin")
    assert r["ok"] and r["result"]["addin"] == "De-Fusion"
    assert wait_for(lambda: order and order[-1] == "run")


def test_restart_sequence_and_single_schedule(restart_env):
    srv, app, order = restart_env
    app.scripts = FakeScripts(FakeScript(order))
    first = call("restart_addin")["result"]
    assert first["scheduled"] and first["already_scheduled"] is False and first["delay_ms"] == 750
    second = call("restart_addin")["result"]
    assert second["already_scheduled"] is True
    assert len(ImmediateTimer.created) == 1 and ImmediateTimer.created[0].delay == 0.75
    assert ImmediateTimer.created[0].daemon is True
    assert wait_for(lambda: order[-1:] == ["run"])
    assert order == ["stop", "unregister", "run"]
    assert not [m for m in sys.modules if m == "defusion" or m.startswith("defusion.")]


def test_restart_failing_stop_is_logged_and_run_still_happens(restart_env, caplog):
    srv, app, order = restart_env
    app.scripts = FakeScripts(FakeScript(order, stop_raises=True))
    with caplog.at_level("ERROR", logger="defusion"):
        assert call("restart_addin")["ok"]
        assert wait_for(lambda: order[-1:] == ["run"])
    assert order == ["stop", "unregister", "run"]
    assert any("script.stop" in r.getMessage() for r in caplog.records)


def test_restart_undelivered_event_clears_pending(restart_env):
    srv, app, order = restart_env
    app.scripts = FakeScripts(FakeScript(order))
    real_fire = app.fireCustomEvent
    app.fireCustomEvent = lambda event_id, info="": False if event_id.startswith("defusion.restart") else real_fire(event_id, info)
    assert call("restart_addin")["ok"]
    assert wait_for(lambda: srv._PENDING is None, 10)
    assert order == []


# --- run_text_command ------------------------------------------------------------------------


def test_text_command_requires_confirm(srv, dispatcher, fake_app):
    r = call("run_text_command", {"command": "Foo.Bar"})
    assert r["error"]["code"] == "CONFIRM_REQUIRED"
    assert fake_app.commands == []


def test_text_command_runs_and_returns_output(srv, dispatcher, fake_app):
    fake_app.text_command_results["Foo.Bar"] = "done"
    r = call("run_text_command", {"command": "Foo.Bar", "confirm": True})
    assert r["ok"], r
    assert r["result"] == {"command": "Foo.Bar", "output": "done"}
    assert fake_app.commands == ["Foo.Bar"]  # no undo-group transaction around it


def test_text_command_failure_maps_to_error(srv, dispatcher, fake_app):
    fake_app.text_command_errors["Boom"] = RuntimeError("bad")
    r = call("run_text_command", {"command": "Boom", "confirm": True})
    assert r["error"]["code"] == "TEXT_COMMAND_FAILED"


def test_text_command_empty_is_invalid(srv, dispatcher, fake_app):
    r = call("run_text_command", {"command": "  ", "confirm": True})
    assert r["error"]["code"] == "INVALID_PARAMS" and fake_app.commands == []
