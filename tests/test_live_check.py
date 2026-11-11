"""scripts/live_check.py against the in-process app with fake actions and fake scenarios."""
import asyncio
import socket
import sys
import time
import types as pytypes
from typing import Any
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import live_check  # noqa: E402

from adsk import core  # noqa: E402
from defusion import auth, config, server  # noqa: E402
from defusion.actions import action  # noqa: E402

CALLS = []


async def passing(session):
    env = await live_check.call(session, "model", "peek", {})
    assert env["ok"] is True


async def failing(session):
    raise AssertionError("boom")


async def other(session):
    pass


@pytest.fixture
def live(tmp_path, monkeypatch, registry, fake_design, fake_app):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.delenv("DEFUSION_TOKEN", raising=False)
    tools = pytypes.ModuleType("defusion.tools")
    tools.TOOL_MODULES = ("fake", "other", "empty")
    fake = pytypes.ModuleType("defusion.tools.fake")
    fake.LIVE_SCENARIOS = [("good", "model.peek", passing), ("bad", "model.make", failing)]
    oth = pytypes.ModuleType("defusion.tools.other")
    oth.LIVE_SCENARIOS = [("alone", "document.create", other)]
    monkeypatch.setitem(sys.modules, "defusion.tools", tools)
    monkeypatch.setitem(sys.modules, "defusion.tools.fake", fake)
    monkeypatch.setitem(sys.modules, "defusion.tools.other", oth)
    monkeypatch.setitem(sys.modules, "defusion.tools.empty", pytypes.ModuleType("defusion.tools.empty"))
    CALLS.clear()

    @action("model", access="read")
    def peek(ctx) -> dict:
        """Peek."""
        return {"bodies": len(list(ctx.component().bRepBodies))}

    @action("model", access="write")
    def make(ctx) -> dict:
        """Make."""
        return {}

    @action("model", access="write")
    def extrude(ctx, profile: dict[str, Any], distance: float | str, operation: str = "new_body") -> dict:
        """Extrude."""
        CALLS.append(("extrude", profile, distance, operation))
        return {"bodies": [{"type": "BRepBody", "name": "Body1", "token": "t1"}]}

    @action("model", access="write")
    def rename(ctx, target: dict[str, Any], name: str) -> dict:
        """Rename."""
        CALLS.append(("rename", target, name))
        return {}

    @action("sketch", access="write")
    def create(ctx, plane: str) -> dict:
        """Create."""
        CALLS.append(("sketch.create", plane))
        return {"sketch": {"type": "Sketch", "name": "S", "token": "s1"}}

    @action("sketch", access="write")
    def rectangle(ctx, sketch: dict[str, Any], mode: str, p1: list[float], p2: list[float]) -> dict:
        """Rectangle."""
        CALLS.append(("rectangle", sketch, mode, p1, p2))
        return {}

    @action("document", access="write")
    def create(ctx) -> dict:
        """Create a document."""
        CALLS.append(("document.create",))
        return {}

    @action("document", access="destructive")
    def close(ctx) -> dict:
        """Close."""
        CALLS.append(("document.close",))
        return {}

    @action("script", access="write")
    def run(ctx) -> dict:
        """Run."""
        return {}

    @action("server", access="read")
    def status(ctx) -> dict:
        """Status."""
        return {}

    @action("server", access="read")
    def job_status(ctx) -> dict:
        """Job status."""
        return {}

    (tmp_path / "config.toml").write_text("port = %d\n" % port)
    live_check.configure(port, None)
    core.reset_calls()
    server.start()
    end = time.time() + 5
    while time.time() < end and server.state() != "running":
        time.sleep(0.02)
    assert server.state() == "running"
    yield pytypes.SimpleNamespace(port=port, dir=tmp_path)
    server.stop()
    auth.set_token(None)
    live_check.configure(None, None)
    config.load()


