"""Manufacture (CAM) setups, operations, toolpaths and post processing of the active document."""
import os
import time
from typing import Literal

from pydantic import BaseModel, TypeAdapter, ValidationError

from ..actions import action
from ..context import Ctx
from ..errors import (
    AMBIGUOUS,
    FUSION_ERROR,
    INVALID_PARAMS,
    NO_DESIGN,
    NO_DOCUMENT,
    NOT_FOUND,
    UNSUPPORTED,
    ActionError,
)
from ..types import Body, CamOperation, CamSetup, FileName, Length, Occurrence

CAM_WORKSPACE = "CAMEnvironment"
WAIT_SECONDS = 570  # generate_toolpath stops waiting here, inside the 600 s action timeout


# --- product access (design § 2) ----------------------------------------------------------------


def _product(doc, product_type):
    """``doc.products.itemByProductType`` that returns ``None`` where Fusion raises."""
    try:
        return doc.products.itemByProductType(product_type)
    except Exception:
        return None


def _cam(ctx, create):
    """The document's CAM product; ``None`` when absent and ``create`` is false. A write
    (``create``) activates the Manufacture workspace so Fusion creates the product, then restores
    the previous workspace."""
    doc = ctx.document
    if doc is None:
        raise ActionError(NO_DOCUMENT, "no active document")
    try:
        import adsk.cam
    except ImportError as exc:
        raise ActionError(UNSUPPORTED, "adsk.cam is not available") from exc
    if _product(doc, "DesignProductType") is None:
        raise ActionError(NO_DESIGN, "CAM needs a Fusion design document")
    p = _product(doc, "CAMProductType")
    if p is None and not create:
        return None
    if p is None:
        prev = ctx.ui.activeWorkspace
        ws = ctx.ui.workspaces.itemById(CAM_WORKSPACE)
        if ws is None:
            raise ActionError(UNSUPPORTED, "the Manufacture workspace is not available")
        try:
            ws.activate()
            adsk.doEvents()
            p = _product(doc, "CAMProductType")
        finally:
            if prev is not None and prev.id != CAM_WORKSPACE:
                prev.activate()
        if p is None:
            raise ActionError(UNSUPPORTED, "Fusion did not create the Manufacture product for this document")
    return adsk.cam.CAM.cast(p)


# --- refs and output helpers (design § 3) -----------------------------------------------------------


def _setup_ref(s):
    return {"type": "Setup", "name": s.name}


def _op_ref(op):
    return {"type": "Operation", "name": op.name, "setup": op.parentSetup.name}


def _enum_name(value, enum, table):
    """The ``table`` label of the ``enum`` member equal to ``value``; ``None`` when unknown."""
    for attr, label in table:
        member = getattr(enum, attr, None)
        if member is not None and value == member:
            return label
    return None


def _type_name(setup):
    import adsk.cam

    return (
        _enum_name(
            setup.operationType,
            adsk.cam.OperationTypes,
            (
                ("MillingOperation", "milling"),
                ("TurningOperation", "turning"),
                ("JetOperation", "cutting"),
                ("AdditiveOperation", "additive"),
            ),
        )
        or "other"
    )


def _state(op):
    import adsk.cam

    try:
        value = op.operationState
    except AttributeError:
        return None
    return _enum_name(
        value,
        adsk.cam.OperationStates,
        (
            ("IsValidOperationState", "valid"),
            ("IsInvalidOperationState", "invalid"),
            ("SuspendedOperationState", "suspended"),
            ("NoToolpathOperationState", "no_toolpath"),
        ),
    )


def _tool_value(tool, name):
    p = tool.parameters.itemByName(name)
    return None if p is None else p.value.value


def _tool_out(ctx, tool):
    if tool is None:
        return None
    number, diameter = _tool_value(tool, "tool_number"), _tool_value(tool, "tool_diameter")
    description, kind = _tool_value(tool, "tool_description"), _tool_value(tool, "tool_type")
    return {
        "number": None if number is None else int(number),
        "description": None if description is None else str(description),
        "type": None if kind is None else str(kind),
        "diameter": None if diameter is None else ctx.length_out(float(diameter)),
    }


