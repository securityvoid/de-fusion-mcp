# design.md — docs/specs/core/
> De-Fusion MCP — add-in runtime, action framework, profiles, auth and installer

## 1. Overview
De-Fusion is a Fusion add-in that hosts an MCP server inside Fusion's own Python. Every tool is a
bundle of *actions*. An action is one typed Python function registered with `@action`; the
framework supplies everything else (schema, validation, entity resolution, units, main-thread
execution, undo grouping, change summary, errors, size limits, profile filtering). Tool sets in
`docs/specs/<tool>/` define actions only and depend on this set for all shared behaviour.

The code is written from the Fusion API documentation and these specs. Source code of other
Fusion MCP servers is not copied.

## 2. Repository layout
```text
De-Fusion/                      # the add-in folder; installer links it into Fusion's AddIns dir
  De-Fusion.py                  # add-in entry: run(context) / stop(context)
  De-Fusion.manifest            # runOnStartup true, supportedOS windows|mac
  lib/                          # third-party packages, written by install.py (gitignored)
  defusion/
    __init__.py
    config.py                   # config file load + built-in defaults
    profiles.py                 # selector grammar and resolution
    auth.py                     # optional bearer-token ASGI middleware
    server.py                   # MCPServer construction, HTTP app, uvicorn thread, start/stop
    dispatch.py                 # main-thread dispatcher and job registry
    actions.py                  # @action, registry, tool building, help, call pipeline
    types.py                    # parameter types: Length, Angle, entity refs, enums
    units.py                    # input evaluation and output conversion
    context.py                  # Ctx passed to every action
    errors.py                   # ActionError and error codes
    results.py                  # envelope, size budget, paging, change summary, Image
    guard.py                    # read-only guard and folder limit (§ 5.13)
    log.py                      # file logger
    tools/
      __init__.py               # imports every tool module so @action registers
      document.py sketch.py model.py assembly.py history.py
      inspect.py view.py cam.py script.py server.py
install.py                      # run outside Fusion with any Python >= 3.10
requirements.in                 # direct pins: mcp==2.3.0, uvicorn
requirements.lock               # full pinned set installed into De-Fusion/lib
tests/
  fakes/adsk/                   # minimal fake adsk.core / adsk.fusion / adsk.cam for unit tests
  test_*.py
scripts/live_check.py           # drives the running add-in over /mcp (live tests)
README.md
```
Test and repo files: `tests/test_*.py`, `tests/fakes/adsk/*.py`, `tests/conftest.py`, `.gitignore`,
`pyproject.toml` (pytest config).

The add-in folder, `.py` and `.manifest` share the name `De-Fusion`, which is the name shown in
Fusion's Scripts and Add-Ins dialog. Fusion loads the `.py` by path, so the hyphen is not imported
as a module name; the package code lives in `defusion/`.

## 3. Runtime
### 3.1 Start
`De-Fusion.py` `run()`:
1. Inserts `<add-in>/lib` and `<add-in>` at the front of `sys.path`.
2. Configures the file logger (§ 3.6) at level `INFO`.
3. Imports `defusion.server`; on `ImportError` logs `Dependencies missing in <lib>; run install.py`
   and returns. It never runs pip (inside Fusion, `sys.executable` is Fusion itself).
4. Calls `server.start()`, which loads the config (§ 6), calls `log.setup` again with the loaded
   `log_level` (replacing the file handler, never adding one), registers the dispatcher custom event
   (§ 4), imports `defusion.tools` and starts uvicorn on a daemon thread.

`start()` does not block the main thread. The uvicorn thread records `starting`, `running` or
`failed` in a module-level status read by `server.status` through `server.state()` (§ 12); a bind failure (port in use) logs
`Port <port> in use; set port in <CONFIG_PATH> and restart Fusion` and sets `failed`. No modal dialogs are shown by the runtime.

### 3.2 Transport
- `MCPServer(name="De-Fusion", version=<package version>, instructions=INSTRUCTIONS)` from
  `mcp==2.3.0`. No SSE routes, no stdio, no resources, no prompts.
- App: `streamable_http_app(streamable_http_path="/mcp", stateless_http=True,
  json_response=True, host="127.0.0.1", transport_security=...)` allowing Host and Origin of
  `127.0.0.1:<port>` and `localhost:<port>` only.
- The SDK's default maximum request body size applies.
- Wrapped by the auth middleware (§ 7) and served by `uvicorn.Server` bound to `127.0.0.1:<port>`
  (default 8642).
- `INSTRUCTIONS`: one paragraph telling the client that each tool takes `action` + `params`, that
  `action="help"` returns the schema for an action, and that lengths accept unit expressions,
  followed by these three sentences, verbatim: `Document file actions take a list of 1 to 50
  entries and return one result per entry with ok and failed counts. A write to a saved read-only
  document makes it editable first unless params.preserve_read_only is true; document.save and
  document.save_as make the saved file read-only again. A folder=<Project/Path> query on the URL
  limits cloud files, folders and edits of saved documents to that folder and its subfolders.`

### 3.3 Stop
`stop()`: sets `should_exit`, joins the uvicorn thread for up to 5 s, then waits until the port
accepts no connection (up to 5 s more), fails every queued dispatcher item with `NOT_EXECUTED`,
unregisters the custom event and clears the job registry. A second `start()` after `stop()`
succeeds in the same Fusion session.

### 3.4 Threads
Fusion API objects are touched only on Fusion's main thread, inside dispatcher items. HTTP and
uvicorn threads never call `adsk.*` and never call `app.log`.

### 3.5 Version
`defusion/__init__.py` holds `__version__`; the manifest version matches it.

### 3.6 Logging
Python `logging` to `<config dir>/defusion.log`, rotating at 1 MiB × 3 files, level from config
`log_level` (default `INFO`). Every action call logs tool, action, duration and outcome code.

## 4. Dispatcher (`dispatch.py`)
- One custom event id `defusion.dispatch`. `run(fn, timeout)` from any non-main thread creates an
  item `{id, fn, state: queued, done: threading.Event, result, exc}`, appends it to a locked
  queue and calls `app.fireCustomEvent`. A `threading.Timer` re-fires the event every 100 ms
  while the queue is non-empty (fire from a non-main thread can be dropped). With 64 items already
  queued, `run` returns `NOT_EXECUTED` ("queue full; retry") without queuing.
