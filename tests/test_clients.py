import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from defusion import clients, config

URL = "http://127.0.0.1:8642/mcp"
TOKEN = "tok-123"
BY_ID = {c.id: c for c in clients.CLIENTS}
JSON_IDS = [c.id for c in clients.CLIENTS if c.fmt == "json"]


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def plat(home):
    return clients.platform(env={}, home=home)


def path_of(cid, plat):
    return BY_ID[cid].path(plat)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def run_plan(cid, plat, token=TOKEN, url=URL):
    c = BY_ID[cid]
    return clients.plan(c, url, token, clients.read(c, plat))


def baks(path):
    return sorted(path.parent.glob(path.name + ".defusion-bak-*"))


def fixture_text(c):
    return json.dumps({"keep": [1, {"a": None}], c.key: {"other": {"url": "x"}}, "zlast": True}, indent=2) + "\n"


# --- table, detection, paths (REQ-SETUP-004) ---
def test_table_shape():
    assert len(clients.CLIENTS) == 10
    assert {c.id for c in clients.CLIENTS if c.terminal} == {"claude-code", "codex", "gemini"}


def test_detection_only_cursor(home, plat):
    write(home / ".cursor" / "mcp.json", "{}")
    assert [c.id for c in clients.CLIENTS if clients.detect(c, plat)] == ["cursor"]


def test_empty_app_directory_not_detected(home, plat):
    (home / ".cursor").mkdir()
    assert not clients.detect(BY_ID["cursor"], plat)


def test_env_overrides(home, tmp_path):
    cop, cli = tmp_path / "cop", tmp_path / "cli" / "s.json"
    p = clients.platform(env={"COPILOT_HOME": str(cop), "CLINE_MCP_SETTINGS_PATH": str(cli)}, home=home)
    assert path_of("copilot", p) == cop / "mcp-config.json" and path_of("cline-cli", p) == cli
    p = clients.platform(env={"COPILOT_HOME": ""}, home=home)
    assert path_of("copilot", p) == home / ".copilot" / "mcp-config.json"


