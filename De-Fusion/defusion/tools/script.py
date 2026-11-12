"""Fusion API reference, modelling guide, saved scripts and Python execution."""
import ast
import builtins
import collections
import contextlib
import dataclasses
import importlib
import inspect
import io
import math
import re
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import TypeAdapter, ValidationError

from .. import config
from ..actions import action
from ..context import Ctx
from ..errors import FUSION_ERROR, INVALID_PARAMS, NOT_FOUND, ActionError
from ..types import FileName

SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_SCRIPT_BYTES = 60_000
MAX_SESSIONS = 32
MAX_STREAM_CHARS = 16_384
MAX_REPR_CHARS = 8_192
EXACT_RE = re.compile(r"^adsk\.(core|fusion|cam)\.[A-Za-z_]\w*(\.[A-Za-z_]\w*)?$")
MAX_DOC_CHARS = 16_384

# --- API index (design § 3) ---------------------------------------------------------------------


@dataclasses.dataclass
class _Entry:
    name: str
    module: str
    cls_lower: str
    member_lower: str
    kind: str
    signature: str
    summary: str
    summary_lower: str
    obj: object


_INDEX = None


def _fmt_annotation(ann):
    if ann is inspect.Parameter.empty or ann is inspect.Signature.empty:
        return ""
    if isinstance(ann, str):
        return ann
    return inspect.formatannotation(ann)


def _fmt_signature(sig, drop_first=False):
    params = list(sig.parameters.values())
    if drop_first and params:
        params = params[1:]
    parts = []
    for p in params:
        text = p.name
        if p.kind is p.VAR_POSITIONAL:
            text = "*" + text
        elif p.kind is p.VAR_KEYWORD:
            text = "**" + text
        ann = _fmt_annotation(p.annotation)
        if ann:
            text += ": " + ann
        if p.default is not p.empty:
            text += (" = " if ann else "=") + repr(p.default)
        parts.append(text)
    out = "(" + ", ".join(parts) + ")"
    ret = _fmt_annotation(sig.return_annotation)
    return out + (" -> " + ret if ret else "")


def _sig_text(fn, drop_first=False):
    try:
        return _fmt_signature(inspect.signature(fn), drop_first)
    except (ValueError, TypeError):
        return "(...)"


def _member_info(mname, attr):
    """(kind, signature, doc object) of a class member, or None when it is not indexed."""
    if isinstance(attr, property):
        ann = ""
        if attr.fget is not None:
            try:
                ann = _fmt_annotation(inspect.signature(attr.fget).return_annotation)
            except (ValueError, TypeError):
                ann = ""
        sig = "%s: %s" % (mname, ann) + (" (read-only)" if attr.fset is None else "")
        return "property", sig, attr
    if isinstance(attr, (staticmethod, classmethod)):
        return "staticmethod", mname + _sig_text(attr.__func__), attr.__func__
    if inspect.isfunction(attr):
        return "method", mname + _sig_text(attr, drop_first=True), attr
    if isinstance(attr, int) and not isinstance(attr, bool):
        return "constant", "%s = %s" % (mname, attr), attr
    return None


def _summary_of(obj):
    for line in (inspect.getdoc(obj) or "").splitlines():
        if line.strip():
            return line.strip()[:200]
    return ""


def _entry(name, module, cls_name, member, kind, signature, obj, doc_obj):
    summary = _summary_of(doc_obj)
    return _Entry(
        name, module, cls_name.lower(), member.lower(), kind, signature, summary, summary.lower(), obj
    )


def _class_entry(modname, cls):
    sig = "%s(%s)" % (cls.__name__, ", ".join(b.__name__ for b in cls.__bases__))
    return _entry("%s.%s" % (modname, cls.__name__), modname.split(".")[1], cls.__name__, "", "class", sig, cls, cls)


