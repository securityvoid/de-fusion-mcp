"""script tool unit tests (fake adsk, no Fusion)."""
import pytest

from defusion import actions
from defusion.actions import call
from defusion.tools import script
from helpers import ok, err


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.setattr(script, "_INDEX", None)


@pytest.fixture
def env(dispatcher, fake_design):
    return fake_design


def run(act, params=None):
    args = {"action": act}
    if params is not None:
        args["params"] = params
    allowed = frozenset(a.key for a in actions.registry())
    return call("script", args, allowed, ["full"])


# --- api_docs ---------------------------------------------------------------------------------


def test_api_docs_search(env):
    res = ok(run("api_docs", {"query": "extrude input"}))
    assert res["match"] == "search"
    assert res["items"][0]["name"] == "adsk.fusion.ExtrudeFeatures.createInput"
    assert {"name", "kind", "signature", "summary", "score"} <= set(res["items"][0])
    cam = ok(run("api_docs", {"query": "setup", "module": "cam"}))
    assert cam["total"] > 0 and all(i["name"].startswith("adsk.cam.") for i in cam["items"])
    assert ok(run("api_docs", {"query": "count"}))["total"] > 0
    assert not [i for i in ok(run("api_docs", {"query": "count", "module": "fusion"}))["items"]
                if not i["name"].startswith("adsk.fusion.")]
    assert not [i for i in ok(run("api_docs", {"query": "createinput", "module": "core"}))["items"]
                if i["name"].startswith("adsk.fusion.")]
    assert ok(run("api_docs", {"query": "createinput", "module": "fusion"}))["total"] > 0
    page = ok(run("api_docs", {"query": "extrude", "limit": 1}))
    assert len(page["items"]) == 1 and page["next_offset"] == 1 and page["total"] > 1
    assert err(run("api_docs", {"query": "  . "}))["code"] == "INVALID_PARAMS"


def test_api_docs_exact(env):
    m = ok(run("api_docs", {"query": "adsk.fusion.ExtrudeFeatures.createInput"}))
    assert m["match"] == "exact" and m["kind"] == "method"
    assert m["signature"] == "createInput(profile, operation)" and "Creates an" in m["doc"]
    c = ok(run("api_docs", {"query": "adsk.fusion.ExtrudeFeatures"}))
    assert c["kind"] == "class" and c["members"]["total"] >= 2
    names = [i["name"] for i in c["members"]["items"]]
    assert names == sorted(names) and "score" not in c["members"]["items"][0]
    inh = ok(run("api_docs", {"query": "adsk.fusion.Sketch.isValid"}))
    assert inh["kind"] == "property"
    e = err(run("api_docs", {"query": "adsk.fusion.NoSuchThing"}))
    assert e["code"] == "NOT_FOUND"
    e = err(run("api_docs", {"query": "adsk.fusion.ExtrudeFeatures.nope", "module": "cam"}))
    assert e["code"] == "NOT_FOUND" and isinstance(e["hints"], list)


# --- saved scripts ----------------------------------------------------------------------------


@pytest.fixture
def store(env, tmp_path, monkeypatch):
    monkeypatch.setattr(script.config, "CONFIG_DIR", tmp_path)
    return tmp_path / "scripts"


def test_scripts_roundtrip_and_summary(store):
    doc = '"""Make a box."""\nx = 1\n'
    assert ok(run("save_script", {"name": "box", "code": doc})) == {"script": {"name": "box", "bytes": len(doc)}}
    assert (store / "box.py").read_text() == doc
    ok(run("save_script", {"name": "cm.py", "code": "# helper  \nx = 1\n"}))
    ok(run("save_script", {"name": "plain", "code": "x = 1\n"}))
    ok(run("save_script", {"name": "broken", "code": "def (\n# late comment\n"}))
    rows = {r["name"]: r for r in ok(run("list_scripts"))["items"]}
    assert list(rows) == sorted(rows) == ["box", "broken", "cm", "plain"]
    assert rows["box"]["summary"] == "Make a box." and rows["box"]["bytes"] == len(doc)
    assert rows["cm"]["summary"] == "helper"
    assert rows["plain"]["summary"] == "" and rows["broken"]["summary"] == "late comment"
    assert rows["box"]["modified"].endswith("Z") and len(rows["box"]["modified"]) == 20
    assert ok(run("list_scripts", {"limit": 1}))["next_offset"] == 1
    assert ok(run("load_script", {"name": "box"})) == {"name": "box", "code": doc}
    assert err(run("load_script", {"name": "missing"}))["code"] == "NOT_FOUND"