def test_relative_override_skipped_and_nothing_created(home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = clients.platform(env={"CLINE_MCP_SETTINGS_PATH": "rel/x.json"}, home=home)
    c = BY_ID["cline-cli"]
    assert not clients.detect(c, p)
    pl = clients.plan(c, URL, TOKEN, clients.read(c, p))
    assert pl.action == "skip" and "CLINE_MCP_SETTINGS_PATH is not an absolute path" in pl.reason
    assert clients.apply(pl).status == "skipped"
    assert not (tmp_path / "rel").exists() and not any(home.iterdir())


def test_windows_paths_and_missing_appdata(home, tmp_path):
    appdata = tmp_path / "AppData"
    w = clients.Platform(True, {"APPDATA": str(appdata)}, home)
    assert path_of("zed", w) == appdata / "Zed" / "settings.json"
    assert path_of("windsurf", w) == appdata / "devin" / "mcp_config.json"
    assert path_of("claude-desktop", w) == appdata / "Claude" / "claude_desktop_config.json"
    assert path_of("cline", w) == appdata / "Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json"
    assert path_of("cursor", w) == home / ".cursor" / "mcp.json"
    n = clients.Platform(True, {}, home)
    c = BY_ID["zed"]
    assert path_of("zed", n) == "APPDATA is not set; set it or add the entry manually"
    assert not clients.detect(c, n) and clients.plan(c, URL, TOKEN, clients.read(c, n)).action == "skip"


def test_non_windows_uses_mac_paths(home):
    p = clients.Platform(False, {}, home)  # a Linux-injected platform
    assert path_of("claude-desktop", p) == home / "Library/Application Support/Claude/claude_desktop_config.json"
    assert path_of("zed", p) == home / ".config/zed/settings.json"


# --- url and entries (REQ-SETUP-006) ---
def test_build_url():
    assert clients.build_url(8642, None) == URL and clients.build_url(8642, "") == URL
    assert clients.build_url(9000, "My Project/Parts") == "http://127.0.0.1:9000/mcp?folder=My%20Project%2FParts"
    assert clients.build_url(8642, "/A/B/").endswith("?folder=A%2FB")


BEARER_ENV = {"Authorization": "Bearer ${DEFUSION_TOKEN}"}
BEARER = {"Authorization": "Bearer " + TOKEN}
GOLDEN = {
    "claude-code": {"type": "http", "url": URL, "headers": BEARER_ENV},
    "gemini": {"httpUrl": URL, "headers": BEARER_ENV},
    "cursor": {"url": URL, "headers": BEARER},
    "copilot": {"type": "http", "url": URL, "headers": BEARER, "tools": ["*"]},
    "windsurf": {"serverUrl": URL, "headers": BEARER},
    "zed": {"url": URL, "headers": BEARER},
    "cline": {"type": "streamableHttp", "url": URL, "headers": BEARER, "disabled": False},
    "cline-cli": {"type": "streamableHttp", "url": URL, "headers": BEARER, "disabled": False},
    "claude-desktop": {
        "command": "npx",
        "args": ["-y", clients.MCP_REMOTE, URL, "--allow-http", "--transport", "http-only", "--header", "Authorization:${AUTH_HEADER}"],
        "env": {"AUTH_HEADER": "Bearer " + TOKEN},
    },
}


@pytest.mark.parametrize("cid", JSON_IDS)
def test_golden_entry_with_token(cid, plat):
    c = BY_ID[cid]
    assert c.build(URL, TOKEN) == GOLDEN[cid]
    pl = run_plan(cid, plat)
    assert json.loads(pl.new)[c.key]["de-fusion"] == GOLDEN[cid]


@pytest.mark.parametrize("cid", list(BY_ID))
def test_no_auth_entries_have_no_token_parts(cid):
    text = json.dumps(BY_ID[cid].build(URL, None))
    assert not re.search(r"Authorization|--header|AUTH_HEADER|DEFUSION_TOKEN|env", text)


def test_desktop_pin():
    assert re.fullmatch(r"mcp-remote@\d+\.\d+\.\d+", GOLDEN["claude-desktop"]["args"][1])


# --- JSON merge (REQ-SETUP-005) ---
@pytest.mark.parametrize("cid", JSON_IDS)
def test_merge_roundtrip_backup_idempotent(cid, plat):
    c = BY_ID[cid]
    path = write(path_of(cid, plat), fixture_text(c))
    before = path.read_bytes()
    pl = run_plan(cid, plat)
    assert pl.action == "add"
    r = clients.apply(pl)
    assert r.status == "ok" and r.backup.read_bytes() == before
    got, orig = json.loads(path.read_text()), json.loads(before)
    assert list(got) == list(orig)  # key order preserved
    got[c.key].pop("de-fusion")
    assert got == orig
    again = run_plan(cid, plat)
    assert again.action == "unchanged" and clients.apply(again).status == "unchanged"
    assert len(baks(path)) == 1


def test_update_keeps_position(plat):
    path = write(path_of("cursor", plat), json.dumps({"mcpServers": {"a": 1, "de-fusion": {"url": "old"}, "b": 2}}))
    pl = run_plan("cursor", plat)
    assert pl.action == "update"
    clients.apply(pl)
    assert list(json.loads(path.read_text())["mcpServers"]) == ["a", "de-fusion", "b"]


def test_claude_json_only_mcpservers_changes(plat):
    src = {"projects": {"/x": {"mcpServers": {"p": 1}}}, "numStartups": 3, "mcpServers": {"o": {"command": "c"}}}
    path = write(path_of("claude-code", plat), json.dumps(src, indent=2))
    clients.apply(run_plan("claude-code", plat))
    got = json.loads(path.read_text())
    assert got["projects"] == src["projects"] and got["numStartups"] == 3 and set(got["mcpServers"]) == {"o", "de-fusion"}


def test_indent_and_trailing_newline_kept(plat):
    for indent, nl in ((4, "\n"), (2, "")):
        path = write(path_of("cursor", plat), json.dumps({"mcpServers": {}}, indent=indent) + nl)
        pl = run_plan("cursor", plat)
        assert pl.new.decode().splitlines()[1].startswith(" " * indent + '"') and not pl.new.decode().splitlines()[1].startswith(" " * (indent + 1))
        assert pl.new.decode().endswith("\n") == bool(nl)
        path.unlink()


def test_new_file_created_0600_with_token_and_parents(plat):
    pl = run_plan("zed", plat)
    assert pl.action == "add" and pl.original is None
    r = clients.apply(pl)
    path = path_of("zed", plat)
    assert r.status == "ok" and r.backup is None and path.stat().st_mode & 0o777 == 0o600
    assert path.read_text().endswith("}\n") and not baks(path)


def test_modes(plat):
    path = write(path_of("cursor", plat), "{}")
    path.chmod(0o644)
    clients.apply(run_plan("cursor", plat, token=None))
    assert path.stat().st_mode & 0o777 == 0o644
    clients.apply(run_plan("cursor", plat))
    assert path.stat().st_mode & 0o777 == 0o600


BAD = {
    "invalid": ("{not json", "not strict JSON"),
    "list": ('{"mcpServers": []}', 'top-level value or "mcpServers" has the wrong type'),
    "top-list": ("[]", "has the wrong type"),
    "dup": ('{"a": 1, "a": 2}', 'duplicate key "a"'),
    "nan": ('{"a": NaN}', "not strict JSON"),
}


@pytest.mark.parametrize("name", BAD)
def test_bad_files_skipped_untouched(name, plat):
    text, reason = BAD[name]
    path = write(path_of("cursor", plat), text)
    pl = run_plan("cursor", plat)
    assert pl.action == "skip" and reason in pl.reason and str(path) in pl.reason
    r = clients.apply(pl)
    assert r.status == "skipped" and path.read_text() == text and not baks(path)


def test_zed_jsonc_skipped(plat):
    path = write(path_of("zed", plat), '{\n  // theme\n  "theme": "x"\n}\n')
    pl = run_plan("zed", plat)
    assert pl.action == "skip" and "contains comments or is not strict JSON; add the entry manually" in pl.reason
    assert clients.apply(pl).status == "skipped" and "// theme" in path.read_text()


def test_size_bound(plat):
    path = path_of("cursor", plat)
    pad = '{"mcpServers": {}, "pad": "'
    body = pad + "x" * (clients.MAX_BYTES - len(pad) - 2) + '"}'
    write(path, body)
    assert path.stat().st_size == clients.MAX_BYTES
    assert run_plan("cursor", plat).action == "add"
    path.write_text(body + " ")
    pl = run_plan("cursor", plat)
    assert pl.action == "skip" and "larger than 10 MB" in pl.reason and path.read_text() == body + " "


def test_symlinked_file_and_parent(home, plat, tmp_path):
    real = write(tmp_path / "real.json", "{}")
    link = path_of("cursor", plat)
    link.parent.mkdir(parents=True)
    link.symlink_to(real)
    pl = run_plan("cursor", plat)
    assert pl.action == "skip" and "is a symlink; add the entry manually" in pl.reason
    assert clients.apply(pl).status == "skipped" and link.is_symlink() and real.read_text() == "{}"
    other = tmp_path / "dir"
    write(other / "mcp_config.json", "{}")
    (home / ".config").mkdir()
    (home / ".config" / "devin").symlink_to(other)
    pl = run_plan("windsurf", plat)
    assert pl.action == "skip" and "symlink" in pl.reason
    assert clients.apply(pl).status == "skipped" and (other / "mcp_config.json").read_text() == "{}"
    assert not list(other.glob("*bak*"))


def test_env_override_dir_symlink_above_base_ignored(tmp_path, home):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "ln").symlink_to(real)
    p = clients.platform(env={"COPILOT_HOME": str(tmp_path / "ln")}, home=home)
    assert clients.read(BY_ID["copilot"], p).skip is None


