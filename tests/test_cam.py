"""Unit tests for the ``cam`` tool (docs/specs/cam)."""
import importlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import adsk.cam
import pytest
from adsk import core, fusion
from adsk.cam import (
    CAM, Library, Tool, attach_workspaces, document, parameters,
)

import defusion
from defusion import actions
from defusion.errors import ActionError
from helpers import ok, err


def _import_cam():
    """Import ``defusion.tools.cam`` without importing sibling tool modules."""
    if "defusion.tools" in sys.modules:
        return importlib.import_module("defusion.tools.cam")
    stub = types.ModuleType("defusion.tools")
    stub.__path__ = [str(Path(defusion.__file__).parent / "tools")]
    sys.modules["defusion.tools"] = stub
    try:
        return importlib.import_module("defusion.tools.cam")
    finally:
        del sys.modules["defusion.tools"]


cam_mod = _import_cam()


def raises(code, fn, *a, **k):
    with pytest.raises(ActionError) as e:
        fn(*a, **k)
    assert e.value.code == code
    return e.value


# ---- _cam / product access -----------------------------------------------------------------------


@pytest.fixture
def ui():
    return core.UserInterface()


def ctx_for(doc, ui):
    return SimpleNamespace(document=doc, ui=ui)


def test_cam_product_no_document(ui):
    raises("NO_DOCUMENT", cam_mod._cam, ctx_for(None, ui), False)


def test_cam_product_missing_adsk_cam(ui, monkeypatch):
    monkeypatch.setitem(sys.modules, "adsk.cam", None)
    e = raises("UNSUPPORTED", cam_mod._cam, ctx_for(document(fusion.Design()), ui), False)
    assert "adsk.cam" in e.message


def test_cam_product_no_design(ui):
    raises("NO_DESIGN", cam_mod._cam, ctx_for(document(cam=CAM()), ui), False)


def test_cam_product_existing_is_returned(ui):
    cam = CAM()
    assert cam_mod._cam(ctx_for(document(fusion.Design(), cam), ui), False) is cam
    assert cam_mod._cam(ctx_for(document(fusion.Design(), cam), ui), True) is cam


def test_cam_product_read_without_product_does_not_activate(ui):
    doc = document(fusion.Design())
    attach_workspaces(ui, doc, create_cam=CAM())
    assert cam_mod._cam(ctx_for(doc, ui), False) is None
    assert ui.activations == []


def test_cam_product_write_activates_and_restores_workspace(ui):
    doc, cam = document(fusion.Design()), CAM()
    attach_workspaces(ui, doc, create_cam=cam)
    assert cam_mod._cam(ctx_for(doc, ui), True) is cam
    assert ui.activations == ["CAMEnvironment", "FusionSolidEnvironment"]
    assert ui.activeWorkspace.id == "FusionSolidEnvironment"


def test_cam_product_write_keeps_cam_workspace_when_already_active(ui):
    doc, cam = document(fusion.Design()), CAM()
    attach_workspaces(ui, doc, create_cam=cam, active="CAMEnvironment")
    assert cam_mod._cam(ctx_for(doc, ui), True) is cam
    assert ui.activations == ["CAMEnvironment"]


def test_cam_product_write_restores_after_failure(ui):
    doc = document(fusion.Design())
    attach_workspaces(ui, doc, create_cam=None)  # Fusion never creates the product
    e = raises("UNSUPPORTED", cam_mod._cam, ctx_for(doc, ui), True)
    assert "Manufacture product" in e.message
    assert ui.activations == ["CAMEnvironment", "FusionSolidEnvironment"]
    assert ui.activeWorkspace.id == "FusionSolidEnvironment"


def test_cam_product_write_restores_when_activation_raises(ui):
    doc = document(fusion.Design())
    attach_workspaces(ui, doc)
    ws = ui.workspaces.itemById("CAMEnvironment")

    def boom():
        raise RuntimeError("activate failed")

    ws._on_activate = boom
    with pytest.raises(RuntimeError):
        cam_mod._cam(ctx_for(doc, ui), True)
    assert ui.activations == ["CAMEnvironment", "FusionSolidEnvironment"]


def test_cam_product_write_without_workspace(ui):
    doc = document(fusion.Design())
    attach_workspaces(ui, doc)
    ui.workspaces.itemById = lambda i: None
    raises("UNSUPPORTED", cam_mod._cam, ctx_for(doc, ui), True)


# ---- refs and shared helpers -----------------------------------------------------------------------


def test_refs_shapes():
    cam = CAM()
    s = cam.add_setup("Setup1")
    op = s.add_operation("Face1")
    assert cam_mod._setup_ref(s) == {"type": "Setup", "name": "Setup1"}
    assert cam_mod._op_ref(op) == {"type": "Operation", "name": "Face1", "setup": "Setup1"}


def test_refs_operation_in_folder_keeps_setup_name():
    s = CAM().add_setup("Setup1")
    folder_op = s.add_operation("Pocket in folder")
    assert cam_mod._op_ref(folder_op)["setup"] == "Setup1"


def test_refs_type_name():
    t = adsk.cam.OperationTypes
    cam = CAM()
    for value, label in ((t.MillingOperation, "milling"), (t.TurningOperation, "turning"),
                         (t.JetOperation, "cutting"), (t.AdditiveOperation, "additive"), (99, "other")):
        assert cam_mod._type_name(cam.add_setup("s", operationType=value)) == label


def test_refs_type_name_without_additive(monkeypatch):
    monkeypatch.delattr(adsk.cam.OperationTypes, "AdditiveOperation")
    s = CAM().add_setup("s", operationType=3)
    assert cam_mod._type_name(s) == "other"


