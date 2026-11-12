import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import install  # noqa: E402

REL = "Autodesk Fusion.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3"


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("DEFUSION_CONFIG_DIR", raising=False)
    monkeypatch.setattr(install, "WINDOWS", False)
    root = tmp_path / "repo"
    (root / "De-Fusion" / "defusion").mkdir(parents=True)
    (root / "De-Fusion" / "defusion" / "config.py").write_text((ROOT / "De-Fusion/defusion/config.py").read_text())
    (root / "requirements.lock").write_text("x==1\n")
    monkeypatch.setattr(install, "ROOT", root)
    calls = []

    def fake_pip(cmd):
        calls.append(cmd)
        (root / "De-Fusion" / "lib").mkdir(parents=True, exist_ok=True)
        return 0, "ok"

    monkeypatch.setattr(install, "run_pip", fake_pip)

    def build(name, mtime):
        py = home / "Library/Application Support/Autodesk/webdeploy/production" / name / REL
        py.parent.mkdir(parents=True)
        py.write_text("")
        os.utime(py.parents[7], (mtime, mtime))
        return py

    class E:
        pass

    e = E()
    e.home, e.root, e.calls, e.build, e.pip = home, root, calls, build, fake_pip
    e.link = home / "Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/De-Fusion"
    e.cfg = home / ".defusion/config.toml"
    return e


def test_newest_build_chosen(env):
    env.build("old", 1000)
    new = env.build("new", 2000)
    assert install.main([]) == 0
    assert env.calls[0][0] == str(new)


def test_not_found_exits_2_with_paths(env, capsys):
    assert install.main([]) == 2
    assert "webdeploy/production/*" in capsys.readouterr().err
    assert not env.calls


def test_fusion_python_override_and_exact_pip_command(env):
    py = env.home / "mypy"
    py.write_text("")
    assert install.main(["--fusion-python", str(py)]) == 0
    assert env.calls == [[str(py), "-m", "pip", "install", "--upgrade", "--no-compile", "--target",
                          str(env.root / "De-Fusion/lib"), "-r", str(env.root / "requirements.lock")]]


def test_lib_deleted_before_pip(env):
    env.build("b", 1)
    stale = env.root / "De-Fusion/lib/stale.txt"
    stale.parent.mkdir()
    stale.write_text("")
    install.run_pip = lambda cmd: (0, "")
    assert install.main([]) == 0
    assert not stale.exists()


def test_link_created_and_existing_kept(env):
    env.build("b", 1)
    assert install.main([]) == 0
    assert env.link.is_symlink() and env.link.resolve() == (env.root / "De-Fusion").resolve()
    assert install.main([]) == 0
    assert env.link.is_symlink()


def test_directory_at_link_path_stops_with_exit_3(env, capsys):
    env.build("b", 1)
    env.link.mkdir(parents=True)
    assert install.main([]) == 3
    assert "%s exists and is not the De-Fusion link; remove it and rerun install.py" % env.link in capsys.readouterr().err
    assert not env.cfg.exists()


def test_pip_failure_exits_1_no_later_step(env, capsys):
    env.build("b", 1)
    install.run_pip = lambda cmd: (1, "boom from pip")
    assert install.main([]) == 1
    assert "boom from pip" in capsys.readouterr().err
    assert not env.link.is_symlink() and not env.cfg.exists()


def test_config_written_if_absent_not_overwritten(env):
    env.build("b", 1)
    install.main([])
    from_spec = env.cfg.read_text()
    assert 'log_level = "INFO"' in from_spec and "port = 8642" in from_spec
    env.cfg.write_text("port = 9000\n")
    install.main([])
    assert env.cfg.read_text() == "port = 9000\n"


def entry(out):
    start = out.index("{")
    return json.loads(out[start:out.index("\nAppend")])["mcpServers"]["de-fusion"]