def test_pass_and_fail_lines_exit_1_and_scratch_lifecycle(live, capsys):
    assert live_check.main(["--port", str(live.port)]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out == ["PASS good (model.peek)", "FAIL bad (model.make): boom", "PASS alone (document.create)"]
    assert [c[0] for c in CALLS] == ["document.create", "document.close"]


def test_only_tool_and_tag(live, capsys):
    assert live_check.main(["--port", str(live.port), "--only", "other"]) == 0
    assert capsys.readouterr().out == "PASS alone (document.create)\n"
    assert live_check.main(["--port", str(live.port), "--only", "model.peek"]) == 0
    assert capsys.readouterr().out == "PASS good (model.peek)\n"


def test_only_matching_nothing_fails(live, capsys):
    assert live_check.main(["--port", str(live.port), "--only", "empty"]) == 1
    assert "no live scenarios" in capsys.readouterr().err


def test_port_defaults_to_config_port(live, capsys):
    assert live_check.main(["--only", "other"]) == 0
    assert "PASS alone" in capsys.readouterr().out


def test_profile_readonly_session_denies_writes(live, capsys):
    async def writes(session):
        env = await live_check.call(session, "model", "make", {})
        assert env["error"]["code"] == "NOT_ALLOWED"

    sys.modules["defusion.tools.other"].LIVE_SCENARIOS = [("ro", "model.make", writes)]
    assert live_check.main(["--port", str(live.port), "--profile", "readonly", "--only", "other"]) == 0
    assert capsys.readouterr().out == "PASS ro (model.make)\n"


def test_token_from_env_only(live, capsys, monkeypatch):
    (live.dir / "config.toml").write_text('port = %d\ntoken = "t0k"\n' % live.port)
    server.stop()
    server.start()
    end = time.time() + 5
    while time.time() < end and server.state() != "running":
        time.sleep(0.02)
    assert live_check.main(["--port", str(live.port), "--only", "other"]) == 2
    capsys.readouterr()
    monkeypatch.setenv("DEFUSION_TOKEN", "t0k")
    assert live_check.main(["--port", str(live.port), "--only", "other"]) == 0
    assert "PASS alone" in capsys.readouterr().out


def test_token_flag_rejected(live, capsys):
    assert live_check.main(["--token", "x", "--only", "other"]) == 2
    assert "DEFUSION_TOKEN" in capsys.readouterr().err
    assert live_check.main(["--token=x"]) == 2


def test_unreachable_exits_2(tmp_path, monkeypatch, capsys):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setitem(sys.modules, "defusion.tools", pytypes.ModuleType("defusion.tools"))
    sys.modules["defusion.tools"].TOOL_MODULES = ()
    assert live_check.main(["--port", str(port)]) == 2
    assert "unreachable" in capsys.readouterr().err


def test_help_lists_flags_and_exit_codes(capsys):
    with pytest.raises(SystemExit) as e:
        live_check.main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert all(x in out for x in ("--profile", "--port", "--only", "DEFUSION_TOKEN", "1 any failure", "2 server unreachable"))


def test_open_session_readonly_lists_only_read_actions(live):
    async def go():
        async with live_check.open_session("readonly") as s:
            tools = {t.name: t for t in (await s.list_tools()).tools}
        return {n: t.input_schema["properties"]["action"]["enum"] for n, t in tools.items()}

    enums = asyncio.run(go())
    assert enums["model"] == ["help", "peek"] and "document" not in enums


def test_box_creates_body(live):
    async def go():
        async with live_check.open_session() as s:
            return await live_check.box(s, 10, 20, "5 mm", name="Plate")

    body = asyncio.run(go())
    assert body["name"] == "Body1"
    kinds = [c[0] for c in CALLS]
    assert kinds == ["sketch.create", "rectangle", "extrude", "rename"]
    assert CALLS[0][1] == "XY" and CALLS[1][2:] == ("two_point", [0, 0], [10, 20])
    assert CALLS[2][1] == {"sketch": CALLS[1][1], "index": 0} and CALLS[2][2:] == ("5 mm", "new_body")
    assert CALLS[3][1:] == (body, "Plate")


def test_freeze_leftovers_fixes_only_editable_live_files(monkeypatch, capsys):
    files = {"f1": ("defusion-live-1", False), "f2": ("defusion-live-2", True), "f3": ("theirs", False)}
    calls = []

    async def fake_call(session, tool, action, params, units=None):
        calls.append((action, params.get("file") or params.get("document")))
        res = {"list_projects": {"items": [{"name": "AI Test", "active": True}]},
               "list_folders": {"items": [{"kind": "file", "id": k, "name": n, "path": "AI Test/" + n}
                                          for k, (n, _) in files.items()],
                             "next_offset": None},
               "datafile_get": {"read_only": files.get(params.get("file"), ("", True))[1]},
               "open": {"id": "doc"}, "set_read_only": {}, "close": {}}[action]
        return {"ok": True, "result": res}

    monkeypatch.setattr(live_check, "call", fake_call)
    assert asyncio.run(live_check.freeze_leftovers(None)) is True
    assert capsys.readouterr().out == "FIXED editable leftover AI Test/defusion-live-1\n"
    assert [c for c in calls if c[0] in ("open", "set_read_only", "close")] == [
        ("open", "f1"), ("set_read_only", "doc"), ("close", "doc")]


def test_freeze_leftovers_reports_failed_fix(monkeypatch, capsys):
    async def fake_call(session, tool, action, params, units=None):
        if action == "open":
            return {"ok": False, "error": {"message": "no"}}
        res = {"list_projects": {"items": [{"name": "P", "active": True}]},
               "list_folders": {"items": [{"kind": "file", "id": "a", "name": "defusion-live-9", "path": "P/defusion-live-9"}],
                             "next_offset": None},
               "datafile_get": {"read_only": False}}[action]
        return {"ok": True, "result": res}

    monkeypatch.setattr(live_check, "call", fake_call)
    assert asyncio.run(live_check.freeze_leftovers(None)) is False
    assert capsys.readouterr().out.startswith("FAIL editable leftover")