def test_scripts_list_empty_without_dir(store):
    assert ok(run("list_scripts"))["total"] == 0
    assert not store.exists()


def test_scripts_save_guards(store):
    ok(run("save_script", {"name": "a", "code": "x"}))
    e = err(run("save_script", {"name": "a", "code": "y"}))
    assert e["code"] == "INVALID_PARAMS" and "script.delete_script" in e["hints"][0]
    assert (store / "a.py").read_text() == "x"
    assert err(run("save_script", {"name": "big", "code": "x" * 60_001}))["code"] == "INVALID_PARAMS"
    ok(run("save_script", {"name": "edge", "code": "é" * 30_000}))
    assert err(run("save_script", {"name": "wide", "code": "é" * 30_001}))["code"] == "INVALID_PARAMS"
    assert not (store / "big.py").exists()


@pytest.mark.parametrize("bad", ["../x", "a/b", ".hidden", "", ".py", "a\\b"])
def test_scripts_bad_names(store, bad):
    for act, params in (("save_script", {"code": "x"}), ("load_script", {}), ("delete_script", {"confirm": True})):
        assert err(run(act, {"name": bad, **params}))["code"] == "INVALID_PARAMS"
    assert not store.exists()


def test_scripts_symlink_ignored(store, tmp_path):
    store.mkdir()
    (tmp_path / "real.py").write_text("x = 1\n")
    (store / "evil.py").symlink_to(tmp_path / "real.py")
    assert ok(run("list_scripts"))["total"] == 0
    assert err(run("load_script", {"name": "evil"}))["code"] == "NOT_FOUND"
    assert err(run("delete_script", {"name": "evil", "confirm": True}))["code"] == "NOT_FOUND"
    assert err(run("save_script", {"name": "evil", "code": "y"}))["code"] == "INVALID_PARAMS"
    assert (tmp_path / "real.py").read_text() == "x = 1\n"


def test_scripts_delete(store):
    ok(run("save_script", {"name": "a", "code": "x"}))
    assert err(run("delete_script", {"name": "a"}))["code"] == "CONFIRM_REQUIRED"
    assert (store / "a.py").exists()
    assert ok(run("delete_script", {"name": "a", "confirm": True})) == {"deleted": "a"}
    assert not (store / "a.py").exists()
    assert err(run("delete_script", {"name": "a", "confirm": True}))["code"] == "NOT_FOUND"


# --- execute_python ---------------------------------------------------------------------------


def exe(code, **kw):
    return run("execute_python", {"code": code, "confirm": True, **kw})


def test_execute_values(env):
    r = ok(exe("print('hi')\nroot.name"))
    assert r["stdout"] == "hi\n" and r["value"] == env.rootComponent.name and r["session_id"] == "default"
    assert isinstance(r["duration_ms"], int)
    assert ok(exe("x = 1"))["value"] is None
    assert ok(exe("1 + 1; 'last'"))["value"] == "last"
    assert ok(exe("import sys; print('e', file=sys.stderr)"))["stderr"] == "e\n"
    assert ok(exe("5"))["value"] == 5 and ok(exe("_ * 2"))["value"] == 10
    assert ok(exe("None"))["value"] is None and ok(exe("_"))["value"] == 10  # None leaves _ alone
    assert ok(exe("{'a': (1, 2.5), 'b': [None]}"))["value"] == {"a": [1, 2.5], "b": [None]}
    assert ok(exe("float('nan')"))["value"] == "nan"
    assert ok(exe("object()"))["value"]["repr"].startswith("<object object")
    assert ok(exe("{1: 2}"))["value"] == {"repr": "{1: 2}"}
    body = ok(exe("root.add_body('Box')"))["value"]
    assert body["type"] == "BRepBody" and body["name"] == "Box" and body["token"]
    big = ok(exe("list(range(1500))"))["value"]
    assert len(big) == 1001 and big[:1000] == list(range(1000)) and big[-1] == "[+500 more]"
    assert ok(exe("'x' * 20000"))["value"] == "x" * 20000
    assert ok(exe("type('T', (), {'__repr__': lambda s: 'r' * 9000})()"))["value"]["repr"] == "r" * 8192
    deep = "v = 1\nfor _i in range(10): v = [v]\nv"
    assert "repr" in str(ok(exe(deep))["value"])