- The main-thread handler drains the queue: per item sets `running`, calls `fn()`, stores
  result or exception, sets `done`.
- The handler is not re-entrant: while an item is running (including while an action calls
  `adsk.doEvents()`, which can deliver the custom event again) a module flag `_running` is set and
  the handler returns immediately without draining.
- The handler's `queued`→`running` change and the waiter's timeout check-and-remove both happen
  under the queue lock, so exactly one wins.
- Timeout outcomes for the waiting caller:
  - item still `queued`: removed from the queue; the call returns error `NOT_EXECUTED`
    ("did not run; safe to retry"). While a registry job is `running`, the message is instead
    "Fusion is busy with job <id> (<tool>.<action>); wait or poll server.job_status" with
    `error.job_id`.
  - item `running`: moved to the job registry; the call returns result
    `{"status": "running", "job_id": "<id>"}` (`id` = `uuid4().hex[:8]`) with the hint to poll `server.job_status`.
- Job registry: last 100 jobs, oldest evicted; each `{action: "<tool>.<action>", state:
  running|done|failed, result|error, started, finished}` (`started`/`finished` epoch seconds,
  `finished` null while running). A finishing job's result goes through the § 5.5 size check and
  is stored as `failed` with the `RESULT_TOO_LARGE` error and its hint.
  `server.job_status` reads it without the main thread (§ 12).
- An action that waits with `adsk.doEvents()` stops at its own `timeout` and returns normally.
- When `adsk` cannot be imported (unit tests), `run` executes `fn` inline.
- Default timeout 30 s; an action may declare its own (§ 5.1). Actions declared
  `main_thread=False` bypass the dispatcher (§ 5.3).

## 5. Action framework (`actions.py`)
### 5.1 Declaring an action
```python
@action(tool, name=None, access=..., timeout=30, undo_group=True, main_thread=True,
        edits_document=None)                                                    # signature

@action("model", access="write", timeout=60)
def extrude(ctx: Ctx, profile: Profile, distance: Length,
            operation: Operation = "new_body", name: str | None = None) -> dict:
    """Extrude a sketch profile.

    Longer help text: shown only by action=help."""
    feat = ...
    return {"feature": ctx.ref(feat)}

@action("sketch", name="list", access="read")      # action name differs from the Python name
def list_sketches(ctx: Ctx, offset: int = 0, limit: int = 100) -> dict: ...
```
- `tool`: one of `document sketch model assembly history inspect view cam script server`.
- `access`: `read` (no design or document change; camera moves are `write`; file export is
  `read` only because it creates new files and never replaces one; an action that runs
  client-supplied code is never `read`), `write`, or `destructive` (deletes or discards user data).
- `name` defaults to the function name (use `name=` when the action name is a Python builtin or
  keyword such as `list`, `open`, `import`). `timeout` defaults to 30 s.
- `undo_group=False` opts out of undo grouping (§ 5.7).
- `main_thread=False`: the function runs on the HTTP worker thread instead of the dispatcher, with
  a `Ctx` whose `app`, `ui`, `document` and `design` are `None` (no Fusion objects, no Ref types
  allowed in the signature, no undo group, no change summary). Used by `server.status` and
  `server.job_status` so they answer while the main-thread queue is stuck. Only `read` actions may
  set it (else raises at import time).
- `edits_document`: `"active"`, `"action"`, `"none"`, or `None` (default) meaning `"active"` for
  `write`/`destructive` and `"none"` for `read`. `"active"`: the pipeline guards the active
  document before the call (§ 5.3 step 6, § 5.13). `"action"`: the action guards each document it
  edits itself with `guard.ensure_editable(ctx, doc)` (used when the edited document is not simply the
  active one, or per bulk entry). `"none"`: the action edits no open document (document lifecycle,
  camera, local files). `"active"` or `"action"` on a `read` or `main_thread=False` action raises
  at decoration time. For `"active"` and `"action"` the framework adds
  `preserve_read_only: bool = false` to the action's model (like `confirm`); the pipeline removes
  it from the params before calling the function and sets `ctx.preserve_read_only`. The actions
  declaring `"none"` are listed in § 5.13.
- The first docstring line is the summary; the full docstring is the help text.
- Parameters after `ctx` define the input. Allowed annotations: `str`, `int`, `float`, `bool`,
  `list[...]`, `dict[str, Any]`, `dict[str, T]` with `T` one of `str int float bool`, `Literal[...]`, unions of these (`X | None`, `int | str`,
  `int | TimelineItem`, ...), `Annotated[..., Field(...)]`, pydantic models, and the types of § 5.4.
  Names are snake_case. No `**kwargs`.
- A duplicate `(tool, name)` raises at import time. Importing `defusion.tools` and finding a tool
  module that registered no action also raises (an action counts for a module when its function's
  module is that module or a `<module>_*` module it imports, e.g. `model.py` indexing `model_*.py`).

### 5.2 Tool surface
Each tool is registered once with the SDK with this input schema:
```json
{"type": "object",
 "properties": {
   "action": {"type": "string", "enum": ["help", "<allowed actions>..."]},
   "params": {"type": "object", "default": {}},
   "units":  {"type": "object", "properties": {"length": {"type": "string"}, "angle": {"type": "string"}}}
 },
 "required": ["action"], "additionalProperties": false}
```
Description: `<tool summary> Actions: <comma-separated allowed names>. Call
action="help" with params {"action": "<name>"} for its parameters.` The tool summary is the tool
module docstring's first line, a full sentence ending in a period. Per-action schemas are never
in `tools/list`.

### 5.3 Call pipeline
For `tools/call` of tool T with `{action, params, units}`:
1. Profile check (§ 6): action not in the request's allowed set → `NOT_ALLOWED`.
2. `action == "help"` → § 5.6.
3. Unknown action → `UNKNOWN_ACTION` with the allowed list as hints.
4. Validate top-level `units` with `OutputUnits` (§ 8; `help` ignores it) and `params` against the action's pydantic model (built once at registration,
   `extra="forbid"`). Failure → `INVALID_PARAMS` with pydantic's messages as hints and the
   action's JSON schema under `error.schema`. Invalid `units` → `INVALID_PARAMS`.