def test_refs_state():
    st = adsk.cam.OperationStates
    op = CAM().add_setup("s").add_operation("o")
    for value, label in ((st.IsValidOperationState, "valid"), (st.IsInvalidOperationState, "invalid"),
                         (st.SuspendedOperationState, "suspended"),
                         (st.NoToolpathOperationState, "no_toolpath"), (99, None)):
        op.operationState = value
        assert cam_mod._state(op) == label
    del op.operationState
    assert cam_mod._state(op) is None


def test_refs_tool_out():
    ctx = SimpleNamespace(length_out=lambda cm: cm * 10)
    assert cam_mod._tool_out(ctx, None) is None
    tool = Tool(number=3, description="6mm flat", type="flat end mill", diameter=0.6)
    assert cam_mod._tool_out(ctx, tool) == {
        "number": 3, "description": "6mm flat", "type": "flat end mill", "diameter": 6.0}
    tool.parameters._items.pop()  # no tool_diameter
    assert cam_mod._tool_out(ctx, tool)["diameter"] is None


def test_refs_param_out():
    p = parameters(job_stockMode="'default'").item(0)
    assert cam_mod._param_out(p) == {"name": "job_stockMode", "title": "job_stockMode",
                                     "expression": "'default'"}


def test_refs_set_param():
    params = parameters(wcs_a="1", wcs_b="2", job_x="3")
    cam_mod._set_param(None, params, "wcs_a", "5 mm")
    assert params.itemByName("wcs_a").expression == "5 mm"
    e = raises("INVALID_PARAMS", cam_mod._set_param, None, params, "wcs_nope", "1")
    assert e.hints == ["wcs_a", "wcs_b"] and "wcs_nope" in e.message
    params.itemByName("job_x")._readonly = True
    e = raises("FUSION_ERROR", cam_mod._set_param, None, params, "job_x", "4")
    assert "job_x" in e.message and "'4'" in e.message


def test_refs_set_param_hints_capped_at_20():
    params = parameters(**{"p_%d" % i: "0" for i in range(30)})
    assert len(raises("INVALID_PARAMS", cam_mod._set_param, None, params, "p_none", "1").hints) == 20


def make_library():
    L = adsk.cam.LibraryLocations
    roots = {L.LocalLibraryLocation: "local://", L.Fusion360LibraryLocation: "sys://"}
    tree = {
        "local://": (["local://sub"], ["local://Fanuc.cps"]),
        "local://sub": ([], ["local://sub/Mill.cps", "local://sub/mill.json"]),
        "sys://": ([], ["sys://fanuc.cps", "sys://Haas.cps"]),
    }
    return Library(roots, tree)


def names(urls):
    return [u.toString() for u in urls]


def test_refs_walk_and_locations():
    lib = make_library()
    assert names(cam_mod._walk(lib, lib.urlByLocation(0))) == [
        "local://Fanuc.cps", "local://sub/Mill.cps", "local://sub/mill.json"]
    assert cam_mod._locations() == [0, 1, 2]
    assert cam_mod._basename(core.URL.create("local://sub/Mill.cps")) == "Mill"


def test_refs_walk_skips_unreadable_folder():
    lib = make_library()
    real = lib.childAssetURLs

    def assets(url):
        if url.toString() == "local://sub":
            raise RuntimeError("3 : Given URL does not point to an existing folder.")
        return real(url)

    lib.childAssetURLs = assets
    assert names(cam_mod._walk(lib, lib.urlByLocation(0))) == ["local://Fanuc.cps"]


def test_refs_lib_find():
    lib = make_library()
    ctx = SimpleNamespace()
    assert cam_mod._lib_find(ctx, lib, "x://anything", "post").toString() == "x://anything"
    assert cam_mod._lib_find(ctx, lib, "FANUC", "post").toString() == "local://Fanuc.cps"  # first location wins
    assert cam_mod._lib_find(ctx, lib, "haas", "post").toString() == "sys://Haas.cps"
    e = raises("AMBIGUOUS", cam_mod._lib_find, ctx, lib, "mill", "post")
    assert e.extra["candidates"] == ["local://sub/Mill.cps", "local://sub/mill.json"]
    e = raises("NOT_FOUND", cam_mod._lib_find, ctx, lib, "nope", "post")
    assert "post" in e.message and "Haas" in e.hints


# ---- action harness ------------------------------------------------------------------------------------


def run(action, params=None, **kw):
    allowed = frozenset(a.key for a in actions.registry())
    return actions.call("cam", {"action": action, "params": params or {}, **kw}, allowed, ["all"])


@pytest.fixture
def env(dispatcher, fake_app, fake_design):
    """A document with a design and a CAM product holding two setups."""
    cam = CAM()
    s1, s2 = cam.add_setup("Setup1"), cam.add_setup("Setup2", operationType=1)
    doc = document(fake_design, cam)
    fake_app.activeDocument = doc
    attach_workspaces(fake_app.userInterface, doc, create_cam=cam)
    return SimpleNamespace(cam=cam, s1=s1, s2=s2, doc=doc, app=fake_app, design=fake_design)


def add_op(setup, name, **kw):
    state = kw.pop("state", adsk.cam.OperationStates.NoToolpathOperationState)
    op = setup.add_operation(name, **kw)
    op.operationState = state
    return op


# ---- list_setups --------------------------------------------------------------------------------------------