def _param_out(p):
    return {"name": p.name, "title": p.title, "expression": p.expression}


def _param_hints(params, name):
    prefix = name.split("_")[0]
    names = [params.item(i).name for i in range(params.count)]
    return [n for n in names if n.split("_")[0] == prefix][:20]


def _unknown_param(params, name):
    return ActionError(INVALID_PARAMS, "unknown parameter %s" % name, hints=_param_hints(params, name))


def _set_param(ctx, params, name, expression):
    """Set a parameter by ``expression`` (``value`` is read-only on current builds)."""
    p = params.itemByName(name)
    if p is None:
        raise _unknown_param(params, name)
    try:
        p.expression = expression
    except Exception as exc:
        raise ActionError(
            FUSION_ERROR, "cannot set parameter %s to %r: %s" % (name, expression, exc)
        ) from exc


# --- libraries -------------------------------------------------------------------------------------


def _walk(lib, url):
    """Asset URLs under ``url``, folders recursed."""
    try:
        assets, folders = list(lib.childAssetURLs(url)), list(lib.childFolderURLs(url))
    except RuntimeError:  # a location whose folder does not exist (e.g. an unsynced cloud library)
        return
    yield from assets
    for folder in folders:
        yield from _walk(lib, folder)


def _locations():
    import adsk.cam

    names = ("LocalLibraryLocation", "CloudLibraryLocation", "Fusion360LibraryLocation")
    return [getattr(adsk.cam.LibraryLocations, n) for n in names if hasattr(adsk.cam.LibraryLocations, n)]


def _basename(url):
    return os.path.splitext(url.toString().rsplit("/", 1)[-1])[0]


def _lib_find(ctx, lib, name, kind):
    """The URL of the asset called ``name`` (a URL, or a basename searched per location)."""
    import adsk.core

    if "://" in name:
        return adsk.core.URL.create(name)
    seen = []
    for loc in _locations():
        root = lib.urlByLocation(loc)
        if root is None:
            continue
        assets = list(_walk(lib, root))
        seen.extend(_basename(u) for u in assets)
        matches = [u for u in assets if _basename(u).lower() == name.lower()]
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise ActionError(
                AMBIGUOUS, "%d %s assets are named %r" % (len(matches), kind, name),
                hints=["pass the full URL"], candidates=[u.toString() for u in matches],
            )
    raise ActionError(NOT_FOUND, "no %s named %r" % (kind, name), hints=seen[:20])


# --- read actions (design § 4.1-4.3) -----------------------------------------------------------------


def _setup_item(ctx, s):
    ops = s.allOperations
    stock = s.parameters.itemByName("job_stockMode")
    return {
        "setup": _setup_ref(s),
        "type": _type_name(s),
        "models": [ctx.ref(m) for m in s.models] if hasattr(s, "models") else None,
        "operations": ops.count,
        "toolpaths_valid": ops.count > 0 and all(ops.item(i).isToolpathValid for i in range(ops.count)),
        "stock_mode": None if stock is None else stock.expression,
    }


@action("cam", access="read")
def list_setups(ctx: Ctx, offset: int = 0, limit: int = 100) -> dict:
    """List the Manufacture setups: type, models, operation count, toolpath validity and stock mode."""
    cam = _cam(ctx, create=False)
    setups = [] if cam is None else list(cam.setups)
    return ctx.page(setups, offset, limit, lambda s: _setup_item(ctx, s))


def _op_item(ctx, op):
    return {
        "operation": _op_ref(op),
        "strategy": op.strategy,
        "tool": _tool_out(ctx, op.tool),
        "has_toolpath": op.hasToolpath,
        "toolpath_valid": op.isToolpathValid,
        "state": _state(op),
        "suppressed": op.isSuppressed,
        "generating": op.isGenerating,
    }