@pytest.mark.parametrize("which", ["copy2", "makedirs", "replace"])
def test_oserrors_skip_without_writing(which, plat, monkeypatch):
    path = write(path_of("cursor", plat), "{}")
    if which == "makedirs":
        path.unlink()
        path = path_of("windsurf", plat)
    cid = "windsurf" if which == "makedirs" else "cursor"
    pl = run_plan(cid, plat)

    def boom(*a, **k):
        raise OSError(28, "No space left on device")

    target = {"copy2": (clients.shutil, "copy2"), "makedirs": (os, "makedirs"), "replace": (os, "replace")}[which]
    monkeypatch.setattr(*target, boom)
    r = clients.apply(pl)
    assert r.status == "skipped" and "No space left on device" in r.reason and "Apply again" in r.reason
    assert (path.read_text() == "{}") if path.exists() else which == "makedirs"
    assert not list(path.parent.glob("tmp*")) and (which != "copy2" or not baks(path))


def test_changed_since_dialog_opened(plat):
    path = write(path_of("cursor", plat), "{}")
    pl = run_plan("cursor", plat)
    path.write_text('{"mutated": 1}')
    r = clients.apply(pl)
    assert r.status == "skipped" and "changed since the dialog opened; reopen the dialog" in r.reason
    assert path.read_text() == '{"mutated": 1}' and not baks(path)


