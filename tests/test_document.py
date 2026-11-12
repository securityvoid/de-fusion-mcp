"""Tests for the document tool (docs/specs/document/design.md § 10.1)."""
import importlib
import pathlib
import sys
import types
from types import SimpleNamespace

import pytest
from adsk import core, fusion

from defusion import actions, guard, units
from defusion.actions import call
from defusion.context import Ctx
import defusion
from helpers import err
from helpers import raises_action


def _load_document_module():
    """Import ``defusion.tools.document`` without running ``defusion/tools/__init__.py``, which
    needs every sibling tool module (written by other task sets) to exist and register actions."""
    if "defusion.tools" in sys.modules:
        return importlib.import_module("defusion.tools.document")
    stub = types.ModuleType("defusion.tools")
    stub.__path__ = [str(pathlib.Path(defusion.__file__).parent / "tools")]
    sys.modules["defusion.tools"] = stub
    try:
        return importlib.import_module("defusion.tools.document")
    finally:
        del sys.modules["defusion.tools"]


doc_mod = _load_document_module()


@pytest.fixture
def env(fake_app):
    """Hub "Hub" > project "Proj" (root > "Sub"), and one open unsaved document "Scratch"."""
    hub = fake_app.data.add_hub("Hub")
    proj = hub.add_project("Proj")
    root = proj.rootFolder
    sub = root.add_folder("Sub")
    scratch = fake_app.documents._new("Scratch")
    ctx = Ctx.for_app(fake_app, frozenset(), [], units.OutputUnits.for_call())
    return SimpleNamespace(app=fake_app, hub=hub, proj=proj, root=root, sub=sub, scratch=scratch, ctx=ctx)


def run(act, params=None, units=None, **kw):
    allowed = frozenset(a.key for a in actions.registry() if a.tool == "document")
    args = {"action": act}
    if params is not None:
        args["params"] = params
    if units is not None:
        args["units"] = units
    return call("document", args, allowed, ["all"], **kw)


@pytest.fixture(autouse=True)
def fast_clean_guard(monkeypatch):
    monkeypatch.setattr(guard, "VERIFY_DELAY", 0)
    guard.UNLOCKED.clear()
    yield
    guard.UNLOCKED.clear()


@pytest.fixture
def live(env, dispatcher):
    """``env`` plus the dispatcher, for tests that go through the call pipeline."""
    return env


def ok(r):
    assert r.is_error is False, r.structured_content
    return r.structured_content["result"] if "result" in r.structured_content else r.structured_content


def one(r):
    """The single entry's result of a one-entry list call (design § 2.7)."""
    res = ok(r)
    assert (res["ok"], res["failed"]) == (1, 0), res
    return res["results"][0]["result"]


def eerr(r):
    """The single entry's error of a one-entry list call that failed per entry."""
    res = ok(r)
    assert (res["ok"], res["failed"]) == (0, 1), res
    return res["results"][0]["error"]


# --- helpers -----------------------------------------------------------------------------------------


def test_helpers_doc_selector_order(env):
    c = env.ctx
    assert doc_mod._doc(c, None) is env.scratch
    other = env.app.documents._new("Other")
    assert doc_mod._doc(c, "Scratch") is env.scratch
    assert doc_mod._doc(c, other.creationId) is other
    # name beats id when both could match
    env.scratch.creationId = "Other"
    assert doc_mod._doc(c, "Other") is other


def test_helpers_doc_errors(env):
    c = env.ctx
    assert raises_action(doc_mod._doc, c, "nope").code == "NOT_FOUND"
    twin = env.app.documents._new("Scratch")
    e = raises_action(doc_mod._doc, c, "Scratch")
    assert e.code == "AMBIGUOUS" and len(e.extra["candidates"]) == 2
    assert doc_mod._doc(c, twin.creationId) is twin
    env.app.activeDocument = None
    assert raises_action(doc_mod._doc, c, None).code == "NO_DOCUMENT"


def test_helpers_folder_and_file_addressing(env):
    c = env.ctx
    df = env.sub.add_file("Part")
    assert doc_mod._folder(c, "Proj") is env.root
    assert doc_mod._folder(c, "/Proj/Sub/") is env.sub
    assert doc_mod._folder(c, env.sub.id) is env.sub
    assert doc_mod._file(c, "Proj/Sub/Part") is df
    assert doc_mod._file(c, df.id) is df
    assert doc_mod.guard.folder_path(env.sub) == "Proj/Sub"
    assert doc_mod.guard.folder_path(env.root) == "Proj"
    assert doc_mod._file_path(df) == "Proj/Sub/Part"


def test_helpers_path_errors(env):
    c = env.ctx
    env.sub.add_file("Part")
    e = raises_action(doc_mod._folder, c, "Proj/Missing")
    assert e.code == "NOT_FOUND" and e.message == "Path segment 'Missing' not found under 'Proj'"
    assert raises_action(doc_mod._folder, c, "Nope").message == "Path segment 'Nope' not found under '/'"
    assert raises_action(doc_mod._file, c, "Proj/Sub/Nada").code == "NOT_FOUND"
    assert raises_action(doc_mod._folder, c, "urn:nothing").code == "NOT_FOUND"
    assert raises_action(doc_mod._file, c, "urn:nothing").code == "NOT_FOUND"
    assert raises_action(doc_mod._folder, c, " / ").code == "INVALID_PARAMS"
    assert raises_action(doc_mod._file, c, "Proj").code == "INVALID_PARAMS"
    env.root.add_folder("Sub")
    e = raises_action(doc_mod._folder, c, "Proj/Sub")
    assert e.code == "AMBIGUOUS" and {x["kind"] for x in e.extra["candidates"]} == {"folder"}
    env.sub.add_file("Part")
    assert raises_action(doc_mod._file, c, "Proj/Sub/Part").code == "AMBIGUOUS"


def test_helpers_hub_selector(env):
    c = env.ctx
    assert doc_mod._hub(c) is env.hub
    assert doc_mod._hub(c, env.hub.id) is env.hub
    assert doc_mod._hub(c, "Hub") is env.hub
    assert raises_action(doc_mod._hub, c, "x").code == "NOT_FOUND"
    env.app.data.add_hub("Hub")
    assert raises_action(doc_mod._hub, c, "Hub").code == "AMBIGUOUS"


def test_helpers_summaries_and_pending_upload_id(env):
    df = env.root.add_file("P", pending=True)
    assert df.id == "/local/P"
    assert doc_mod._file_ref(df) == {"id": None, "name": "P", "path": "Proj/P"}
    df.finish_upload()
    assert doc_mod._file_ref(df)["id"] == df.id and df.id.startswith("urn:")
    s = doc_mod._doc_summary(env.scratch)
    assert s["saved"] is False and s["file"] is None and s["type"] == "design"
    assert s["design_type"] == "parametric" and s["active"] is True and s["id"] == env.scratch.creationId
    env.scratch.dataFile = df
    assert doc_mod._doc_summary(env.scratch)["file"]["name"] == "P"
    env.scratch.objectType = "adsk::drawing::DrawingDocument"
    d = doc_mod._doc_summary(env.scratch)
    assert d["type"] == "drawing" and d["design_type"] is None
    assert doc_mod._folder_summary(env.sub) == {"kind": "folder", "id": env.sub.id, "name": "Sub", "path": "Proj/Sub"}
    assert doc_mod._file_summary(df)["extension"] == "f3d"


def test_helpers_file_props_tolerates_missing(env):
    df = env.root.add_file("P")
    assert doc_mod._file_props(df)["created_by"] == "Tester"
    del df.createdBy  # an absent property reads null, not an error
    p = doc_mod._file_props(df)
    assert p["created_by"] is None and p["version_number"] == 1 and p["path"] == "Proj/P"
    assert len(p) == 17


def test_helpers_need(env):
    doc_mod._need(env.scratch, "activate")
    e = raises_action(doc_mod._need, env.scratch, "nope")
    assert e.code == "UNSUPPORTED" and "FusionDocument.nope" in e.message


def test_helpers_abs_path(tmp_path):
    f = tmp_path / "a.stl"
    f.write_text("x")
    assert doc_mod._abs_path(str(f), True) == f
    assert doc_mod._abs_path(str(tmp_path / "new.stl"), False) == tmp_path / "new.stl"
    assert raises_action(doc_mod._abs_path, "rel/a.stl", True).code == "INVALID_PARAMS"
    assert raises_action(doc_mod._abs_path, str(tmp_path / "gone.stl"), True).code == "NOT_FOUND"
    assert raises_action(doc_mod._abs_path, str(tmp_path / "nodir" / "x.stl"), False).code == "NOT_FOUND"


# --- cloud navigation reads --------------------------------------------------------------------------


def test_list_hubs(live):
    live.app.data.add_hub("Second")
    r = ok(run("list_hubs"))
    assert r["total"] == 2
    assert [(h["name"], h["active"]) for h in r["items"]] == [("Hub", True), ("Second", False)]
    assert ok(run("list_hubs", {"limit": 1}))["next_offset"] == 1


def test_list_projects(live):
    live.hub.add_project("Other")
    r = ok(run("list_projects"))
    assert [(p["name"], p["active"]) for p in r["items"]] == [("Proj", True), ("Other", False)]
    assert r["items"][0]["root_folder_id"] == live.root.id
    assert ok(run("list_projects", {"hub": "Hub"}))["total"] == 2
    assert err(run("list_projects", {"hub": "nope"}))["code"] == "NOT_FOUND"
    live.app.data.activeProject = None
    assert not any(p["active"] for p in ok(run("list_projects"))["items"])


def test_list_folders_and_resolve_folder(live):
    live.sub.add_file("Part")
    live.root.add_file("Top")
    r = ok(run("list_folders", {"folder": "Proj"}))
    assert [(i["kind"], i["name"]) for i in r["items"]] == [("folder", "Sub"), ("file", "Top")]
    assert r["items"][1]["path"] == "Proj/Top" and r["items"][1]["version_number"] == 1
    assert [i["kind"] for i in ok(run("list_folders", {"folder": live.root.id, "files": False}))["items"]] == ["folder"]
    assert err(run("list_folders", {"folder": "Proj/Zzz"}))["message"] == "Path segment 'Zzz' not found under 'Proj'"
    rf = ok(run("resolve_folder", {"folder": "Proj/Sub"}))
    assert rf["id"] == live.sub.id and rf["is_root"] is False and rf["project_name"] == "Proj"
    assert rf["path"] == "Proj/Sub" and rf["project_id"] == live.proj.id
    assert ok(run("resolve_folder", {"folder": "Proj"}))["is_root"] is True
    live.root.add_folder("Sub")
    amb = err(run("resolve_folder", {"folder": "Proj/Sub"}))
    assert amb["code"] == "AMBIGUOUS" and len(amb["candidates"]) == 2


def test_list_folders_skips_entries_that_raise(live):
    class Boom(core.Collection):
        def item(self, i):
            raise RuntimeError("mid-upload")

    live.sub.add_file("Part")
    type(live.root).dataFiles = property(lambda s: Boom([1]))
    try:
        r = ok(run("list_folders", {"folder": "Proj"}))
    finally:
        type(live.root).dataFiles = property(lambda s: core.Collection(s._files))
    assert [i["name"] for i in r["items"]] == ["Sub"]


def test_datafile_get(live):
    df = live.sub.add_file("Part")
    df.description = "hi"
    by_id = ok(run("datafile_get", {"file": df.id}))
    by_path = ok(run("datafile_get", {"file": "Proj/Sub/Part"}))
    assert by_id == by_path and by_id["description"] == "hi" and by_id["folder_name"] == "Sub"
    assert err(run("datafile_get", {"file": "urn:x"}))["code"] == "NOT_FOUND"