@action("cam", access="read")
def list_operations(
    ctx: Ctx, setup: CamSetup | None = None, offset: int = 0, limit: int = 100
) -> dict:
    """List operations of a setup, or of every setup: strategy, tool and toolpath state."""
    cam = _cam(ctx, create=False)
    ops = [] if cam is None else list((cam if setup is None else setup).allOperations)
    return ctx.page(ops, offset, limit, lambda op: _op_item(ctx, op))


def _flag(obj, name):
    value = getattr(obj, name, None)
    return None if value is None else bool(value)


@action("cam", access="read")
def operation_info(
    ctx: Ctx,
    operation: CamOperation | None = None,
    setup: CamSetup | None = None,
    names: list[str] | None = None,
    offset: int = 0,
    limit: int = 100,
) -> dict:
    """Parameters of one operation or setup (all, or the given names); discovers parameter names."""
    if (operation is None) == (setup is None):
        raise ActionError(INVALID_PARAMS, "pass exactly one of operation, setup")
    target = operation if operation is not None else setup
    params = target.parameters
    if names is None:
        found = [params.item(i) for i in range(params.count)]
    else:
        found = []
        for n in names:
            p = params.itemByName(n)
            if p is None:
                raise _unknown_param(params, n)
            found.append(p)
    page = ctx.page(found, offset, limit, _param_out)
    if operation is None:
        return {"setup": _setup_ref(setup), "type": _type_name(setup), "parameters": page}
    return {
        "operation": _op_ref(operation),
        "setup": _setup_ref(operation.parentSetup),
        "strategy": operation.strategy,
        "tool": _tool_out(ctx, operation.tool),
        "toolpath": {
            "has_toolpath": operation.hasToolpath,
            "valid": operation.isToolpathValid,
            "generating": operation.isGenerating,
            "state": _state(operation),
            "warning": _flag(operation, "hasWarning"),
            "error": _flag(operation, "hasError"),
        },
        "parameters": page,
    }


# --- create_setup (design § 4.5) -----------------------------------------------------------------------------

StockMode = Literal["relative_box", "fixed_box", "relative_cylinder", "fixed_cylinder", "from_solid"]
_STOCK_MODES = {
    "relative_box": "'default'", "fixed_box": "'fixedbox'", "relative_cylinder": "'relativecylinder'",
    "fixed_cylinder": "'fixedcylinder'", "from_solid": "'solid'",
}


def _find_machine(ctx, name):
    import adsk.cam

    lm = adsk.cam.CAMManager.get().libraryManager
    if not hasattr(lm, "machineLibrary"):
        raise ActionError(UNSUPPORTED, "this Fusion build has no machine library")
    ml, seen = lm.machineLibrary, []
    for loc in _locations():
        root = ml.urlByLocation(loc)
        if root is None:
            continue
        try:
            machines = list(ml.childMachines(root))
        except Exception:  # a location whose folder does not exist (e.g. an unsynced cloud library)
            continue
        for m in machines:
            if name.lower() in ((m.description or "").lower(), (getattr(m, "model", None) or "").lower()):
                return m
        seen.extend(m.description for m in machines)
    raise ActionError(NOT_FOUND, "no machine named %r" % name, hints=seen[:20])