def test_backup_name_collision(plat, monkeypatch):
    class D:
        @staticmethod
        def now(tz):
            import datetime
            return datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=tz)

    monkeypatch.setattr(clients, "datetime", D)
    path = write(path_of("cursor", plat), "{}")
    clients.apply(run_plan("cursor", plat))
    path.write_text("{}")
    clients.apply(run_plan("cursor", plat))
    assert {b.name.split("bak-")[1] for b in baks(path)} == {"20260102T030405Z", "20260102T030405Z-1"}


# --- remove (REQ-SETUP-008) ---
@pytest.mark.parametrize("cid", JSON_IDS)
def test_merge_then_remove_restores_content(cid, plat):
    c = BY_ID[cid]
    path = write(path_of(cid, plat), fixture_text(c))
    orig = json.loads(path.read_text())
    clients.apply(run_plan(cid, plat))
    merged = path.read_bytes()
    pl = clients.remove_plan(c, clients.read(c, plat))
    r = clients.apply(pl)
    assert pl.action == "remove" and r.status == "ok"
    assert json.loads(path.read_text()) == orig
    assert r.backup.read_bytes() == merged and len(baks(path)) == 2


def test_remove_not_present_no_write(plat):
    c = BY_ID["cursor"]
    path = write(path_of("cursor", plat), fixture_text(c))
    before = path.read_bytes()
    pl = clients.remove_plan(c, clients.read(c, plat))
    assert pl.action == "not-present" and clients.apply(pl).status == "not-present"
    assert path.read_bytes() == before and not baks(path)
    absent = clients.remove_plan(BY_ID["zed"], clients.read(BY_ID["zed"], plat))
    assert absent.action == "not-present" and not path_of("zed", plat).exists()


# --- Codex TOML (REQ-SETUP-006/007) ---
CODEX = """\
# my codex
model = "o3"

[mcp_servers.other]
command = "x"

[mcp_servers.defusion]
url = "http://old"

[mcp_servers.defusion.env]
A = "b"

[tools]
web = true
"""


def codex_plan(plat, text=None, token=TOKEN, url=URL):
    if text is not None:
        write(path_of("codex", plat), text)
    return run_plan("codex", plat, token=token, url=url)


def test_codex_block_in_middle_exact_bytes(plat):
    pl = codex_plan(plat, CODEX)
    block = '[mcp_servers.defusion]\nurl = "%s"\nbearer_token_env_var = "DEFUSION_TOKEN"\n' % URL
    expect = CODEX.replace('[mcp_servers.defusion]\nurl = "http://old"\n\n[mcp_servers.defusion.env]\nA = "b"\n\n', "") + "\n" + block
    assert pl.action == "update" and pl.new.decode() == expect
    r = clients.apply(pl)
    assert r.backup.read_text() == CODEX
    data = tomllib.loads(path_of("codex", plat).read_text())
    assert data["mcp_servers"]["other"] == {"command": "x"} and data["tools"] == {"web": True}