5. `destructive` and `params.confirm` is not `true` → `CONFIRM_REQUIRED`. The framework adds
   `confirm: bool = false` to every destructive action's model.
6. Dispatch to the main thread (§ 4) a closure that: builds `Ctx`; resolves typed parameters
   (§ 5.4); for `edits_document == "active"` with an open document, calls
   `guard.ensure_editable(ctx, ctx.document)` (§ 5.13; its error ends the call before any undo
   group or snapshot); for `write`/`destructive`, snapshots the change summary (§ 5.8) and opens the undo
   group (§ 5.7); calls the function; closes the undo group; computes the change summary.
   For `main_thread=False` actions the function is instead called directly on the request thread
   with a Fusion-free `Ctx` (§ 5.1); they are not time-limited and run to completion.
7. Shape the result (§ 5.5).

### 5.4 Parameter types (`types.py`)
Resolution happens on the main thread before the function runs. Types are pydantic-compatible
for validation and schema; resolution produces the Fusion object.

| Type | Accepts | Resolves to |
|---|---|---|
| `Length` | number (default length unit) or string expression (`"1.5 in"`, `"d1*2"`) | `Value` with `.expr` (string fed to `ValueInput.createByString`), `.cm` (float, evaluated), `.value_input()` |
| `Angle` | number (default angle unit) or string expression | `Value` with `.expr`, `.rad`, `.value_input()` |
| `FileName` | string, 1-64 characters, no `/`, `\`, `..` or control characters, not starting with `.` or `-`; otherwise `INVALID_PARAMS` before any file work. The one rule for every client-supplied bare file name | `str` |
| `Expr` | string or number | `Value` without unit (counts, ratios) |
| `Point3` | `[x, y, z]` of `Length` items, or `{"x":..,"y":..,"z":..}` | `adsk.core.Point3D` in cm |
| `Point2` | `[x, y]` of `Length` items (sketch space), or `[x, y, z]` (model space) | sketch-space `adsk.core.Point3D` in cm with z = 0; the 3-item form is converted with `sketch.modelToSketchSpace` of the action's `sketch` parameter and projected onto the sketch plane (z set to 0); a 3-item value in an action without a `sketch` parameter → `INVALID_PARAMS` |
| `Vector3` | `[x, y, z]` numbers | `adsk.core.Vector3D` |
| `Body` (`BRepBody`), `MeshBody`, `Face`, `Edge`, `Vertex`, `Sketch`, `SketchCurve`, `SketchPoint`, `Feature`, `Component`, `Occurrence`, `ConstructionPlane`, `ConstructionAxis`, `Joint`, `JointOrigin`, `RigidGroup`, `Parameter` | Ref (below) | the Fusion object |
| `ContactSet` | Ref (below) | the `ContactSet`; when `design` has no `contactSets` attribute → `UNSUPPORTED` |
| `TimelineItem` | Ref (below), or `{"index": n}` | token/name: the resolved entity's `timelineObject` (`INVALID_PARAMS` if it has none); `{"index": n}`: `design.timeline.item(n)` (out of range → `NOT_FOUND`) |
| `Entity` | Ref (below) | any entity, including `MeshBody`; no class check (the action checks). Name search covers bodies, mesh bodies, features, sketches, construction planes/axes/points, components, occurrences, joints, joint origins and parameters |
| `CamSetup`, `CamOperation` | bare name string, `{"name": s}`, `{"name": s, "setup": <setup name>}` (operation only), or `{"token": t}`; the output refs `{"type": "Setup", "name"}` and `{"type": "Operation", "name", "setup"}` are accepted unchanged | the `adsk.cam.Setup` / `adsk.cam.Operation` of the active document's CAM product (`document.products.itemByProductType("CAMProductType")`). Name: exact match over `cam.setups` / `cam.allOperations` (scoped to the named setup's `allOperations` when `setup` is given); token: `entityToken` equality over the same items. No CAM product or no match → `NOT_FOUND`; several → `AMBIGUOUS`. Named with the `Cam` prefix because `Operation` is the feature-operation enum |
| `Plane` | `"XY"`, `"XZ"`, `"YZ"` (origin planes), or a Ref to a construction plane or planar face | planar entity |
| `Axis` | `"X"`, `"Y"`, `"Z"` (origin axes), or a Ref to a construction axis, linear edge or sketch line | linear entity |
| `Profile` | `{"sketch": Ref, "index": int}` or `{"sketch": Ref, "index": [int,...]}`; `index` may be omitted only when the sketch has exactly one profile | `Profile` or `ObjectCollection` |
| `Ref[...]` lists | `list` of the above | Python list |

Origin names of `Plane`/`Axis` resolve against the action's `component` parameter when the action
declares one and it is set, else `design.activeComponent`.

**Composite annotations.** Unions beyond `X | None` are allowed (e.g. `int | TimelineItem`,
`int | str`): pydantic validates the input, and resolution applies only when the validated value
matched a Ref-typed branch (an `int` stays an `int`). Ref-typed fields inside pydantic-model
parameters, and lists of them, are resolved recursively in place before the function runs.

**Ref** forms (all entity types):
- `{"token": "<entityToken>"}` — `design.findEntityByToken`; first match of the expected type.
- `{"name": "<name>", "component": <Ref|name>?}` — exact name match within the component (default:
  search the whole design).
- `{"body": <Ref>, "index": n}` for `Face`, `Edge`, `Vertex` (index into `body.faces/edges/vertices`
  as listed by `inspect` actions).
- bare string — treated as `{"name": s}`; if no name matches, retried as `{"token": s}`.

Errors: none found → `NOT_FOUND`; more than one name match → `AMBIGUOUS` with each candidate's
`ctx.ref()` in `error.candidates`; wrong object type → `INVALID_PARAMS`.

All design-entity Refs resolve against `ctx.design` (§ 5.9), so they work while the Manufacture
workspace is active.

`Ctx.ref(obj)` serialises any entity as `{"type": <class name>, "name": <name or null>,
"token": <entityToken>, "component": <component name or null>}`. All action outputs refer to
entities this way so outputs can be passed back as inputs.

Enum literals (e.g. `Operation = Literal["new_body","join","cut","intersect","new_component"]`) are
defined once in `types.py` with a mapping to the `adsk` enum.

### 5.5 Results (`results.py`)
Success: `CallToolResult(isError=False, content=[TextContent(json)], structuredContent=obj)` where
```json
{"ok": true, "tool": "model", "action": "extrude", "result": {...},
 "units": {"length": "mm", "angle": "deg"}, "changes": {...}}