def test_list_setups_items(env):
    env.s1.parameters.itemByName("job_stockMode")._expression = "'fixedbox'"
    body = env.design.rootComponent.add_body("Body1")
    env.s1.models = [body]
    op = add_op(env.s1, "Face1")
    op.isToolpathValid = True
    r = ok(run("list_setups"))
    assert r["total"] == 2 and r["next_offset"] is None
    a, b = r["items"]
    assert a["setup"] == {"type": "Setup", "name": "Setup1"}
    assert a["type"] == "milling" and a["operations"] == 1 and a["toolpaths_valid"] is True
    assert a["stock_mode"] == "'fixedbox'"
    assert a["models"][0]["type"] == "BRepBody" and a["models"][0]["name"] == "Body1"
    assert b["type"] == "turning" and b["operations"] == 0 and b["toolpaths_valid"] is False
    assert b["models"] is None  # attribute absent on this build


def test_list_setups_invalid_toolpath_and_paging(env):
    add_op(env.s1, "Face1").isToolpathValid = True
    add_op(env.s1, "Face2")  # not valid
    r = ok(run("list_setups", {"limit": 1}))
    assert r["items"][0]["toolpaths_valid"] is False and r["next_offset"] == 1
    assert ok(run("list_setups", {"offset": 1, "limit": 1}))["items"][0]["setup"]["name"] == "Setup2"


def test_list_setups_without_cam_product_is_empty(env):
    env.doc.products._items.pop("CAMProductType")
    r = ok(run("list_setups"))
    assert r["items"] == [] and r["total"] == 0
    assert env.app.userInterface.activations == []


def test_list_setups_errors(env):
    env.app.activeDocument = None
    assert err(run("list_setups"))["code"] == "NO_DOCUMENT"


# ---- list_operations ----------------------------------------------------------------------------------------


def test_list_operations_all_and_by_setup(env):
    st = adsk.cam.OperationStates
    add_op(env.s1, "Face1", tool=Tool(2, "8mm flat", "flat end mill", 0.8), state=st.IsValidOperationState)
    o2 = add_op(env.s2, "Turn1", strategy="turningFace")
    o2.isSuppressed, o2.isGenerating = True, True
    r = ok(run("list_operations"))
    assert r["total"] == 2
    f, t = r["items"]
    assert f["operation"] == {"type": "Operation", "name": "Face1", "setup": "Setup1"}
    assert f["strategy"] == "face" and f["state"] == "valid"
    assert f["tool"] == {"number": 2, "description": "8mm flat", "type": "flat end mill", "diameter": 8.0}
    assert f["has_toolpath"] is False and f["suppressed"] is False and f["generating"] is False
    assert t["tool"] is None and t["suppressed"] is True and t["generating"] is True
    only = ok(run("list_operations", {"setup": "Setup2"}))
    assert [i["operation"]["name"] for i in only["items"]] == ["Turn1"]
    assert ok(run("list_operations", {"setup": {"type": "Setup", "name": "Setup1"}}))["total"] == 1


def test_list_operations_unknown_setup_and_no_product(env):
    assert err(run("list_operations", {"setup": "Nope"}))["code"] == "NOT_FOUND"
    env.doc.products._items.pop("CAMProductType")
    assert ok(run("list_operations"))["items"] == []


def test_list_operations_paging(env):
    for i in range(3):
        add_op(env.s1, "Op%d" % i)
    r = ok(run("list_operations", {"offset": 1, "limit": 1}))
    assert [i["operation"]["name"] for i in r["items"]] == ["Op1"] and r["next_offset"] == 2


# ---- operation_info -------------------------------------------------------------------------------------------


@pytest.fixture
def op_env(env):
    op = add_op(env.s1, "Face1", tool=Tool(1, "flat", "flat end mill", 1.0))
    op.parameters = parameters(tool_number="1", cutting_feed="500 mm/min", cutting_depth="1 mm")
    op.hasToolpath = op.isToolpathValid = True
    env.op = op
    return env


def test_operation_info_operation(op_env):
    r = ok(run("operation_info", {"operation": {"name": "Face1", "setup": "Setup1"}}))
    assert r["operation"] == {"type": "Operation", "name": "Face1", "setup": "Setup1"}
    assert r["setup"] == {"type": "Setup", "name": "Setup1"} and r["strategy"] == "face"
    assert r["tool"]["number"] == 1
    assert r["toolpath"] == {"has_toolpath": True, "valid": True, "generating": False,
                             "state": "no_toolpath", "warning": False, "error": False}
    assert [p["name"] for p in r["parameters"]["items"]] == ["tool_number", "cutting_feed", "cutting_depth"]
    assert r["parameters"]["items"][1] == {"name": "cutting_feed", "title": "cutting_feed",
                                           "expression": "500 mm/min"}
    assert r["parameters"]["total"] == 3 and r["parameters"]["next_offset"] is None


def test_operation_info_warning_error_null_when_missing(op_env):
    del op_env.op.hasWarning
    del op_env.op.hasError
    t = ok(run("operation_info", {"operation": "Face1"}))["toolpath"]
    assert t["warning"] is None and t["error"] is None


def test_operation_info_names_in_given_order_and_paging(op_env):
    r = ok(run("operation_info", {"operation": "Face1", "names": ["cutting_depth", "tool_number"]}))
    assert [p["name"] for p in r["parameters"]["items"]] == ["cutting_depth", "tool_number"]
    r = ok(run("operation_info", {"operation": "Face1", "offset": 1, "limit": 1}))
    assert [p["name"] for p in r["parameters"]["items"]] == ["cutting_feed"]
    assert r["parameters"]["next_offset"] == 2


def test_operation_info_unknown_name_hints(op_env):
    e = err(run("operation_info", {"operation": "Face1", "names": ["cutting_nope"]}))
    assert e["code"] == "INVALID_PARAMS" and e["hints"] == ["cutting_feed", "cutting_depth"]