def test_cloud_reads_registered_read_only(live):
    h = ok(run("help"))
    acts = {a["name"]: a["access"] for a in h["actions"]}
    for n in ("list_hubs", "list_projects", "list_folders", "resolve_folder", "datafile_get"):
        assert acts[n] == "read"


# --- open-document lifecycle ---------------------------------------------------------------------------


def test_list_open(live):
    live.app.documents._new("B")
    r = ok(run("list_open"))
    assert r["total"] == 2 and [d["active"] for d in r["items"]] == [False, True]
    assert r["items"][0]["name"] == "Scratch" and ok(run("list_open", {"limit": 1}))["next_offset"] == 1


def test_list_references(live):
    a = live.root.add_file("Ref")
    live.scratch._refs.append(core.DocumentReference(a, True, 2, None))
    live.scratch._all_refs = live.scratch._refs + [core.DocumentReference(a, False, 1, live.scratch)]
    r = ok(run("list_references"))
    assert r["total"] == 1
    assert r["items"][0] == {"file": {"id": a.id, "name": "Ref", "path": "Proj/Ref"}, "out_of_date": True,
                             "version_number": 2, "open": False}
    n = ok(run("list_references", {"document": "Scratch", "nested": True}))
    assert n["total"] == 2 and n["items"][1]["open"] is True
    assert err(run("list_references", {"document": "zzz"}))["code"] == "NOT_FOUND"


def test_list_references_nested_unsupported(live, monkeypatch):
    monkeypatch.delattr(core.FusionDocument, "allDocumentReferences")
    assert err(run("list_references", {"nested": True}))["code"] == "UNSUPPORTED"
    assert ok(run("list_references"))["total"] == 0


def test_create(live):
    r = ok(run("create"))
    assert r["active"] and not r["saved"] and live.app.documents.count == 2
    assert live.app.documents.calls[-1] == ("add", core.DocumentTypes.FusionDesignDocumentType)
    assert ok(run("create", {"design_type": "direct"}))["design_type"] == "direct"
    assert ok(run("create", {"design_type": "parametric"}))["design_type"] == "parametric"
    assert err(run("create", {"design_type": "x"}))["code"] == "INVALID_PARAMS"
    live.app.documents.fail.add("add")
    assert err(run("create"))["code"] == "FUSION_ERROR"


def test_open_and_already_open(live):
    df = live.sub.add_file("Part")
    r = one(run("open", {"files": ["Proj/Sub/Part"]}))
    assert r["already_open"] is False and r["name"] == "Part" and r["saved"] is True
    assert live.app.documents.calls[-1][2] is True
    live.app.activeDocument = live.scratch
    again = one(run("open", {"files": [df.id]}))
    assert again["already_open"] is True and again["active"] is True
    assert live.app.documents.count == 2
    assert eerr(run("open", {"files": ["Proj/Gone"]}))["code"] == "NOT_FOUND"


def test_open_failure(live):
    live.sub.add_file("Part")
    live.app.documents.fail.add("open")
    assert eerr(run("open", {"files": ["Proj/Sub/Part"]}))["code"] == "FUSION_ERROR"


def test_activate(live):
    other = live.app.documents._new("Other")
    r = ok(run("activate", {"document": "Scratch"}))
    assert r["active"] and live.app.activeDocument is live.scratch
    live.scratch.fail.add("activate")
    assert err(run("activate", {"document": "Scratch"}))["code"] == "FUSION_ERROR"
    assert err(run("activate", {"document": "none"}))["code"] == "NOT_FOUND"
    assert err(run("activate"))["code"] == "INVALID_PARAMS"


def test_doc_summary_tolerates_dataFile_error(live):
    class Boom(type(live.scratch)):
        @property
        def dataFile(self):
            raise RuntimeError("3 : Failed to get temporary file")

    live.scratch.__class__ = Boom
    r = ok(run("list_open"))
    assert r["items"][0]["file"] is None


def _registered(live, read_only=False):
    """Scratch saved to Proj and registered in guard.UNLOCKED, as ensure_editable leaves it."""
    df = live.root.add_file("Reg")
    df.isReadOnly = read_only
    live.scratch.dataFile = df
    live.app.activeDocument = live.scratch
    guard.UNLOCKED[df.id] = "Scratch"
    return df


def test_close_relock_registered_document(live, monkeypatch):
    df = _registered(live)
    log = []
    real_close = live.scratch.close
    monkeypatch.setattr(live.scratch, "close", lambda save: (log.append(("close", save)), real_close(save))[1])
    real_cmd = live.app.executeTextCommand
    monkeypatch.setattr(live.app, "executeTextCommand", lambda c: (log.append(c), real_cmd(c))[1])
    r = one(run("close", {"confirm": True}))
    assert r["relocked"] is True and "warnings" not in r and df.isReadOnly is True
    assert live.app.commands == ["Commands.Start ActiveDocumentDeactivateCommand"]
    assert log == ["Commands.Start ActiveDocumentDeactivateCommand", ("close", False)]
    assert ("close", False) in live.scratch.calls and guard.UNLOCKED == {}


def test_close_relock_activates_document_to_lock(live):
    _registered(live)
    other = live.app.documents._new("Other")
    live.app.activeDocument = other
    r = one(run("close", {"documents": ["Scratch"], "confirm": True}))
    assert r["relocked"] is True and live.app.activeDocument is other


def test_close_already_read_only_via_fresh_lookup_issues_no_command(live):
    _registered(live, read_only=True)
    r = one(run("close", {"confirm": True}))
    assert r["relocked"] is True and live.app.commands == [] and guard.UNLOCKED == {}


def test_close_lookup_raises_not_relocked(live):
    df = _registered(live, read_only=True)
    live.app.data.findFileById = lambda i: (_ for _ in ()).throw(RuntimeError("gone"))
    r = one(run("close", {"confirm": True}))
    assert r["relocked"] is False and df.isReadOnly is True  # df fallback is not an observation
    assert "Scratch" in r["warnings"][0] and "document.set_read_only" in r["warnings"][0]
    assert ("close", False) in live.scratch.calls and guard.UNLOCKED == {}


def test_close_lookup_none_while_own_file_read_only_not_relocked(live):
    _registered(live, read_only=True)
    live.app.data.findFileById = lambda i: None
    r = one(run("close", {"confirm": True}))
    assert r["relocked"] is False and "document.set_read_only" in r["warnings"][0]
    assert guard.UNLOCKED == {}


def test_close_lock_never_observed_still_closes(live):
    _registered(live)
    live.app.read_only_reads = None
    r = one(run("close", {"confirm": True}))
    assert r["relocked"] is False and "Scratch" in r["warnings"][0]
    assert ("close", False) in live.scratch.calls and guard.UNLOCKED == {}


def test_close_command_raises_still_closes(live):
    _registered(live)
    live.app.text_command_errors["Deactivate"] = RuntimeError("boom")
    r = one(run("close", {"confirm": True}))
    assert r["relocked"] is False and r["warnings"]
    assert ("close", False) in live.scratch.calls and guard.UNLOCKED == {}


def test_close_outside_folder_limit_still_closes(limited):
    df = limited.proj.rootFolder.add_file("Out")  # root is outside the limit
    limited.scratch.dataFile = df
    limited.app.activeDocument = limited.scratch
    guard.UNLOCKED[df.id] = "Scratch"
    r = one(limited.run("close", {"confirm": True}))
    assert r["relocked"] is False and r["warnings"] and limited.app.commands == []
    assert ("close", False) in limited.scratch.calls and guard.UNLOCKED == {}


def test_close_unregistered_saved_document_untouched(live):
    live.scratch.dataFile = live.root.add_file("Plain")
    live.app.activeDocument = live.scratch
    r = one(run("close", {"confirm": True}))
    assert r["relocked"] is False and "warnings" not in r and live.app.commands == []


def test_close_never_saved_document_untouched(live):
    r = one(run("close", {"confirm": True}))
    assert r["relocked"] is False and "warnings" not in r and live.app.commands == []


def test_close_failure_still_drops_registry_entry(live):
    _registered(live)
    live.scratch.fail.add("close")
    assert eerr(run("close", {"confirm": True}))["code"] == "FUSION_ERROR"
    assert guard.UNLOCKED == {}


def test_close_upload_pending_warns_and_drops(live):
    live.scratch.saveAs("N", live.root, "", "")  # fake dataFile has a local-path id until finished
    live.scratch.dataFile.id = "/tmp/local.f3d"
    live.app.activeDocument = live.scratch
    guard.UNLOCKED["urn:x"] = "N"
    r = one(run("close", {"confirm": True}))
    assert r["relocked"] is False and "N is" in r["warnings"][0] and "document.set_read_only" in r["warnings"][0]
    assert ("close", False) in live.scratch.calls and guard.UNLOCKED == {} and live.app.commands == []


def test_close_help_mentions_relocked(live):
    assert "relocked" in ok(run("help", {"action": "close"}))["help"]


def test_set_name(live):
    r = ok(run("set_name", {"name": "Fresh"}))
    assert r["name"] == "Fresh" and live.scratch.name == "Fresh"
    live.scratch.dataFile = live.root.add_file("P")
    e = err(run("set_name", {"name": "X"}))
    assert e["code"] == "INVALID_PARAMS" and "datafile_set" in e["hints"][0]
    live.app.activeDocument = None
    assert err(run("set_name", {"name": "X"}))["code"] == "NO_DOCUMENT"


def test_close(live):
    other = live.app.documents._new("Other")
    other.isModified = True
    assert err(run("close", {"documents": ["Other"]}))["code"] == "CONFIRM_REQUIRED"
    assert other.calls == []
    r = one(run("close", {"documents": ["Other"], "confirm": True}))
    assert other.calls == [("close", False)]
    assert r["discarded_changes"] is True and r["closed"]["name"] == "Other"
    assert [d.name for d in live.app.documents] == ["Scratch"]
    assert one(run("close", {"confirm": True}))["discarded_changes"] is False
    assert live.app.activeDocument is None
    live2 = live.app.documents._new("Z")
    live2.fail.add("close")
    assert eerr(run("close", {"confirm": True}))["code"] == "FUSION_ERROR"


# --- save, save as, read-only, references update -------------------------------------------------------------


LOCK = "Commands.Start ActiveDocumentDeactivateCommand"
UNLOCK = "Commands.Start ActiveDocumentActivateCommand"


def test_save_as_then_save(live):
    r = one(run("save_as", {"documents": [{"name": "Part"}], "folder": "Proj/Sub", "description": "d"}))
    assert r["saved"] and r["name"] == "Part" and r["file"]["id"] is None and r["file"]["path"] == "Proj/Sub/Part"
    assert r["read_only"] is True and live.scratch.dataFile.isReadOnly is True
    assert live.scratch.calls[-1] == ("saveAs", "Part", live.sub, "d", "")
    assert live.app.commands == [LOCK]  # Make Read-Only after the save
    assert eerr(run("save_as", {"documents": [{"name": "Part"}], "folder": "Proj/Sub"}))["code"] == "INVALID_PARAMS"
    assert err(run("save_as", {"documents": [{"name": "X"}], "folder": "Proj/Gone"}))["code"] == "NOT_FOUND"
    live.scratch.fail.add("saveAs")
    assert eerr(run("save_as", {"documents": [{"name": "Q"}], "folder": "Proj"}))["code"] == "FUSION_ERROR"
    live.scratch.dataFile.finish_upload()
    live.app.commands.clear()
    s = run("save", {"description": "v2"})
    r = s.structured_content["result"]["results"][0]["result"]
    assert r["read_only"] is True and s.structured_content["made_editable"] == ["Part"]
    assert live.app.commands == [UNLOCK, LOCK]  # editable before the save, read-only after it
    assert live.scratch.calls[-1] == ("save", "v2") and live.scratch.dataFile.versionNumber == 2
    live.scratch.fail.add("save")
    assert eerr(run("save"))["code"] == "FUSION_ERROR"