def _member_entries(modname, cls):
    out = []
    for mname, attr in vars(cls).items():
        if mname.startswith("_"):
            continue
        info = _member_info(mname, attr)
        if info is not None:
            kind, sig, doc_obj = info
            out.append(
                _entry("%s.%s.%s" % (modname, cls.__name__, mname), modname.split(".")[1],
                       cls.__name__, mname, kind, sig, attr, doc_obj)
            )
    return out


def _build_index():
    entries = []
    for modname in ("adsk.core", "adsk.fusion", "adsk.cam"):
        try:
            module = importlib.import_module(modname)
        except ImportError:
            continue
        for name, obj in vars(module).items():
            if inspect.isclass(obj) and not name.startswith("_") and obj.__module__.startswith("adsk."):
                entries.append(_class_entry(modname, obj))
                entries.extend(_member_entries(modname, obj))
    return entries


def _index():
    global _INDEX
    if _INDEX is None:
        _INDEX = _build_index()
    return _INDEX


def _score(entry, tokens):
    total = 0
    for t in tokens:
        if entry.member_lower and t == entry.member_lower:
            total += 8
        elif t in entry.member_lower:
            total += 4
        elif t == entry.cls_lower:
            total += 6
        elif t in entry.cls_lower:
            total += 3
        elif t in entry.summary_lower:
            total += 1
        else:
            return 0
    return total


def _search(query, module):
    tokens = [t for t in re.split(r"[\s.,()]+", query.lower()) if t and t != "adsk"]
    if not tokens:
        raise ActionError(INVALID_PARAMS, "query is empty")
    scored = []
    for e in _index():
        if module is not None and e.module != module:
            continue
        s = _score(e, tokens)
        if s:
            scored.append((s, e))
    scored.sort(key=lambda se: (-se[0], len(se[1].name), se[1].name))
    return scored


def _resolve_exact(name):
    """(entry-like tuple) for an exact dotted name, or None."""
    parts = name.split(".")
    try:
        module = importlib.import_module("%s.%s" % (parts[0], parts[1]))
    except ImportError:
        return None
    cls = getattr(module, parts[2], None)
    if not inspect.isclass(cls) or parts[2].startswith("_"):
        return None
    modname = "%s.%s" % (parts[0], parts[1])
    if len(parts) == 3:
        return _class_entry(modname, cls), cls
    if parts[3].startswith("_"):
        return None
    try:
        attr = inspect.getattr_static(cls, parts[3])
    except AttributeError:
        return None
    info = _member_info(parts[3], attr)
    if info is None:
        return None
    kind, sig, doc_obj = info
    return _entry(name, parts[1], parts[2], parts[3], kind, sig, attr, doc_obj), cls


def _item(e, score=None):
    d = {"name": e.name, "kind": e.kind, "signature": e.signature, "summary": e.summary}
    if score is not None:
        d["score"] = score
    return d


@action("script", access="read", timeout=60)
def api_docs(ctx: Ctx, query: str, module: Literal["core", "fusion", "cam"] | None = None,
             offset: int = 0, limit: int = 100) -> dict:
    """Search the live Fusion Python API (adsk.core/fusion/cam) or look up one exact name."""
    q = query.strip()
    if not EXACT_RE.match(q):
        scored = _search(query, module)
        return {"match": "search", "query": query,
                **ctx.page(scored, offset, limit, build=lambda se: _item(se[1], se[0]))}
    found = _resolve_exact(q)
    if found is None:
        hints = [e.name for _, e in _search(q, None)[:5]]
        raise ActionError(NOT_FOUND, "%s is not in the live adsk API" % q, hints=hints)
    entry, cls = found
    doc = (inspect.getdoc(entry.obj) or "")[:MAX_DOC_CHARS]
    out = {"match": "exact", "name": entry.name, "kind": entry.kind,
           "signature": entry.signature, "doc": doc}
    if entry.kind == "class":
        members = sorted(_member_entries(entry.name.rsplit(".", 1)[0], cls), key=lambda m: m.name)
        out["members"] = ctx.page(members, offset, limit, build=_item)
    return out