def test_execute_sessions(env):
    ok(exe("x = 41"))
    assert ok(exe("x + 1"))["value"] == 42
    assert err(exe("x", session_id="other"))["exception_type"] == "NameError"
    assert ok(exe("x", session_id="default"))["value"] == 41
    assert err(exe("x", reset=True))["exception_type"] == "NameError"
    assert err(exe("1", session_id="bad id!"))["code"] == "INVALID_PARAMS"
    assert err(exe("1", session_id="a" * 65))["code"] == "INVALID_PARAMS"
    ok(exe("app = 5; app"))
    assert ok(exe("app"))["value"]["repr"].startswith("<adsk.core.Application")


def test_execute_session_lru(env):
    script._SESSIONS.clear()
    for i in range(32):
        ok(exe("v = %d" % i, session_id="s%d" % i))
    ok(exe("v", session_id="s0"))  # touch s0: s1 becomes least recently used
    ok(exe("v = 33", session_id="s32"))
    assert len(script._SESSIONS) == 32 and "s1" not in script._SESSIONS and "s0" in script._SESSIONS
    assert err(exe("v", session_id="s1"))["exception_type"] == "NameError"


def test_execute_errors(env):
    e = err(exe("def ("))
    assert e["code"] == "INVALID_PARAMS" and "SyntaxError" in e["message"] and "line 1" in e["message"]
    e = err(exe("keep = 1\nprint('a')\nroot.add_body('Tmp')\nraise ValueError('x')"))
    assert e["code"] == "FUSION_ERROR" and e["exception_type"] == "ValueError"
    assert e["stdout"] == "a\n" and e["stderr"] == ""
    assert "<execute_python>" in e["traceback"] and "ValueError: x" in e["traceback"]
    assert "ValueError: x" in e["message"] and "script.py" not in e["traceback"]
    assert core_app_commands()[-1] == "PTransaction.Abort"
    assert ok(exe("keep"))["value"] == 1  # names bound before the exception persist
    e = err(exe("import sys; sys.exit(3)"))
    assert e["code"] == "FUSION_ERROR" and e["exception_type"] == "SystemExit"
    e = err(exe("print('z' * 20000); raise RuntimeError('q')"))
    assert e["stdout"].startswith("[truncated 3617 chars]\n") and len(e["stdout"]) < 16_500
    assert ok(exe("print('z' * 20000)"))["stdout"].startswith("[truncated")


def core_app_commands():
    from adsk import core

    return list(core.Application.get().commands)


# --- design_guide -----------------------------------------------------------------------------

GUIDE_HEADINGS = [
    "# De-Fusion modelling guide", "## 1. Calling De-Fusion", "## 2. Units", "## 3. Components first",
    "## 4. Parameters", "## 5. Sketch to feature", "## 6. Profiles", "## 7. Refs",
    "## 8. Timeline and rollback", "## 9. Undo", "## 10. Coils and threads", "## 11. Verifying work",
    "## 12. Escape hatch",
]


def test_guide_content(env):
    import re
    from pathlib import Path

    res = ok(run("design_guide"))
    path = Path(script.__file__).resolve().parent.parent / "guide.md"
    assert res["guide"] == path.read_text(encoding="utf-8")
    assert res["bytes"] == len(res["guide"].encode("utf-8")) < 24576
    lines = res["guide"].splitlines()
    pos = [lines.index(h) for h in GUIDE_HEADINGS]
    assert pos == sorted(pos)
    tools = {a.tool for a in actions.registry()}
    pairs = {m for m in re.findall(r"`(\w+)\.(\w+)`", res["guide"]) if m[0] in tools}
    assert len(pairs) > 20
    assert [p for p in pairs if actions.registry().get(*p) is None] == []


def test_guide_missing(env, monkeypatch, tmp_path):
    monkeypatch.setattr(script, "GUIDE_PATH", tmp_path / "nope.md")
    assert err(run("design_guide"))["code"] == "NOT_FOUND"