def test_save_never_saved_and_bad_names(live):
    e = eerr(run("save"))
    assert e["code"] == "INVALID_PARAMS" and "save_as" in e["hints"][0]
    assert err(run("save_as", {"documents": [{"name": "../x"}], "folder": "Proj"}))["code"] == "INVALID_PARAMS"
    assert live.scratch.calls == []


def test_save_upload_pending_without_limit_is_invalid(live):
    live.scratch.dataFile = live.root.add_file("P", pending=True)
    e = eerr(run("save"))
    assert e["code"] == "INVALID_PARAMS" and "upload" in e["message"] and live.scratch.calls == []


def test_save_as_by_folder_id_and_document_selector(live):
    other = live.app.documents._new("Other")
    one(run("save_as", {"documents": [{"document": "Other", "name": "O"}], "folder": live.root.id}))
    assert other.dataFile.name == "O" and live.scratch.calls == []


def test_save_as_name_repeated_in_call(live):
    other = live.app.documents._new("Other")
    r = ok(run("save_as", {"documents": [{"document": "Scratch", "name": "N"}, {"document": "Other", "name": "N"}],
                           "folder": "Proj"}))
    assert [x["ok"] for x in r["results"]] == [True, False]
    e = r["results"][1]["error"]
    assert e["code"] == "INVALID_PARAMS" and "twice" in e["message"] and "saveAs" not in [c[0] for c in other.calls]


def test_save_as_without_folder_and_no_limit_is_invalid(live):
    e = err(run("save_as", {"documents": [{"name": "N"}]}))
    assert e["code"] == "INVALID_PARAMS" and "folder" in e["message"] and live.scratch.calls == []


def test_save_as_lock_never_observed(live):
    live.app.read_only_reads = None
    e = eerr(run("save_as", {"documents": [{"name": "N"}], "folder": "Proj"}))
    assert e["code"] == "READ_ONLY_NOT_APPLIED" and "Proj/N" in e["message"] and "set_read_only" in e["hints"][0]
    assert live.scratch.dataFile.name == "N"  # the file is saved either way


def test_save_as_read_only_false_issues_no_command(live):
    r = one(run("save_as", {"documents": [{"name": "N"}], "folder": "Proj", "read_only": False}))
    assert r["read_only"] is False and live.app.commands == [] and live.scratch.dataFile.isReadOnly is False


def _saved(live):
    live.scratch.dataFile = live.root.add_file("P")
    return live.scratch


def test_save_locks_after_save(live):
    doc = _saved(live)
    r = one(run("save"))
    assert r["read_only"] is True and doc.dataFile.isReadOnly is True
    assert live.app.commands == [LOCK] and [c[0] for c in doc.calls] == ["save"]


def test_save_locks_when_data_file_id_becomes_local(live, monkeypatch):
    doc = _saved(live)
    df = doc.dataFile
    orig = core.FusionDocument.save

    def save(self, description):
        done = orig(self, description)
        df.id = "/local/P"  # Fusion: a local path until the new version is uploaded
        return done

    monkeypatch.setattr(core.FusionDocument, "save", save)
    live.app.read_only_reads = 2
    assert one(run("save"))["read_only"] is True and df.isReadOnly is True


def test_save_lock_never_observed_still_saves(live):
    doc = _saved(live)
    live.app.read_only_reads = None
    e = eerr(run("save", {"description": "v2"}))
    assert e["code"] == "READ_ONLY_NOT_APPLIED" and "set_read_only" in e["hints"][0]
    assert doc.dataFile.versionNumber == 2


def test_save_read_only_false_issues_no_command(live):
    doc = _saved(live)
    assert one(run("save", {"read_only": False}))["read_only"] is False
    assert live.app.commands == [] and doc.dataFile.isReadOnly is False


def test_save_read_only_document_unlocks_first_or_preserves(live):
    doc = _saved(live)
    doc.dataFile.isReadOnly = True
    e = eerr(run("save", {"preserve_read_only": True}))
    assert e["code"] == "READ_ONLY" and doc.calls == [] and live.app.commands == []
    s = run("save")
    assert s.structured_content["made_editable"] == ["Scratch"]
    assert live.app.commands == [UNLOCK, LOCK] and [c[0] for c in doc.calls] == ["save"]


def test_set_read_only(live):
    doc = _saved(live)
    prev = live.app.documents._new("Prev")  # active
    r = one(run("set_read_only", {"documents": ["Scratch"]}))
    assert r["changed"] is True and r["read_only"] is True and doc.dataFile.isReadOnly is True
    assert live.app.commands == [LOCK]
    assert doc.calls == [("activate",)] and live.app.activeDocument is prev
    live.app.commands.clear()
    r = one(run("set_read_only", {"read_only": False, "documents": ["Scratch"]}))
    assert r["changed"] is True and live.app.commands == [UNLOCK] and doc.dataFile.isReadOnly is False


def test_set_read_only_active_doc_and_noop(live):
    doc = _saved(live)
    live.app.activeDocument = doc
    one(run("set_read_only"))
    assert doc.calls == [] and live.app.activeDocument is doc
    live.app.commands.clear()
    r = one(run("set_read_only"))
    assert r["changed"] is False and live.app.commands == []


def test_set_read_only_never_observed(live):
    _saved(live)
    live.app.read_only_reads = None
    e = eerr(run("set_read_only"))
    assert e["code"] == "READ_ONLY_NOT_APPLIED" and "read-only" in e["message"] and e["hints"]


def test_set_read_only_when_find_file_raises(live, monkeypatch):
    """Fusion raises "file not found" while a just-saved file is not yet listed: step 2 falls back to df."""
    _saved(live)
    orig = core.Data.findFileById
    calls = []

    def first_raises(self, fid):
        calls.append(fid)
        if len(calls) == 1:
            raise RuntimeError("3 : file not found")
        return orig(self, fid)

    monkeypatch.setattr(core.Data, "findFileById", first_raises)
    assert one(run("set_read_only"))["changed"] is True and live.app.commands == [LOCK]


def test_set_read_only_errors(live):
    assert eerr(run("set_read_only"))["code"] == "INVALID_PARAMS"
    doc = _saved(live)
    live.app.documents._new("Prev")
    doc.fail.add("activate")
    assert eerr(run("set_read_only", {"documents": ["Scratch"]}))["code"] == "FUSION_ERROR"
    assert live.app.commands == []


def test_advance_and_update_all(live):
    a = live.root.add_file("R")
    live.scratch._refs += [core.DocumentReference(a, True), core.DocumentReference(a, False)]
    r = ok(run("advance_to_latest"))
    assert r["updated"] is True and r["references_out_of_date"] == 1
    live.scratch.fail.add("updateAllReferences")
    r = ok(run("update_all_references", {"document": "Scratch"}))
    assert r["updated"] is False
    assert [c[0] for c in live.scratch.calls] == ["advanceToLatest", "updateAllReferences"]


def test_advance_raising_is_reported_not_raised(live, monkeypatch):
    def boom(self):
        raise RuntimeError("Document cannot be advanced to latest")

    monkeypatch.setattr(core.FusionDocument, "advanceToLatest", boom)
    assert ok(run("advance_to_latest"))["updated"] is False


def test_update_unsupported(live, monkeypatch):
    monkeypatch.delattr(core.FusionDocument, "advanceToLatest")
    monkeypatch.delattr(core.FusionDocument, "updateAllReferences")
    assert err(run("advance_to_latest"))["code"] == "UNSUPPORTED"
    assert err(run("update_all_references"))["code"] == "UNSUPPORTED"


# --- data file changes ---------------------------------------------------------------------------------------


def test_datafile_set(live):
    df = live.sub.add_file("Part")
    r = one(run("datafile_set", {"files": [{"file": "Proj/Sub/Part", "name": "New"}]}))
    assert (df.name, r["name"]) == ("New", "New")
    one(run("datafile_set", {"files": [{"file": df.id, "name": "Third"}]}))
    assert df.name == "Third"
    assert err(run("datafile_set", {"files": [{"file": df.id}]}))["code"] == "INVALID_PARAMS"
    assert err(run("datafile_set", {"files": [{"file": df.id, "name": "../x"}]}))["code"] == "INVALID_PARAMS"
    assert df.name == "Third"


def test_datafile_refresh(live):
    df = live.sub.add_file("Part")
    r = one(run("datafile_refresh", {"files": [df.id]}))
    assert r["refreshed"] is True and r["file"]["id"] == df.id
    df.fail.add("refresh")
    assert one(run("datafile_refresh", {"files": [df.id]}))["refreshed"] is False


def test_datafile_copy_and_move(live):
    df = live.sub.add_file("Part")
    c = one(run("datafile_copy", {"files": ["Proj/Sub/Part"], "folder": "Proj"}))
    assert c["id"] != df.id and c["path"] == "Proj/Part" and len(live.root._files) == 1
    m = one(run("datafile_move", {"files": [df.id], "folder": live.root.id}))
    assert m["folder_id"] == live.root.id and m["path"] == "Proj/Part" and live.sub._files == []
    df.fail.update({"copy", "move"})
    assert eerr(run("datafile_copy", {"files": [df.id], "folder": "Proj"}))["code"] == "FUSION_ERROR"
    assert eerr(run("datafile_move", {"files": [df.id], "folder": "Proj/Sub"}))["code"] == "FUSION_ERROR"
    assert err(run("datafile_move", {"files": [df.id], "folder": "Proj/Zzz"}))["code"] == "NOT_FOUND"


def test_datafile_members_unsupported(live, monkeypatch):
    df = live.sub.add_file("Part")
    for member, act, extra in (("refresh", "datafile_refresh", {}), ("copy", "datafile_copy", {"folder": "Proj"}),
                               ("move", "datafile_move", {"folder": "Proj"})):
        monkeypatch.delattr(core.DataFile, member)
        assert eerr(run(act, {"files": [df.id], **extra}))["code"] == "UNSUPPORTED"


def test_datafile_delete(live):
    df = live.sub.add_file("Part")
    assert err(run("datafile_delete", {"files": [df.id]}))["code"] == "CONFIRM_REQUIRED"
    assert live.sub._files == [df]
    live.scratch.dataFile = df
    e = eerr(run("datafile_delete", {"files": [df.id], "confirm": True}))
    assert e["code"] == "INVALID_PARAMS" and "close the document" in e["hints"][0]
    live.scratch.dataFile = None
    r = one(run("datafile_delete", {"files": ["Proj/Sub/Part"], "confirm": True}))
    assert r == {"deleted": {"id": df.id, "name": "Part", "path": "Proj/Sub/Part"}}
    assert live.sub._files == [] and err(run("datafile_get", {"file": df.id}))["code"] == "NOT_FOUND"
    df2 = live.sub.add_file("B")
    df2.fail.add("deleteMe")
    assert eerr(run("datafile_delete", {"files": [df2.id], "confirm": True}))["code"] == "FUSION_ERROR"


# --- export -------------------------------------------------------------------------------------------------


@pytest.fixture
def design_env(live, tmp_path):
    """The scratch document's design with a body; ``tmp_path`` is the output directory."""
    design = live.scratch.products.itemByProductType("DesignProductType")
    body = design.rootComponent.add_body("Box")
    design.rootComponent.add_body("Box2")
    live.design, live.body, live.out = design, body, tmp_path
    return live


def _em(env):
    return env.design.exportManager