# --- design_guide (design § 4) ----------------------------------------------------------------------

GUIDE_PATH = Path(__file__).resolve().parent.parent / "guide.md"


@action("script", access="read", timeout=30)
def design_guide(ctx: Ctx) -> dict:
    """Return the De-Fusion modelling guide for agents (read before building models)."""
    try:
        text = GUIDE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ActionError(NOT_FOUND, "guide.md missing from the add-in; reinstall") from None
    return {"guide": text, "bytes": len(text.encode("utf-8"))}


# --- saved scripts (design § 5) -------------------------------------------------------------------

_FILE_NAME = TypeAdapter(FileName)


def _scripts_dir():
    return config.CONFIG_DIR / "scripts"


def _valid_stem(name):
    try:
        _FILE_NAME.validate_python(name)
    except ValidationError as exc:
        return exc.errors()[0]["msg"]
    return None


def _script_path(ctx, name):
    if name.endswith(".py"):
        name = name[:-3]
    problem = _valid_stem(name)
    if problem is not None:
        raise ActionError(INVALID_PARAMS, "bad script name %r: %s" % (name, problem))
    return _scripts_dir() / ("%s.py" % name)


def _is_saved(path):
    return not path.is_symlink() and path.is_file()


def _summary(src):
    try:
        doc = ast.get_docstring(ast.parse(src))
    except (SyntaxError, ValueError):
        doc = None
    if doc:
        return doc.strip().splitlines()[0][:120]
    for line in src.splitlines():
        if line.startswith("#"):
            return line.lstrip("# ").strip()[:120]
    return ""


def _script_row(path):
    st = path.stat()
    modified = datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"name": path.stem, "bytes": st.st_size, "modified": modified, "summary": _summary(text)}


@action("script", access="read")
def list_scripts(ctx: Ctx, offset: int = 0, limit: int = 100) -> dict:
    """List saved scripts in <config dir>/scripts."""
    d = _scripts_dir()
    paths = []
    if d.is_dir():
        paths = [p for p in sorted(d.glob("*.py")) if _is_saved(p) and _valid_stem(p.stem) is None]
    return ctx.page(paths, offset, limit, build=_script_row)


@action("script", access="read")
def load_script(ctx: Ctx, name: str) -> dict:
    """Return the code of a saved script."""
    path = _script_path(ctx, name)
    if not _is_saved(path):
        raise ActionError(NOT_FOUND, "no saved script %s" % name, hints=["script.list_scripts"])
    return {"name": path.stem, "code": path.read_text(encoding="utf-8")}


@action("script", access="write", undo_group=False, edits_document="none")
def save_script(ctx: Ctx, name: str, code: str) -> dict:
    """Save Python code as a new named script. Refuses to replace an existing one."""
    path = _script_path(ctx, name)
    size = len(code.encode("utf-8"))
    if size > MAX_SCRIPT_BYTES:
        raise ActionError(INVALID_PARAMS, "script is %d bytes; limit %d" % (size, MAX_SCRIPT_BYTES))
    exists = ActionError(
        INVALID_PARAMS, "script %s exists" % path.stem,
        hints=["delete it first with script.delete_script (confirm: true)"],
    )
    if path.exists() or path.is_symlink():
        raise exists
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "x", encoding="utf-8", newline="") as f:
            f.write(code)
    except FileExistsError:
        raise exists from None
    return {"script": {"name": path.stem, "bytes": size}}


@action("script", access="destructive", undo_group=False, edits_document="none")
def delete_script(ctx: Ctx, name: str) -> dict:
    """Delete a saved script."""
    path = _script_path(ctx, name)
    if not _is_saved(path):
        raise ActionError(NOT_FOUND, "no saved script %s" % name, hints=["script.list_scripts"])
    path.unlink()
    return {"deleted": path.stem}