@action("cam", access="write", timeout=60)
def create_setup(
    ctx: Ctx,
    type: Literal["milling", "turning", "additive"] = "milling",
    bodies: list[Body] | None = None,
    occurrences: list[Occurrence] | None = None,
    name: str | None = None,
    stock_mode: StockMode | None = None,
    stock_offset_sides: Length | None = None,
    stock_offset_top: Length | None = None,
    stock_offset_bottom: Length | None = None,
    machine: str | None = None,
    parameters: dict[str, str] | None = None,
) -> dict:
    """Create a Manufacture setup on bodies or occurrences, with optional stock, machine and parameters."""
    import adsk.cam

    models = list(bodies or []) + list(occurrences or [])
    if not models:
        raise ActionError(INVALID_PARAMS, "pass bodies or occurrences for the setup")
    if type == "additive" and not hasattr(adsk.cam.OperationTypes, "AdditiveOperation"):
        raise ActionError(UNSUPPORTED, "this Fusion build has no additive setups")
    if type == "additive" and machine is None:
        raise ActionError(INVALID_PARAMS, "additive setups need a machine")
    cam = _cam(ctx, create=True)
    op_type = getattr(adsk.cam.OperationTypes, type.capitalize() + "Operation")
    si = cam.setups.createInput(op_type)
    si.models = models
    if name is not None:
        si.name = name
    if machine is not None:
        si.machine = _find_machine(ctx, machine)
    setup = cam.setups.add(si)
    writes = []
    if stock_mode is not None:
        writes.append(("job_stockMode", _STOCK_MODES[stock_mode]))
    offsets = (("job_stockOffsetSides", stock_offset_sides), ("job_stockOffsetTop", stock_offset_top),
               ("job_stockOffsetBottom", stock_offset_bottom))
    if any(v is not None for _, v in offsets):
        writes.append(("job_stockOffsetMode", "'simple'"))
    writes += [(n, v.expr) for n, v in offsets if v is not None]
    writes += list((parameters or {}).items())
    try:
        for n, expression in writes:
            _set_param(ctx, setup.parameters, n, expression)
    except Exception:
        setup.deleteMe()  # a rejected parameter leaves no half-configured setup behind
        raise
    return {
        "setup": _setup_ref(setup),
        "type": _type_name(setup),
        "models": [ctx.ref(m) for m in models],
        "parameters_set": {n: setup.parameters.itemByName(n).expression for n, _ in writes},
    }


# --- create_operation (design § 4.6) --------------------------------------------------------------------------


class ToolSpec(BaseModel, extra="forbid"):
    library: str | None = None  # library name or URL; None = document tool library
    number: int | None = None
    description: str | None = None
    index: int | None = None


def _find_tool(ctx, cam, spec):
    import adsk.cam

    if sum(x is not None for x in (spec.number, spec.description, spec.index)) != 1:
        raise ActionError(INVALID_PARAMS, "tool needs exactly one of number, description, index")
    if spec.library is None:
        if not hasattr(cam, "documentToolLibrary"):
            raise ActionError(UNSUPPORTED, "this Fusion build has no document tool library")
        lib = cam.documentToolLibrary
    else:
        tl = adsk.cam.CAMManager.get().libraryManager.toolLibraries
        lib = tl.toolLibraryAtURL(_lib_find(ctx, tl, spec.library, "tool library"))
    tools = [lib.item(i) for i in range(lib.count)]
    if spec.index is not None:
        if not 0 <= spec.index < len(tools):
            raise ActionError(INVALID_PARAMS, "tool index %d is out of range (0..%d)" % (spec.index, len(tools) - 1))
        return tools[spec.index]
    key, want = ("tool_number", spec.number) if spec.number is not None else ("tool_description", spec.description)
    found = [t for t in tools if _tool_value(t, key) == want]
    label = ["#%s %s" % (_tool_value(t, "tool_number"), _tool_value(t, "tool_description")) for t in found]
    if not found:
        raise ActionError(
            NOT_FOUND, "no tool with %s %r" % (key[5:], want),
            hints=["#%s %s" % (_tool_value(t, "tool_number"), _tool_value(t, "tool_description"))
                   for t in tools][:20],
        )
    if len(found) > 1:
        raise ActionError(AMBIGUOUS, "%d tools match %r" % (len(found), want), candidates=label)
    return found[0]