def test_operation_info_setup(op_env):
    r = ok(run("operation_info", {"setup": "Setup1", "names": ["job_stockMode"]}))
    assert r == {"setup": {"type": "Setup", "name": "Setup1"}, "type": "milling",
                 "parameters": {"items": [{"name": "job_stockMode", "title": "job_stockMode",
                                           "expression": "'default'"}],
                                "total": 1, "offset": 0, "next_offset": None}}


def test_operation_info_both_or_neither(op_env):
    for params in ({}, {"operation": "Face1", "setup": "Setup1"}):
        e = err(run("operation_info", params))
        assert e["code"] == "INVALID_PARAMS" and "exactly one" in e["message"]


def test_operation_info_output_ref_round_trips(op_env):
    ref = ok(run("list_operations"))["items"][0]["operation"]
    assert ok(run("operation_info", {"operation": ref}))["operation"] == ref
    sref = ok(run("list_setups"))["items"][0]["setup"]
    assert ok(run("operation_info", {"setup": sref}))["setup"] == sref


# ---- generate_toolpath ----------------------------------------------------------------------------------------


@pytest.fixture
def gen_env(env):
    env.o1, env.o2 = add_op(env.s1, "Face1"), add_op(env.s1, "Pocket1")
    env.o3 = add_op(env.s2, "Turn1")
    return env


def test_generate_toolpath_all(gen_env):
    r = ok(run("generate_toolpath"))
    assert gen_env.cam.generate_calls == [("all", False)]
    assert r["completed"] is True
    assert [o["operation"]["name"] for o in r["operations"]] == ["Face1", "Pocket1", "Turn1"]
    assert all(o["has_toolpath"] and o["toolpath_valid"] and o["state"] == "valid" for o in r["operations"])
    assert r["operations"][0]["warning"] is False and r["operations"][0]["error"] is False
    ok(run("generate_toolpath", {"skip_valid": True}))
    assert gen_env.cam.generate_calls[-1] == ("all", True)


def test_generate_toolpath_setup_and_operations(gen_env):
    r = ok(run("generate_toolpath", {"setup": "Setup1"}))
    assert gen_env.cam.generate_calls == [("ops", ["Face1", "Pocket1"])]
    assert [o["operation"]["name"] for o in r["operations"]] == ["Face1", "Pocket1"]
    assert not gen_env.o3.hasToolpath
    r = ok(run("generate_toolpath", {"operations": [{"name": "Turn1", "setup": "Setup2"}]}))
    assert gen_env.cam.generate_calls[-1] == ("ops", ["Turn1"]) and gen_env.o3.isToolpathValid
    assert r["operations"][0]["operation"] == {"type": "Operation", "name": "Turn1", "setup": "Setup2"}


def test_generate_toolpath_skip_valid_filters(gen_env):
    gen_env.o1.isToolpathValid = True
    r = ok(run("generate_toolpath", {"setup": "Setup1", "skip_valid": True}))
    assert gen_env.cam.generate_calls == [("ops", ["Pocket1"])]
    assert [o["operation"]["name"] for o in r["operations"]] == ["Face1", "Pocket1"]


def test_generate_toolpath_skip_valid_nothing_left(gen_env):
    gen_env.o1.isToolpathValid = gen_env.o2.isToolpathValid = True
    r = ok(run("generate_toolpath", {"setup": "Setup1", "skip_valid": True}))
    assert gen_env.cam.generate_calls == [] and r["completed"] is True and len(r["operations"]) == 2


def test_generate_toolpath_waits_until_complete(gen_env):
    futures = []

    def make(ops):
        futures.append(adsk.cam.GenerateToolpathFuture(ops, polls=3))
        return futures[-1]

    gen_env.cam.future = make
    r = ok(run("generate_toolpath", {"setup": "Setup1"}))
    assert r["completed"] is True and futures[0]._left == 0
    assert all(o["toolpath_valid"] for o in r["operations"])


def test_generate_toolpath_future_never_completes_stops_at_deadline(gen_env, monkeypatch):
    monkeypatch.setattr(cam_mod, "WAIT_SECONDS", 0.3)
    gen_env.cam.future = lambda ops: adsk.cam.GenerateToolpathFuture(ops, never=True)
    r = ok(run("generate_toolpath", {"setup": "Setup1"}))
    assert r["completed"] is False
    assert [o["toolpath_valid"] for o in r["operations"]] == [False, False]


def test_generate_toolpath_no_wait(gen_env):
    gen_env.cam.future = lambda ops: adsk.cam.GenerateToolpathFuture(ops, polls=1)
    r = ok(run("generate_toolpath", {"setup": "Setup1", "wait": False}))
    assert r["completed"] is False and r["operations"][0]["toolpath_valid"] is False


def test_generate_toolpath_legacy_probe(gen_env):
    gen_env.cam.future = lambda ops: adsk.cam.LegacyFuture(ops, polls=1)
    assert ok(run("generate_toolpath", {"setup": "Setup1"}))["completed"] is True


def test_generate_toolpath_missing_probe_behaves_as_no_wait(gen_env):
    gen_env.cam.future = lambda ops: adsk.cam.NoProbeFuture()
    assert ok(run("generate_toolpath", {"setup": "Setup1"}))["completed"] is False


def test_generate_toolpath_setup_and_operations_rejected(gen_env):
    e = err(run("generate_toolpath", {"setup": "Setup1", "operations": ["Face1"]}))
    assert e["code"] == "INVALID_PARAMS" and gen_env.cam.generate_calls == []


def test_generate_toolpath_without_cam_product(gen_env):
    gen_env.doc.products._items.pop("CAMProductType")
    assert ok(run("generate_toolpath")) == {"completed": True, "operations": []}