def test_codex_idempotent_and_remove(plat):
    path = path_of("codex", plat)
    base = '# c\nmodel = "o3"\n\n[mcp_servers.other]\ncommand = "x"\n'
    clients.apply(codex_plan(plat, base))
    assert codex_plan(plat).action == "unchanged"
    c = BY_ID["codex"]
    merged = path.read_bytes()
    pl = clients.remove_plan(c, clients.read(c, plat))
    r = clients.apply(pl)
    assert pl.action == "remove" and r.status == "ok"
    assert r.backup.read_bytes() == merged
    assert tomllib.loads(path.read_text()) == tomllib.loads(base)
    assert clients.remove_plan(c, clients.read(c, plat)).action == "not-present"


def test_codex_remove_middle_exact_bytes(plat):
    write(path_of("codex", plat), CODEX)
    c = BY_ID["codex"]
    pl = clients.remove_plan(c, clients.read(c, plat))
    assert pl.new.decode() == '# my codex\nmodel = "o3"\n\n[mcp_servers.other]\ncommand = "x"\n\n[tools]\nweb = true\n'


def test_codex_new_file_and_no_auth(plat):
    pl = codex_plan(plat, token=None)
    assert pl.action == "add" and pl.new.decode() == '[mcp_servers.defusion]\nurl = "%s"\n' % URL


def test_codex_similar_name_untouched(plat):
    text = '[mcp_servers.defusion2]\nurl = "u"\n'
    pl = codex_plan(plat, text)
    assert pl.action == "add" and pl.new.decode().startswith(text)


def test_codex_invalid_toml_and_failed_verification(plat):
    path = write(path_of("codex", plat), "[broken\n")
    pl = run_plan("codex", plat)
    assert pl.action == "skip" and "not valid TOML" in pl.reason and clients.apply(pl).status == "skipped"
    assert path.read_text() == "[broken\n" and not baks(path)
    # defusion defined inline under [mcp_servers]: appending a second definition is invalid TOML
    write(path, '[mcp_servers]\ndefusion = { url = "u" }\n')
    pl = run_plan("codex", plat)
    assert pl.action == "skip" and path.read_text() == '[mcp_servers]\ndefusion = { url = "u" }\n'


# --- config.toml (REQ-SETUP-003) ---
@pytest.fixture
def cfg(tmp_path, monkeypatch):
    d = tmp_path / "cfgdir" / ".defusion"
    monkeypatch.setattr(config, "CONFIG_DIR", d)
    monkeypatch.setattr(config, "CONFIG_PATH", d / "config.toml")
    return d / "config.toml"


def cplan(port=8642, folder="", token=None):
    return clients.plan_config(port, folder, token, clients.read_config())


RICH = """\
port = 8000                      # my port
token = "old"
log_level = "DEBUG"
# folder = "x"

[units]
length = "cm"   # keep me

[profiles]
mine = ["all"]
"""


def test_config_preserves_everything_else(cfg):
    write(cfg, RICH)
    pl = cplan(9000, "My Project/Parts", "newtok")
    out = pl.new.decode()
    assert pl.action == "update"
    expect = RICH.replace("port = 8000                      # my port", "port = 9000").replace('token = "old"', 'token = "newtok"')
    assert out == expect.replace('log_level = "DEBUG"\n', 'log_level = "DEBUG"\nfolder = "My Project/Parts"\n', 1)
    data = tomllib.loads(out)
    assert data["port"] == 9000 and data["folder"] == "My Project/Parts" and data["token"] == "newtok"
    assert data["units"] == {"length": "cm"} and data["profiles"] == {"mine": ["all"]}
    for kept in ('log_level = "DEBUG"', '# folder = "x"', 'length = "cm"   # keep me'):
        assert kept in out
    r = clients.apply(pl)
    assert r.status == "ok" and r.backup.read_text() == RICH