@action("cam", access="write", timeout=60)
def create_operation(
    ctx: Ctx,
    setup: CamSetup,
    strategy: str,
    tool: ToolSpec | None = None,
    name: str | None = None,
    parameters: dict[str, str] | None = None,
) -> dict:
    """Add a machining operation (strategy, optional tool and parameters) to a setup."""
    cam = _cam(ctx, create=True)
    if hasattr(setup.operations, "compatibleStrategies"):
        names = [x.name for x in setup.operations.compatibleStrategies]
        if strategy not in names:
            raise ActionError(INVALID_PARAMS, "unknown strategy %r" % strategy, hints=names[:40])
    tool_obj = None if tool is None else _find_tool(ctx, cam, tool)
    oi = setup.operations.createInput(strategy)
    if tool_obj is not None:
        if not hasattr(oi, "tool"):
            raise ActionError(UNSUPPORTED, "this Fusion build cannot set an operation's tool")
        oi.tool = tool_obj
    if name is not None and hasattr(oi, "displayName"):
        oi.displayName = name
    op = setup.operations.add(oi)
    if name is not None and op.name != name:
        op.name = name
    writes = list((parameters or {}).items())
    for n, expression in writes:
        _set_param(ctx, op.parameters, n, expression)
    return {
        "operation": _op_ref(op),
        "strategy": op.strategy,
        "tool": _tool_out(ctx, op.tool),
        "parameters_set": {n: op.parameters.itemByName(n).expression for n, _ in writes},
        "has_toolpath": False,
    }


# --- generate_toolpath (design § 4.7) -------------------------------------------------------------------


def _generated_item(op):
    return {
        "operation": _op_ref(op),
        "has_toolpath": op.hasToolpath,
        "toolpath_valid": op.isToolpathValid,
        "state": _state(op),
        "warning": _flag(op, "hasWarning"),
        "error": _flag(op, "hasError"),
    }


def _probe(future):
    """A zero-argument completion probe for ``future``, or ``None`` when it has none."""
    for name in ("isGenerationCompleted", "isCompleted"):
        if hasattr(future, name):
            return lambda: getattr(future, name)
    return None


@action("cam", access="write", timeout=600, undo_group=False)
def generate_toolpath(
    ctx: Ctx,
    setup: CamSetup | None = None,
    operations: list[CamOperation] | None = None,
    skip_valid: bool = False,
    wait: bool = True,
) -> dict:
    """Generate toolpaths for all operations, a setup or listed operations; waits for completion."""
    import adsk
    import adsk.core

    if setup is not None and operations is not None:
        raise ActionError(INVALID_PARAMS, "pass at most one of setup, operations")
    cam = _cam(ctx, create=False)
    if cam is None:
        return {"completed": True, "operations": []}
    if setup is None and operations is None:
        targets = list(cam.allOperations)
        future = cam.generateAllToolpaths(skip_valid)
    else:
        targets = list(setup.allOperations) if setup is not None else list(operations)
        todo = [op for op in targets if not (skip_valid and op.isToolpathValid)]
        if not todo:
            return {"completed": True, "operations": [_generated_item(op) for op in targets]}
        coll = adsk.core.ObjectCollection.create()
        for op in todo:
            coll.add(op)
        future = cam.generateToolpath(coll)
    probe = _probe(future) if wait else None
    completed = False
    if probe is not None:
        deadline = time.monotonic() + WAIT_SECONDS
        while not completed and time.monotonic() < deadline:
            completed = bool(probe())
            if not completed:
                adsk.doEvents()
                time.sleep(0.1)
    return {"completed": completed, "operations": [_generated_item(op) for op in targets]}


# --- post_process (design § 4.4) --------------------------------------------------------------------------


def _find_post(ctx, lib, post):
    """A post URL by name, URL or, failing that, the post's own description."""
    try:
        return _lib_find(ctx, lib, post, "post")
    except ActionError as exc:
        if exc.code != NOT_FOUND or "://" in post:
            raise
        for loc in _locations():
            root = lib.urlByLocation(loc)
            for url in () if root is None else _walk(lib, root):
                try:
                    description = lib.postConfigurationAtURL(url).description
                except Exception:
                    continue
                if description and description.lower() == post.lower():
                    return url
        raise


def _post_file(ctx, cam, post):
    """The ``.cps`` path for a file-route ``post``, or ``None`` for the library route."""
    if post.endswith(".cps") or "/" in post or "\\" in post or os.sep in post:
        if not os.path.isfile(post):
            raise ActionError(NOT_FOUND, "post file not found: %s" % post)
        return post
    folder = getattr(cam, "genericPostFolder", None)
    candidate = os.path.join(folder, post + ".cps") if folder else None
    return candidate if candidate and os.path.isfile(candidate) else None