```
`changes` appears only for `write`/`destructive`. `undo_grouped` is a top-level key beside `changes`,
present only when `false` (§ 5.7). `made_editable` is a top-level list of the names of documents
the call made editable (§ 5.13), present only when non-empty, on success and error envelopes. Error: `isError=True` with
```json
{"ok": false, "tool": "...", "action": "...",
 "error": {"code": "...", "message": "...", "hints": ["..."], "schema": {...}, "candidates": [...]}}
```
(`schema`, `candidates` only when relevant). An action may return `Image(png: bytes,
meta: dict)` or a list of them; the result content is then one `ImageContent` per image in order,
then `TextContent(json of meta envelope)`.

**Size budget.** Serialised `result` over 65,536 UTF-8 bytes of JSON (exactly 65,536 is accepted) →
error `RESULT_TOO_LARGE` with a hint to use a smaller `limit` or a larger `offset` (bulk results: § 5.12). The total PNG
bytes of all images in one result over 4 MiB → `RESULT_TOO_LARGE` naming the size and saying to
reduce the image size (`width`/`height`, or `tile` for `view_sheet`). List-returning actions declare `offset: int = 0, limit: int = 100` and return
`ctx.page(seq, offset, limit, build=None)` = `{"items": [...], "total": n, "offset": o, "next_offset": o+len or null}`;
it requires `offset >= 0` and `1 <= limit <= 500` (else `INVALID_PARAMS`), slices `seq` first and
only then applies `build` (default identity) to each sliced item.

### 5.6 help
`help` is implicit on every tool and always allowed when the tool is visible.
- `params {}` → `{"tool": T, "actions": [{"name", "summary", "access"} ...]}` for allowed actions.
- `params {"action": "x"}` → `{"name", "summary", "help", "access", "timeout", "input_schema"}`.
  `x` not allowed → `NOT_ALLOWED`.

### 5.7 Undo grouping
For `write`/`destructive` calls when a document is open, the pipeline runs
`app.executeTextCommand('PTransaction.Start "De-Fusion <tool>.<action>"')` before and
`PTransaction.Commit` after; on an exception it runs `PTransaction.Abort`, rolling back partial
work, then returns the error. If `PTransaction.Start` raises, the call proceeds without grouping
and the envelope carries `"undo_grouped": false`. A raising `Commit` returns the result with
`"undo_grouped": false` and a logged warning; a raising `Abort` is logged and the original error is
returned. An action may opt out with `undo_group=False`
(document open/close/save, server actions, actions that are themselves undo/redo).

### 5.8 Change summary
Before and after a `write`/`destructive` call with an active design:
`{"bodies": <count over all components>, "timeline": <timeline.count or null in direct mode>,
"bbox": <union of visible body bounding boxes in output length units, or null when more than 500 bodies are visible>}`.
`changes = {"bodies": [before, after], "timeline": [before, after], "bbox": [before, after]}`
with keys whose values did not change omitted. Bounding-box failure yields `null`, never an error.

### 5.9 Ctx (`context.py`)
Fields and helpers available to every action (all main-thread, except that a `main_thread=False`
action gets `None` for `app`, `ui`, `document`, `design`):
- `app`, `ui`, `document` (active or `None`), `design` =
  `document.products.itemByProductType("DesignProductType")` cast to `Design` (not
  `app.activeProduct`, so it is set while the Manufacture workspace is active), or `None`.
- `allowed`: `frozenset[str]` of `"tool.action"` names allowed for this request (§ 6.4).
- `profile_items`: `list[str]`, the request's expanded profile items (query items or
  `default_profile`, profiles spliced).
- `units`: `{"length": <name>, "angle": <name>}`, the output units of this call (§ 8).
- `require_design()` → `Design` or raises `NO_DESIGN`. `require_parametric()` raises
  `WRONG_DESIGN_TYPE` in direct mode.
- `component(ref=None)` → given component, else `design.activeComponent`.
- `ref(obj)`, `refs(objs)` (§ 5.4).
- `length_out(cm: float) -> float`, `angle_out(rad: float) -> float`, `area_out(cm2)`,
  `volume_out(cm3)`, `mass_out(kg)` in the call's output units (§ 8).
- `page(seq, offset, limit, build=None)` (§ 5.5).
- `text_command(cmd: str) -> str` → `app.executeTextCommand`; raises `TEXT_COMMAND_FAILED` with the
  command and Fusion's message.
- `attr_set(entity, key, value)` stores `json.dumps(value)` in attribute group `defusion`;
  `attr_get(entity, key)` returns the `json.loads` value, or `None` when the attribute is absent.
- `fail(code, message, hints=())` raises `ActionError`.
- `folder_limit`: the request's normalised folder limit (`"Project/Sub"`) or `None` (§ 6.4).
- `preserve_read_only`: `bool`, from the call's params (§ 5.1); `False` when the action has none.
- `made_editable`: `list[str]`, filled by `guard.ensure_editable`, copied into the envelope (§ 5.5).
- `bulk(entries, fn)` (§ 5.12); no `ensure_editable`/`check_folder` methods: actions call the § 5.13
  `guard.ensure_editable(ctx, doc)` and `guard.check_folder(ctx, path)` functions.

### 5.10 Errors (`errors.py`)
`ActionError(code, message, hints=(), **extra)`. Each `extra` keyword is copied as a key of the
envelope's `error` object (e.g. `candidates=[...]`, `schema={...}`, `job_id="..."`). Any other
exception from an action becomes `FUSION_ERROR` with the exception text and its type name. Codes:

| Code | Meaning |
|---|---|
| `INVALID_PARAMS` | params fail validation or a value is out of range |
| `UNKNOWN_ACTION` | no such action on the tool |
| `NOT_ALLOWED` | action excluded by the request's profile, or target outside the request's folder limit (§ 5.13) |
| `READ_ONLY` | the target document is read-only and `preserve_read_only` is true (§ 5.13) |
| `READ_ONLY_NOT_APPLIED` | the requested read-only state was not observed on the cloud file within 10 tries 1 s apart (§ 5.13) |
| `CONFIRM_REQUIRED` | destructive action without `confirm: true` |
| `NOT_FOUND` / `AMBIGUOUS` | Ref resolution |
| `NO_DOCUMENT` / `NO_DESIGN` | nothing active |
| `WRONG_DESIGN_TYPE` | needs parametric (or direct) mode |
| `UNSUPPORTED` | this Fusion build lacks the API (feature-detected with `hasattr`) |
| `TEXT_COMMAND_FAILED` | `executeTextCommand` raised |
| `NOT_EXECUTED` | timed out while queued; safe to retry |
| `RESULT_TOO_LARGE` | § 5.5 |
| `FUSION_ERROR` | Fusion API raised |
| `INVALID_PROFILE` | bad profile selector; JSON-RPC-level error, not an envelope (§ 6.4) |

### 5.11 Inline calls (`actions.call_inline`)
`call_inline(ctx, tool, action, params) -> result` runs another registered action inside the
current main-thread call: § 5.3 step 4 validation (`INVALID_PARAMS`), step 5 confirm check,
§ 5.4 resolution, then the function with the same `ctx`. It opens no undo group, takes no change
snapshot and does not dispatch; the caller's group and summary cover it. `"tool.action"` not in
`ctx.allowed` → `NOT_ALLOWED`; unknown → `UNKNOWN_ACTION`. Returns the function's raw result
(not an envelope); errors propagate as `ActionError`. Used by `history.apply_recipe_step`.
It runs no document guard (§ 5.13): the outer call's guard covers it; an inner
`preserve_read_only` is validated and dropped.

### 5.12 Bulk entries
An action that acts on several files or documents declares its list parameter with
`types.bulk(t)` = `Annotated[list[t], Field(min_length=1, max_length=50)]`; an empty list or more
than 50 entries fails step 4 with `INVALID_PARAMS` before anything runs. Entries are strings or
pydantic models (`extra="forbid"`).

`ctx.bulk(entries, fn) -> dict` calls `fn(entry)` for each entry in order, inside the call's one
dispatcher item. Per entry it records `{"input": <entry>, "ok": true, "result": <fn return>}`, or
on an exception `{"input": <entry>, "ok": false, "error": <error object>}` where the error object
is the envelope's `error` shape (§ 5.5): an `ActionError` gives its code, message, hints and
extras; any other exception gives `FUSION_ERROR` as § 5.10. `input` is the entry as validated
(`model_dump()` for model entries, `null` for a defaulted entry). A failed entry never stops later
entries. It returns `{"results": [...], "ok": <entries ok>, "failed": <entries failed>}`.

The envelope's `ok` is `true` whenever the call ran, even when every entry failed. Call-level
errors (validation and bounds, `NOT_ALLOWED`, `CONFIRM_REQUIRED`, and errors raised by the action
before it calls `ctx.bulk`, e.g. resolving a shared `folder`) give an error envelope as usual.
`confirm` is given once for the whole call. The action's `timeout` covers the whole call; a call
that outlives it becomes a job (§ 4) whose result is the bulk result. Undo grouping and the change
summary stay one per call. A failed entry's partial work is not rolled back (the § 5.7 `Abort` runs only
for call-level errors); a bulk action that needs rollback undoes it inside its entry function. The
§ 5.5 size budget applies to the whole result; over it, `RESULT_TOO_LARGE` has the message "all
entries ran" and the hints "check state with document.list_open or datafile_get" and "send fewer
entries per call"; per-entry output (e.g. export `files`) is bounded.

### 5.13 Document guard (`guard.py`)
Constants `VERIFY_TRIES = 10` and `VERIFY_DELAY = 1.0` seconds (unit tests set the delay to 0).

- `saved_file(doc) -> DataFile | None`: `doc.dataFile`; `None` (never saved), a raising read, or
  an `id` not starting with `urn:` (upload pending) → `None`.
- `never_saved(doc) -> bool`: `doc.dataFile is None`; a raising read → `False` (upload pending).
- `folder_path(folder) -> str | None`: walk `folder.parentFolder` until `isRoot`, prepend
  `parentProject.name`, join with `/` (a project root folder's path is the project name); any
  exception → `None`. The document tool's path helpers use it (document design § 2.2).
- `norm_folder(value) -> str | None`: the `/`-joined path of a folder-path list/string value, or `None`
  when empty; shared by callers that normalise a folder argument.
- `norm(path) -> list[str]`: `[s for s in path.strip().strip("/").split("/") if s]`.
- `check_folder(ctx, path)`: `ctx.folder_limit is None` → return. `path is None` →
  `NOT_ALLOWED` ("cannot tell the folder of the target; folder limit '<L>' is set", hint "address
  the file by a path inside the limit, or retry after its upload finishes"). Otherwise
  `norm(path)[:len(norm(L))] == norm(L)` → return, else `NOT_ALLOWED` ("'<path>' is outside the
  folder limit '<L>'", hint "use a file or folder under <L>; the limit comes from ?folder= or
  config folder"). Segments compare exactly and case-sensitively, so `P/AB` is not inside `P/A`;
  subfolders at any depth are inside.
- `wait_read_only(lookup, want) -> bool`: up to `VERIFY_TRIES` times: `df = lookup()` (an
  exception counts as `None`); `df is not None and df.isReadOnly == want` → `True`. Between tries
  (not after the last) it waits `VERIFY_DELAY` seconds, calling `adsk.doEvents()` every 50 ms so
  Fusion applies the cloud update. After the last try → `False`.
- `set_read_only(ctx, doc, want, lookup) -> bool`: `prev = ctx.app.activeDocument`; if `doc` is not
  `prev`, `doc.activate()` (`False` → `FUSION_ERROR` "could not activate <name>");
  `ctx.text_command("Commands.Start ActiveDocumentDeactivateCommand")` when `want` (Fusion's Make
  Read-Only) else `"Commands.Start ActiveDocumentActivateCommand"` (Make Editable); in `finally`
  re-activate `prev` when it differs (`prev.activate()` returning `False` → `FUSION_ERROR` "could not
  re-activate <prev name>", unless an error is already propagating); then return `wait_read_only(lookup, want)`. `lookup` returns
  a fresh `DataFile` (normally `lambda: ctx.app.data.findFileById(<urn>)`); the open document's own
  `dataFile.isReadOnly` reads stale right after a change, so it is never used for verification.
- `ensure_editable(ctx, doc)`:
  1. `doc is None` or `never_saved(doc)` → return (never-saved documents are
     always editable and never folder-limited). `df = saved_file(doc)`; `None` (upload pending) →
     `check_folder(ctx, None)` (`NOT_ALLOWED` under a folder limit), then return.
  2. `fresh = fresh_file(ctx, df)` (`ctx.app.data.findFileById(df.id) or df`; shared helper); a raising `findFileById` (file not yet
     listed right after a save) also falls back to `df`.
  3. `check_folder(ctx, folder_path(fresh.parentFolder))`.
  4. `fresh.isReadOnly` false → return.
  5. `ctx.preserve_read_only` → `READ_ONLY` ("<name> is read-only and preserve_read_only is
     true", hint "omit preserve_read_only to make it editable first, or call
     document.set_read_only with read_only false").
  6. `set_read_only(ctx, doc, False, lambda: ctx.app.data.findFileById(df.id))`; `False` →
     `READ_ONLY_NOT_APPLIED` ("<name> did not become editable within 10 tries, 1 s apart", hint
     "retry, or call document.set_read_only with read_only false and check datafile_get"); `True` → append
     `doc.name` to `ctx.made_editable`.
  The document stays editable afterwards until `document.save`/`save_as` (document design § 6.4,
  § 6.5) or `document.set_read_only` locks it again. Each guarded call on a saved document costs one
  `findFileById`.

**Folder-limit scope.** With a limit set (§ 6.4): (a) every cloud folder or file a document action
addresses through a `folder`/`file` parameter or entry, (b) the target of `document.save`,
`document.save_as` and `document.set_read_only`, and (c) every edit of a saved open document through `ensure_editable` (the
pipeline for `"active"` actions, the action for `"action"` actions) must be inside the limit, else
`NOT_ALLOWED`. Not limited: never-saved documents (creating, editing, exporting, closing them),
`read` actions on open documents (document-tool list: document design § 2.8). Code run by `script.execute_python` or
`server.run_text_command` can reach any file; like `?profile=`, the limit is a guardrail for the
client, not access control (§ 11).

**Actions declaring `edits_document="none"` outside the document tool** (the document tool's
declarations are in document design § 3):

| Action | Why |
|---|---|
| `server.restart_addin` | restarts the add-in |
| `script.save_script`, `script.delete_script` | local script files only |
| `view.set_camera` | camera only |
| `cam.post_process` | reads the CAM product, writes local NC files |

## 6. Configuration and profiles
### 6.1 Config file
`<config dir>/config.toml`, config dir = `~/.defusion` (Windows: `%USERPROFILE%\.defusion`).
Read with `tomllib` at `start()`; changes apply on the next start (`server.restart_addin`).
```toml
port = 8642                      # 127.0.0.1 only
# token = "..."                  # optional; see § 7
log_level = "INFO"
default_profile = ["all", "-script"]
# folder = "My Project/Parts"    # optional default folder limit; see § 6.4