def test_generate_toolpath_action_metadata():
    a = actions.registry().get("cam", "generate_toolpath")
    assert (a.access, a.timeout, a.undo_group) == ("write", 600, False)
    assert cam_mod.WAIT_SECONDS < a.timeout


# ---- post_process -----------------------------------------------------------------------------------------------

FIXTURE_POST = Path(__file__).parent / "fixtures" / "posts" / "defusion_test.cps"


@pytest.fixture
def post_env(env, tmp_path):
    """A setup with two generated operations and a post library with one post."""
    adsk.cam.CAMManager.reset()
    env.o1 = add_op(env.s1, "Face1")
    env.o2 = add_op(env.s1, "Pocket1")
    for op in (env.o1, env.o2):
        op.hasToolpath = op.isToolpathValid = True
    L = adsk.cam.LibraryLocations
    env.lib = Library(
        {L.Fusion360LibraryLocation: "sys://"},
        {"sys://": ([], ["sys://fanuc.cps"])},
        items={"sys://fanuc.cps": SimpleNamespace(description="FANUC Mill")},
    )
    adsk.cam.CAMManager.get().libraryManager.postLibrary = env.lib
    env.out = str(tmp_path / "nc" / "out")
    env.cps = tmp_path / "my.cps"
    env.cps.write_text("// post")
    yield env
    adsk.cam.CAMManager.reset()


def test_post_process_action_metadata():
    a = actions.registry().get("cam", "post_process")
    assert (a.access, a.timeout) == ("write", 120)


def test_post_process_file_route_setup(post_env):
    r = ok(run("post_process", {"post": str(post_env.cps), "output_folder": post_env.out,
                                "setup": "Setup1", "program_comment": "hello"}))
    kind, target, inp = post_env.cam.post_calls[0]
    assert kind == "file" and target is post_env.s1
    assert (inp.programName, inp.postConfig, inp.outputFolder) == ("Setup1", str(post_env.cps), post_env.out)
    assert inp.programComment == "hello" and inp.isOpenInEditor is False
    assert inp.postOutputUnitOptions == adsk.cam.PostOutputUnitOptions.DocumentUnitsOutput
    assert r["route"] == "file" and r["post"] == str(post_env.cps) and r["program_name"] == "Setup1"
    assert r["files"] == [post_env.out + "/Setup1.nc"] and r["output_folder"] == post_env.out
    assert [o["name"] for o in r["operations"]] == ["Face1", "Pocket1"]
    assert "(DEFUSION)" in Path(r["files"][0]).read_text()


@pytest.mark.parametrize("units,member", [("document", "DocumentUnitsOutput"),
                                          ("mm", "MillimetersOutput"), ("in", "InchesOutput")])
def test_post_process_file_route_units(post_env, units, member):
    ok(run("post_process", {"post": str(post_env.cps), "output_folder": post_env.out,
                            "setup": "Setup1", "output_units": units}))
    assert post_env.cam.post_calls[0][2].postOutputUnitOptions == getattr(adsk.cam.PostOutputUnitOptions, member)


def test_post_process_file_route_operations_and_program_name(post_env):
    r = ok(run("post_process", {"post": str(post_env.cps), "output_folder": post_env.out,
                                "operations": [{"name": "Pocket1", "setup": "Setup1"}],
                                "program_name": "prog1"}))
    target = post_env.cam.post_calls[0][1]
    assert isinstance(target, core.ObjectCollection) and list(target) == [post_env.o2]
    assert r["program_name"] == "prog1" and r["files"] == [post_env.out + "/prog1.nc"]
    # default name comes from the first operation's setup
    r = ok(run("post_process", {"post": str(post_env.cps), "output_folder": post_env.out,
                                "operations": ["Face1"]}))
    assert r["program_name"] == "Setup1"


def test_post_process_setup_skips_suppressed_operations(post_env):
    post_env.o2.isSuppressed = True
    post_env.o2.hasToolpath = False  # would otherwise block posting
    r = ok(run("post_process", {"post": str(post_env.cps), "output_folder": post_env.out, "setup": "Setup1"}))
    assert [o["name"] for o in r["operations"]] == ["Face1"]


def test_post_process_generic_post_folder(post_env, tmp_path):
    folder = tmp_path / "generic"
    folder.mkdir()
    (folder / "grbl.cps").write_text("// grbl")
    post_env.cam.genericPostFolder = str(folder)
    r = ok(run("post_process", {"post": "grbl", "output_folder": post_env.out, "setup": "Setup1"}))
    assert r["route"] == "file" and r["post"] == str(folder / "grbl.cps")


def test_post_process_file_not_found(post_env):
    e = err(run("post_process", {"post": "/nope/x.cps", "output_folder": post_env.out, "setup": "Setup1"}))
    assert e["code"] == "NOT_FOUND"
    e = err(run("post_process", {"post": "sub/fanuc", "output_folder": post_env.out, "setup": "Setup1"}))
    assert e["code"] == "NOT_FOUND"


def test_post_process_file_route_unsupported(post_env, monkeypatch):
    monkeypatch.delattr(adsk.cam, "PostProcessInput")
    e = err(run("post_process", {"post": str(post_env.cps), "output_folder": post_env.out, "setup": "Setup1"}))
    assert e["code"] == "UNSUPPORTED"