def _post_via_file(cam, path, target, name, folder, comment, output_units):
    import adsk.cam

    if not hasattr(adsk.cam, "PostProcessInput"):
        raise ActionError(UNSUPPORTED, "this Fusion build cannot post process from a .cps file")
    opts = adsk.cam.PostOutputUnitOptions
    units = {"document": opts.DocumentUnitsOutput, "mm": opts.MillimetersOutput,
             "in": opts.InchesOutput}[output_units]
    inp = adsk.cam.PostProcessInput.create(name, path, folder, units)
    inp.programComment = comment
    inp.isOpenInEditor = False
    cam.postProcess(target, inp)


def _post_via_library(ctx, cam, post, targets, name, folder, comment, output_units):
    import adsk.cam

    if not (hasattr(adsk.cam, "CAMManager") and hasattr(cam, "ncPrograms")):
        raise ActionError(UNSUPPORTED, "this Fusion build cannot post process from the post library")
    if output_units != "document":
        raise ActionError(
            INVALID_PARAMS,
            "library posts use the post's own unit setting; pass a .cps path to force units",
        )
    lib = adsk.cam.CAMManager.get().libraryManager.postLibrary
    url = _find_post(ctx, lib, post)
    cfg = lib.postConfigurationAtURL(url)
    ni = cam.ncPrograms.createInput()
    ni.displayName = name
    for key, value in (("nc_program_filename", name), ("nc_program_output_folder", folder),
                       ("nc_program_comment", comment), ("nc_program_openInEditor", False)):
        ni.parameters.itemByName(key).value.value = value
    ni.operations = targets
    nc = cam.ncPrograms.add(ni)
    try:
        nc.postConfiguration = cfg
        nc.postProcess(adsk.cam.NCProgramPostProcessOptions.create())
    finally:
        nc.deleteMe()
    return url.toString()


@action("cam", access="write", timeout=120, edits_document="none")
def post_process(
    ctx: Ctx,
    post: str,
    output_folder: str,
    setup: CamSetup | None = None,
    operations: list[CamOperation] | None = None,
    program_name: FileName | None = None,
    program_comment: str = "",
    output_units: Literal["document", "mm", "in"] = "document",
) -> dict:
    """Post process a setup or operations with a .cps file or a library post; returns the NC files."""
    import adsk.core

    if (setup is None) == (operations is None):
        raise ActionError(INVALID_PARAMS, "pass exactly one of setup, operations")
    if not os.path.isabs(output_folder):
        raise ActionError(INVALID_PARAMS, "output_folder must be an absolute path")
    cam = _cam(ctx, create=False)
    targets = [op for op in setup.allOperations if not op.isSuppressed] if setup is not None else list(operations)
    stale = [op.name for op in targets if not (op.hasToolpath and op.isToolpathValid)]
    if stale:
        raise ActionError(
            INVALID_PARAMS, "operations without a valid toolpath: %s" % ", ".join(stale),
            hints=["run cam.generate_toolpath first"],
        )
    if not targets:
        raise ActionError(INVALID_PARAMS, "nothing to post")
    if program_name is None:
        program_name = (setup if setup is not None else targets[0].parentSetup).name
        try:
            TypeAdapter(FileName).validate_python(program_name)
        except ValidationError as exc:
            raise ActionError(
                INVALID_PARAMS, "program_name defaults to %r, which is not a valid file name; pass "
                "program_name (%s)" % (program_name, exc.errors()[0]["msg"])
            ) from exc
    os.makedirs(output_folder, exist_ok=True)
    started = time.time()
    path = _post_file(ctx, cam, post)
    if path is not None:
        target = setup
        if target is None:
            target = adsk.core.ObjectCollection.create()
            for op in targets:
                target.add(op)
        _post_via_file(cam, path, target, program_name, output_folder, program_comment, output_units)
        route, used = "file", path
    else:
        used = _post_via_library(
            ctx, cam, post, targets, program_name, output_folder, program_comment, output_units
        )
        route = "library"
    files = sorted(
        e.path for e in os.scandir(output_folder) if e.is_file() and e.stat().st_mtime >= started
    )
    if not files:
        raise ActionError(FUSION_ERROR, "post processor wrote no file")
    return {
        "post": used, "route": route, "program_name": program_name, "output_folder": output_folder,
        "files": files, "operations": [_op_ref(op) for op in targets],
    }