# --- execute_python (design § 6) -------------------------------------------------------------------

_SESSIONS = collections.OrderedDict()
_CAP = 1000


def _session(ctx, session_id, reset):
    if reset:
        _SESSIONS.pop(session_id, None)
    ns = _SESSIONS.get(session_id)
    if ns is None:
        ns = _SESSIONS[session_id] = {"__name__": "__defusion__", "__builtins__": builtins}
    _SESSIONS.move_to_end(session_id)
    while len(_SESSIONS) > MAX_SESSIONS:
        _SESSIONS.popitem(last=False)
    import adsk.cam  # noqa: F401
    import adsk.core  # noqa: F401
    import adsk.fusion  # noqa: F401

    ns["adsk"] = adsk
    ns["app"] = ctx.app
    ns["ui"] = ctx.ui
    ns["design"] = ctx.design
    ns["root"] = ctx.design.rootComponent if ctx.design else None
    return ns


def _clip(text):
    if len(text) > MAX_STREAM_CHARS:
        return "[truncated %d chars]\n%s" % (len(text) - MAX_STREAM_CHARS, text[-MAX_STREAM_CHARS:])
    return text


def _entity_token(v):
    try:
        token = getattr(v, "entityToken", None)
    except Exception:
        return None
    return token if isinstance(token, str) and token else None


def _json_safe(ctx, v, depth=0):
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else str(v)
    if depth <= 8:
        if isinstance(v, (list, tuple, set)):
            items = list(v)
            out = [_json_safe(ctx, i, depth + 1) for i in items[:_CAP]]
            return out + (["[+%d more]" % (len(items) - _CAP)] if len(items) > _CAP else [])
        if isinstance(v, dict) and all(isinstance(k, str) for k in v):
            out = {k: _json_safe(ctx, i, depth + 1) for k, i in list(v.items())[:_CAP]}
            if len(v) > _CAP:
                out["[+%d more]" % (len(v) - _CAP)] = None
            return out
        if _entity_token(v):
            return ctx.ref(v)
    out = {"repr": repr(v)[:MAX_REPR_CHARS]}
    try:
        out["type"] = v.objectType
    except Exception:
        pass
    return out


def _user_traceback(exc):
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == "<execute_python>"]
    text = "Traceback (most recent call last):\n" if frames else ""
    text += "".join(traceback.format_list(frames)) + "".join(traceback.format_exception_only(exc))
    return _clip(text)


@action("script", access="destructive", timeout=60)
def execute_python(ctx: Ctx, code: str, session_id: str = "default", reset: bool = False) -> dict:
    """Run Python in Fusion's main thread; the last expression's value is returned.

    Preloaded: adsk, app, ui, design, root. Variables persist per session_id. stdout/stderr are
    captured. Blocks Fusion while running; a call longer than 60 s continues as a job
    (server.job_status). An endless loop cannot be cancelled. Do not call adsk.doEvents()."""
    if not SESSION_RE.fullmatch(session_id):
        raise ActionError(INVALID_PARAMS, "session_id must match [A-Za-z0-9_-]{1,64}")
    try:
        tree = ast.parse(code, "<execute_python>", "exec")
    except SyntaxError as e:
        raise ActionError(INVALID_PARAMS, "SyntaxError: %s (line %s)" % (e.msg, e.lineno)) from None
    expr = None
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        expr = compile(ast.Expression(tree.body.pop().value), "<execute_python>", "eval")
    body = compile(tree, "<execute_python>", "exec")
    ns = _session(ctx, session_id, reset)
    out, err = io.StringIO(), io.StringIO()
    t0 = time.monotonic()
    value = None
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            exec(body, ns)
            if expr is not None:
                value = eval(expr, ns)
    except (Exception, SystemExit) as e:
        raise ActionError(
            FUSION_ERROR, "%s: %s" % (type(e).__name__, e), exception_type=type(e).__name__,
            traceback=_user_traceback(e), stdout=_clip(out.getvalue()), stderr=_clip(err.getvalue()),
        ) from e
    if value is not None:
        ns["_"] = value
    return {
        "session_id": session_id, "value": _json_safe(ctx, value), "stdout": _clip(out.getvalue()),
        "stderr": _clip(err.getvalue()), "duration_ms": int((time.monotonic() - t0) * 1000),
    }