def test_post_process_library_route(post_env):
    r = ok(run("post_process", {"post": "fanuc", "output_folder": post_env.out, "setup": "Setup1",
                                "program_name": "lib1", "program_comment": "note"}))
    kind, cfg, open_in_editor = post_env.cam.post_calls[0]
    assert kind == "nc" and cfg.description == "FANUC Mill" and open_in_editor is False
    nc_in = post_env.cam.ncPrograms.inputs[0]
    p = lambda n: nc_in.parameters.itemByName(n).value.value  # noqa: E731
    assert (p("nc_program_filename"), p("nc_program_output_folder"), p("nc_program_comment")) == (
        "lib1", post_env.out, "note")
    assert nc_in.displayName == "lib1" and nc_in.operations == [post_env.o1, post_env.o2]
    assert post_env.cam.ncPrograms.programs == []  # temporary NC program deleted
    assert r["route"] == "library" and r["post"] == "sys://fanuc.cps"
    assert r["files"] == [post_env.out + "/lib1.nc"]


def test_post_process_library_route_matches_description(post_env):
    r = ok(run("post_process", {"post": "fanuc mill", "output_folder": post_env.out, "setup": "Setup1"}))
    assert r["post"] == "sys://fanuc.cps"
    e = err(run("post_process", {"post": "nope", "output_folder": post_env.out, "setup": "Setup1"}))
    assert e["code"] == "NOT_FOUND" and "fanuc" in e["hints"]


def test_post_process_library_route_deletes_program_on_exception(post_env):
    post_env.cam.post_error = RuntimeError("post crashed")
    e = err(run("post_process", {"post": "fanuc", "output_folder": post_env.out, "setup": "Setup1"}))
    assert e["code"] == "FUSION_ERROR" and "post crashed" in e["message"]
    assert post_env.cam.ncPrograms.programs == []


def test_post_process_library_route_rejects_units(post_env):
    e = err(run("post_process", {"post": "fanuc", "output_folder": post_env.out, "setup": "Setup1",
                                 "output_units": "mm"}))
    assert e["code"] == "INVALID_PARAMS" and ".cps" in e["message"]
    assert post_env.cam.ncPrograms.programs == [] and post_env.cam.ncPrograms.inputs == []


def test_post_process_library_route_unsupported(post_env, monkeypatch):
    monkeypatch.delattr(adsk.cam, "CAMManager")
    e = err(run("post_process", {"post": "fanuc", "output_folder": post_env.out, "setup": "Setup1"}))
    assert e["code"] == "UNSUPPORTED"
    monkeypatch.undo()
    del post_env.cam.ncPrograms
    e = err(run("post_process", {"post": "fanuc", "output_folder": post_env.out, "setup": "Setup1"}))
    assert e["code"] == "UNSUPPORTED"


def test_post_process_missing_toolpath(post_env):
    post_env.o2.isToolpathValid = False
    e = err(run("post_process", {"post": str(post_env.cps), "output_folder": post_env.out, "setup": "Setup1"}))
    assert e["code"] == "INVALID_PARAMS" and "Pocket1" in e["message"] and "Face1" not in e["message"]
    assert e["hints"] == ["run cam.generate_toolpath first"]
    assert post_env.cam.post_calls == []


def test_post_process_nothing_to_post(post_env):
    e = err(run("post_process", {"post": str(post_env.cps), "output_folder": post_env.out, "setup": "Setup2"}))
    assert e["code"] == "INVALID_PARAMS" and "nothing to post" in e["message"]


def test_post_process_relative_folder(post_env):
    e = err(run("post_process", {"post": str(post_env.cps), "output_folder": "rel/out", "setup": "Setup1"}))
    assert e["code"] == "INVALID_PARAMS" and "absolute" in e["message"]


def test_post_process_exactly_one_target(post_env):
    for params in ({}, {"setup": "Setup1", "operations": ["Face1"]}):
        e = err(run("post_process", {"post": str(post_env.cps), "output_folder": post_env.out, **params}))
        assert e["code"] == "INVALID_PARAMS" and "exactly one" in e["message"]


def test_post_process_no_output_file(post_env):
    post_env.cam.post_writes = False
    e = err(run("post_process", {"post": str(post_env.cps), "output_folder": post_env.out, "setup": "Setup1"}))
    assert e["code"] == "FUSION_ERROR" and "no file" in e["message"]


def test_post_process_ignores_files_older_than_the_call(post_env):
    import os

    os.makedirs(post_env.out)
    old = Path(post_env.out) / "old.nc"
    old.write_text("old")
    os.utime(old, (1, 1))
    r = ok(run("post_process", {"post": str(post_env.cps), "output_folder": post_env.out, "setup": "Setup1"}))
    assert r["files"] == [post_env.out + "/Setup1.nc"]


def test_post_process_bad_program_name_leaves_folder_alone(post_env):
    e = err(run("post_process", {"post": str(post_env.cps), "output_folder": post_env.out,
                                 "setup": "Setup1", "program_name": "../x"}))
    assert e["code"] == "INVALID_PARAMS"
    assert not Path(post_env.out).exists() and post_env.cam.post_calls == []


def test_post_process_defaulted_program_name_is_validated(post_env):
    post_env.s1.name = "a/b"
    e = err(run("post_process", {"post": str(post_env.cps), "output_folder": post_env.out,
                                 "operations": [{"token": post_env.o1.entityToken}]}))
    assert e["code"] == "INVALID_PARAMS" and "program_name" in e["message"]
    assert not Path(post_env.out).exists()


# ---- create_setup ---------------------------------------------------------------------------------------------


@pytest.fixture
def setup_env(env):
    adsk.cam.CAMManager.reset()
    env.body = env.design.rootComponent.add_body("Box")
    env.n = len(env.cam._setups)
    yield env
    adsk.cam.CAMManager.reset()


def _machines(env, *machines):
    L = adsk.cam.LibraryLocations
    lib = Library({L.LocalLibraryLocation: "m://"}, {}, machines={"m://": machines})
    adsk.cam.CAMManager.get().libraryManager.machineLibrary = lib