def test_export_each_format_by_extension(design_env):
    e = design_env
    for ext, fmt in (("f3d", "f3d"), ("stp", "step"), ("igs", "iges"), ("sat", "sat"), ("smt", "smt"),
                     ("stl", "stl"), ("3mf", "3mf"), ("obj", "obj"), ("usdz", "usdz")):
        p = str(e.out / ("a." + ext))
        r = one(run("export", {"paths": [p]}))
        assert r["format"] == fmt and r["files"] == [p] and r["bytes"] == 4
        assert r["geometry"]["name"] == "Root"
        name, filename, geometry = _em(e).calls[-2]
        assert filename == p and geometry is e.design.rootComponent  # keyword call recorded
    assert eerr(run("export", {"paths": [str(e.out / "a.xyz")]}))["code"] == "INVALID_PARAMS"


def test_export_positional_argument_order(design_env, monkeypatch):
    """Fusion's binding rejects keywords: step takes (filename, geometry), stl takes (geometry, filename)."""
    e = design_env
    seen = {}

    def step(self, *args, **kw):
        seen["step"] = (args, kw)
        return fusion.ExportOptions("step", args[0], args[1])

    def stl(self, *args, **kw):
        seen["stl"] = (args, kw)
        return fusion.MeshExportOptions("stl", args[1], args[0])

    monkeypatch.setattr(fusion.ExportManager, "createSTEPExportOptions", step)
    monkeypatch.setattr(fusion.ExportManager, "createSTLExportOptions", stl)
    one(run("export", {"paths": [str(e.out / "k.step")]}))
    one(run("export", {"paths": [str(e.out / "k.stl")]}))
    assert seen["step"][1] == {} and isinstance(seen["step"][0][0], str)
    assert seen["stl"][1] == {} and isinstance(seen["stl"][0][1], str)


def test_export_format_extension_mismatch_and_both_geometries(design_env):
    e = design_env
    p = str(e.out / "a.stl")
    assert eerr(run("export", {"paths": [p], "format": "step"}))["code"] == "INVALID_PARAMS"
    one(run("export", {"paths": [str(e.out / "b.stp")], "format": "step"}))
    bad = err(run("export", {"paths": [p], "component": "Root", "body": "Box"}))
    assert bad["code"] == "INVALID_PARAMS"
    assert not (e.out / "a.stl").exists()


def test_export_body_rules(design_env):
    e = design_env
    r = one(run("export", {"paths": [str(e.out / "b.stl")], "body": "Box"}))
    assert r["geometry"]["name"] == "Box" and _em(e).calls[0][2] is e.body
    assert eerr(run("export", {"paths": [str(e.out / "b.step")], "body": "Box"}))["code"] == "INVALID_PARAMS"
    one(run("export", {"paths": [str(e.out / "c.step")], "component": "Root"}))


def test_export_mesh_options(design_env):
    e = design_env
    one(run("export", {"paths": [str(e.out / "m.stl")], "refinement": "high", "one_file_per_body": True}))
    opts = _em(e).calls[-1][1]
    assert opts.meshRefinement == fusion.MeshRefinementSettings.MeshRefinementHigh
    assert opts.isOneFilePerBodyInOccurrence is True and opts.isBinaryFormat is True
    assert opts.sendToPrintUtility is False
    one(run("export", {"paths": [str(e.out / "m.obj")], "refinement": "low"}))
    o = _em(e).calls[-1][1]
    assert o.meshRefinement == fusion.MeshRefinementSettings.MeshRefinementLow and o.isBinaryFormat is False
    assert err(run("export", {"paths": [str(e.out / "z.stl")], "refinement": "ultra"}))["code"] == "INVALID_PARAMS"
    assert err(run("export", {"paths": [str(e.out / "z.stl")], "overwrite": True}))["code"] == "INVALID_PARAMS"


def test_export_one_file_per_body_listing(design_env):
    e = design_env
    r = one(run("export", {"paths": [str(e.out / "p.stl")], "one_file_per_body": True}))
    assert r["files"] == [str(e.out / "p_0.stl"), str(e.out / "p_1.stl")] and r["bytes"] == 2
    (e.out / "other.stl").write_text("keep")
    assert sorted(x.name for x in e.out.iterdir()) == ["other.stl", "p_0.stl", "p_1.stl"]


def test_export_never_replaces_existing_files(design_env):
    e = design_env
    (e.out / "x.stl").write_text("old")
    assert eerr(run("export", {"paths": [str(e.out / "x.stl")]}))["code"] == "INVALID_PARAMS"
    assert (e.out / "x.stl").read_text() == "old" and _em(e).calls == []
    (e.out / "y_0.stl").write_text("old")
    r = eerr(run("export", {"paths": [str(e.out / "y.stl")], "one_file_per_body": True}))
    assert r["code"] == "INVALID_PARAMS" and (e.out / "y_0.stl").read_text() == "old"
    assert _em(e).calls == [] and sorted(x.name for x in e.out.iterdir()) == ["x.stl", "y_0.stl"]


def test_export_path_and_state_errors(design_env):
    e = design_env
    assert eerr(run("export", {"paths": ["rel.stl"]}))["code"] == "INVALID_PARAMS"
    assert eerr(run("export", {"paths": [str(e.out / "no" / "a.stl")]}))["code"] == "NOT_FOUND"
    _em(e).fail = True
    assert eerr(run("export", {"paths": [str(e.out / "a.stl")]}))["code"] == "FUSION_ERROR"
    _em(e).fail = False
    e.app.activeDocument = core.FusionDocument("Drw")
    assert err(run("export", {"paths": [str(e.out / "a.stl")]}))["code"] == "NO_DESIGN"


def test_export_licence_restricted_is_unsupported(design_env):
    e = design_env
    e.design.exportManager.execute = lambda opts: (_ for _ in ()).throw(RuntimeError(
        "3 : Cannot execute the export operation to create the file in the format specified by the "
        "ExportOptions as the user is restricted."))
    r = eerr(run("export", {"paths": [str(e.out / "a.iges")]}))
    assert r["code"] == "UNSUPPORTED" and "iges" in r["message"] and "licence" in r["message"]
    assert not (e.out / "a.iges").exists()


def test_export_unsupported_members(design_env, monkeypatch):
    e = design_env
    for method in ("createSTEPExportOptions", "createUSDExportOptions"):
        monkeypatch.delattr(fusion.ExportManager, method)
    assert eerr(run("export", {"paths": [str(e.out / "a.step")]}))["code"] == "UNSUPPORTED"
    assert eerr(run("export", {"paths": [str(e.out / "a.usdz")]}))["code"] == "UNSUPPORTED"
    monkeypatch.delattr(fusion.MeshExportOptions, "meshRefinement")
    one(run("export", {"paths": [str(e.out / "a.stl")]}))  # default value: property skipped
    assert eerr(run("export", {"paths": [str(e.out / "b.stl")], "refinement": "high"}))["code"] == "UNSUPPORTED"
    monkeypatch.delattr(fusion.MeshExportOptions, "isOneFilePerBodyInOccurrence")
    assert eerr(run("export", {"paths": [str(e.out / "c.stl")], "one_file_per_body": True}))["code"] == "UNSUPPORTED"
    monkeypatch.delattr(fusion.MeshExportOptions, "isBinaryFormat")
    monkeypatch.delattr(fusion.MeshExportOptions, "sendToPrintUtility")
    one(run("export", {"paths": [str(e.out / "d.stl")]}))


# --- datafile_upload (design § 6.13, REQ-DOC-029) -----------------------------------------------------------


@pytest.fixture
def up(live, tmp_path):
    """Local .f3d/.f3z files in ``tmp_path``; ``live`` plus ``local(name)`` giving a path."""
    def local(name):
        (tmp_path / name).write_text("x")
        return str(tmp_path / name)

    live.local = local
    return live


def _docs_opened(live):
    return [c for c in live.app.documents.calls if c[0] == "open"]


def test_datafile_upload_entry_shape_and_default_folder(up):
    r = one(run("datafile_upload", {"paths": [up.local("Part.f3d")]}))
    df = next(f for f in up.root._files if f.name == "Part")
    assert r == {"id": df.id, "name": "Part", "path": "Proj/Part", "version_number": 1, "read_only": True}
    assert up.root.upload_calls == [str(up.local("Part.f3d"))] and df.isReadOnly is True
    assert df.fileExtension == "f3d"


def test_datafile_upload_f3z_into_folder_by_path_and_id(up):
    one(run("datafile_upload", {"paths": [up.local("Asm.f3z")], "folder": "Proj/Sub"}))
    assert [f.name for f in up.sub._files] == ["Asm"] and up.sub._files[0].fileExtension == "f3z"
    one(run("datafile_upload", {"paths": [up.local("Two.f3d")], "folder": up.sub.id}))
    assert [f.name for f in up.sub._files] == ["Asm", "Two"]


def test_datafile_upload_locks_through_transient_document_and_restores_state(up):
    one(run("datafile_upload", {"paths": [up.local("Part.f3d")]}))
    assert up.app.commands == ["Commands.Start ActiveDocumentDeactivateCommand"]
    assert len(_docs_opened(up)) == 1
    assert [d.name for d in up.app.documents] == ["Scratch"] and up.app.activeDocument is up.scratch
    assert up.root._files[0].isReadOnly is True and guard.UNLOCKED == {}


def test_datafile_upload_read_only_false_opens_nothing(up):
    r = one(run("datafile_upload", {"paths": [up.local("Part.f3d")], "read_only": False}))
    assert r["read_only"] is False and up.app.commands == [] and up.app.documents.calls == []
    assert up.root._files[0].isReadOnly is False


def test_datafile_upload_lock_never_observed(up):
    up.app.read_only_reads = None
    e = eerr(run("datafile_upload", {"paths": [up.local("Part.f3d")]}))
    df = up.root._files[0]
    assert e["code"] == "READ_ONLY_NOT_APPLIED" and df.id in e["message"] and "Proj/Part" in e["message"]
    assert "document.set_read_only" in e["hints"][0]
    assert [d.name for d in up.app.documents] == ["Scratch"] and up.app.activeDocument is up.scratch


def test_datafile_upload_raising_lock_is_not_applied_and_cleans_up(up, monkeypatch):
    def boom(*a):
        raise RuntimeError("boom")

    monkeypatch.setattr(guard, "set_read_only", boom)
    e = eerr(run("datafile_upload", {"paths": [up.local("Part.f3d")]}))
    assert e["code"] == "READ_ONLY_NOT_APPLIED" and up.root._files[0].id in e["message"] and "Proj/Part" in e["message"]
    assert [d.name for d in up.app.documents] == ["Scratch"] and up.app.activeDocument is up.scratch


def test_datafile_upload_transient_open_failure(up):
    up.app.documents.fail.add("open")
    e = eerr(run("datafile_upload", {"paths": [up.local("Part.f3d")]}))
    assert e["code"] == "READ_ONLY_NOT_APPLIED" and "could not open it to lock it" in e["message"]
    assert "document.set_read_only" in e["hints"][0] and len(up.root._files) == 1


def test_datafile_upload_warns_when_temporary_close_or_activate_fails(up, monkeypatch):
    real_open = up.app.documents.open

    def open_failing_close(df, visible=True):
        doc = real_open(df, visible)
        doc.fail.add("close")
        return doc

    monkeypatch.setattr(up.app.documents, "open", open_failing_close)
    r = one(run("datafile_upload", {"paths": [up.local("A.f3d")]}))
    assert r["read_only"] is True and r["warnings"] == ["closing the temporary document failed (A)"]
    up.scratch.fail.add("activate")
    up.app.documents.item(1).close(False)  # drop the stuck transient document
    r = one(run("datafile_upload", {"paths": [up.local("B.f3d")]}))
    assert r["read_only"] is True and "re-activating the original document failed (Scratch)" in r["warnings"]


