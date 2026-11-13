import json
import logging
import sys
from pathlib import Path

import pytest
from adsk import core
from test_entry import entry, fake_server  # noqa: F401  (entry is a fixture)

from defusion import clients, config, setup_ui

GEN, OWN, NONE = setup_ui.AUTH


@pytest.fixture
def env(tmp_path, monkeypatch, fake_app):
    """A temp HOME and config dir, the fake UI with the add-ins panel, and the add-in button added."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("COPILOT_HOME", "CLINE_MCP_SETTINGS_PATH"):
        monkeypatch.delenv(var, raising=False)
    cfg_dir = home / ".defusion"
    monkeypatch.setattr(config, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(config, "CONFIG_PATH", cfg_dir / "config.toml")
    monkeypatch.setattr(setup_ui.shutil, "which", lambda name: "/usr/bin/npx")
    ui = fake_app.userInterface
    ui.allToolbarPanels.add_panel(setup_ui.PANEL_ID)
    setup_ui.add()
    yield home
    setup_ui.remove()


def ui():
    return core.Application.get().userInterface


def open_dialog(home=None, *ids):
    """Open the dialog after making the given clients detected (their config file exists)."""
    for cid in ids:
        c = next(c for c in clients.CLIENTS if c.id == cid)
        f = c.path(clients.platform())
        f.parent.mkdir(parents=True, exist_ok=True)
        if not f.exists():
            f.write_text("{}" if c.fmt == "json" else "")
    d = ui().commandDefinitions.itemById(setup_ui.CMD_ID)
    d.execute()
    return d.last_command


def box(cmd, input_id):
    return cmd.commandInputs.itemById(input_id)


def edit(cmd, input_id, value):
    inp = box(cmd, input_id)
    if input_id in ("auth", "mode"):
        inp.select(value)
    else:
        inp.value = value
    cmd.change(inp)


def result_box(input_id):
    return ui().commandDefinitions.itemById(setup_ui.RESULT_ID).last_command.commandInputs.itemById(input_id)


def untick_all_but(cmd, *ids):
    for c in clients.CLIENTS:
        if box(cmd, "client_" + c.id):
            box(cmd, "client_" + c.id).value = c.id in ids


def cursor_path(home):
    return home / ".cursor" / "mcp.json"


# --- REQ-SETUP-001: button, wiring ---
def controls():
    panel = ui().allToolbarPanels.itemById(setup_ui.PANEL_ID)
    return panel.controls.count


def test_add_remove_cycles(env):
    defs = ui().commandDefinitions
    assert controls() == 1 and defs.itemById(setup_ui.CMD_ID) and defs.itemById(setup_ui.RESULT_ID)
    setup_ui.remove()
    assert controls() == 0 and not defs.itemById(setup_ui.CMD_ID) and not defs.itemById(setup_ui.RESULT_ID)
    assert setup_ui._handlers == []
    setup_ui.add()
    setup_ui.add()  # a stale definition from a crashed run is replaced, not duplicated
    assert controls() == 1 and len(setup_ui._handlers) == 2
    setup_ui.remove()
    assert controls() == 0


def test_missing_panel_logs_and_skips(fake_app, caplog):
    with caplog.at_level(logging.ERROR, logger="defusion"):
        setup_ui.add()
    assert "not found" in caplog.text and not fake_app.userInterface.commandDefinitions.itemById(setup_ui.CMD_ID)


def test_run_starts_server_when_add_raises(entry, monkeypatch, tmp_path):
    calls = []
    fake_server(monkeypatch, start=lambda: calls.append("start"), stop=lambda: calls.append("stop"))

    def boom():
        raise RuntimeError("ui down")

    monkeypatch.setattr(setup_ui, "add", boom)
    entry.run(None)
    assert calls == ["start"] and "De-Fusion Setup button failed" in (tmp_path / "defusion.log").read_text()


def test_run_stop_cycle_through_entry(entry, monkeypatch, fake_app):
    fake_app.userInterface.allToolbarPanels.add_panel(setup_ui.PANEL_ID)
    fake_server(monkeypatch, start=lambda: None, stop=lambda: None)
    for _ in range(2):
        entry.run(None)
        assert controls() == 1
        entry.stop(None)
        assert controls() == 0 and not ui().commandDefinitions.itemById(setup_ui.CMD_ID)


def test_stop_removes_even_if_server_stop_raises(entry, monkeypatch, fake_app, tmp_path):
    from defusion import log as dlog

    dlog.setup(tmp_path, "INFO")
    fake_app.userInterface.allToolbarPanels.add_panel(setup_ui.PANEL_ID)
    setup_ui.add()

    def boom():
        raise RuntimeError("x")

    fake_server(monkeypatch, stop=boom)
    entry.stop(None)
    assert controls() == 0


# --- REQ-SETUP-002: defaults and validation ---
def test_defaults(env):
    cmd = open_dialog(env, "cursor")
    assert box(cmd, "port").value == "8642" and box(cmd, "folder").value == ""
    assert box(cmd, "auth").selectedItem.name == GEN and box(cmd, "mode").selectedItem.name == "Apply"
    assert box(cmd, "own_token").isPassword
    assert cmd.isOKButtonEnabled and cmd.okButtonText == "Apply"
    assert box(cmd, "own_token").isVisible is False and box(cmd, "gen_token").isVisible
    present = {c.id: box(cmd, "client_" + c.id) for c in clients.CLIENTS if box(cmd, "client_" + c.id)}
    assert list(present) == ["cursor"] and present["cursor"].value is True and present["cursor"].name == "Cursor"


def test_no_client_detected(env):
    cmd = open_dialog()
    assert not any(box(cmd, "client_" + c.id) for c in clients.CLIENTS)
    assert cmd.isOKButtonEnabled is False
    edit(cmd, "mode", "Remove")
    assert cmd.isOKButtonEnabled is False
    edit(cmd, "mode", "Apply")
    edit(cmd, "port", "9000")
    assert cmd.isOKButtonEnabled is False
    edit(cmd, "port", "abc")
    assert not cmd.isOKButtonEnabled


def test_existing_config_token_prefilled_and_unchanged(env):
    config.CONFIG_PATH.parent.mkdir(parents=True)
    config.CONFIG_PATH.write_text('port = 9001\ntoken = "abc-1"\nfolder = "P/F"\n')
    cmd = open_dialog(env, "cursor")
    assert box(cmd, "auth").selectedItem.name == OWN and box(cmd, "own_token").value == "abc-1"
    assert box(cmd, "port").value == "9001" and box(cmd, "folder").value == "P/F"
    assert cmd.isOKButtonEnabled
    cmd.ok()
    assert "config.toml: unchanged" in result_box("results").text


@pytest.mark.parametrize("field,value", [
    ("port", "80"),
    ("port", "abc"),
    ("port", "65536"),
    ("folder", "/"),
    ("folder", "x" * 513),
    ("port", "1" * 5000),
])
def test_invalid_fields_disable_apply(env, field, value):
    cmd = open_dialog(env, "cursor")
    edit(cmd, field, value)
    assert not cmd.isOKButtonEnabled
    edit(cmd, field, "8642" if field == "port" else "")
    assert cmd.isOKButtonEnabled


@pytest.mark.parametrize("token", ["   ", " abc", "a\nb", 'a"b', "", "a b", "a\n"])
def test_invalid_own_tokens(env, token):
    cmd = open_dialog(env, "cursor")
    edit(cmd, "auth", OWN)
    edit(cmd, "own_token", token)
    assert not cmd.isOKButtonEnabled


def test_token_and_folder_limits(env):
    cmd = open_dialog(env, "cursor")
    edit(cmd, "auth", OWN)
    edit(cmd, "own_token", "a" * 256)
    assert cmd.isOKButtonEnabled
    edit(cmd, "own_token", "a" * 257)
    assert not cmd.isOKButtonEnabled
    edit(cmd, "own_token", "abc-123_")
    assert cmd.isOKButtonEnabled
    edit(cmd, "own_token", "ab==")
    assert cmd.isOKButtonEnabled
    edit(cmd, "folder", "x" * 512)
    assert cmd.isOKButtonEnabled
    edit(cmd, "folder", "x" * 513)
    assert not cmd.isOKButtonEnabled


def test_remove_mode_skips_validation(env):
    cmd = open_dialog(env, "cursor")
    edit(cmd, "port", "abc")
    assert not cmd.isOKButtonEnabled
    edit(cmd, "mode", "Remove")
    assert cmd.isOKButtonEnabled and cmd.okButtonText == "Remove"
    assert not box(cmd, "gen_token").isVisible
    edit(cmd, "mode", "Apply")
    assert not cmd.isOKButtonEnabled


def test_unreadable_config_does_not_disable_ok(env):
    config.CONFIG_PATH.parent.mkdir(parents=True)
    config.CONFIG_PATH.write_text("[broken\n")
    cmd = open_dialog(env, "cursor")
    assert cmd.isOKButtonEnabled
    cmd.ok()
    assert "config.toml: skipped: " in result_box("results").text


def test_reads_once_per_session(env, monkeypatch):
    reads = []
    real = clients.read_path
    monkeypatch.setattr(clients, "read_path", lambda p, b: reads.append(p) or real(p, b))
    cmd = open_dialog(env, "cursor", "zed")
    n = len(reads)
    assert n == 3
    for i in range(20):
        edit(cmd, "port", str(9000 + i))
    assert len(reads) == n


# --- REQ-SETUP-010: tokens, export, warnings ---
def test_generated_token_per_session(env):
    a = open_dialog()
    token = box(a, "gen_token").text
    assert len(token) == 43
    edit(a, "port", "9000")
    edit(a, "mode", "Remove")
    edit(a, "mode", "Apply")
    assert box(a, "gen_token").text == token
    assert box(open_dialog(), "gen_token").text != token


def test_export_line_and_warnings(env):
    cmd = open_dialog(env, "cursor", "codex")
    untick_all_but(cmd, "cursor")
    cmd.change(box(cmd, "client_cursor"))
    assert box(cmd, "export").text == ""
    box(cmd, "client_codex").value = True
    cmd.change(box(cmd, "client_codex"))
    token = box(cmd, "gen_token").text
    assert box(cmd, "export").text == "export DEFUSION_TOKEN=" + token
    assert "Codex ignores" in box(cmd, "warnings").text
    edit(cmd, "auth", NONE)
    assert box(cmd, "export").text == "" and box(cmd, "warnings").text == ""
    edit(cmd, "auth", GEN)
    edit(cmd, "mode", "Remove")
    assert box(cmd, "export").text == "" and box(cmd, "warnings").text == ""


def test_npx_warning(env, monkeypatch):
    monkeypatch.setattr(setup_ui.shutil, "which", lambda name: None)
    cmd = open_dialog(env, "cursor", "claude-desktop")
    untick_all_but(cmd, "cursor")
    cmd.change(box(cmd, "client_cursor"))
    assert box(cmd, "warnings").text == ""
    box(cmd, "client_claude-desktop").value = True
    cmd.change(box(cmd, "client_claude-desktop"))
    assert "Node.js" in box(cmd, "warnings").text
    edit(cmd, "mode", "Remove")
    assert box(cmd, "warnings").text == ""
    monkeypatch.setattr(setup_ui.shutil, "which", lambda name: "/usr/bin/npx")
    edit(cmd, "mode", "Apply")
    assert "Node.js" not in box(cmd, "warnings").text


def test_no_shell_profile_writes():
    src = Path(setup_ui.__file__).read_text() + Path(clients.__file__).read_text()
    for name in (".zshrc", ".bashrc", ".profile", "winreg", "setx"):
        assert name not in src


# --- Execute and result (REQ-SETUP-009) ---
def test_execute_applies_and_shows_result(env):
    cmd = open_dialog(env, "cursor", "codex", "zed")
    untick_all_but(cmd, "cursor", "codex")
    edit(cmd, "auth", OWN)
    edit(cmd, "own_token", "tok-1")
    edit(cmd, "folder", "My Project/Parts")
    cmd.ok()
    data = json.loads(cursor_path(env).read_text())["mcpServers"]["de-fusion"]
    assert data["url"] == "http://127.0.0.1:8642/mcp?folder=My%20Project%2FParts" and data["headers"] == {"Authorization": "Bearer tok-1"}
    assert 'token = "tok-1"' in config.CONFIG_PATH.read_text() and config.CONFIG_PATH.stat().st_mode & 0o777 == 0o600
    lines = result_box("results").text.splitlines()
    assert lines[:3] == ["config.toml: ok", "Codex CLI: ok", "Cursor: ok"]
    assert (env / ".config" / "zed" / "settings.json").read_text() == "{}" and "Zed" not in result_box("results").text
    assert setup_ui.RESTART in lines and "Add-Ins" in setup_ui.RESTART and "restart_addin" not in setup_ui.RESTART
    assert result_box("export").text == "export DEFUSION_TOKEN=tok-1" and result_box("token").text == "tok-1"
    assert "Codex ignores" in result_box("warnings").text
    assert ui().message_boxes == []
    assert ui().commandDefinitions.itemById(setup_ui.RESULT_ID).last_command.isCancelButtonVisible is False


def test_result_second_run_changed_port_lists_backup(env):
    cmd = open_dialog(env, "cursor")
    untick_all_but(cmd, "cursor")
    edit(cmd, "auth", NONE)
    cmd.ok()
    assert result_box("export") is None and result_box("token") is None
    cmd = open_dialog(env, "cursor")
    untick_all_but(cmd, "cursor")
    edit(cmd, "auth", NONE)
    edit(cmd, "port", "8643")
    cmd.ok()
    text = result_box("results").text
    assert "config.toml: ok" in text and "Cursor: ok" in text
    assert "backup: %s" % next(config.CONFIG_PATH.parent.glob("config.toml.defusion-bak-*")) in text


def test_remove_mode_execute(env):
    cmd = open_dialog(env, "cursor", "zed")
    untick_all_but(cmd, "cursor", "zed")
    edit(cmd, "auth", NONE)
    cmd.ok()
    cfg_before = config.CONFIG_PATH.read_bytes()
    cmd = open_dialog(env, "cursor", "windsurf")
    untick_all_but(cmd, "cursor", "windsurf")
    edit(cmd, "mode", "Remove")
    cmd.ok()
    lines = result_box("results").text.splitlines()
    assert lines[:2] == ["Cursor: ok", "Windsurf/Devin: not-present"]
    assert json.loads(cursor_path(env).read_text())["mcpServers"] == {}
    assert config.CONFIG_PATH.read_bytes() == cfg_before and result_box("export") is None and result_box("token") is None


def test_remove_ignores_invalid_config(env):
    cmd = open_dialog(env, "cursor")
    untick_all_but(cmd, "cursor")
    edit(cmd, "auth", NONE)
    cmd.ok()
    config.CONFIG_PATH.write_text("[broken\n")
    cmd = open_dialog(env, "cursor")
    untick_all_but(cmd, "cursor")
    edit(cmd, "mode", "Remove")
    cmd.ok()
    text = result_box("results").text
    assert "Cursor: ok" in text and "config.toml not updated" not in text
    assert json.loads(cursor_path(env).read_text())["mcpServers"] == {}


def test_changed_since_dialog_opened(env):
    cursor_path(env).parent.mkdir()
    cursor_path(env).write_text("{}")
    cmd = open_dialog(env, "cursor")
    untick_all_but(cmd, "cursor")
    cursor_path(env).write_text('{"mutated": true}')
    cmd.ok()
    assert "Cursor: skipped: " in result_box("results").text and "changed since the dialog opened; reopen the dialog" in result_box("results").text
    assert cursor_path(env).read_text() == '{"mutated": true}'
    assert not list(cursor_path(env).parent.glob("*bak*"))


@pytest.mark.parametrize("auth", [GEN, NONE, OWN])
def test_invalid_config_blocks_all_clients(env, auth):
    config.CONFIG_PATH.parent.mkdir(parents=True)
    config.CONFIG_PATH.write_text("[broken\n")
    cursor_path(env).parent.mkdir()
    cursor_path(env).write_text("{}")
    cmd = open_dialog(env, "cursor", "codex")
    untick_all_but(cmd, "cursor", "codex")
    edit(cmd, "auth", auth)
    if auth == OWN:
        edit(cmd, "own_token", "abc")
    cmd.ok()
    lines = [l for l in result_box("results").text.splitlines() if not l.startswith("  ")]
    assert lines[0].startswith("config.toml: skipped: config.toml: ")
    assert lines[1:3] == ["Codex CLI: skipped: config.toml not updated; fix config.toml and Apply again",
                          "Cursor: skipped: config.toml not updated; fix config.toml and Apply again"]
    assert cursor_path(env).read_text() == "{}" and config.CONFIG_PATH.read_text() == "[broken\n"
    assert not list(cursor_path(env).parent.glob("*bak*")) and (env / ".codex" / "config.toml").read_text() == ""


def test_raising_client_does_not_stop_others(env, monkeypatch):
    real = clients.apply

    def apply(p):
        if "cursor" in str(p.path):
            raise RuntimeError("kaput")
        return real(p)

    monkeypatch.setattr(clients, "apply", apply)
    cmd = open_dialog(env, "cursor", "zed")
    untick_all_but(cmd, "cursor", "zed")
    edit(cmd, "auth", NONE)
    cmd.ok()
    lines = [l for l in result_box("results").text.splitlines() if not l.startswith("  ")]
    assert lines[1] == "Cursor: skipped: %s: kaput; Apply again" % cursor_path(env) and lines[2] == "Zed: ok"
    assert (env / ".config" / "zed" / "settings.json").exists()


def test_handler_exception_is_logged_not_raised(env, monkeypatch, caplog):
    monkeypatch.setattr(setup_ui, "_refresh", lambda *a: 1 / 0)
    with caplog.at_level(logging.ERROR, logger="defusion"):
        open_dialog()
    assert "handler failed" in caplog.text


def test_server_and_dispatch_do_not_reference_setup_ui():
    base = Path(setup_ui.__file__).parent
    for name in ("server.py", "dispatch.py"):
        assert "setup_ui" not in (base / name).read_text()


def test_identical_second_apply_unchanged_no_new_backup(env):
    for _ in range(2):
        cmd = open_dialog(env, "cursor")
        untick_all_but(cmd, "cursor")
        edit(cmd, "auth", OWN)
        edit(cmd, "own_token", "tok-1")
        cmd.ok()
    lines = result_box("results").text.splitlines()
    assert lines[:2] == ["config.toml: unchanged", "Cursor: unchanged"]
    assert "backup" not in result_box("results").text
    assert not list(config.CONFIG_PATH.parent.glob("*bak*")) and len(list(cursor_path(env).parent.glob("*bak*"))) == 1


def test_config_changed_since_dialog_opened_blocks_clients(env):
    config.CONFIG_PATH.parent.mkdir(parents=True)
    config.CONFIG_PATH.write_text("port = 8000\n")
    cmd = open_dialog(env, "cursor")
    untick_all_but(cmd, "cursor")
    config.CONFIG_PATH.write_text("port = 8001\n")
    cmd.ok()
    lines = result_box("results").text.splitlines()
    assert lines[0] == "config.toml: skipped: " + lines[0].split("skipped: ", 1)[1] and "changed since the dialog opened; reopen the dialog" in lines[0]
    assert lines[1].startswith("Cursor: skipped: config.toml not updated")
    assert config.CONFIG_PATH.read_text() == "port = 8001\n"
    assert not list(config.CONFIG_PATH.parent.glob("*bak*")) and cursor_path(env).read_text() == "{}"


def test_skipped_client_result_has_reason_but_no_snippet(env):
    cursor_path(env).parent.mkdir()
    cursor_path(env).write_text("{broken")
    cmd = open_dialog(env, "cursor")
    untick_all_but(cmd, "cursor")
    cmd.ok()
    text = result_box("results").text
    assert "Cursor: skipped: " in text and "mcpServers" not in text and '"de-fusion"' not in text