[units]
length = "mm"                    # default for bare numbers in and for outputs
angle = "deg"

[profiles]                       # adds to / overrides the built-in profiles
# my-print = ["3dprinting", "-model.coil"]
```
Missing file → built-in defaults. Valid keys:

| Key | Type | Default | Valid |
|---|---|---|---|
| `port` | int | 8642 | 1024-65535 |
| `token` | str | none | not blank (§ 7) |
| `log_level` | str | `INFO` | `DEBUG INFO WARNING ERROR` |
| `default_profile` | list[str] | `["all", "-script"]` | valid profile items (§ 6.3) |
| `folder` | str | none | `norm(value)` (§ 5.13) non-empty; the default folder limit (§ 6.4); otherwise the config error `config folder '<value>': needs a project-qualified path like Project/Folder` (blank or non-string value), recorded with the `invalid config <path>: ` prefix of every invalid key |
| `units.length` | str | `mm` | `mm cm m in ft um` |
| `units.angle` | str | `deg` | `deg rad` |
| `profiles.<name>` | list[str] | none | valid profile items (§ 6.3) |

**Config error rule (fail closed).** An unparseable file, an invalid key value, an invalid
profile or a blank token is a config error (`load()` checks only syntax, name rules and cycles; a profile item matching no action is checked in `start()` after `defusion.tools` is imported, and recorded the same way): it is logged and recorded (`config.load_error()`,
§ 12), and no built-in-default fallback is used. The server starts, with `port` 8642, log level
`INFO` and the token from env only, but every request's allowed set is only `server.status` and
`server.job_status`; every other call returns `NOT_ALLOWED` naming the config error, and
`server.status` shows `config.error`.

### 6.2 Built-in profiles
| Name | Items |
|---|---|
| `full` | `["all"]` |
| `readonly` | `["-write", "-destructive"]` |
| `3dprinting` | `["all", "-cam", "-model.sheet_*", "-script"]` |
| `metalworking` | `["all", "-script"]` |

### 6.3 Selectors and resolution (`profiles.py`)
An item is `[-]selector`. Selector kinds, matched in this order:
1. `all` — every action.
2. `read`, `write`, `destructive` — actions of that access class.
3. a tool name — every action of the tool.
4. `tool.pattern` — `fnmatch` on action names of that tool (`model.sheet_*`, `model.coil`).
5. a profile name — **spliced in place**: replaced by that profile's items, recursively.
   `-<profile>` is an error. A profile cycle is an error.

Resolution of an item list: splice profiles; the running set starts **empty** if the first
expanded item adds, or **all actions** if it removes; apply items left to right (add = union,
remove = difference). A selector that matches no action is an error (`INVALID_PROFILE`), so typos
surface. Profile names may not equal `all`, an access class or a tool name.

Examples: `3dprinting,readonly` = 3dprinting minus writes; `readonly` = all minus writes;
`readonly,cam` = read-only plus all CAM actions.

### 6.4 Per request
The client selects a profile with the URL query: `/mcp?profile=3dprinting,readonly`
(comma-separated items, URL-encoded). No `profile` → `default_profile`. An empty `profile` value, a blank
item and a repeated `profile` key each give `INVALID_PROFILE`. Resolution is cached per
distinct query string. An `INVALID_PROFILE` makes every MCP request on that URL fail with a
JSON-RPC error `invalid profile item '<item>': <reason>; valid selectors are in design § 6.3`.

`server.status` and `server.job_status` are always allowed, including under a config error (§ 6.1).

**Folder limit.** The query key `folder` selects the request's folder limit:
`/mcp?folder=My%20Project%2FParts` (URL-encoded, combinable with `profile`:
`/mcp?folder=P%2FA&profile=readonly`). The value is a project-qualified path in the active hub,
`<project>/<folder>/...` or just `<project>`; it is normalised to `"/".join(norm(value))`
(§ 5.13). No `folder` key → config `folder` (none → no limit); a request cannot clear a config
default. An empty value after normalising or a repeated `folder` key makes every MCP request on
that URL fail with a JSON-RPC error `invalid folder '<value>': <reason>; use ?folder=<Project>/<Folder> (URL-encoded)` (the reason
names an empty value or a repeated key), handled like
`INVALID_PROFILE`: `parse_query` raises `profiles.InvalidFolder(value, reason)`, caught beside
`InvalidProfile` at the two server.py sites. The folder need not exist: paths are compared as strings (§ 5.13).
`profiles.parse_query(qs)` returns `(profile_items | None, folder | None)`; the triple (allowed set,
profile items, folder limit) is cached per distinct query string; `actions.call(tool, args, allowed,
profile_items, output_units, folder_limit=None)` receives the limit and sets `ctx.folder_limit`
(added with the guard, TASK-CORE-027, not the query/middleware step).

### 6.5 Applying the filter
An SDK `ServerMiddleware` reads the query string from the HTTP request on the request context:
- `tools/list`: keeps tools with at least one allowed action, rewrites each tool's `action` enum
  and description to the allowed names, and sets annotations from the allowed actions:
  `readOnlyHint` = all allowed actions are `read`; `destructiveHint` = any is `destructive`;
  `idempotentHint` = false; `openWorldHint` = false.
- `tools/call`: the pipeline's step 1 (§ 5.3) checks the same allowed set, so hidden actions
  cannot be called.

## 7. Auth (`auth.py`)
Token = config `token`, else env `DEFUSION_TOKEN`, else none. A blank or whitespace-only token from
either is a config error (§ 6.1). With no token, no auth is applied.
With a token, an ASGI middleware requires `Authorization: Bearer <token>` on every HTTP request
(`hmac.compare_digest` on the UTF-8 bytes of the header value and the token) and answers anything
else, including a header that fails to decode, with 401 JSON
`{"error": "unauthorized", "hint": "send Authorization: Bearer <token>"}`. Clients pass it via `headers` in `mcp.json`.

## 8. Units (`units.py`)
- Inputs: a number becomes `"<n> <default unit>"`; a string is used as written. Expressions are
  checked with `design.unitsManager.isValidExpression(expr, "cm")` (angles: `"rad"`); invalid →
  `INVALID_PARAMS` naming the parameter. `.cm`/`.rad` come from
  `unitsManager.evaluateExpression`. Features receive `ValueInput.createByString(expr)` so
  parameter references and units are preserved in the timeline.
- Without an active design (e.g. document actions), lengths are evaluated with a fixed table for
  `mm cm m in ft um` and angles for `deg rad`; other units → `INVALID_PARAMS`.
- Bare-number inputs always use the config `[units]` defaults; the per-call `units` never changes
  input interpretation.
- Outputs: every length, area, volume, angle and mass in a result is in the call's output units.
  Default from config `[units]`; per call via top-level `units` (`{"length": "in"}`), which affects
  outputs only. Accepted
  output length units: `mm cm m in ft um`; angle: `deg rad`. Mass is `kg`; area and volume are the
  length unit squared/cubed. Keys carry no unit suffix; the envelope's `units` field states them.

## 9. Installer (`install.py`)
Run with any Python ≥ 3.10 outside Fusion: `python install.py [--uninstall] [--fusion-python PATH]
[--folder PATH]` (`--help` prints these flags and the exit codes). Exit codes: 0 success, 1 pip
failed, 2 Fusion Python not found, 3 link conflict, 4 invalid `--folder` (checked first, before
any other step: `norm(value)` empty → `--folder needs a project-qualified path like
Project/Folder`).
1. Finds Fusion's newest bundled Python:
   - macOS: newest `~/Library/Application Support/Autodesk/webdeploy/production/*/Autodesk Fusion.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3`.
   - Windows: newest `%LOCALAPPDATA%\Autodesk\webdeploy\production\*\Python\python.exe`.
   - `--fusion-python` overrides. Not found → exits 2 with the paths searched.
2. Runs `<fusion python> -m pip install --upgrade --no-compile --target De-Fusion/lib -r requirements.lock`
   after deleting the old `lib/`. Binary wheels therefore match Fusion's Python and platform. A
   non-zero pip exit prints pip's output, exits 1 and performs no later step.
3. Creates exactly one link to `De-Fusion/` in Fusion's AddIns folder
   (macOS `~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/De-Fusion`,
   Windows `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\De-Fusion`): a symlink on macOS, a
   directory junction on Windows. An existing correct link is kept; anything else at that path
   stops the installer (exit 3) with `<path> exists and is not the De-Fusion link; remove it and
   rerun install.py`.
4. Writes `config.toml` with the § 6.1 content if absent.
5. Prints `Add to your MCP client config:`, then the `mcp.json` entry
   `{"mcpServers": {"de-fusion": {"type": "http", "url": "http://127.0.0.1:<port>/mcp"}}}` with
   `<port>` from the effective `config.toml` (default 8642), plus a
   `"headers": {"Authorization": "Bearer <token>"}` member when a token is configured, then
   `Append ?profile=<items> to the url to select a profile.` When a folder is set (`--folder`, else
   the effective config `folder`; a config `folder` whose `norm` is empty is a config error and the url
   is printed without `?folder=`), the url is
   `http://127.0.0.1:<port>/mcp?folder=<urllib.parse.quote("/".join(norm(folder)), safe="")>` and
   the last line reads `Append &profile=<items> to the url to select a profile.` `--folder` only
   changes the printed url; it does not write `config.toml`.
`--uninstall` removes the link and `lib/`; it keeps `config.toml`.

The manifest sets `runOnStartup: true`, so after install the add-in loads with Fusion and appears
once in Scripts and Add-Ins as `De-Fusion`. Users must not also add the repo path with "+".

## 10. Testability
- **Unit (no Fusion):** `tests/fakes/adsk` provides enough of `adsk.core`/`adsk.fusion` for the
  framework (Application with `fireCustomEvent`, `executeTextCommand`, a Design with
  `unitsManager`, `findEntityByToken`, root component with bodies/sketches). Tests cover action
  registration and schema building, the call pipeline, every error code reachable without Fusion,
  profile resolution, auth, units, dispatcher timeouts and jobs, size budget, help, config loading
  and the installer's path discovery (with temp dirs).
- **MCP surface (no Fusion):** an in-process test starts the HTTP app with fakes and uses the SDK
  client to call `tools/list` and `tools/call` with and without `?profile=` and token, with a valid Host and a foreign `Origin` (rejected, no
  action runs), and with a config error (only `server.status`/`server.job_status` callable).
- **Live:** `scripts/live_check.py [--profile P] [--port N] [--only tool[.action]]` (`--port` default: the config `port`
  (8642 when unset); the token is read from env `DEFUSION_TOKEN` only; `--help` prints these flags and the exit
  codes) connects to the running add-in, creates a new scratch design document on its own `full`
  profile session (same token) while scenarios use the `--profile` session, runs each tool set's
  live scenarios (each tool module exposes `LIVE_SCENARIOS`), prints one line per scenario,
  `PASS <name> (<tool.action>)` or `FAIL <name> (<tool.action>): <error>`, and closes the scratch
  document without saving. Finally it lists the active project's root files named `defusion-live-*` and sets any still editable
  read-only (`FIXED editable leftover <path>`; `FAIL ...` and exit 1 if it cannot). Exit codes: 0 all pass, 1 any failure, 2 server unreachable.
  - `LIVE_SCENARIOS: list[tuple[str, str, Callable[[ClientSession], Awaitable[None]]]]` =
    `(name, "tool.action", async fn(session))`. The tag names the action the scenario tests;
    `session` is the SDK `ClientSession` connected with the harness's profile and token.
    `scripts/live_check.open_session(profile=None, folder=None)` is an async context manager that
    opens another session with the harness's port and token and the given profile and, when
    `folder` is given, `folder=<URL-encoded folder>` in the query (§ 6.4). The live run requires the
    config `folder` to be unset (scenarios save in the active project's root; `--help` and the README say
    so); folder-limit
    scenarios use `open_session(folder=...)`. The final freeze step calls the list forms
    `open {"files": [<id>]}`, `set_read_only {"documents": [<doc id>]}` and
    `close {"documents": [<doc id>], "confirm": true}` (document design § 2.7); each call counts as
    done only when the envelope `ok` and `results[0].ok` are both true, else `FAIL ...` and exit 1.
    A scenario fails by raising.
  - `--only tool` runs every scenario of that tool module; `--only tool.action` runs the
    scenarios tagged `tool.action`. Scenarios run in list order.
  - Helper `scripts/live_check.box(session, w, d, h, name=None)` runs `sketch.create` on XY,
    `sketch.rectangle` and `model.extrude` (new body) and returns the body Ref; tool sets call it
    instead of spelling out the sequence.
  - Helper `scripts/live_check.call(session, tool, action, params, units=None) -> dict` calls
    `tools/call` and returns the parsed envelope (`ok` true or false).

## 11. Security
Bound to 127.0.0.1; Host/Origin checks from the SDK; optional bearer token. `?profile=` and
`confirm` are chosen by the client and do not restrict a local caller; the token is the only
access control. The default profile excludes `script` actions only as a convenience. The same
holds for `?folder=` and `preserve_read_only` (§ 5.13): they keep a cooperative client inside a
folder and away from locked documents; they do not restrict code it runs.

## 12. Module accessors used by tool sets
Stable names other modules (chiefly `tools/server.py`) read; none touches `adsk`.

| Name | Meaning |
|---|---|
| `dispatch.queue_depth() -> int` | items in state `queued` |
| `dispatch.running_count() -> int` | registry jobs in state `running` |
| `dispatch.get_job(job_id) -> dict \| None` | registry entry `{action, state, result \| error, started, finished}` (§ 4) |
| `config.CONFIG_DIR: Path` | `~/.defusion` (Windows `%USERPROFILE%\.defusion`; env `DEFUSION_CONFIG_DIR` overrides for tests) |
| `config.CONFIG_PATH: Path` | `CONFIG_DIR / "config.toml"` |
| `config.settings()` | the effective config loaded at `start()` (`.port`, `.token`, `.log_level`, `.default_profile`, `.folder`, `.units`, `.profiles`) |
| `config.load_error() -> str \| None` | config error recorded at `start()` (§ 6.1) |
| `auth.token() -> str \| None` | effective token (§ 7), `None` when auth is off |
| `server.state() -> str` | `starting`, `running` or `failed` (§ 3.1) |
