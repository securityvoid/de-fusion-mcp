import importlib.util
import logging
import pathlib
import sys
import types as pytypes

import pytest

from defusion import config

ENTRY = pathlib.Path(config.__file__).resolve().parents[1] / "De-Fusion.py"


@pytest.fixture
def entry(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))
    spec = importlib.util.spec_from_file_location("de_fusion_entry", ENTRY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    lg = logging.getLogger("defusion")
    for h in list(lg.handlers):
        lg.removeHandler(h)
        h.close()
    lg.setLevel(logging.NOTSET)
    lg.propagate = True


def fake_server(monkeypatch, **fns):
    mod = pytypes.ModuleType("defusion.server")
    for k, v in fns.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, "defusion.server", mod)
    monkeypatch.setattr(sys.modules["defusion"], "server", mod, raising=False)
    return mod


def read_log(tmp_path):
    return (tmp_path / "defusion.log").read_text()


def test_run_sets_path_logs_at_info_and_starts(entry, monkeypatch, tmp_path):
    calls = []
    fake_server(monkeypatch, start=lambda: calls.append("start"), stop=lambda: calls.append("stop"))
    entry.run(None)
    assert sys.path[0] == str(ENTRY.parent / "lib") and sys.path[1] == str(ENTRY.parent)
    assert calls == ["start"]
    lg = logging.getLogger("defusion")
    assert lg.level == logging.INFO and len(lg.handlers) == 1
    lg.info("hello")
    assert "hello" in read_log(tmp_path)


def test_run_twice_does_not_duplicate_path_entries(entry, monkeypatch):
    fake_server(monkeypatch, start=lambda: None)
    entry.run(None)
    entry.run(None)
    assert sys.path.count(str(ENTRY.parent)) == 1 and sys.path.count(str(ENTRY.parent / "lib")) == 1


def test_missing_dependencies_logged_and_no_start(entry, monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "defusion.server", None)  # `from defusion import server` -> ImportError
    monkeypatch.delattr(sys.modules["defusion"], "server", raising=False)
    entry.run(None)
    assert "Dependencies missing in %s; run install.py" % (ENTRY.parent / "lib") in read_log(tmp_path)
    assert "failed to start" not in read_log(tmp_path)  # returned before start()


def test_start_exception_is_logged_not_raised(entry, monkeypatch, tmp_path):
    def boom():
        raise RuntimeError("kaput")

    fake_server(monkeypatch, start=boom)
    entry.run(None)
    text = read_log(tmp_path)
    assert "De-Fusion failed to start" in text and "kaput" in text


def test_stop_calls_server_stop_and_swallows_errors(entry, monkeypatch, tmp_path):
    calls = []
    fake_server(monkeypatch, stop=lambda: calls.append("stop"))
    entry.stop(None)
    assert calls == ["stop"]

    def boom():
        raise RuntimeError("nope")

    fake_server(monkeypatch, stop=boom)
    from defusion import log as dlog

    dlog.setup(tmp_path, "INFO")
    entry.stop(None)
    assert "De-Fusion failed to stop" in read_log(tmp_path)


def test_entry_never_runs_pip():
    src = ENTRY.read_text()
    assert "pip" not in src.replace("run install.py", "") and "subprocess" not in src