def test_prints_entry_with_port_and_note(env, capsys):
    env.build("b", 1)
    env.cfg.parent.mkdir()
    env.cfg.write_text('port = 9001\n[units]\nlength = "mm"\n')
    install.main([])
    out = capsys.readouterr().out
    assert out.startswith("Add to your MCP client config:")
    assert entry(out) == {"type": "http", "url": "http://127.0.0.1:9001/mcp"}
    assert "Append ?profile=<items> to the url to select a profile." in out


def test_token_adds_authorization_header(env, capsys):
    env.build("b", 1)
    env.cfg.parent.mkdir()
    env.cfg.write_text('port = 8642\ntoken = "t0k"\n')
    install.main([])
    assert entry(capsys.readouterr().out)["headers"] == {"Authorization": "Bearer t0k"}


def test_uninstall_leaves_only_config(env):
    env.build("b", 1)
    install.main([])
    assert install.main(["--uninstall"]) == 0
    assert not env.link.is_symlink() and not (env.root / "De-Fusion/lib").exists()
    assert env.cfg.exists()


def test_uninstall_refuses_foreign_directory(env):
    env.link.mkdir(parents=True)
    assert install.main(["--uninstall"]) == 3
    assert env.link.is_dir()


def test_help_lists_flags_and_exit_codes(capsys):
    with pytest.raises(SystemExit) as e:
        install.main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "--uninstall" in out and "--fusion-python" in out and "1 pip failed" in out and "3 link conflict" in out
    assert "--folder" in out and "4 invalid --folder" in out


# --- --folder and config folder (REQ-CORE-038, REQ-CORE-039, REQ-CORE-053) ------------------------------

def test_folder_flag_sets_url_and_profile_hint(env, capsys):
    env.build("b", 1)
    assert install.main(["--folder", "My Project/Parts"]) == 0
    out = capsys.readouterr().out
    assert entry(out)["url"] == "http://127.0.0.1:8642/mcp?folder=My%20Project%2FParts"
    assert "Append &profile=<items> to the url to select a profile." in out
    assert env.cfg.read_text() == install.default_toml()  # --folder does not write config.toml


def test_folder_flag_is_normalised(env, capsys):
    env.build("b", 1)
    install.main(["--folder", "/P/A/"])
    assert entry(capsys.readouterr().out)["url"] == "http://127.0.0.1:8642/mcp?folder=P%2FA"


def test_config_folder_used_without_flag_and_flag_wins(env, capsys):
    env.build("b", 1)
    env.cfg.parent.mkdir()
    env.cfg.write_text('port = 8642\nfolder = "P"   # default limit\n')
    install.main([])
    assert entry(capsys.readouterr().out)["url"] == "http://127.0.0.1:8642/mcp?folder=P"
    install.main(["--folder", "Q/B"])
    assert entry(capsys.readouterr().out)["url"] == "http://127.0.0.1:8642/mcp?folder=Q%2FB"


def test_no_folder_url_has_no_query(env, capsys):
    env.build("b", 1)
    install.main([])
    out = capsys.readouterr().out
    assert entry(out)["url"] == "http://127.0.0.1:8642/mcp"
    assert "Append ?profile=<items> to the url to select a profile." in out


@pytest.mark.parametrize("line,shown", [('folder = " / "', " / "), ("folder = 5", "5")])
def test_invalid_config_folder_prints_url_without_query_and_names_error(env, capsys, line, shown):
    env.build("b", 1)
    env.cfg.parent.mkdir()
    env.cfg.write_text("port = 8642\n%s\n" % line)
    assert install.main([]) == 0
    cap = capsys.readouterr()
    assert entry(cap.out)["url"] == "http://127.0.0.1:8642/mcp"
    assert "Append ?profile=" in cap.out
    assert "config folder '%s': needs a project-qualified path like Project/Folder" % shown in cap.err


@pytest.mark.parametrize("value", [" / ", "", "//"])
def test_invalid_folder_flag_exits_4_before_anything(env, capsys, value):
    env.build("b", 1)
    assert install.main(["--folder", value]) == 4
    assert capsys.readouterr().err.strip() == "--folder needs a project-qualified path like Project/Folder"
    assert env.calls == [] and not env.link.is_symlink()
