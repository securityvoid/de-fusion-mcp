# design.md — docs/specs/cam/
> De-Fusion MCP — `cam` tool: Manufacture setups, operations, toolpath generation and post processing (`adsk.cam`)

## 1. Overview
One module, `De-Fusion/defusion/tools/cam.py`, registers seven actions on tool `cam` (tool summary
sentence: "Manufacture (CAM) setups, operations, toolpaths and post processing of the active
document."). Everything not written here is core behaviour (core § 5). Lengths in results go
through `ctx.length_out` (core § 8); design entities in results are `ctx.ref(obj)` (core § 5.4);
setup and operation parameters are core `CamSetup`/`CamOperation` (core § 5.4) and results use the
CAM output refs of § 3.

| Action | Access | Timeout | REQ |
|---|---|---|---|
| `list_setups` | read | 30 | REQ-CAM-003 |
| `list_operations` | read | 30 | REQ-CAM-004 |
| `operation_info` | read | 30 | REQ-CAM-005 |
| `post_process` | write | 120 | REQ-CAM-006 |
| `create_setup` | write | 60 | REQ-CAM-007 |
| `create_operation` | write | 60 | REQ-CAM-008 |
| `generate_toolpath` | write, `undo_group=False` | 600 | REQ-CAM-009 |

REQ-CAM-001 (§ 2) and REQ-CAM-002 (§ 3) apply to every action. `post_process` is `write` because
the file route runs a client-named `.cps` post processor as code (the export rule of core § 5.1
does not apply); its temporary NC program (§ 4.4) is deleted before return.

## 2. Obtaining the CAM product (`_cam`)
```python
def _cam(ctx: Ctx, create: bool) -> "adsk.cam.CAM | None":
```
1. `ctx.document is None` → `NO_DOCUMENT`.
2. `import adsk.cam` raising `ImportError` → `UNSUPPORTED` ("adsk.cam is not available").
3. `_product(doc, "DesignProductType") is None` → `NO_DESIGN` ("CAM needs a Fusion design
   document"). `_product(doc, type)` = `doc.products.itemByProductType(type)` inside `try`;
   an exception or `None` returns `None` (the call raises when the product does not exist).
4. `p = _product(doc, "CAMProductType")`.
5. `p is None and not create` → return `None`. Read actions treat `None` as zero setups:
   `list_setups`/`list_operations` return an empty page (a named setup or operation already fails
   core resolution with `NOT_FOUND`, core § 5.4).
6. `p is None and create`: `prev = ctx.ui.activeWorkspace`;
   `ws = ctx.ui.workspaces.itemById("CAMEnvironment")`; `ws is None` → `UNSUPPORTED`;
   `try: ws.activate(); adsk.doEvents(); p = _product(doc, "CAMProductType")`
   `finally: if prev is not None and prev.id != "CAMEnvironment": prev.activate()`.
   Still `None` → `UNSUPPORTED` ("Fusion did not create the Manufacture product for this
   document").
7. Return `adsk.cam.CAM.cast(p)`.

Read actions call `_cam(ctx, create=False)`; write actions `_cam(ctx, create=True)`. The user never
switches workspaces; a write's transient switch is restored within the same call. Design-entity
Refs (`Body`, `Occurrence`) resolve against `ctx.design`, which is the Design product even while
Manufacture is active (core § 5.9, DEP-CAM-001).

## 3. CAM refs
Setup and operation parameters use the core types `CamSetup` and `CamOperation` (core § 5.4:
bare name, `{"name", "setup"?}`, token or the output refs below; `NOT_FOUND`/`AMBIGUOUS` from
core). They arrive as resolved `adsk.cam.Setup` / `adsk.cam.Operation` objects.
- Output: `_setup_ref(s) = {"type": "Setup", "name": s.name}`;
  `_op_ref(op) = {"type": "Operation", "name": op.name, "setup": op.parentSetup.name}`. Both are
  valid `CamSetup`/`CamOperation` inputs unchanged.

Shared helpers:
- `_type_name(setup)`: `setup.operationType` → `milling` (`OperationTypes.MillingOperation`),
  `turning` (`TurningOperation`), `cutting` (`JetOperation`), `additive` (`AdditiveOperation`,
  only if the attribute exists); unknown → `"other"`.
- `_state(op)`: `op.operationState` → `valid` (`OperationStates.IsValidOperationState`),
  `invalid` (`IsInvalidOperationState`), `suspended` (`SuspendedOperationState`), `no_toolpath`
  (`NoToolpathOperationState`); attribute missing or unknown value → `null`.
- `_tool_out(ctx, tool)`: `None` → `null`; else from `tool.parameters.itemByName(n).value.value`
  for `tool_number` (int), `tool_description` (str), `tool_type` (str), `tool_diameter` (float, cm,
  via `ctx.length_out`): `{"number", "description", "type", "diameter"}`; a missing parameter
  gives `null` for that key.
- `_param_out(p)`: `{"name": p.name, "title": p.title, "expression": p.expression}`.
- `_set_param(ctx, params, name, expression)`: `p = params.itemByName(name)`; `None` →
  `INVALID_PARAMS` ("unknown parameter <name>"), hints = up to 20 parameter names that share the
  text before the first `_` of `name`; else `p.expression = expression`; an exception →
  `FUSION_ERROR` naming the parameter and expression. Values are always set through
  `expression` (`value` is read-only on current builds). Expressions are passed verbatim:
  enumeration values need single quotes (`"'flood'"`), lengths should carry units (`"2 mm"`).
- `_walk(lib, url)`: yields asset URLs under `url` from `lib.childAssetURLs(url)` and recursively
  `lib.childFolderURLs(url)`. `_locations()` = the members of `adsk.cam.LibraryLocations` among
  `LocalLibraryLocation`, `CloudLibraryLocation`, `Fusion360LibraryLocation` that exist, in that
  order. `_basename(url)` = last `/` segment of `url.toString()` without extension.
- `_lib_find(ctx, lib, name, kind)`: `name` containing `://` → `adsk.core.URL.create(name)`. Else
  for each location: matches = assets whose `_basename` equals `name` case-insensitively; first
  location with matches wins; one → its URL; several → `AMBIGUOUS` (candidates = URL strings);
  none anywhere → `NOT_FOUND`, hints = up to 20 basenames seen. `kind` names the library in the
  message.

## 4. Actions
### 4.1 list_setups
```python
@action("cam", access="read")
def list_setups(ctx: Ctx, offset: int = 0, limit: int = 100) -> dict:
```
- API: `cam.setups`; per setup `name`, `operationType`, `models` (feature-detected with `hasattr`;
  iterable of bodies/occurrences), `allOperations` (`count`, `item(i).isToolpathValid`),
  `parameters.itemByName("job_stockMode").expression`.
- Item: `{"setup": <cam ref>, "type": str, "models": [<ref>...] | null, "operations": int,
  "toolpaths_valid": bool, "stock_mode": str | null}`. `toolpaths_valid` is `false` for a setup
  with no operations. Result: `ctx.page(items, offset, limit)`.

### 4.2 list_operations
```python
@action("cam", access="read")
def list_operations(ctx: Ctx, setup: CamSetup | None = None,
                    offset: int = 0, limit: int = 100) -> dict:
```
- API: `setup.allOperations` or `cam.allOperations`; per op `strategy`, `tool`, `hasToolpath`,
  `isToolpathValid`, `isSuppressed`, `isGenerating`, `operationState`.
- Item: `{"operation": <cam ref>, "strategy": str, "tool": <_tool_out>, "has_toolpath": bool,
  "toolpath_valid": bool, "state": str | null, "suppressed": bool, "generating": bool}`.
  Result: `ctx.page(items, offset, limit)`.

### 4.3 operation_info
```python
@action("cam", access="read")
def operation_info(ctx: Ctx, operation: CamOperation | None = None,
                   setup: CamSetup | None = None, names: list[str] | None = None,
                   offset: int = 0, limit: int = 100) -> dict:
```
- Exactly one of `operation`, `setup` → else `INVALID_PARAMS`.
- Parameters: `target.parameters` (`count`/`item(i)`) in Fusion order, or, with `names`,
  `itemByName` per name in the given order; a missing name → `INVALID_PARAMS` (hints as
  `_set_param`).
- Operation result:
  ```json
  {"operation": <cam ref>, "setup": <cam ref>, "strategy": "face", "tool": <_tool_out>,
   "toolpath": {"has_toolpath": true, "valid": true, "generating": false, "state": "valid",
                "warning": false, "error": false},
   "parameters": {"items": [<_param_out>...], "total": n, "offset": 0, "next_offset": null}}
  ```
  `warning`/`error` from `op.hasWarning`/`op.hasError` (`null` when missing).
- Setup result: `{"setup": <cam ref>, "type": str, "parameters": <page>}`. This is how callers
  discover stock and WCS parameter names for `create_setup`.

### 4.4 post_process
```python
@action("cam", access="write", timeout=120)
def post_process(ctx: Ctx, post: str, output_folder: str, setup: CamSetup | None = None,
                 operations: list[CamOperation] | None = None, program_name: FileName | None = None,
                 program_comment: str = "",
                 output_units: Literal["document", "mm", "in"] = "document") -> dict:
```
1. Exactly one of `setup`, `operations` → else `INVALID_PARAMS`. `output_folder` not
   absolute → `INVALID_PARAMS`.
2. Targets: `setup.allOperations` minus suppressed ones, or the resolved `operations`. Any
   target without `hasToolpath and isToolpathValid` → `INVALID_PARAMS` listing their names, hint
   "run cam.generate_toolpath first". No targets → `INVALID_PARAMS` ("nothing to post").
3. `program_name` default: the setup name (operations: the first operation's setup name); the
   defaulted value is validated as `FileName` too (failure → `INVALID_PARAMS` naming
   `program_name`). Then `os.makedirs(output_folder, exist_ok=True)`.
4. Post selection:
   - **File route**: `post` ends with `.cps` or contains a path separator → the file must exist
     (`NOT_FOUND` otherwise). Also used when `post` is a bare name and `cam.genericPostFolder`
     exists and contains `<post>.cps`.
     `not hasattr(adsk.cam, "PostProcessInput")` → `UNSUPPORTED`.
     `inp = adsk.cam.PostProcessInput.create(program_name, path, output_folder, units)` with
     `units` = `adsk.cam.PostOutputUnitOptions.DocumentUnitsOutput` / `MillimetersOutput` /
     `InchesOutput`; `inp.programComment = program_comment`; `inp.isOpenInEditor = False`;
     `cam.postProcess(target, inp)` where `target` is the setup or an `ObjectCollection` of the
     operations.
   - **Library route** (otherwise): requires `hasattr(adsk.cam, "CAMManager")` and
     `hasattr(cam, "ncPrograms")`, else `UNSUPPORTED`. `output_units != "document"` →
     `INVALID_PARAMS` ("library posts use the post's own unit setting; pass a .cps path to force
     units"). `lib = adsk.cam.CAMManager.get().libraryManager.postLibrary`;
     `url = _lib_find(ctx, lib, post, "post")`, also matching an asset whose
     `lib.postConfigurationAtURL(u).description` equals `post` case-insensitively;
     `cfg = lib.postConfigurationAtURL(url)`.
     `ni = cam.ncPrograms.createInput()`; `ni.displayName = program_name`; on `ni.parameters`
     set `.value.value` of `nc_program_filename` = `program_name`, `nc_program_output_folder` =
     `output_folder`, `nc_program_comment` = `program_comment`, `nc_program_openInEditor` =
     `False`; `ni.operations = targets`; `nc = cam.ncPrograms.add(ni)`; `try:
     nc.postConfiguration = cfg; nc.postProcess(adsk.cam.NCProgramPostProcessOptions.create())
     finally: nc.deleteMe()`.
5. `files` = absolute paths of regular files in `output_folder` whose mtime is at or after the
   time recorded before step 4, sorted. Empty → `FUSION_ERROR` ("post processor wrote no file").
- Result: `{"post": <path or URL string>, "route": "file"|"library", "program_name": str,
  "output_folder": str, "files": [str...], "operations": [<cam ref>...]}`.
- Gotcha: the library route briefly adds an NC program to the document and deletes it; Fusion may
  mark the document modified.

### 4.5 create_setup
```python
StockMode = Literal["relative_box", "fixed_box", "relative_cylinder", "fixed_cylinder", "from_solid"]

@action("cam", access="write", timeout=60)
def create_setup(ctx: Ctx, type: Literal["milling", "turning", "additive"] = "milling",
                 bodies: list[Body] | None = None, occurrences: list[Occurrence] | None = None,
                 name: str | None = None, stock_mode: StockMode | None = None,
                 stock_offset_sides: Length | None = None, stock_offset_top: Length | None = None,
                 stock_offset_bottom: Length | None = None, machine: str | None = None,
                 parameters: dict[str, str] | None = None) -> dict:
```
1. No bodies and no occurrences → `INVALID_PARAMS`. `additive` and
   `not hasattr(adsk.cam.OperationTypes, "AdditiveOperation")` → `UNSUPPORTED`. `additive`
   without `machine` → `INVALID_PARAMS` ("additive setups need a machine").
2. `cam = _cam(ctx, create=True)`; `si = cam.setups.createInput(op_type)` with `op_type` =
   `OperationTypes.MillingOperation` / `TurningOperation` / `AdditiveOperation`;
   `si.models = bodies + occurrences`; `si.name = name` when given.
3. `machine`: `not hasattr(libraryManager, "machineLibrary")` → `UNSUPPORTED`;
   `ml = adsk.cam.CAMManager.get().libraryManager.machineLibrary`; for each `_locations()`
   entry, `ml.childMachines(ml.urlByLocation(loc))`; match `m.description` or `m.model` equal to
   `machine` case-insensitively; first location with a match wins; none → `NOT_FOUND` with up to
   20 descriptions as hints; `si.machine = m`.
4. `setup = cam.setups.add(si)`.
5. Typed stock, on `setup.parameters` via `_set_param`, in this order:
   | Field | Parameter | Expression |
   |---|---|---|
   | `stock_mode` | `job_stockMode` | `'default'`, `'fixedbox'`, `'relativecylinder'`, `'fixedcylinder'`, `'solid'` (same order as `StockMode`) |
   | any offset given | `job_stockOffsetMode` | `'simple'` |
   | `stock_offset_sides` / `_top` / `_bottom` | `job_stockOffsetSides` / `job_stockOffsetTop` / `job_stockOffsetBottom` | `value.expr` |
6. `parameters`: each `name → expression` via `_set_param`, applied last (overrides typed fields).
   WCS is set this way, e.g. `wcs_orientation_mode`, `wcs_origin_mode`, `wcs_origin_boxPoint`;
   names are discovered with `operation_info {"setup": ...}`.
- Any error after step 4 aborts the undo group (core § 5.7), so no setup remains.
- Result: `{"setup": <cam ref>, "type": str, "models": [<ref>...], "parameters_set": {name:
  readback expression}}` covering every parameter written in steps 5–6.
- Gotcha: stock offsets only take effect for the relative stock modes; Fusion keeps them
  otherwise.

### 4.6 create_operation
```python
class ToolSpec(BaseModel, extra="forbid"):
    library: str | None = None     # library name or URL; None = document tool library
    number: int | None = None
    description: str | None = None
    index: int | None = None

@action("cam", access="write", timeout=60)
def create_operation(ctx: Ctx, setup: CamSetup, strategy: str, tool: ToolSpec | None = None,
                     name: str | None = None, parameters: dict[str, str] | None = None) -> dict:
```
1. `cam = _cam(ctx, create=True)`; `s = setup` (resolved by core).
2. When `hasattr(s.operations, "compatibleStrategies")`: names = `[x.name for x in
   s.operations.compatibleStrategies]`; `strategy` not in names → `INVALID_PARAMS`, hints = up to
   40 names. Examples: `face`, `adaptive`, `pocket2d`, `contour2d`, `drill`, `parallel`,
   `scallop`, `turningFace`, `turningProfileRoughing`.
3. Tool (`_find_tool`): exactly one of `number`, `description`, `index` → else
   `INVALID_PARAMS`. Library: `library is None` → `cam.documentToolLibrary`
   (`UNSUPPORTED` if missing); else `tl = adsk.cam.CAMManager.get().libraryManager.toolLibraries`,
   `tl.toolLibraryAtURL(_lib_find(ctx, tl, library, "tool library"))`. Tools: `lib.item(i)` for
   `i < lib.count`. Match `tool_number` value == `number`, or `tool_description` value ==
   `description` (exact), or `index` (out of range → `INVALID_PARAMS`). None → `NOT_FOUND`, hints
   = up to 20 `"#<number> <description>"`; several → `AMBIGUOUS` with those strings as candidates.
4. `oi = s.operations.createInput(strategy)`; tool given and `not hasattr(oi, "tool")` →
   `UNSUPPORTED`; `oi.tool = tool_obj`; `oi.displayName = name` when given and the attribute
   exists; `op = s.operations.add(oi)`; `op.name = name` when given and `op.name != name`.
5. `parameters` on `op.parameters` via `_set_param`.
- Result: `{"operation": <cam ref>, "strategy": str, "tool": <_tool_out>, "parameters_set":
  {name: readback expression}, "has_toolpath": false}`.
- Gotcha: an operation without a tool is created but stays invalid; generation reports `error`.

### 4.7 generate_toolpath
```python
@action("cam", access="write", timeout=600, undo_group=False)
def generate_toolpath(ctx: Ctx, setup: CamSetup | None = None,
                      operations: list[CamOperation] | None = None,
                      skip_valid: bool = False, wait: bool = True) -> dict:
```
1. Both `setup` and `operations` → `INVALID_PARAMS`.
2. Targets: neither → `cam.allOperations`, `future = cam.generateAllToolpaths(skip_valid)`.
   Setup or operations → target ops (`setup.allOperations` or resolved list); with `skip_valid`,
   drop ops with `isToolpathValid`; none left → return the result with `completed: true` without
   generating; else `future = cam.generateToolpath(coll)` with an `ObjectCollection` of the ops.
3. `wait`: completion probe = `future.isGenerationCompleted` if present, else
   `future.isCompleted`; neither → behave as `wait=False`. Loop `while not probe() and
   time.monotonic() < deadline: adsk.doEvents(); time.sleep(0.1)` with `deadline` = start +
   `WAIT_SECONDS` (570, module constant so tests can shorten it). On expiry the loop stops and the
   result has `completed: false`; the call ends normally.
- Result: `{"completed": bool, "operations": [{"operation": <cam ref>, "has_toolpath": bool,
  "toolpath_valid": bool, "state": str | null, "warning": bool | null, "error": bool | null}]}`
  read after the loop (or immediately for `wait=False`); `completed` is `false` for `wait=False`
  and on deadline expiry.
- Why `undo_group=False`: toolpath generation is not a design edit, and a transaction must not
  stay open across a long `doEvents` loop.

## 5. Testability
### 5.1 Unit (fake adsk)
`tests/test_cam.py`, using `tests/fakes/adsk` with `tests/fakes/adsk/cam.py` providing: `CAM`
(`cast`, `setups`, `allOperations`, `generateToolpath`, `generateAllToolpaths`, `postProcess`,
`ncPrograms`, `documentToolLibrary`, `genericPostFolder`), `Setups`/`Setup`/`Operations`/
`Operation` with `parameters` (`CAMParameters.itemByName`, `CAMParameter.expression`), a
`GenerateToolpathFuture` completing after N polls, `OperationTypes`, `OperationStates`,
`LibraryLocations`, `CAMManager` with post/tool/machine libraries over an in-memory URL tree,
`PostProcessInput`, `PostOutputUnitOptions`, `NCProgramPostProcessOptions`; plus documents whose
`products.itemByProductType` raises for missing products and a `UserInterface` with workspaces
recording `activate` calls. Tests (names are `-k` selectors):
- `cam_product`: no document, no design product, missing `adsk.cam`, read without product (no
  activation, empty page), write activating and restoring the workspace, restore after failure.
- `refs`: `_setup_ref`/`_op_ref` shapes (including an operation in a folder).
- `list_setups`, `list_operations`, `operation_info` (operation, setup, names, unknown name,
  both/neither error).
- `post_process`: file route args and units, generic post folder, library route NC program
  parameters and deletion (also on exception), units rejection, missing toolpath, relative
  folder, no output file, `program_name` `../x` → `INVALID_PARAMS` with the folder unchanged,
  action access `write`.
- `create_setup`: each type, models, stock table expressions, parameters override, unknown
  parameter error, additive without machine, machine lookup, `UNSUPPORTED` additive.
- `create_operation`: strategy check, tool by number/description/index, document library,
  library `NOT_FOUND` hints, `AMBIGUOUS`, parameters.
- `generate_toolpath`: all/setup/operations calls, skip_valid filtering, wait loop polls until
  complete, a future that never completes with a short `WAIT_SECONDS` returns `completed: false`,
  `wait=False`, missing probe, both-given error, action metadata (timeout 600,
  `undo_group=False`).

### 5.2 Live (`LIVE_SCENARIOS` in `cam.py`, run by `scripts/live_check.py --only cam[.action]`)
Each row is one `LIVE_SCENARIOS` entry `(name, "cam.<action>", async fn(session))` (core § 10),
tagged with the action in the first column; calls use `live_check.call(session, tool, action,
params, units=None)`. Each scenario starts from the harness's scratch design with a
50 × 40 × 20 mm fixture box (DEP-CAM-002): `sketch.create {"plane": "XY"}` →
`sketch.rectangle {"sketch": S, "mode": "two_point", "p1": [0, 0], "p2": ["50 mm", "40 mm"]}` →
`model.extrude {"profile": {"sketch": S, "index": 0}, "distance": "20 mm"}`; the body is
`bodies[0]` of the extrude result. Scenarios that need a setup or operation create them first with
the actions below.

| Scenario (action) | Steps and checks |
|---|---|
| `list_setups` | Before any write: `total` 0 (proves the read path without a CAM product). |
| `create_setup` | Milling setup on the box with `stock_mode` `relative_box`, `stock_offset_sides` `"1 mm"`; `operation_info {"setup": S, "names": ["job_stockMode", "job_stockOffsetSides"]}` reads back `'default'` and 1 mm; read `wcs_origin_mode` the same way and pass that expression back in `parameters` of a second setup (round trip); `parameters {"wcs_nope": "1"}` → `INVALID_PARAMS` with `wcs_` hints and setup count unchanged; turning setup → `type` `turning`; additive without machine → `INVALID_PARAMS`; `machine "__none__"` → `NOT_FOUND` (or `UNSUPPORTED`), and when hints exist, additive with the first hint → `type` `additive` (or `UNSUPPORTED` on builds without additive). |
| `create_operation` | `library "__none__"` → `NOT_FOUND` with hints; choose the first hint containing `Milling` (else the first); for `index` 0..9 create `face` operations until one reports `tool.type` `flat end mill`; strategy `"nope"` → `INVALID_PARAMS` with `face` in hints. |
| `list_operations` | The setup lists the created operations with `has_toolpath: false`. |
| `operation_info` | Face op: `toolpath.has_toolpath` false, `names ["tool_number"]` returns one item; returned operation ref passed back resolves. |
| `generate_toolpath` | The flat-end-mill face op: `completed: true`, `toolpath_valid: true`; `skip_valid: true` again returns without generating. |
| `post_process` | File route with `tests/fixtures/posts/defusion_test.cps`, `operations` = [the generated op], into a temp folder → `files` non-empty and the first file contains `(DEFUSION)`; library route `fanuc` with the same op → `files` non-empty; `setup` = the first setup (it holds non-generated ops) → `INVALID_PARAMS` naming them; a relative `output_folder` → `INVALID_PARAMS`. |

`tests/fixtures/posts/defusion_test.cps` is a minimal post written for this project (description,
`extension = "nc"`, `capabilities = CAPABILITY_MILLING`, `onOpen` writing `(DEFUSION)`,
`onSection`, `onRapid`/`onLinear` writing `G0`/`G1` lines, `onClose` writing `%`).