# --- live scenarios (design § 5.2) ---------------------------------------------------------------------

import json  # noqa: E402
import pathlib  # noqa: E402
import tempfile  # noqa: E402

from .live_common import _fresh, _lc  # noqa: E402

_POST = pathlib.Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "posts" / "defusion_test.cps"


async def _c(session, act, params=None):
    env = await _lc().call(session, "cam", act, params or {})
    assert env["ok"], "cam.%s: %s" % (act, json.dumps(env.get("error"))[:500])
    return env["result"]


async def _c_err(session, act, params, *codes):
    env = await _lc().call(session, "cam", act, params)
    assert not env["ok"] and env["error"]["code"] in codes, "cam.%s expected %s, got %s" % (
        act, codes, json.dumps(env)[:400])
    return env["error"]


async def _box(session):
    """The 50 x 40 x 20 mm fixture box (DEP-CAM-002); returns the body ref."""
    return await _lc().box(session, 50, 40, 20)


async def _milling_setup(session, body, **kw):
    return await _c(session, "create_setup", dict(type="milling", bodies=[body], **kw))


async def _face_op(session, setup):
    """A face operation whose tool is a flat end mill, searching the first Milling library's tools."""
    err = await _c_err(session, "create_operation", {"setup": setup, "strategy": "face",
                                                     "tool": {"library": "__none__", "index": 0}}, "NOT_FOUND")
    hints = err["hints"]
    assert hints, err
    lib = next((h for h in hints if "Milling" in h), hints[0])
    for i in range(10):
        r = await _c(session, "create_operation", {"setup": setup, "strategy": "face",
                                                   "tool": {"library": lib, "index": i}})
        if (r["tool"] or {}).get("type") == "flat end mill":
            return r
    raise AssertionError("no flat end mill among the first 10 tools of %s" % lib)


async def _list_setups(session):
    async with _fresh(session):
        await _box(session)
        assert (await _c(session, "list_setups"))["total"] == 0


async def _expr(session, setup, name):
    r = await _c(session, "operation_info", {"setup": setup, "names": [name]})
    return r["parameters"]["items"][0]["expression"]


async def _create_setup(session):
    async with _fresh(session):
        body = await _box(session)
        r = await _milling_setup(session, body, stock_mode="relative_box", stock_offset_sides="1 mm")
        assert r["type"] == "milling", r
        assert await _expr(session, r["setup"], "job_stockMode") == "'default'", r
        assert (await _expr(session, r["setup"], "job_stockOffsetSides")).replace(" ", "") in ("1mm", "0.1cm"), r
        # the default expression is a conditional formula Fusion refuses back ("Invalid enumeration value"),
        # so the round trip passes the literal enumeration value it evaluates to
        assert "stockPoint" in await _expr(session, r["setup"], "wcs_origin_mode")
        r2 = await _milling_setup(session, body, parameters={"wcs_origin_mode": "'stockPoint'"})
        assert await _expr(session, r2["setup"], "wcs_origin_mode") == "'stockPoint'", r2
        n = (await _c(session, "list_setups"))["total"]
        err = await _c_err(session, "create_setup", {"type": "milling", "bodies": [body],
                                                     "parameters": {"wcs_nope": "1"}}, "INVALID_PARAMS")
        assert any(h.startswith("wcs_") for h in err["hints"]), err
        assert (await _c(session, "list_setups"))["total"] == n
        r3 = await _c(session, "create_setup", {"type": "turning", "bodies": [body]})
        assert r3["type"] == "turning", r3
        await _c_err(session, "create_setup", {"type": "additive", "bodies": [body]}, "INVALID_PARAMS")
        err = await _c_err(session, "create_setup", {"type": "additive", "bodies": [body], "machine": "__none__"},
                           "NOT_FOUND", "UNSUPPORTED")
        if err["code"] == "NOT_FOUND" and err["hints"]:
            env = await _lc().call(session, "cam", "create_setup", {"type": "additive", "bodies": [body],
                                                                    "machine": err["hints"][0]})
            # an account without the additive licence gets "Setup creation failed" from Fusion for every machine
            assert env["ok"] and env["result"]["type"] == "additive" or env["error"]["code"] == "UNSUPPORTED" or (
                "Setup creation failed" in env["error"]["message"]), env


