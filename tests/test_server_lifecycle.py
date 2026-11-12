import json
import socket
import sys
import threading
import time
import types as pytypes
import urllib.request

import pytest

from defusion import auth, config, dispatch, server
from defusion.actions import action


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def env(tmp_path, monkeypatch, registry, fake_design):
    """Config dir with a free port, test actions in an isolated registry, a stubbed defusion.tools."""
    port = free_port()
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.delenv("DEFUSION_TOKEN", raising=False)
    monkeypatch.setitem(sys.modules, "defusion.tools", pytypes.ModuleType("defusion.tools"))

    @action("model", access="read")
    def peek(ctx) -> dict:
        """Peek."""
        return {"thread": threading.get_ident()}

    @action("model", access="write")
    def make(ctx) -> dict:
        """Make."""
        return {}

    @action("script", access="write")
    def run(ctx) -> dict:
        """Run."""
        return {}

    @action("server", access="read")
    def status(ctx) -> dict:
        """Status."""
        return {"state": server.state()}

    @action("server", access="read")
    def job_status(ctx) -> dict:
        """Job."""
        return {}

    (tmp_path / "config.toml").write_text("port = %d\n" % port)
    yield pytypes.SimpleNamespace(port=port, dir=tmp_path)
    server.stop()
    auth.set_token(None)
    (tmp_path / "config.toml").unlink()
    config.load()


def wait_state(want, timeout=5):
    end = time.time() + timeout
    while time.time() < end and server.state() != want:
        time.sleep(0.02)
    return server.state()


def post(port, body, headers=None):
    req = urllib.request.Request(
        "http://127.0.0.1:%d/mcp" % port, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                 **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, e.read()


INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}


def test_state_before_start():
    assert server.state() in ("stopped",)


def test_start_returns_quickly_then_runs(env):
    t0 = time.monotonic()
    server.start()
    assert time.monotonic() - t0 < 1.0
    assert server.state() in ("starting", "running")
    assert wait_state("running") == "running"
    status, body = post(env.port, INIT)
    assert status == 200 and body["result"]["serverInfo"]["name"] == "De-Fusion"
    assert body["result"]["instructions"] == server.INSTRUCTIONS


def test_instructions_one_paragraph_with_required_statements():
    t = server.INSTRUCTIONS
    assert "\n" not in t and "action" in t and "params" in t and 'action="help"' in t and "unit" in t
    for sentence in (
        "Document file actions take a list of 1 to 50 entries and return one result per entry with ok and failed counts.",
        "A write to a saved read-only document makes it editable first unless params.preserve_read_only is true; "
        "document.save and document.save_as make the saved file read-only again.",
        "A folder=<Project/Path> query on the URL limits cloud files, folders and edits of saved documents to that "
        "folder and its subfolders.",
    ):
        assert sentence in t, sentence


def test_listens_on_loopback_only(env):
    server.start()
    wait_state("running")
    assert server._run["server"].config.host == "127.0.0.1"
    assert server._run["server"].servers[0].sockets[0].getsockname()[0] == "127.0.0.1"


def test_listening_socket_not_inherited_by_child_processes(env):
    server.start()
    wait_state("running")
    assert not server._run["server"].servers[0].sockets[0].get_inheritable()


def test_bind_failure_logs_and_fails(env, caplog):
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", env.port))
    blocker.listen()
    try:
        with caplog.at_level("ERROR", logger="defusion"):
            server.start()
        assert server.state() == "failed"
        msg = "Port %d in use; set port in %s and restart Fusion" % (env.port, config.CONFIG_PATH)
        assert any(msg in r.getMessage() for r in caplog.records)
    finally:
        blocker.close()


def test_stop_releases_port_and_dispatcher(env):
    server.start()
    wait_state("running")
    server.stop()
    assert server.state() == "stopped"
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", env.port), timeout=1)
    assert dispatch._event is None


def test_start_stop_start_serves_again(env):
    for _ in range(2):
        server.start()
        assert wait_state("running") == "running"
        assert post(env.port, INIT)[0] == 200
        server.stop()


def test_stop_fails_queued_items_with_not_executed(env, fake_app):
    server.start()
    wait_state("running")
    fake_app.hold()
    out = []
    t = threading.Thread(target=lambda: out.append(_call_quiet()))
    t.start()
    time.sleep(0.3)
    server.stop()
    t.join(5)
    fake_app.release()
    assert out and out[0] == "NOT_EXECUTED"


def _call_quiet():
    try:
        dispatch.run(lambda: 1, timeout=10)
    except Exception as exc:
        return getattr(exc, "code", repr(exc))


def test_config_applied_and_token_from_env(env, monkeypatch):
    monkeypatch.setenv("DEFUSION_TOKEN", "envtok")
    server.start()
    assert auth.token() == "envtok"
    wait_state("running")
    assert post(env.port, INIT)[0] == 401


def test_config_token_wins_over_env(env, monkeypatch):
    monkeypatch.setenv("DEFUSION_TOKEN", "envtok")
    (env.dir / "config.toml").write_text('port = %d\ntoken = "cfgtok"\n' % env.port)
    server.start()
    assert auth.token() == "cfgtok"
    wait_state("running")
    assert post(env.port, INIT, {"Authorization": "Bearer cfgtok"})[0] == 200
    assert post(env.port, INIT, {"Authorization": "Bearer envtok"})[0] == 401


def test_blank_env_token_is_config_error(env, monkeypatch):
    monkeypatch.setenv("DEFUSION_TOKEN", "  ")
    server.start()
    assert "DEFUSION_TOKEN" in config.load_error() and auth.token() is None


def test_unmatched_selector_is_config_error(env):
    (env.dir / "config.toml").write_text('port = %d\n[profiles]\nbad = ["model.nosuch*"]\n' % env.port)
    server.start()
    assert "model.nosuch*" in config.load_error()
    (env.dir / "config.toml").write_text('port = %d\ndefault_profile = ["cam"]\n' % env.port)
    server.stop()
    server.start()
    assert "cam" in config.load_error()


def test_valid_config_has_no_error(env):
    server.start()
    assert config.load_error() is None


def test_config_error_still_starts_on_default_port_settings(env):
    (env.dir / "config.toml").write_text("port = 1\n")
    server.start()
    assert config.load_error() and config.settings().port == 8642
    assert server.state() in ("starting", "running", "failed")  # 8642 may be taken on this machine


def test_log_file_written_in_config_dir(env):
    server.start()
    assert (env.dir / "defusion.log").exists()


def test_tools_call_reaches_pipeline_on_main_thread(env, fake_app):
    server.start()
    wait_state("running")
    body = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "model", "arguments": {"action": "peek"}}}
    status, out = post(env.port, body)
    assert status == 200
    env_ = out["result"]["structuredContent"]
    assert env_["ok"] is True and env_["result"]["thread"] == fake_app.main_thread_id
    # default profile ["all", "-script"]: script.run is NOT_ALLOWED
    body["params"] = {"name": "script", "arguments": {"action": "run"}}
    out = post(env.port, body)[1]["result"]
    assert out["isError"] is True and out["structuredContent"]["error"]["code"] == "NOT_ALLOWED"