def test_datafile_upload_temporary_close_and_activate_raising_warn(up, monkeypatch):
    real_open = up.app.documents.open

    def open_raising_close(df, visible=True):
        doc = real_open(df, visible)

        def boom(save):
            raise RuntimeError("boom")

        doc.close = boom
        return doc

    monkeypatch.setattr(up.app.documents, "open", open_raising_close)

    def boom_activate():
        raise RuntimeError("boom")

    monkeypatch.setattr(up.scratch, "activate", boom_activate)
    r = one(run("datafile_upload", {"paths": [up.local("A.f3d")]}))
    assert r["read_only"] is True
    assert "closing the temporary document failed (A)" in r["warnings"]
    assert "re-activating the original document failed (Scratch)" in r["warnings"]


def test_datafile_upload_deactivates_before_closing_temporary_document(up, monkeypatch):
    log = []
    real_open = up.app.documents.open

    def open_logged(df, visible=True):
        doc = real_open(df, visible)
        real_close = doc.close

        def close(save):
            log.append("close")
            return real_close(save)

        doc.close = close
        return doc

    monkeypatch.setattr(up.app.documents, "open", open_logged)
    real_cmd = up.app.executeTextCommand
    monkeypatch.setattr(up.app, "executeTextCommand", lambda c: (log.append(c), real_cmd(c))[1])
    one(run("datafile_upload", {"paths": [up.local("A.f3d")]}))
    assert log == [LOCK, "close"]


def test_datafile_upload_unlistable_folder_fails_before_uploading(up, monkeypatch):
    def boom(self):
        raise RuntimeError("boom")

    monkeypatch.setattr(type(up.root), "dataFiles", property(boom))
    e = eerr(run("datafile_upload", {"paths": [up.local("A.f3d")]}))
    assert e["code"] == "FUSION_ERROR" and "could not list" in e["message"]
    assert up.root.upload_calls == [] and up.app.documents.calls == []


def test_datafile_upload_upload_failures_are_fusion_errors(up, monkeypatch):
    up.root.upload = "fail"
    e = eerr(run("datafile_upload", {"paths": [up.local("A.f3d")]}))
    assert e["code"] == "FUSION_ERROR" and "A.f3d" in e["message"] and "failed" in e["message"]
    up.root.upload = "none"
    assert eerr(run("datafile_upload", {"paths": [up.local("B.f3d")]}))["code"] == "FUSION_ERROR"
    monkeypatch.setattr(doc_mod, "UPLOAD_WAIT", 0)
    up.root.upload = "hang"
    e = eerr(run("datafile_upload", {"paths": [up.local("C.f3d")]}))
    assert e["code"] == "FUSION_ERROR" and "did not finish" in e["message"]
    assert "document.list_folders" in e["hints"][0] and "document.set_read_only" in e["hints"][0]
    assert up.root._files == [] and up.app.documents.calls == []


def test_datafile_upload_long_list_with_hanging_future_becomes_a_job(up, monkeypatch):
    monkeypatch.setattr(doc_mod, "UPLOAD_WAIT", 0.6)
    up.root.upload = "hang"
    act = next(a for a in actions.registry() if (a.tool, a.name) == ("document", "datafile_upload"))
    monkeypatch.setattr(act, "timeout", 0.3)
    r = ok(run("datafile_upload", {"paths": [up.local("a.f3d"), up.local("b.f3d"), up.local("c.f3d")]}))
    assert r["status"] == "running" and r["job_id"]
    import time

    end = time.monotonic() + 10
    while time.monotonic() < end and actions.dispatch.get_job(r["job_id"])["state"] == "running":
        time.sleep(0.05)
    job = actions.dispatch.get_job(r["job_id"])
    assert job["state"] == "done" and job["result"]["failed"] == 3


def test_datafile_upload_path_and_name_errors(up):
    for path, code in (("/no/such/x.f3d", "NOT_FOUND"), ("rel.f3d", "INVALID_PARAMS"),
                       (up.local("x.step"), "INVALID_PARAMS")):
        e = eerr(run("datafile_upload", {"paths": [path]}))
        assert e["code"] == code and (code == "NOT_FOUND" or e["hints"]), (path, e)
    up.root.add_file("Taken")
    e = eerr(run("datafile_upload", {"paths": [up.local("Taken.f3d")]}))
    assert e["code"] == "INVALID_PARAMS" and "Taken" in e["message"] and "rename" in e["hints"][0]
    assert up.root.upload_calls == []


def test_datafile_upload_repeated_stem_is_one_upload(up, tmp_path, monkeypatch):
    monkeypatch.setattr(doc_mod, "UPLOAD_WAIT", 0)
    up.root.upload = "hang"  # the first entry leaves no file behind, so only the stem check can catch the second
    (tmp_path / "sub").mkdir()
    other = tmp_path / "sub" / "Same.f3z"
    other.write_text("x")
    r = ok(run("datafile_upload", {"paths": [up.local("Same.f3d"), str(other)]}))
    assert (r["ok"], r["failed"]) == (0, 2)
    e = r["results"][1]["error"]
    assert e["code"] == "INVALID_PARAMS" and "appears twice" in e["message"] and e["hints"]
    assert len(up.root.upload_calls) == 1


def test_datafile_upload_unsupported_without_upload_file(up, monkeypatch):
    monkeypatch.delattr(core.DataFolder, "uploadFile")
    assert eerr(run("datafile_upload", {"paths": [up.local("A.f3d")]}))["code"] == "UNSUPPORTED"


def test_datafile_upload_folder_default_without_active_project(live, tmp_path):
    live.app.data.activeProject = None
    f = tmp_path / "A.f3d"
    f.write_text("x")
    e = err(run("datafile_upload", {"paths": [str(f)]}))
    assert e["code"] == "INVALID_PARAMS" and "no active project" in e["message"] and e["hints"]


def _f3z(up, extra=("Child",), **kw):
    up.root.upload_extra = list(extra)
    for k, v in kw.items():
        setattr(up.root, k, v)
    return up.local("Asm.f3z")


def test_datafile_upload_multi_file_returns_stem_root_with_created_and_locks_all(up):
    r = one(run("datafile_upload", {"paths": [_f3z(up, ("Child", "Leaf"))]}))
    files = {f.name: f for f in up.root._files}
    assert r["name"] == "Asm" and r["id"] == files["Asm"].id and r["read_only"] is True
    assert [c["name"] for c in r["created"]] == ["Asm", "Child", "Leaf"]
    assert r["created"][1] == {"id": files["Child"].id, "name": "Child", "path": "Proj/Child"}
    assert all(f.isReadOnly is True for f in files.values()) and len(_docs_opened(up)) == 3
    assert [d.name for d in up.app.documents] == ["Scratch"] and up.app.activeDocument is up.scratch


def test_datafile_upload_single_file_has_no_created(up):
    assert "created" not in one(run("datafile_upload", {"paths": [up.local("Part.f3d")]}))


def test_datafile_upload_multi_file_root_falls_back_to_returned_file(up):
    up.root.upload_extra = ["Other"]
    up.root.upload = "finish"
    f = up.local("Asm.f3z")
    orig = up.root.uploadFile

    def rename(name):
        fut = orig(name)
        next(x for x in up.root._files if x.name == "Asm").name = "Renamed"
        return fut

    up.root.uploadFile = rename
    r = one(run("datafile_upload", {"paths": [f]}))
    assert r["name"] == "Other" and [c["name"] for c in r["created"]] == ["Other", "Renamed"]


def test_datafile_upload_multi_file_one_never_locks_names_it(up, monkeypatch):
    real = guard.set_read_only

    def pick(ctx, doc, flag, lookup):
        if doc.name == "Child":
            return False
        return real(ctx, doc, flag, lookup)

    monkeypatch.setattr(guard, "set_read_only", pick)
    e = eerr(run("datafile_upload", {"paths": [_f3z(up)]}))
    child = next(f for f in up.root._files if f.name == "Child")
    assert e["code"] == "READ_ONLY_NOT_APPLIED" and child.id in e["message"] and "Proj/Child" in e["message"]
    assert "Proj/Asm" not in e["message"] and up.app.activeDocument is up.scratch


def test_datafile_upload_unlisted_created_file(up):
    e = eerr(run("datafile_upload", {"paths": [_f3z(up, (), upload_unlisted=True)]}))
    assert e["code"] == "READ_ONLY_NOT_APPLIED" and "an unlisted file in Proj" in e["message"]
    calls = len(up.root.upload_calls)
    e = eerr(run("datafile_upload", {"paths": [up.local("Other.f3d")], "read_only": False}))
    assert e["code"] == "FUSION_ERROR" and len(up.root.upload_calls) == calls  # the unlisted item persists


def test_datafile_upload_waits_for_cloud_ids_of_created_files(up, monkeypatch):
    f = _f3z(up, ())
    orig, made = up.root.uploadFile, []

    def upload(name):
        fut = orig(name)
        made.append(up.root._add_file("Late", "f3d", pending=True))  # listed with a local id at first
        return fut

    up.root.uploadFile = upload
    sleeps = []

    def tick(_):
        sleeps.append(1)
        made[0].finish_upload()

    monkeypatch.setattr(doc_mod.time, "sleep", tick)
    r = one(run("datafile_upload", {"paths": [f]}))
    assert sleeps and [c["name"] for c in r["created"]] == ["Asm", "Late"] and r["created"][1]["id"].startswith("urn:")


def test_datafile_upload_none_future_has_list_folders_hint(up):
    up.root.upload = "none"
    e = eerr(run("datafile_upload", {"paths": [up.local("A.f3d")]}))
    assert "document.list_folders" in e["hints"][0] and "retry" in e["hints"][0]


def test_folder_limit_datafile_upload_defaults_to_limit_and_limits_folder(limited):
    f = limited.out / "A.f3d"
    f.write_text("x")
    r = one(limited.run("datafile_upload", {"paths": [str(f)]}))
    assert r["path"] == "P/A/A" and limited.a.upload_calls == [str(f)]
    e = err(limited.run("datafile_upload", {"paths": [str(f)], "folder": "P/X"}))
    assert e["code"] == "NOT_ALLOWED" and limited.x.upload_calls == []
    assert one(limited.run("datafile_upload", {"paths": [str(f)], "folder": "P/A/B"}))["path"] == "P/A/B/A"


# --- import and mesh import ----------------------------------------------------------------------------------


@pytest.fixture
def src(tmp_path):
    out = {}
    for ext in ("step", "stp", "f3d", "dxf", "svg", "stl", "obj", "3mf", "txt"):
        f = tmp_path / ("m." + ext)
        f.write_text("x")
        out[ext] = str(f)
    return out


def _im(env):
    return env.app.importManager


def test_import_new_document_default_for_3d(live, src):
    r = one(run("import", {"paths": [src["stp"]]}))
    assert r["target"] == "new_document" and r["created"] == [] and r["document"]["name"] == "Imported"
    assert live.app.activeDocument.name == "Imported" and live.app.documents.count == 2
    assert [c[0] for c in _im(live).calls] == ["create_step", "importToNewDocument"]
    one(run("import", {"paths": [src["f3d"]], "target": "new_document"}))
    assert _im(live).calls[-2][0] == "create_f3d"
    _im(live).fail.add("importToNewDocument")
    assert eerr(run("import", {"paths": [src["step"]]}))["code"] == "FUSION_ERROR"


def test_import_active_created_diff(live, src):
    comp = live.scratch.products.itemByProductType("DesignProductType").rootComponent
    comp.add_body("Existing")
    comp.add_occurrence("Old")
    r = one(run("import", {"paths": [src["step"]], "target": "active"}))
    assert r["target"] == "active" and r["document"]["name"] == "Scratch"
    assert [(c["type"], c["name"]) for c in r["created"]] == [
        ("Occurrence", "Imported occ"), ("BRepBody", "Imported loose body")]
    _im(live).fail.add("importToTarget")
    assert eerr(run("import", {"paths": [src["step"]], "target": "active"}))["code"] == "FUSION_ERROR"