async def _create_operation(session):
    async with _fresh(session):
        body = await _box(session)
        setup = (await _milling_setup(session, body))["setup"]
        r = await _face_op(session, setup)
        assert r["tool"]["type"] == "flat end mill" and r["has_toolpath"] is False, r
        err = await _c_err(session, "create_operation", {"setup": setup, "strategy": "nope"}, "INVALID_PARAMS")
        assert "face" in err["hints"], err


async def _list_operations(session):
    async with _fresh(session):
        body = await _box(session)
        setup = (await _milling_setup(session, body))["setup"]
        op = await _face_op(session, setup)
        items = (await _c(session, "list_operations", {"setup": setup}))["items"]
        assert items and all(i["has_toolpath"] is False for i in items), items
        assert op["operation"]["name"] in {i["operation"]["name"] for i in items}, items


async def _operation_info(session):
    async with _fresh(session):
        body = await _box(session)
        setup = (await _milling_setup(session, body))["setup"]
        op = (await _face_op(session, setup))["operation"]
        r = await _c(session, "operation_info", {"operation": op, "names": ["tool_number"]})
        assert r["toolpath"]["has_toolpath"] is False and len(r["parameters"]["items"]) == 1, r
        again = await _c(session, "operation_info", {"operation": r["operation"], "names": ["tool_number"]})
        assert again["operation"] == r["operation"], again


async def _generate_toolpath(session):
    async with _fresh(session):
        body = await _box(session)
        setup = (await _milling_setup(session, body))["setup"]
        op = (await _face_op(session, setup))["operation"]
        r = await _c(session, "generate_toolpath", {"operations": [op]})
        assert r["completed"] is True and r["operations"][0]["toolpath_valid"] is True, r
        again = await _c(session, "generate_toolpath", {"operations": [op], "skip_valid": True})
        assert again["completed"] is True, again


async def _post_process(session):
    async with _fresh(session):
        body = await _box(session)
        setup = (await _milling_setup(session, body))["setup"]
        op = (await _face_op(session, setup))["operation"]
        await _c(session, "generate_toolpath", {"operations": [op]})
        folder = tempfile.mkdtemp(prefix="defusion-post-")
        r = await _c(session, "post_process", {"post": str(_POST), "output_folder": folder, "operations": [op],
                                               "program_name": "defusion1"})
        assert r["files"] and "(DEFUSION)" in pathlib.Path(r["files"][0]).read_text(), r
        folder2 = tempfile.mkdtemp(prefix="defusion-post-")
        r = await _c(session, "post_process", {"post": "fanuc", "output_folder": folder2, "operations": [op],
                                               "program_name": "1002"})
        assert r["files"], r
        second = await _face_op(session, setup)
        err = await _c_err(session, "post_process", {"post": str(_POST), "output_folder": folder, "setup": setup,
                                                     "program_name": "defusion3"}, "INVALID_PARAMS")
        assert second["operation"]["name"] in err["message"], err
        await _c_err(session, "post_process", {"post": str(_POST), "output_folder": "relative/dir",
                                               "operations": [op]}, "INVALID_PARAMS")


LIVE_SCENARIOS = [(f.__name__[1:], "cam." + f.__name__[1:], f) for f in (
    _list_setups, _create_setup, _create_operation, _list_operations, _operation_info, _generate_toolpath,
    _post_process)]