def test_config_none_removes_token_and_empty_folder(cfg):
    write(cfg, RICH.replace("[units]", 'folder = "A/B"\n\n[units]'))
    out = cplan(8000, "", None).new.decode()
    assert "token" not in tomllib.loads(out) and "folder" not in tomllib.loads(out)
    assert "# folder" in out and "# my port" in out  # untouched lines keep their comments


def test_config_folder_normalised(cfg):
    assert 'folder = "A/B"' in cplan(8642, "/A/B/").new.decode()


def test_config_missing_file_from_default(cfg):
    pl = cplan(8642, "", "t0k")
    assert pl.action == "add" and pl.original is None
    out = pl.new.decode()
    assert out.startswith("port = 8642") and 'token = "t0k"' in out and "[units]" in out
    r = clients.apply(pl)
    assert r.status == "ok" and r.backup is None and cfg.stat().st_mode & 0o777 == 0o600
    _, errors = config._parse(tomllib.loads(cfg.read_text()))
    assert not errors


def test_config_modes(cfg):
    write(cfg, "port = 8642\n")
    cfg.chmod(0o644)
    clients.apply(cplan(8643))
    assert cfg.stat().st_mode & 0o777 == 0o644
    clients.apply(cplan(8643, "", "abc"))
    assert cfg.stat().st_mode & 0o777 == 0o600


def test_config_second_apply_unchanged(cfg):
    clients.apply(cplan(8642, "", "abc"))
    for _ in range(2):
        pl = cplan(8642, "", "abc")
        assert pl.action == "unchanged" and clients.apply(pl).status == "unchanged"
    assert not baks(cfg)


def test_config_existing_equal_value_lines_untouched(cfg):
    write(cfg, 'port = 8642   # keep\ntoken = "abc" # also\n')
    assert cplan(8642, "", "abc").action == "unchanged"


@pytest.mark.parametrize("text", ["[broken\n", "port = 80\n", 'bogus = 1\n', '\xff'])
def test_config_invalid_existing_untouched(cfg, text):
    cfg.parent.mkdir(parents=True)
    cfg.write_bytes(text.encode("latin-1"))
    pl = cplan(8642, "", "abc")
    assert pl.action == "skip" and pl.reason.startswith("config.toml: ") and pl.reason.endswith("fix the file or delete it, then Apply again")
    assert clients.apply(pl).status == "skipped" and cfg.read_bytes() == text.encode("latin-1") and not baks(cfg)


def test_config_symlink_skipped(cfg, tmp_path):
    real = write(tmp_path / "real.toml", "port = 8642\n")
    cfg.parent.mkdir(parents=True)
    cfg.symlink_to(real)
    pl = cplan()
    assert pl.action == "skip" and "symlink" in pl.reason


def test_config_dir_symlink_skipped(cfg, tmp_path, monkeypatch):
    real = tmp_path / "realdir"
    write(real / "config.toml", "port = 8642\n")
    cfg.parent.parent.mkdir(parents=True)
    cfg.parent.symlink_to(real)
    pl = cplan()
    assert pl.action == "skip" and "symlink" in pl.reason
    assert clients.apply(pl).status == "skipped"
    assert (real / "config.toml").read_bytes() == b"port = 8642\n" and not list(real.glob("*bak*"))


def test_config_insert_without_trailing_newline(cfg):
    write(cfg, "log_level = \"INFO\"")
    data = tomllib.loads(cplan(9000, "", "t").new.decode())
    assert data == {"log_level": "INFO", "port": 9000, "token": "t"}


# --- module hygiene (REQ-SETUP-012) ---
def test_no_adsk_in_clients():
    src = Path(clients.__file__).read_text()
    assert "adsk" not in src
    out = subprocess.run([sys.executable, "-c", "import sys; import defusion.clients; sys.exit('adsk' in sys.modules)"],
                         env={**os.environ, "PYTHONPATH": str(Path(clients.__file__).parents[1])}, cwd="/")
    assert out.returncode == 0