def test_create_setup_action_metadata():
    a = actions.registry().get("cam", "create_setup")
    assert (a.access, a.timeout) == ("write", 60)


def test_create_setup_milling_with_stock_table(setup_env):
    e = setup_env
    r = ok(run("create_setup", {"bodies": ["Box"], "name": "Mill", "stock_mode": "relative_box",
                                "stock_offset_sides": "1 mm", "stock_offset_top": 2, "stock_offset_bottom": "0.5 mm"}))
    assert r["setup"]["name"] == "Mill" and r["type"] == "milling"
    assert [m["name"] for m in r["models"]] == ["Box"]
    assert list(r["parameters_set"]) == ["job_stockMode", "job_stockOffsetMode", "job_stockOffsetSides",
                                         "job_stockOffsetTop", "job_stockOffsetBottom"]
    assert r["parameters_set"]["job_stockMode"] == "'default'"
    assert r["parameters_set"]["job_stockOffsetMode"] == "'simple'"
    assert r["parameters_set"]["job_stockOffsetSides"] == "1 mm"
    assert r["parameters_set"]["job_stockOffsetTop"] == "2 mm"
    assert len(e.cam._setups) == e.n + 1 and e.cam._setups[-1].models == [e.body]


@pytest.mark.parametrize("mode,expr", [("fixed_box", "'fixedbox'"), ("relative_cylinder", "'relativecylinder'"),
                                       ("fixed_cylinder", "'fixedcylinder'"), ("from_solid", "'solid'")])
def test_create_setup_stock_mode_table(setup_env, mode, expr):
    assert ok(run("create_setup", {"bodies": ["Box"], "stock_mode": mode}))["parameters_set"] == {"job_stockMode": expr}


def test_create_setup_models_are_bodies_then_occurrences(setup_env):
    occ = setup_env.design.rootComponent.add_occurrence("A:1", "A")
    r = ok(run("create_setup", {"bodies": ["Box"], "occurrences": ["A:1"]}))
    assert [m["type"] for m in r["models"]] == ["BRepBody", "Occurrence"]
    assert setup_env.cam._setups[-1].models[1] is occ


def test_create_setup_turning(setup_env):
    assert ok(run("create_setup", {"type": "turning", "bodies": ["Box"]}))["type"] == "turning"


def test_create_setup_parameters_override_and_round_trip(setup_env):
    r = ok(run("create_setup", {"bodies": ["Box"], "stock_mode": "fixed_box",
                                "parameters": {"job_stockMode": "'solid'", "wcs_origin_mode": "'box'"}}))
    assert r["parameters_set"] == {"job_stockMode": "'solid'", "wcs_origin_mode": "'box'"}


def test_create_setup_unknown_parameter(setup_env):
    e = err(run("create_setup", {"bodies": ["Box"], "parameters": {"wcs_nope": "1"}}))
    assert e["code"] == "INVALID_PARAMS" and "wcs_origin_mode" in e["hints"]
    assert len(setup_env.cam._setups) == setup_env.n  # the rejected setup is deleted again


def test_create_setup_needs_models(setup_env):
    assert err(run("create_setup", {}))["code"] == "INVALID_PARAMS"
    assert len(setup_env.cam._setups) == setup_env.n


def test_create_setup_additive_needs_machine(setup_env):
    assert err(run("create_setup", {"type": "additive", "bodies": ["Box"]}))["code"] == "INVALID_PARAMS"


def test_create_setup_additive_unsupported(setup_env, monkeypatch):
    monkeypatch.delattr(adsk.cam.OperationTypes, "AdditiveOperation")
    e = err(run("create_setup", {"type": "additive", "bodies": ["Box"], "machine": "X"}))
    assert e["code"] == "UNSUPPORTED"


def test_create_setup_machine_lookup(setup_env):
    m = SimpleNamespace(description="Printer One", model="P1")
    _machines(setup_env, SimpleNamespace(description="Other", model="O"), m)
    for key in ("printer one", "p1"):
        r = ok(run("create_setup", {"type": "additive", "bodies": ["Box"], "machine": key}))
        assert r["type"] == "additive" and setup_env.cam._setups[-1].machine is m


def test_create_setup_machine_not_found_has_hints(setup_env):
    _machines(setup_env, SimpleNamespace(description="Printer One", model="P1"))
    e = err(run("create_setup", {"bodies": ["Box"], "machine": "__none__"}))
    assert e["code"] == "NOT_FOUND" and e["hints"] == ["Printer One"]
    assert len(setup_env.cam._setups) == setup_env.n


def test_create_setup_machine_lookup_skips_unreadable_location(setup_env):
    L = adsk.cam.LibraryLocations
    m = SimpleNamespace(description="Printer One", model="P1")
    lib = Library({L.LocalLibraryLocation: "gone://", L.CloudLibraryLocation: "m://"}, {}, machines={"m://": [m]})
    adsk.cam.CAMManager.get().libraryManager.machineLibrary = lib
    assert setup_env.cam is not None
    assert ok(run("create_setup", {"type": "additive", "bodies": ["Box"], "machine": "p1"}))["type"] == "additive"
    assert err(run("create_setup", {"bodies": ["Box"], "machine": "__none__"}))["code"] == "NOT_FOUND"


def test_create_setup_machine_library_missing(setup_env, monkeypatch):
    monkeypatch.delattr(adsk.cam.LibraryManager, "machineLibrary", raising=False)
    lm = adsk.cam.CAMManager.get().libraryManager
    del lm.machineLibrary
    assert err(run("create_setup", {"bodies": ["Box"], "machine": "x"}))["code"] == "UNSUPPORTED"


# ---- create_operation -----------------------------------------------------------------------------------------


