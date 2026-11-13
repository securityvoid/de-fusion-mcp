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
        if cmd[-1] == "--version":
            return 0, "pip 24.0 from /x/pip (python 3.12)\n"
        calls.append(cmd)
        target = Path(cmd[cmd.index("--target") + 1])
        target.mkdir(parents=True, exist_ok=True)
        (target / "pkg.txt").write_text("new")
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
    assert env.calls[0][env.calls[0].index("--python") + 1] == str(new)


def test_not_found_exits_2_with_paths(env, capsys):
    assert install.main([]) == 2
    assert "webdeploy/production/*" in capsys.readouterr().err
    assert not env.calls


def test_fusion_python_override_and_exact_pip_command(env):
    py = env.home / "mypy"
    py.write_text("")
    assert install.main(["--fusion-python", str(py)]) == 0
    tmp = env.root / ("De-Fusion/lib.tmp-%d" % os.getpid())
    assert env.calls == [[sys.executable, "-m", "pip", "--python", str(py), "install", "--upgrade", "--no-compile",
                          "--target", str(tmp), "-r", str(env.root / "requirements.lock")]]


def lib(env):
    return env.root / "De-Fusion/lib"


def test_success_replaces_lib_with_or_without_existing(env):
    env.build("b", 1)
    assert install.main([]) == 0
    assert (lib(env) / "pkg.txt").read_text() == "new"
    stale = lib(env) / "stale.txt"
    stale.write_text("")
    assert install.main([]) == 0
    assert not stale.exists() and (lib(env) / "pkg.txt").exists()
    assert not list((env.root / "De-Fusion").glob("lib.tmp-*"))


def selective_rmtree(monkeypatch, fail_on):
    real = install.shutil.rmtree

    def rm(path, *a, **k):
        if fail_on(Path(path)):
            raise OSError("locked")
        return real(path, *a, **k)

    monkeypatch.setattr(install.shutil, "rmtree", rm)


def test_stale_tmp_removed_before_pip(env):
    env.build("b", 1)
    stale = env.root / "De-Fusion/lib.tmp-1"
    stale.mkdir()
    assert install.main([]) == 0
    assert not stale.exists()


def test_stale_tmp_removal_error_ignored_pip_still_runs(env, monkeypatch):
    env.build("b", 1)
    (env.root / "De-Fusion/lib.tmp-1").mkdir()
    selective_rmtree(monkeypatch, lambda p: p.name == "lib.tmp-1")
    assert install.main([]) == 0
    assert len(env.calls) == 1


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


def test_pip_failure_exits_1_no_later_step_old_lib_and_tmp_untouched(env, capsys, monkeypatch):
    env.build("b", 1)
    assert install.main([]) == 0
    env.link.unlink()
    env.cfg.unlink()

    def fail(cmd):
        if cmd[-1] == "--version":
            return 0, "pip 24.0"
        (env.root / "De-Fusion" / ("lib.tmp-%d" % os.getpid())).mkdir()
        return 1, "boom from pip"

    monkeypatch.setattr(install, "run_pip", fail)
    assert install.main([]) == 1
    assert "boom from pip" in capsys.readouterr().err
    assert not env.link.is_symlink() and not env.cfg.exists()
    assert (lib(env) / "pkg.txt").read_text() == "new"
    assert not list((env.root / "De-Fusion").glob("lib.tmp-*"))


@pytest.mark.parametrize("probe,found", [((1, ""), "none"), ((0, "pip garbage"), "none"), ((0, "pip 22.2"), "22.2")])
def test_bad_probe_fails_closed(env, capsys, monkeypatch, probe, found):
    env.build("b", 1)
    lib(env).mkdir()
    (lib(env) / "keep.txt").write_text("")
    cmds = []
    monkeypatch.setattr(install, "run_pip", lambda cmd: cmds.append(cmd) or probe)
    assert install.main([]) == 1
    err = capsys.readouterr().err
    assert ("pip 22.3 or newer is required to install into Fusion's Python (found %s); run python -m pip install "
            "--upgrade pip, or rerun install.py with a Python that has pip" % found) in err
    assert cmds == [[sys.executable, "-m", "pip", "--version"]]
    assert (lib(env) / "keep.txt").exists() and not env.link.is_symlink() and not env.cfg.exists()


@pytest.mark.parametrize("out", ["pip 22.3.1 from /x", "pip 24.0b1 from /x"])
def test_probe_versions_that_pass(env, monkeypatch, out):
    env.build("b", 1)
    monkeypatch.setattr(install, "run_pip",
                        lambda cmd: (0, out) if cmd[-1] == "--version" else env.pip(cmd))
    assert install.main([]) == 0


def test_rmtree_of_old_lib_failure(env, capsys, monkeypatch):
    env.build("b", 1)
    lib(env).mkdir()
    selective_rmtree(monkeypatch, lambda p: p.name == "lib")
    assert install.main([]) == 1
    assert "close Fusion and rerun install.py" in capsys.readouterr().err
    assert not list((env.root / "De-Fusion").glob("lib.tmp-*"))
    assert not env.link.is_symlink() and not env.cfg.exists()


def test_rename_failure(env, capsys, monkeypatch):
    env.build("b", 1)
    monkeypatch.setattr(install.os, "rename", lambda *a: (_ for _ in ()).throw(OSError("no")))
    assert install.main([]) == 1
    err = capsys.readouterr().err.strip()
    assert err == "rerun install.py" and "close Fusion" not in err
    assert not list((env.root / "De-Fusion").glob("lib.tmp-*")) and not env.link.is_symlink()


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


def test_uninstall_removes_leftover_tmp(env):
    env.build("b", 1)
    install.main([])
    (env.root / "De-Fusion/lib.tmp-x").mkdir()
    assert install.main(["--uninstall"]) == 0
    assert not list((env.root / "De-Fusion").glob("lib*")) and env.cfg.exists()


def test_uninstall_refuses_foreign_directory(env):
    env.link.mkdir(parents=True)
    assert install.main(["--uninstall"]) == 3
    assert env.link.is_dir()


def test_help_lists_flags_and_exit_codes(capsys):
    with pytest.raises(SystemExit) as e:
        install.main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "--uninstall" in out and "--fusion-python" in out and "1 pip failed or missing/older than 22.3" in out and "3 link conflict" in out
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


def test_stale_live_link_is_repointed(env, tmp_path):
    env.build("b", 1)
    old = tmp_path / "old" / "De-Fusion"
    old.mkdir(parents=True)
    env.link.parent.mkdir(parents=True)
    os.symlink(old, env.link)
    assert install.main([]) == 0
    assert os.readlink(env.link) == str(env.root / "De-Fusion")


def test_stale_dangling_link_is_repointed(env, tmp_path):
    env.build("b", 1)
    env.link.parent.mkdir(parents=True)
    os.symlink(tmp_path / "gone" / "De-Fusion", env.link)
    assert install.main([]) == 0
    assert os.readlink(env.link) == str(env.root / "De-Fusion")


def test_real_directory_at_link_path_exits_3_untouched(env):
    env.build("b", 1)
    env.link.mkdir(parents=True)
    (env.link / "keep.txt").write_text("x")
    assert install.main([]) == 3
    assert not env.link.is_symlink() and (env.link / "keep.txt").read_text() == "x"


def test_link_to_other_name_exits_3_untouched(env, tmp_path):
    env.build("b", 1)
    other = tmp_path / "other"
    other.mkdir()
    env.link.parent.mkdir(parents=True)
    os.symlink(other, env.link)
    assert install.main([]) == 3
    assert os.readlink(env.link) == str(other)