# --- live scenarios (design § 8.2) ----------------------------------------------------------------


async def _live_call(session, act, params, tool="script"):
    import live_check

    return await live_check.call(session, tool, act, params)


async def _api_docs_search(session):
    r = await _live_call(session, "api_docs", {"query": "extrude input"})
    assert r["ok"] and r["result"]["total"] > 0, r
    names = [i["name"] for i in r["result"]["items"][:10]]
    assert "adsk.fusion.ExtrudeFeatures.createInput" in names, names
    r = await _live_call(session, "api_docs", {"query": "extrude input", "module": "cam"})
    assert r["ok"], r
    assert not any(i["name"].startswith(("adsk.fusion.", "adsk.core.")) for i in r["result"]["items"]), r
    r = await _live_call(session, "api_docs", {"query": "extrude", "limit": 2})
    assert r["ok"] and len(r["result"]["items"]) == 2 and r["result"]["next_offset"] == 2, r


async def _api_docs_exact(session):
    r = await _live_call(session, "api_docs", {"query": "adsk.fusion.ExtrudeFeatures.createInput"})
    res = r["result"] if r["ok"] else {}
    assert res.get("match") == "exact" and res["kind"] == "method" and res["doc"], r
    r = await _live_call(session, "api_docs", {"query": "adsk.fusion.Sketch"})
    res = r["result"] if r["ok"] else {}
    assert res.get("kind") == "class" and res["members"]["total"] > 0, r
    r = await _live_call(session, "api_docs", {"query": "adsk.fusion.NoSuchThing"})
    assert not r["ok"] and r["error"]["code"] == "NOT_FOUND", r


async def _api_docs_cam(session):
    r = await _live_call(session, "api_docs", {"query": "setup", "module": "cam"})
    assert r["ok"] and r["result"]["total"] > 0, r
    assert all(i["name"].startswith("adsk.cam.") for i in r["result"]["items"]), r


async def _design_guide(session):
    r = await _live_call(session, "design_guide", {})
    assert r["ok"], r
    res = r["result"]
    assert res["bytes"] < 24576 and res["guide"].startswith("# De-Fusion modelling guide"), res["bytes"]


async def _scripts_roundtrip(session):
    name = "livecheck_tmp"
    code = '"""Live check."""\nx = 1\n'
    r = await _live_call(session, "save_script", {"name": name, "code": code})
    assert r["ok"] and r["result"]["script"]["name"] == name, r
    r = await _live_call(session, "list_scripts", {})
    assert r["ok"] and any(i["name"] == name and i["summary"] == "Live check." for i in r["result"]["items"]), r
    r = await _live_call(session, "load_script", {"name": name})
    assert r["ok"] and r["result"]["code"] == code, r
    r = await _live_call(session, "delete_script", {"name": name, "confirm": True})
    assert r["ok"] and r["result"]["deleted"] == name, r
    r = await _live_call(session, "load_script", {"name": name})
    assert not r["ok"] and r["error"]["code"] == "NOT_FOUND", r


async def _execute_value(session):
    r = await _live_call(session, "execute_python", {"code": "print('hi')\nroot.name", "confirm": True})
    res = r["result"] if r["ok"] else {}
    assert res.get("stdout") == "hi\n" and isinstance(res["value"], str) and res["value"], r
    r = await _live_call(session, "execute_python", {"code": "y = 1", "confirm": True})
    assert r["ok"] and r["result"]["value"] is None, r