@pytest.fixture
def opc_env(env):
    adsk.cam.CAMManager.reset()
    env.tools = core.Collection([Tool(1, "Flat 6", "flat end mill"), Tool(2, "Ball 3", "ball end mill"),
                                 Tool(3, "Flat 6", "flat end mill")])
    env.cam.documentToolLibrary = env.tools
    yield env
    adsk.cam.CAMManager.reset()


def _tool_lib(env):
    L = adsk.cam.LibraryLocations
    tools = core.Collection([Tool(7, "Mill Tool", "flat end mill")])
    lib = Library({L.LocalLibraryLocation: "t://"}, {"t://": ([], ["t://Milling.json"])},
                  items={"t://Milling.json": tools})
    adsk.cam.CAMManager.get().libraryManager.toolLibraries = lib
    return tools


def test_create_operation_action_metadata():
    a = actions.registry().get("cam", "create_operation")
    assert (a.access, a.timeout) == ("write", 60)


def test_create_operation_by_number_and_parameters(opc_env):
    r = ok(run("create_operation", {"setup": "Setup1", "strategy": "face", "tool": {"number": 2},
                                    "name": "Skim", "parameters": {"feed": "'250'"}}))
    assert r["operation"] == {"type": "Operation", "name": "Skim", "setup": "Setup1"}
    assert r["strategy"] == "face" and r["tool"]["number"] == 2 and r["has_toolpath"] is False
    assert r["parameters_set"] == {"feed": "'250'"}
    assert opc_env.s1._ops[0].tool is opc_env.tools.item(1)


def test_create_operation_by_description_and_index(opc_env):
    assert ok(run("create_operation", {"setup": "Setup1", "strategy": "face",
                                       "tool": {"description": "Ball 3"}}))["tool"]["number"] == 2
    assert ok(run("create_operation", {"setup": "Setup1", "strategy": "face",
                                       "tool": {"index": 2}}))["tool"]["number"] == 3


def test_create_operation_without_tool(opc_env):
    r = ok(run("create_operation", {"setup": "Setup1", "strategy": "adaptive"}))
    assert r["tool"] is None and r["operation"]["name"] == "adaptive1"


def test_create_operation_strategy_check(opc_env):
    e = err(run("create_operation", {"setup": "Setup1", "strategy": "nope"}))
    assert e["code"] == "INVALID_PARAMS" and "face" in e["hints"]
    assert opc_env.s1._ops == []


def test_create_operation_strategy_unchecked_without_compatible_strategies(opc_env):
    opc_env.s1.operations._strategies = None
    assert ok(run("create_operation", {"setup": "Setup1", "strategy": "anything"}))["strategy"] == "anything"


@pytest.mark.parametrize("tool", [{}, {"number": 1, "index": 0}])
def test_create_operation_tool_needs_exactly_one_selector(opc_env, tool):
    assert err(run("create_operation", {"setup": "Setup1", "strategy": "face", "tool": tool}))["code"] == "INVALID_PARAMS"


def test_create_operation_tool_index_out_of_range(opc_env):
    assert err(run("create_operation", {"setup": "Setup1", "strategy": "face",
                                        "tool": {"index": 3}}))["code"] == "INVALID_PARAMS"


def test_create_operation_tool_not_found_hints(opc_env):
    e = err(run("create_operation", {"setup": "Setup1", "strategy": "face", "tool": {"number": 99}}))
    assert e["code"] == "NOT_FOUND" and e["hints"][0] == "#1 Flat 6"


def test_create_operation_tool_ambiguous(opc_env):
    e = err(run("create_operation", {"setup": "Setup1", "strategy": "face", "tool": {"description": "Flat 6"}}))
    assert e["code"] == "AMBIGUOUS" and e["candidates"] == ["#1 Flat 6", "#3 Flat 6"]


def test_create_operation_named_tool_library(opc_env):
    _tool_lib(opc_env)
    r = ok(run("create_operation", {"setup": "Setup1", "strategy": "face",
                                    "tool": {"library": "milling", "number": 7}}))
    assert r["tool"]["description"] == "Mill Tool"


def test_create_operation_library_not_found_hints(opc_env):
    _tool_lib(opc_env)
    e = err(run("create_operation", {"setup": "Setup1", "strategy": "face",
                                     "tool": {"library": "__none__", "number": 1}}))
    assert e["code"] == "NOT_FOUND" and "Milling" in e["hints"]


def test_create_operation_no_document_tool_library(opc_env):
    del opc_env.cam.documentToolLibrary
    e = err(run("create_operation", {"setup": "Setup1", "strategy": "face", "tool": {"number": 1}}))
    assert e["code"] == "UNSUPPORTED"


def test_create_operation_name_applied_when_build_ignores_display_name(opc_env):
    opc_env.s1.operations.add_ignores_name = True
    assert ok(run("create_operation", {"setup": "Setup1", "strategy": "face", "name": "Mine"}))[
        "operation"]["name"] == "Mine"


def test_create_operation_unknown_parameter(opc_env):
    e = err(run("create_operation", {"setup": "Setup1", "strategy": "face", "parameters": {"zzz": "1"}}))
    assert e["code"] == "INVALID_PARAMS"


def test_create_operation_tool_unsupported(opc_env, monkeypatch):
    monkeypatch.setattr(adsk.cam.OperationInput, "__slots__", (), raising=False)
    orig = adsk.cam.OperationInput.__init__

    def no_tool(self, strategy):
        orig(self, strategy)
        del self.tool

    monkeypatch.setattr(adsk.cam.OperationInput, "__init__", no_tool)
    e = err(run("create_operation", {"setup": "Setup1", "strategy": "face", "tool": {"number": 1}}))
    assert e["code"] == "UNSUPPORTED"