def test_import_active_component(live, src):
    design = live.scratch.products.itemByProductType("DesignProductType")
    occ = design.rootComponent.add_occurrence("Sub")
    one(run("import", {"paths": [src["step"]], "target": "active", "component": occ.component.name}))
    assert _im(live).calls[-1][2] is occ.component and len(occ.component._bodies) == 1


def test_import_2d(live, src):
    design = live.scratch.products.itemByProductType("DesignProductType")
    r = one(run("import", {"paths": [src["dxf"]]}))
    assert r["target"] == "active" and [c["type"] for c in r["created"]] == ["Sketch"]
    assert _im(live).calls[0] == ("create_dxf", src["dxf"], design.rootComponent.xYConstructionPlane)
    r = one(run("import", {"paths": [src["svg"]], "plane": "XZ"}))
    kind, path = _im(live).calls[2][:2]
    assert kind == "create_svg" and _im(live).calls[3][1].planarEntity is design.rootComponent.xZConstructionPlane
    assert eerr(run("import", {"paths": [src["dxf"]], "target": "new_document"}))["code"] == "INVALID_PARAMS"


def test_import_rejects_f3z_before_any_fusion_call(live, src, tmp_path):
    z = tmp_path / "m.f3z"
    z.write_text("x")
    for params in ({"paths": [str(z)]}, {"paths": [src["f3d"]], "format": "f3z"},
                   {"paths": [str(z)], "format": "f3d"}, {"paths": [str(z)], "format": "f3z", "target": "active"}):
        e = eerr(run("import", params))
        assert e["code"] == "INVALID_PARAMS" and "use document.datafile_upload then document.open" in e["message"], params
    assert _im(live).calls == []
    one(run("import", {"paths": [src["f3d"]]}))
    assert [c[0] for c in _im(live).calls] == ["create_f3d", "importToNewDocument"]


def test_import_param_errors(live, src, tmp_path):
    for params in ({"path": src["step"], "component": "Root"},
                   {"path": src["step"], "plane": "XY"},
                   {"path": src["step"], "target": "active", "plane": "XY"},
                   {"path": src["txt"]},
                   {"path": src["stl"]},
                   {"path": src["step"], "format": "f3d"},
                   {"path": "rel.step"}):
        assert eerr(run("import", {k if k != "path" else "paths": v if k != "path" else [v] for k, v in params.items()}))["code"] == "INVALID_PARAMS", params
    assert eerr(run("import", {"paths": [str(tmp_path / "gone.step")]}))["code"] == "NOT_FOUND"
    assert _im(live).calls == []
    live.app.activeDocument = None
    assert eerr(run("import", {"paths": [src["step"]], "target": "active"}))["code"] == "NO_DESIGN"


def test_import_unsupported_members(live, src, monkeypatch):
    monkeypatch.delattr(core.ImportManager, "createSTEPImportOptions")
    assert eerr(run("import", {"paths": [src["step"]]}))["code"] == "UNSUPPORTED"
    monkeypatch.delattr(core.ImportManager, "importToTarget")
    assert eerr(run("import", {"paths": [src["f3d"]], "target": "active"}))["code"] == "UNSUPPORTED"
    monkeypatch.delattr(core.ImportManager, "createDXF2DImportOptions")
    assert eerr(run("import", {"paths": [src["dxf"]]}))["code"] == "UNSUPPORTED"


def _root(env):
    return env.scratch.products.itemByProductType("DesignProductType").rootComponent


def test_import_mesh_parametric_has_base_feature(live, src):
    r = one(run("import_mesh", {"paths": [src["stl"]]}))
    assert r["base_feature"]["type"] == "BaseFeature" and len(r["meshes"]) == 1
    assert r["bbox"] == {"min": [0.0, 0.0, 0.0], "max": [10.0, 10.0, 10.0]}
    path, units, bf = _root(live).mesh_calls[0]
    assert path == src["stl"] and units == fusion.MeshUnits.MillimeterMeshUnit
    assert bf.edits == ["start", "finish"]


def test_import_mesh_direct_has_none_and_units(live, src):
    design = live.scratch.products.itemByProductType("DesignProductType")
    design.designType = fusion.DesignTypes.DirectDesignType
    r = one(run("import_mesh", {"paths": [src["obj"]], "unit": "in", "format": "obj"}))
    assert r["base_feature"] is None and _root(live).mesh_calls[0][1:] == (fusion.MeshUnits.InchMeshUnit, None)
    assert [m["name"] for m in r["meshes"]] == ["mesh0"]


def test_import_mesh_bbox_union_units_and_missing(live, src):
    comp = _root(live)
    comp.mesh_import = {"count": 2, "bbox": ((1, 2, 3), (4, 5, 6))}
    r = one(run("import_mesh", {"paths": [src["3mf"]]}))
    assert r["bbox"] == {"min": [10.0, 20.0, 30.0], "max": [40.0, 50.0, 60.0]}
    mi = one(run("import_mesh", {"paths": [src["stl"]]}, units={"length": "cm"}))
    assert mi["bbox"]["max"] == [4.0, 5.0, 6.0]
    comp.mesh_import = {"count": 1, "bbox": None}
    assert one(run("import_mesh", {"paths": [src["stl"]]}))["bbox"] is None


def test_import_mesh_errors(live, src, monkeypatch):
    comp = _root(live)
    assert eerr(run("import_mesh", {"paths": [src["step"]]}))["code"] == "INVALID_PARAMS"
    assert err(run("import_mesh", {"paths": [src["stl"]], "unit": "km"}))["code"] == "INVALID_PARAMS"
    assert eerr(run("import_mesh", {"paths": [src["stl"]], "format": "obj"}))["code"] == "INVALID_PARAMS"
    comp.mesh_import = {"count": 0, "bbox": None}
    assert eerr(run("import_mesh", {"paths": [src["stl"]]}))["code"] == "FUSION_ERROR"
    assert comp._features[-1].edits == ["start", "finish"]
    monkeypatch.delattr(fusion.MeshBodies, "add")
    assert eerr(run("import_mesh", {"paths": [src["stl"]]}))["code"] == "UNSUPPORTED"
    live.app.activeDocument = None
    assert err(run("import_mesh", {"paths": [src["stl"]]}))["code"] == "NO_DESIGN"


# --- cross-cutting -----------------------------------------------------------------------------------------


EXPECTED = {
    "list_open": ("read", 30), "list_hubs": ("read", 30), "list_projects": ("read", 60),
    "list_folders": ("read", 60), "resolve_folder": ("read", 60), "datafile_get": ("read", 30),
    "list_references": ("read", 60), "export": ("read", 120), "create": ("write", 60),
    "open": ("write", 180), "activate": ("write", 30), "save": ("write", 180),
    "save_as": ("write", 180), "set_name": ("write", 30), "advance_to_latest": ("write", 180),
    "update_all_references": ("write", 180), "set_read_only": ("write", 60),
    "datafile_set": ("write", 60), "datafile_refresh": ("write", 60), "datafile_copy": ("write", 120),
    "datafile_move": ("write", 120), "datafile_upload": ("write", 300), "import": ("write", 300), "import_mesh": ("write", 180),
    "close": ("destructive", 120), "datafile_delete": ("destructive", 60),
}


def test_every_action_registered_with_access_and_timeout(live):
    listed = {a["name"]: a["access"] for a in ok(run("help"))["actions"]}
    assert listed == {n: a for n, (a, _) in EXPECTED.items()}
    for name, (access, timeout) in EXPECTED.items():
        h = ok(run("help", {"action": name}))
        assert (h["access"], h["timeout"]) == (access, timeout), name


def test_no_action_opens_a_transaction(live, src):
    design = live.scratch.products.itemByProductType("DesignProductType")
    design.rootComponent.add_body("Box")
    df = live.sub.add_file("Part")
    gone = live.root.add_file("Gone")
    live.scratch.dataFile = live.root.add_file("Saved")
    out = src["txt"].rsplit("/", 1)[0]
    calls = [  # every action once, in an order that keeps each call valid
        ("list_open", {}), ("list_hubs", {}), ("list_projects", {}), ("list_folders", {"folder": "Proj"}),
        ("resolve_folder", {"folder": "Proj"}), ("datafile_get", {"file": df.id}), ("list_references", {}),
        ("export", {"paths": [out + "/e.stl"]}), ("create", {}), ("set_name", {"name": "N"}),
        ("open", {"files": [df.id]}), ("activate", {"document": "Scratch"}), ("save", {"description": "d"}),
        ("save_as", {"folder": "Proj", "documents": [{"name": "SA"}]}), ("advance_to_latest", {}),
        ("update_all_references", {}), ("set_read_only", {"documents": ["Part"]}),
        ("datafile_set", {"files": [{"file": df.id, "name": "x"}]}), ("datafile_refresh", {"files": [df.id]}),
        ("datafile_copy", {"files": [df.id], "folder": "Proj"}),
        ("datafile_move", {"files": [gone.id], "folder": "Proj/Sub"}),
        ("datafile_delete", {"files": [gone.id], "confirm": True}),
        ("datafile_upload", {"paths": [src["f3d"]]}),
        ("import_mesh", {"paths": [src["stl"]]}), ("import", {"paths": [src["step"]]}), ("close", {"confirm": True}),
    ]
    assert {n for n, _ in calls} == set(EXPECTED)
    for name, params in calls:
        r = run(name, params)
        assert r.is_error is False, name
        assert r.structured_content["result"].get("failed", 0) == 0, (name, r.structured_content)
    assert live.app.commands  # set_read_only ran its text command
    assert not [c for c in live.app.commands if c.startswith("PTransaction")]