async def _execute_ref(session):
    code = (
        "sk = root.sketches.add(root.xYConstructionPlane)\n"
        "sk.sketchCurves.sketchLines.addTwoPointRectangle("
        "adsk.core.Point3D.create(0, 0, 0), adsk.core.Point3D.create(1, 1, 0))\n"
        "inp = root.features.extrudeFeatures.createInput(sk.profiles.item(0), "
        "adsk.fusion.FeatureOperations.NewBodyFeatureOperation)\n"
        "inp.setDistanceExtent(False, adsk.core.ValueInput.createByReal(1))\n"
        "root.features.extrudeFeatures.add(inp)\n"
        "root.bRepBodies.item(root.bRepBodies.count-1)"
    )
    r = await _live_call(session, "execute_python", {"code": code, "confirm": True})
    value = r["result"]["value"] if r["ok"] else {}
    assert value.get("type") == "BRepBody" and value.get("token"), r
    follow = "len(design.findEntityByToken(%r))" % value["token"]
    r = await _live_call(session, "execute_python", {"code": follow, "confirm": True})
    assert r["ok"] and r["result"]["value"] == 1, r


async def _execute_session(session):
    sid = "livecheck-a"
    run = lambda code, **kw: _live_call(  # noqa: E731
        session, "execute_python", {"code": code, "session_id": sid, "confirm": True, **kw})
    r = await run("x = 41", reset=True)
    assert r["ok"], r
    r = await run("x + 1")
    assert r["ok"] and r["result"]["value"] == 42, r
    r = await _live_call(session, "execute_python", {"code": "x", "session_id": "livecheck-b", "confirm": True})
    assert not r["ok"] and r["error"]["exception_type"] == "NameError", r
    r = await run("x", reset=True)
    assert not r["ok"] and r["error"]["exception_type"] == "NameError", r


async def _execute_rollback(session):
    count = "len([s for s in root.sketches])"
    before = (await _live_call(session, "execute_python", {"code": count, "confirm": True}))["result"]["value"]
    r = await _live_call(session, "execute_python", {"code": "def (", "confirm": True})
    assert not r["ok"] and r["error"]["code"] == "INVALID_PARAMS", r
    code = "root.sketches.add(root.xYConstructionPlane)\nprint('a')\nraise ValueError('x')"
    r = await _live_call(session, "execute_python", {"code": code, "confirm": True})
    err = r["error"] if not r["ok"] else {}
    assert err.get("code") == "FUSION_ERROR" and err["stdout"] == "a\n" and err["traceback"], r
    after = (await _live_call(session, "execute_python", {"code": count, "confirm": True}))["result"]["value"]
    assert after == before, (before, after)


async def _execute_job(session):
    import asyncio

    r = await _live_call(session, "execute_python", {"code": "import time; time.sleep(65); 7", "confirm": True})
    assert r["ok"] and r["result"]["status"] == "running", r
    job_id = r["result"]["job_id"]
    for _ in range(30):
        j = await _live_call(session, "job_status", {"job_id": job_id}, tool="server")
        if j["ok"] and j["result"].get("state") == "done":
            assert j["result"]["result"]["value"] == 7, j
            return
        await asyncio.sleep(1)
    raise AssertionError("job did not finish: %r" % (j,))


LIVE_SCENARIOS = [
    ("api_docs_search", "script.api_docs", _api_docs_search),
    ("api_docs_exact", "script.api_docs", _api_docs_exact),
    ("api_docs_cam", "script.api_docs", _api_docs_cam),
    ("design_guide", "script.design_guide", _design_guide),
    ("scripts_roundtrip", "script.save_script", _scripts_roundtrip),
    ("execute_value", "script.execute_python", _execute_value),
    ("execute_ref", "script.execute_python", _execute_ref),
    ("execute_session", "script.execute_python", _execute_session),
    ("execute_rollback", "script.execute_python", _execute_rollback),
    ("execute_job", "script.execute_python", _execute_job),
]