@pytest.mark.parametrize("cls_name, member, action_name, params", [
    ("core.FusionDocument", "advanceToLatest", "advance_to_latest", {}),
    ("core.FusionDocument", "updateAllReferences", "update_all_references", {}),
    ("core.FusionDocument", "allDocumentReferences", "list_references", {"nested": True}),
    ("core.DataFile", "refresh", "datafile_refresh", {"files": ["Proj/Part"]}),
    ("core.DataFile", "copy", "datafile_copy", {"files": ["Proj/Part"], "folder": "Proj"}),
    ("core.DataFile", "move", "datafile_move", {"files": ["Proj/Part"], "folder": "Proj"}),
    ("core.ImportManager", "importToTarget", "import", {"paths": ["{f3d}"], "target": "active"}),
    ("core.ImportManager", "createFusionArchiveImportOptions", "import", {"paths": ["{f3d}"]}),
    ("core.ImportManager", "createSTEPImportOptions", "import", {"paths": ["{step}"]}),
    ("core.ImportManager", "createIGESImportOptions", "import", {"paths": ["{iges}"]}),
    ("core.ImportManager", "createSATImportOptions", "import", {"paths": ["{sat}"]}),
    ("core.ImportManager", "createSMTImportOptions", "import", {"paths": ["{smt}"]}),
    ("core.ImportManager", "createDXF2DImportOptions", "import", {"paths": ["{dxf}"]}),
    ("core.ImportManager", "createSVGImportOptions", "import", {"paths": ["{svg}"]}),
    ("fusion.ExportManager", "createFusionArchiveExportOptions", "export", {"paths": ["{out}/a.f3d"]}),
    ("fusion.ExportManager", "createSTEPExportOptions", "export", {"paths": ["{out}/a.step"]}),
    ("fusion.ExportManager", "createIGESExportOptions", "export", {"paths": ["{out}/a.iges"]}),
    ("fusion.ExportManager", "createSATExportOptions", "export", {"paths": ["{out}/a.sat"]}),
    ("fusion.ExportManager", "createSMTExportOptions", "export", {"paths": ["{out}/a.smt"]}),
    ("fusion.ExportManager", "createSTLExportOptions", "export", {"paths": ["{out}/a.stl"]}),
    ("fusion.ExportManager", "createC3MFExportOptions", "export", {"paths": ["{out}/a.3mf"]}),
    ("fusion.ExportManager", "createOBJExportOptions", "export", {"paths": ["{out}/a.obj"]}),
    ("fusion.ExportManager", "createUSDExportOptions", "export", {"paths": ["{out}/a.usdz"]}),
    ("fusion.MeshExportOptions", "meshRefinement", "export", {"paths": ["{out}/a.stl"], "refinement": "high"}),
    ("fusion.MeshExportOptions", "isOneFilePerBodyInOccurrence", "export",
     {"paths": ["{out}/a.stl"], "one_file_per_body": True}),
    ("fusion.MeshBodies", "add", "import_mesh", {"paths": ["{stl}"]}),
])
def test_each_feature_detected_member_missing_is_unsupported(design_env, src, monkeypatch,
                                                              cls_name, member, action_name, params):
    mod, cls = cls_name.split(".")
    monkeypatch.delattr(getattr(core if mod == "core" else fusion, cls), member)
    design_env.sub.add_file("Part")
    design_env.root.add_file("Part")
    files = {**{k: str(design_env.out / ("m." + k)) for k in ("f3d", "step", "iges", "sat", "smt", "dxf", "svg", "stl")},
             "out": str(design_env.out)}
    for path in files.values():
        if path != files["out"]:
            open(path, "w").write("x")
    fmt = lambda v: v.format(**files) if isinstance(v, str) else v  # noqa: E731
    params = {k: [fmt(x) for x in v] if isinstance(v, list) else fmt(v) for k, v in params.items()}
    r = run(action_name, params)
    e = err(r) if r.is_error else eerr(r)  # an unsupported shared member fails the call, a per-entry one the entry
    assert e["code"] == "UNSUPPORTED"


def test_saved_scenarios_freeze_before_close_and_delete(monkeypatch):
    """Every saved-doc scenario ends with set_read_only(true) before the doc is closed (Personal editable-doc cap)."""
    import asyncio
    calls = []

    async def fake_env(session, act, params):
        calls.append((act, params.get("read_only"), params.get("documents")))
        return _listed(act, {"id": "x", "active": True, "items": [], "name": "n", "read_only": True,
                             "version_number": 2, "saved": True})

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(doc_mod, "_env", fake_env)
    monkeypatch.setattr(doc_mod, "_uploaded", noop)
    monkeypatch.setattr(doc_mod, "_gone", noop)
    monkeypatch.setattr(doc_mod.asyncio, "sleep", noop)
    monkeypatch.setattr(doc_mod, "_project", lambda s: _const("P"))
    monkeypatch.setattr(doc_mod, "_active_id", lambda s: _const("scratch"))

    async def body():
        async with doc_mod._saved(None):
            pass

    asyncio.run(body())
    acts = [c[0] for c in calls]
    assert ("set_read_only", True) in [(c[0], c[1]) for c in calls]
    assert acts.index("set_read_only") < acts.index("close") < acts.index("datafile_delete")


async def _const(v):
    return v


def _listed(act, result):
    """A fake ok envelope; list actions (§ 2.7) wrap ``result`` as their one entry."""
    if act in LIST_PARAMS:
        result = {"results": [{"input": None, "ok": True, "result": result}], "ok": 1, "failed": 0}
    return {"ok": True, "result": result}


def test_live_scenarios_new_tags():
    tags = {name: tag for name, tag, _ in doc_mod.LIVE_SCENARIOS}
    assert {k: tags[k] for k in ("bulk", "save_locks", "auto_unlock", "folder_limit")} == {
        "bulk": "document.open", "save_locks": "document.save", "auto_unlock": "sketch.create",
        "folder_limit": "document.save_as"}


def test_set_read_only_scenario_ends_read_only(monkeypatch):
    import asyncio
    calls = []
    state = {"ro": True}  # save_as locks the file

    async def fake_env(session, act, params):
        calls.append((act, params.get("read_only")))
        changed = None
        if act == "set_read_only":
            changed, state["ro"] = state["ro"] != params["read_only"], params["read_only"]
        return _listed(act, {"read_only": state["ro"], "id": "x", "changed": changed})

    async def noop(*a, **k):
        return None

    async def poll(session, path, field, want, seconds):
        return state["ro"]

    monkeypatch.setattr(doc_mod, "_env", fake_env)
    monkeypatch.setattr(doc_mod, "_uploaded", noop)
    monkeypatch.setattr(doc_mod, "_gone", noop)
    monkeypatch.setattr(doc_mod, "_poll", poll)
    monkeypatch.setattr(doc_mod.asyncio, "sleep", noop)
    monkeypatch.setattr(doc_mod, "_project", lambda s: _const("P"))
    monkeypatch.setattr(doc_mod, "_active_id", lambda s: _const("scratch"))
    asyncio.run(doc_mod._set_read_only(None))
    ro = [c[1] for c in calls if c[0] == "set_read_only"]
    assert ro[:3] == [False, True, True] and ro[-1] is True


# --- list actions, edits_document, guard (design § 2.7, § 3, § 10.1) -------------------------------------

EDITS = {
    "list_open": "none", "list_hubs": "none", "list_projects": "none", "list_folders": "none",
    "resolve_folder": "none", "datafile_get": "none", "list_references": "none", "export": "none",
    "create": "none", "open": "none", "activate": "none", "save": "action", "save_as": "none",
    "set_name": "none", "advance_to_latest": "action", "update_all_references": "action",
    "set_read_only": "none", "datafile_set": "none", "datafile_refresh": "none", "datafile_copy": "none",
    "datafile_move": "none", "datafile_upload": "none", "import": "action", "import_mesh": "active", "close": "none",
    "datafile_delete": "none",
}

# action -> (list parameter, singular parameter)
LIST_PARAMS = {
    "export": ("paths", "path"), "open": ("files", "file"), "close": ("documents", "document"),
    "datafile_set": ("files", "file"), "datafile_refresh": ("files", "file"),
    "datafile_copy": ("files", "file"), "datafile_move": ("files", "file"),
    "datafile_delete": ("files", "file"), "datafile_upload": ("paths", "path"),
    "import": ("paths", "path"), "import_mesh": ("paths", "path"),
    "save": ("documents", "document"), "save_as": ("documents", "name"), "set_read_only": ("documents", "document"),
}


@pytest.fixture(autouse=True)
def no_verify_delay(monkeypatch):
    from defusion import guard

    monkeypatch.setattr(guard, "VERIFY_DELAY", 0)


@pytest.fixture
def lists(design_env, src):
    """Saved files A and B in Proj/Sub, open documents "DA" and "DB", and per-action list calls."""
    e = design_env
    a, b = e.sub.add_file("A"), e.sub.add_file("B")
    e.app.documents._new("DA", e.root.add_file("DA"))
    e.app.documents._new("DB", e.root.add_file("DB"))
    e.app.activeDocument = e.scratch
    out = str(e.out)
    for n in ("u1", "u3"):
        (e.out / (n + ".f3d")).write_text("x")
    gone = "Proj/Gone"
    files3 = ["Proj/Sub/A", gone, "Proj/Sub/B"]
    # action -> (3-entry params with a bad middle entry, middle code, singular params)
    e.cases = {
        "export": ({"paths": [out + "/x1.stl", "rel.stl", out + "/x2.stl"]}, "INVALID_PARAMS",
                   {"path": out + "/x3.stl"}),
        "open": ({"files": files3}, "NOT_FOUND", {"file": "Proj/Sub/A"}),
        "close": ({"documents": ["DA", "nope", "DB"], "confirm": True}, "NOT_FOUND",
                  {"document": "DA", "confirm": True}),
        "datafile_set": ({"files": [{"file": "Proj/Sub/A", "name": "A2"}, {"file": gone, "name": "G"},
                                    {"file": "Proj/Sub/B", "name": "B2"}]}, "NOT_FOUND",
                         {"file": "Proj/Sub/A", "name": "A3"}),
        "datafile_refresh": ({"files": files3}, "NOT_FOUND", {"file": "Proj/Sub/A"}),
        "datafile_copy": ({"files": files3, "folder": "Proj"}, "NOT_FOUND", {"file": "Proj/Sub/A", "folder": "Proj"}),
        "datafile_move": ({"files": files3, "folder": "Proj"}, "NOT_FOUND", {"file": "Proj/Sub/A", "folder": "Proj"}),
        "datafile_delete": ({"files": files3, "confirm": True}, "NOT_FOUND", {"file": "Proj/Sub/A", "confirm": True}),
        "datafile_upload": ({"paths": [out + "/u1.f3d", out + "/gone.f3d", out + "/u3.f3d"]}, "NOT_FOUND",
                            {"path": out + "/u1.f3d"}),
        "import": ({"paths": [src["step"], out + "/gone.step", src["stp"]]}, "NOT_FOUND", {"path": src["step"]}),
        "import_mesh": ({"paths": [src["stl"], out + "/gone.stl", src["obj"]]}, "NOT_FOUND", {"path": src["stl"]}),
        "save": ({"documents": ["DA", "nope", "DB"]}, "NOT_FOUND", {"document": "DA"}),
        "save_as": ({"documents": [{"document": "DA", "name": "N1"}, {"document": "nope", "name": "N2"},
                                  {"document": "DB", "name": "N3"}], "folder": "Proj"}, "NOT_FOUND",
                    {"document": "DA", "name": "N4", "folder": "Proj"}),
        "set_read_only": ({"documents": ["DA", "nope", "DB"]}, "NOT_FOUND", {"document": "DA"}),
    }
    e.files = (a, b)
    return e


def test_every_action_declares_edits_document():
    got = {a.name: a.edits_document for a in actions.registry() if a.tool == "document"}
    assert got == EDITS


@pytest.mark.parametrize("act", sorted(LIST_PARAMS))
def test_list_action_bad_middle_entry_fails_alone(lists, act):
    params, code, _ = lists.cases[act]
    r = ok(run(act, params))
    assert (r["ok"], r["failed"]) == (2, 1)
    lp = LIST_PARAMS[act][0]
    want = [x.model_dump() if hasattr(x, "model_dump") else x for x in params[lp]]
    assert [x["input"] for x in r["results"]] == want
    assert [x["ok"] for x in r["results"]] == [True, False, True]
    assert r["results"][1]["error"]["code"] == code


@pytest.mark.parametrize("act", sorted(LIST_PARAMS))
def test_list_action_singular_param_is_invalid_with_schema(lists, act):
    lp, singular = LIST_PARAMS[act]
    e = err(run(act, lists.cases[act][2]))
    assert e["code"] == "INVALID_PARAMS"
    assert lp in e["schema"]["properties"] and singular not in e["schema"]["properties"]
    assert any(h.startswith(singular + ":") for h in e["hints"]), e["hints"]


@pytest.mark.parametrize("n", [0, 51])
@pytest.mark.parametrize("act", sorted(LIST_PARAMS))
def test_list_action_bounds_make_no_fusion_call(lists, monkeypatch, act, n):
    seen = []
    monkeypatch.setattr(Ctx, "bulk", lambda self, entries, fn: seen.append(entries))
    params, _, _ = lists.cases[act]
    lp = LIST_PARAMS[act][0]
    params = {**params, lp: [params[lp][0]] * n}
    docs = [d.name for d in lists.app.documents]
    e = err(run(act, params))
    assert e["code"] == "INVALID_PARAMS"
    assert seen == [] and [d.name for d in lists.app.documents] == docs
    assert lists.app.documents.calls == [] and _im(lists).calls == [] and _em(lists).calls == []
    assert [f.name for f in lists.sub._files] == ["A", "B"] and all(f.calls == [] for f in lists.files)


@pytest.mark.parametrize("act", sorted(LIST_PARAMS))
def test_list_action_help_names_list_param_bound_and_result(live, act):
    h = " ".join(ok(run("help", {"action": act}))["help"].split())
    assert LIST_PARAMS[act][0] in h and "50" in h and "results" in h
    if act in ("save", "save_as", "datafile_upload"):
        assert "read_only" in h
    if act in ("save_as", "datafile_upload"):
        assert "folder limit" in h
    if act == "datafile_upload":
        assert "f3z" in h and "document.open" in h
        assert "120 s + created files" in h and "more than 2 entries" in h


def test_set_read_only_help_states_per_entry_worst_case(live):
    h = " ".join(ok(run("help", {"action": "set_read_only"}))["help"].split())
    assert "10 tries, 1 s apart" in h and "about 10 s per entry" in h


def test_close_documents_omitted_is_active_document(lists):
    lists.app.activeDocument = lists.app.documents.item(1)
    r = ok(run("close", {"confirm": True}))
    assert r["results"][0]["input"] is None and r["results"][0]["result"]["closed"]["name"] == "DA"


def test_destructive_lists_need_confirm_and_touch_nothing(lists):
    assert err(run("close", {"documents": ["DA", "DB"]}))["code"] == "CONFIRM_REQUIRED"
    assert err(run("datafile_delete", {"files": ["Proj/Sub/A", "Proj/Sub/B"]}))["code"] == "CONFIRM_REQUIRED"
    assert [d.name for d in lists.app.documents] == ["Scratch", "DA", "DB"]
    assert [f.name for f in lists.sub._files] == ["A", "B"]


def test_later_entry_sees_earlier_effects(lists):
    r = ok(run("close", {"documents": ["DA", "DA"], "confirm": True}))
    assert [x["ok"] for x in r["results"]] == [True, False] and r["results"][1]["error"]["code"] == "NOT_FOUND"
    p = str(lists.out / "twice.stl")
    r = ok(run("export", {"paths": [p, p]}))
    assert r["results"][1]["error"]["code"] == "INVALID_PARAMS" and "exists" in r["results"][1]["error"]["message"]


@pytest.mark.parametrize("act", ["datafile_copy", "datafile_move"])
def test_shared_folder_that_does_not_resolve_fails_call(lists, act):
    e = err(run(act, {"files": ["Proj/Sub/A"], "folder": "Proj/Zzz"}))
    assert e["code"] == "NOT_FOUND"
    assert all(f.calls == [] for f in lists.files)


def test_export_shared_component_and_body_fail_call(lists):
    e = err(run("export", {"paths": [str(lists.out / "q.stl")], "component": "Root", "body": "Box"}))
    assert e["code"] == "INVALID_PARAMS" and _em(lists).calls == []


def _read_only_saved(live):
    df = live.root.add_file("Locked")
    df.isReadOnly = True
    live.scratch.dataFile = df
    live.app.activeDocument = live.scratch
    return df


@pytest.mark.parametrize("act, member", [("advance_to_latest", "advanceToLatest"),
                                         ("update_all_references", "updateAllReferences")])
def test_update_actions_unlock_read_only_document_first(live, act, member):
    df = _read_only_saved(live)
    res = run(act)
    assert res.is_error is False and res.structured_content["made_editable"] == ["Scratch"]
    assert live.app.commands == ["Commands.Start ActiveDocumentActivateCommand"] and df.isReadOnly is False
    assert [c[0] for c in live.scratch.calls] == [member]


@pytest.mark.parametrize("act", ["advance_to_latest", "update_all_references"])
def test_update_actions_preserve_read_only_is_read_only(live, act):
    _read_only_saved(live)
    e = err(run(act, {"preserve_read_only": True}))
    assert e["code"] == "READ_ONLY" and live.scratch.calls == [] and live.app.commands == []


def test_import_active_unlocks_and_preserve_read_only_is_entry_error(live, src):
    df = _read_only_saved(live)
    e = eerr(run("import", {"paths": [src["step"]], "target": "active", "preserve_read_only": True}))
    assert e["code"] == "READ_ONLY" and _im(live).calls == [] and live.app.commands == []
    res = run("import", {"paths": [src["step"]], "target": "active"})
    assert res.structured_content["result"]["ok"] == 1
    assert res.structured_content["made_editable"] == ["Scratch"] and df.isReadOnly is False


def test_import_new_document_does_not_unlock(live, src):
    _read_only_saved(live)
    one(run("import", {"paths": [src["step"]], "preserve_read_only": True}))
    assert live.app.commands == []


def test_import_mesh_guarded_by_pipeline(live, src):
    _read_only_saved(live)
    assert err(run("import_mesh", {"paths": [src["stl"]], "preserve_read_only": True}))["code"] == "READ_ONLY"
    assert getattr(_root(live), "mesh_calls", []) == []
    res = run("import_mesh", {"paths": [src["stl"]]})
    assert res.structured_content["made_editable"] == ["Scratch"]


# --- folder limit (design § 2.8, REQ-DOC-025) ----------------------------------------------------------------


@pytest.fixture
def limited(fake_app, dispatcher, tmp_path):
    """Project "P" with folders A, A/B and X, a file "F" in A/B and in X, and an unsaved active "Scratch"."""
    proj = fake_app.data.add_hub("Hub").add_project("P")
    a = proj.rootFolder.add_folder("A")
    b, x = a.add_folder("B"), proj.rootFolder.add_folder("X")
    ns = SimpleNamespace(app=fake_app, proj=proj, a=a, b=b, x=x, out=tmp_path,
                         inside=b.add_file("F"), outside=x.add_file("F"))
    ns.scratch = fake_app.documents._new("Scratch")
    ns.run = lambda act, params=None: run(act, params, folder_limit="P/A")
    return ns


def _in(limited, where):
    """Make the active "Scratch" a document saved in folder ``where``."""
    limited.scratch.dataFile = where.add_file("Doc")
    limited.app.activeDocument = limited.scratch
    return limited.scratch


@pytest.mark.parametrize("act, key", [("resolve_folder", "folder"), ("list_folders", "folder")])
def test_folder_limit_folder_reads_limited(limited, act, key):
    assert err(limited.run(act, {key: "P/X"}))["code"] == "NOT_ALLOWED"
    assert err(limited.run(act, {key: limited.x.id}))["code"] == "NOT_ALLOWED"
    assert err(limited.run(act, {key: "P"}))["code"] == "NOT_ALLOWED"
    ok(limited.run(act, {key: "P/A/B"}))
    ok(limited.run(act, {key: "P/A"}))


def test_folder_limit_datafile_get_limited(limited):
    e = err(limited.run("datafile_get", {"file": "P/X/F"}))
    assert e["code"] == "NOT_ALLOWED" and "P/X" in e["message"] and "P/A" in e["message"]
    assert err(limited.run("datafile_get", {"file": limited.outside.id}))["code"] == "NOT_ALLOWED"
    assert ok(limited.run("datafile_get", {"file": "P/A/B/F"}))["path"] == "P/A/B/F"
    assert ok(limited.run("datafile_get", {"file": limited.inside.id}))["path"] == "P/A/B/F"


@pytest.mark.parametrize("act, extra", [
    ("open", {}), ("datafile_refresh", {}), ("datafile_copy", {"folder": "P/A"}),
    ("datafile_move", {"folder": "P/A"}), ("datafile_delete", {"confirm": True}),
])
def test_folder_limit_file_list_actions_limited_per_entry(limited, act, extra):
    r = ok(limited.run(act, {"files": ["P/X/F", "P/A/B/F"], **extra}))
    assert [x["ok"] for x in r["results"]] == [False, True]
    assert r["results"][0]["error"]["code"] == "NOT_ALLOWED"
    assert limited.outside.calls == [] and limited.outside in limited.x._files


def test_folder_limit_datafile_set_limited_per_entry(limited):
    r = ok(limited.run("datafile_set", {"files": [{"file": limited.outside.id, "name": "G"},
                                                  {"file": "P/A/B/F", "name": "H"}]}))
    assert r["results"][0]["error"]["code"] == "NOT_ALLOWED" and r["ok"] == 1
    assert limited.outside.name == "F" and limited.inside.name == "H"


@pytest.mark.parametrize("act", ["datafile_copy", "datafile_move"])
def test_folder_limit_copy_move_target_outside_fails_call(limited, act):
    e = err(limited.run(act, {"files": ["P/A/B/F"], "folder": "P/X"}))
    assert e["code"] == "NOT_ALLOWED" and limited.inside.calls == []


def test_folder_limit_save_as_defaults_folder_to_limit(limited):
    r = one(limited.run("save_as", {"documents": [{"name": "N"}]}))
    assert r["file"]["path"] == "P/A/N" and r["read_only"] is True
    assert limited.scratch.calls[0] == ("saveAs", "N", limited.a, "", "")


def test_folder_limit_save_as_folder_outside_limit_fails_call(limited):
    e = err(limited.run("save_as", {"documents": [{"name": "N"}], "folder": "P/X"}))
    assert e["code"] == "NOT_ALLOWED" and limited.scratch.calls == []


def test_folder_limit_save_as_limit_naming_missing_folder_is_not_found(fake_app, dispatcher):
    fake_app.data.add_hub("Hub").add_project("P")
    fake_app.documents._new("Scratch")
    e = err(run("save_as", {"documents": [{"name": "N"}]}, folder_limit="P/Missing"))
    assert e["code"] == "NOT_FOUND"


@pytest.mark.parametrize("act", ["save", "set_read_only"])
def test_folder_limit_save_and_set_read_only_outside_limit(limited, act):
    doc = _in(limited, limited.x)
    e = eerr(limited.run(act))
    assert e["code"] == "NOT_ALLOWED" and doc.calls == [] and limited.app.commands == []
    _in(limited, limited.b)
    assert one(limited.run(act))["read_only"] is True


@pytest.mark.parametrize("act", ["advance_to_latest", "update_all_references"])
def test_folder_limit_update_actions_outside_limit(limited, act):
    doc = _in(limited, limited.x)
    assert err(limited.run(act))["code"] == "NOT_ALLOWED" and doc.calls == []
    _in(limited, limited.b)
    ok(limited.run(act))


def test_folder_limit_imports_into_document_outside_limit(limited, src):
    _in(limited, limited.x)
    assert eerr(limited.run("import", {"paths": [src["step"]], "target": "active"}))["code"] == "NOT_ALLOWED"
    assert err(limited.run("import_mesh", {"paths": [src["stl"]]}))["code"] == "NOT_ALLOWED"
    assert limited.app.importManager.calls == []
    assert one(limited.run("import", {"paths": [src["step"]]}))["target"] == "new_document"


def test_folder_limit_never_saved_documents_not_limited(limited):
    design = limited.scratch.products.itemByProductType("DesignProductType")
    design.rootComponent.add_body("Box")
    one(limited.run("export", {"paths": [str(limited.out / "s.stl")]}))
    assert ok(limited.run("set_name", {"name": "Named"}))["name"] == "Named"
    ok(limited.run("create"))
    assert one(limited.run("close", {"confirm": True}))["closed"]["saved"] is False


def test_folder_limit_save_upload_pending_under_limit_not_allowed(limited):
    limited.scratch.dataFile = limited.a.add_file("P", pending=True)
    e = eerr(limited.run("save"))
    assert e["code"] == "NOT_ALLOWED" and limited.scratch.calls == []
