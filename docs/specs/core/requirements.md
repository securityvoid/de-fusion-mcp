# requirements.md — docs/specs/core/
> De-Fusion MCP — add-in runtime, action framework, profiles, auth and installer

## Overview
The core set delivers the De-Fusion add-in shell, the MCP transport, the main-thread dispatcher,
the `@action` framework (schema, validation, Ref resolution, units, undo grouping, change summary,
errors, results, paging, help), configuration and profiles, optional bearer auth, logging, the
installer, the live test harness and README.md. Tool actions are specified by the tool sets in
`docs/specs/<tool>/`; this set defines only the framework they use. Section references (§) point to
`docs/specs/core/design.md`.

## Operator ask
2026-11-10: "comprehensive Fusion MCP server ... union of the four servers"

## Dependencies
- **DEP-CORE-001**: The `server` tool set (`docs/specs/server/`) provides the `server.status`,
  `server.job_status` and `server.restart_addin` actions that read the status and job registry
  defined here. Live verification tasks (Phase 4) wait on it.
- **DEP-CORE-002**: Every tool set provides its module under `defusion/tools/` with at least one
  `@action`; `defusion/tools/__init__.py` (created here) imports all ten. Live verification tasks
  (Phase 4) wait on all ten modules existing.
- **DEP-CORE-003**: MCP Python SDK `mcp==2.3.0` and `uvicorn`, pinned in `requirements.lock`.

## Functional Requirements

### Add-in lifecycle
- **REQ-CORE-001**: The add-in entry `De-Fusion/De-Fusion.py` `run(context)` shall insert
  `<add-in>/lib` and `<add-in>` at the front of `sys.path`, configure the file logger, import
  `defusion.server` and call `server.start()`; on `ImportError` it shall log
  `Dependencies missing in <lib>; run install.py` and return without running pip (§ 3.1).
  - _Acceptance_: With `lib/` absent, `run()` returns, no subprocess is spawned, and the log
    contains the message with the lib path; with `lib/` installed, `server.start()` is called once.
- **REQ-CORE-002**: `server.start()` shall load the config, register the dispatcher custom event,
  import `defusion.tools` and start uvicorn on a daemon thread without blocking the caller; the
  thread shall record `starting`, `running` or `failed` in a module-level status, and a bind
  failure shall log the reason and set `failed`. The runtime shall show no modal dialogs (§ 3.1).
  - _Acceptance_: `start()` returns in under 1 s; status becomes `running` and `/mcp` answers;
    starting with the port already bound sets `failed` and logs the port; `grep -r messageBox
    De-Fusion/` finds no calls.
- **REQ-CORE-003**: `server.stop()` shall set `should_exit`, join the uvicorn thread for up to 5 s,
  wait up to 5 s more until the port refuses connections, fail every queued dispatcher item with
  `NOT_EXECUTED`, unregister the custom event and clear the job registry; `start()` after `stop()`
  shall succeed in the same process (§ 3.3).
  - _Acceptance_: start → stop → start in one process serves `/mcp` again; after `stop()` a TCP
    connect to the port is refused and a queued item's caller receives `NOT_EXECUTED`.
- **REQ-CORE-004**: `De-Fusion/De-Fusion.manifest` shall set `runOnStartup: true`, supportedOS
  `windows|mac` and a `version` equal to `defusion.__version__`; the add-in folder, `.py` and
  `.manifest` shall all be named `De-Fusion`, so the add-in appears exactly once in Fusion's
  Scripts and Add-Ins dialog as `De-Fusion` (§ 2, § 3.5, § 9).
  - _Acceptance_: A unit test parses the manifest and asserts `runOnStartup` is true and the
    version equals `__version__`; live, after install and Fusion restart the dialog lists one
    `De-Fusion` entry, running.
- **REQ-CORE-005**: Fusion API objects shall be touched only on Fusion's main thread inside
  dispatcher items; HTTP and uvicorn threads shall never call `adsk.*` or `app.log` (§ 3.4).
  - _Acceptance_: A unit test with the fake `adsk` records the calling thread of every fake API
    call during an in-process `tools/call` and asserts all are the dispatcher's main thread.

### Transport
- **REQ-CORE-006**: The server shall be `MCPServer(name="De-Fusion", version=__version__,
  instructions=INSTRUCTIONS)` served as stateless streamable HTTP with JSON responses at `/mcp`,
  bound to `127.0.0.1:<port>` (default 8642), with transport security allowing only Host/Origin
  `127.0.0.1:<port>` and `localhost:<port>`; it shall expose no SSE routes, stdio, resources or
  prompts. `INSTRUCTIONS` shall be one paragraph stating that each tool takes `action` + `params`,
  that `action="help"` returns an action's schema, and that lengths accept unit expressions (§ 3.2).
  - _Acceptance_: SDK client `initialize` reports server name `De-Fusion` and the instructions
    text; `resources/list` and `prompts/list` return method-not-found or empty; a request with
    `Host: evil.example` is rejected; a `tools/call` with `Host: 127.0.0.1:<port>` and
    `Origin: http://evil.example` is rejected and runs no action; `GET /sse` returns 404.

### Dispatcher and jobs
- **REQ-CORE-007**: `dispatch.run(fn, timeout)` shall queue an item under a lock, fire custom event
  `defusion.dispatch`, re-fire it every 100 ms while the queue is non-empty, and have the
  main-thread handler run items in order, storing result or exception; when `adsk` cannot be
  imported it shall run `fn` inline. Default timeout is 30 s. At most 64 items shall be queued; a
  further call shall return `NOT_EXECUTED` immediately with a retry hint (§ 4).
  - _Acceptance_: Unit tests: a fake app whose first `fireCustomEvent` is dropped still runs the
    item via re-fire; results and exceptions propagate to the caller; without `adsk`, `fn` runs
    inline.
- **REQ-CORE-008**: On timeout, a still-queued item shall be removed and the call shall return
  `NOT_EXECUTED` ("did not run; safe to retry"), or when a registry job is running "Fusion is busy
  with job <id> (<tool>.<action>); wait or poll server.job_status" with `error.job_id`; the handler's
  queued→running change and the timeout removal shall both happen under the queue lock so exactly
  one wins; a running item shall move to the job registry and
  the call shall return `{"status": "running", "job_id": "<id>"}` with a hint to poll
  `server.job_status` (§ 4).
  - _Acceptance_: Unit tests drive both outcomes with a held main-thread handler and assert the
    returned envelope and that the queued item never runs; with 64 queued items and the handler held,
    the 65th `dispatch.run` is refused at once with `NOT_EXECUTED` and never runs.
- **REQ-CORE-009**: The job registry shall keep the last 100 jobs (oldest evicted), each
  `{state: running|done|failed, result|error, started, finished}`, readable without the main
  thread (§ 4).
  - _Acceptance_: Unit test adds 101 jobs and asserts the first is evicted; a job finishing after
    timeout shows `done` with its result, or `failed` with its error.

### Action framework
- **REQ-CORE-010**: `@action(tool, name=None, access=..., timeout=30, undo_group=True,
  main_thread=True)` shall register
  a function under `(tool, name)` with `tool` in `document sketch model assembly history inspect
  view cam script server`, `access` in `read write destructive`, name defaulting to the function
  name (overridable, e.g. `name="list"`), summary = first docstring line and help = full
  docstring; parameters after `ctx` shall use only the annotations of § 5.1 (including unions such
  as `int | TimelineItem`), have snake_case names and no `**kwargs`; a pydantic model with
  `extra="forbid"` shall be built at registration (§ 5.1).
  - _Acceptance_: Unit tests: a valid action registers with the expected summary, help, access,
    timeout and JSON schema; `name="list"` registers action `list`; an unsupported annotation, a
    non-snake_case name, `**kwargs`, an unknown tool or access each raise at decoration time.
- **REQ-CORE-011**: A duplicate `(tool, name)` registration shall raise at import time, and
  importing `defusion.tools` shall raise if any tool module registered no action. 
  `defusion/tools/__init__.py` shall import the ten tool modules of § 2 (§ 5.1).
  - _Acceptance_: Unit tests: registering the same name twice raises; a temporary empty tool
    module in the import list raises naming the module.
- **REQ-CORE-012**: Each tool shall be registered once with the SDK with the input schema of § 5.2
  (`action` enum = `help` + allowed actions, `params` object default `{}`, optional `units` with
  `length`/`angle` strings, `required: ["action"]`, `additionalProperties: false`) and description
  `<tool summary> Actions: <names>. Call action="help" with params {"action": "<name>"} for its
  parameters.`, where the tool summary is the first line of the tool module's docstring, a full
  sentence ending in a period.
  Per-action schemas shall never appear in `tools/list` (§ 5.2).
  - _Acceptance_: In-process `tools/list` returns exactly one entry per tool whose schema equals the
    § 5.2 shape and whose description matches the template with no `..`; no action parameter names appear.
- **REQ-CORE-013**: `tools/call` shall apply, in order: profile check (`NOT_ALLOWED`), `help`
  (§ 5.6), unknown action (`UNKNOWN_ACTION` with the allowed names as hints), params validation
  (`INVALID_PARAMS` with pydantic messages as hints and the action schema in `error.schema`;
  top-level `units` is validated with `params`, and `help` ignores it), and
  for `destructive` actions without `params.confirm == true`, `CONFIRM_REQUIRED`; the framework
  shall add `confirm: bool = false` to every destructive action's model (§ 5.3 steps 1–5).
  - _Acceptance_: Unit tests reach each error code in this order (e.g. a disallowed unknown action
    yields `NOT_ALLOWED`, not `UNKNOWN_ACTION`) and assert hints and schema content; an invalid
    top-level `units` on a `read` action → `INVALID_PARAMS`, on a hidden action → `NOT_ALLOWED`.
- **REQ-CORE-014**: After validation the pipeline shall dispatch to the main thread one closure
  that builds `Ctx`, resolves typed parameters, and for `write`/`destructive` snapshots the change
  summary, opens the undo group, calls the function, closes the undo group and computes the change
  summary; then it shall shape the result (§ 5.3 steps 6–7).
  - _Acceptance_: Unit test with a fake design records the sequence snapshot → `PTransaction.Start`
    → function → `PTransaction.Commit` → snapshot, all on the dispatcher thread; a `read` action
    records no transaction or snapshot.

### Parameter types and Refs
- **REQ-CORE-015**: `Length`, `Angle` and `Expr` shall accept a number or string and resolve to a
  `Value` with `.expr`, `.cm`/`.rad` (Expr: unitless) and `.value_input()` returning
  `ValueInput.createByString(expr)`; enum literals (e.g. `Operation`) shall be defined once in
  `types.py` with a mapping to the `adsk` enum (§ 5.4).
  - _Acceptance_: Unit tests: `5` → expr `"5 mm"`, cm `0.5`; `"1 in"` → cm `2.54`; `90` angle → rad
    `π/2`; `value_input()` is created by string; each `Operation` literal maps to its fake enum.
- **REQ-CORE-016**: `Point3` shall accept `[x, y, z]` of Length items or `{"x","y","z"}` and
  resolve to `Point3D` in cm; `Vector3` shall accept `[x, y, z]` numbers and resolve to `Vector3D`
  (§ 5.4).
  - _Acceptance_: Unit tests: `[10, 0, "1 in"]` → `Point3D(1.0, 0, 2.54)`; the dict form gives the
    same; wrong length lists fail with `INVALID_PARAMS`.
- **REQ-CORE-017**: Entity types (`Body`, `Face`, `Edge`, `Vertex`, `Sketch`, `SketchCurve`,
  `SketchPoint`, `Feature`, `Component`, `Occurrence`, `ConstructionPlane`, `ConstructionAxis`,
  `Joint`, `Parameter`, `TimelineItem`) and lists of them shall accept the Ref forms `{"token"}`
  (via `design.findEntityByToken`, first match of the expected type), `{"name", "component"?}`
  (exact name within the component, default whole design), `{"body", "index"}` for
  Face/Edge/Vertex, and a bare string (name first, then token) (§ 5.4).
  - _Acceptance_: Unit tests resolve each form against the fake design, including a bare string
    that is a token and a name scoped to a component.
- **REQ-CORE-018**: `Plane` shall accept `"XY"`, `"XZ"`, `"YZ"` (origin planes of the target
  component) or a Ref to a construction plane or planar face; `Axis` shall accept `"X"`, `"Y"`,
  `"Z"` or a Ref to a construction axis, linear edge or sketch line (§ 5.4).
  - _Acceptance_: Unit tests resolve each literal to the fake component's origin plane/axis and
    reject a non-planar face / non-linear edge with `INVALID_PARAMS`.
- **REQ-CORE-019**: `Profile` shall accept `{"sketch": Ref, "index": int | [int,...]}` resolving
  to a `Profile` or `ObjectCollection`; `index` may be omitted only when the sketch has exactly one
  profile (§ 5.4).
  - _Acceptance_: Unit tests: single index, index list, omitted index with one profile succeed;
    omitted index with two profiles and out-of-range index fail with `INVALID_PARAMS`.
- **REQ-CORE-020**: Ref resolution shall fail with `NOT_FOUND` when nothing matches, `AMBIGUOUS`
  with every candidate's `ctx.ref()` in `error.candidates` when more than one name matches, and
  `INVALID_PARAMS` when the object has the wrong type (§ 5.4).
  - _Acceptance_: Unit tests produce each code; the `AMBIGUOUS` error lists both candidates.
- **REQ-CORE-021**: `Ctx.ref(obj)` shall return `{"type", "name", "token", "component"}` and every
  such dict shall be accepted back as a Ref of the matching type (§ 5.4).
  - _Acceptance_: Unit test round-trips `ctx.ref(body)` through `Body` resolution to the same fake
    object.

### Results, help and errors
- **REQ-CORE-022**: Success results shall be `CallToolResult(isError=False,
  content=[TextContent(json)], structuredContent=envelope)` with envelope `{"ok": true, "tool",
  "action", "result", "units", "changes"?}` (`changes` only for write/destructive); errors shall be
  `isError=True` with `{"ok": false, "tool", "action", "error": {"code", "message", "hints",
  "schema"?, "candidates"?}}`; an `Image(png, meta)` return (or a list of them) shall produce one `ImageContent` per image
  followed by `TextContent(json of the meta envelope)` (§ 5.5).
  - _Acceptance_: Unit tests assert each shape; `structuredContent` equals the parsed text JSON.
- **REQ-CORE-023**: A serialised `result` over 64 KiB (65,536 UTF-8 bytes) shall return
  `RESULT_TOO_LARGE` with a hint naming a smaller `limit`, and a result whose images total over 4 MiB
  (4,194,304 bytes) shall return `RESULT_TOO_LARGE` naming the size and saying to reduce
  the image size (`width`/`height`, or `tile` for `view_sheet`); a finishing job's result shall pass the same checks and be stored as `failed` with
  that error. `ctx.page(seq, offset, limit, build=None)` shall require `offset >= 0` and
  `1 <= limit <= 500` (`INVALID_PARAMS` otherwise), slice `seq` before applying `build` to each
  item, and return `{"items", "total", "offset", "next_offset"}` with `next_offset` null at the end
  (§ 4, § 5.5).
  - _Acceptance_: Unit tests: results of exactly 65,536 bytes succeed and 65,537 yield
    `RESULT_TOO_LARGE`; images totalling 4,194,304 bytes succeed and one byte more fail; an
    oversize job result is `failed` with `RESULT_TOO_LARGE`; paging 250 items with limit 100 gives
    `next_offset` 100, 200, null; `limit` 0 or 501 and `offset` -1 → `INVALID_PARAMS`; `build` is
    called only for the returned slice.
- **REQ-CORE-024**: `action="help"` shall be implicit on every tool and always allowed when the tool
  is visible: `params {}` lists allowed actions `{name, summary, access}`; `params {"action": x}`
  returns `{name, summary, help, access, timeout, input_schema}`, or `NOT_ALLOWED` when `x` is not
  allowed (§ 5.6).
  - _Acceptance_: Unit tests under a profile that hides one action: the list omits it and asking
    help for it returns `NOT_ALLOWED`.
- **REQ-CORE-025**: Errors shall be raised as `ActionError(code, message, hints=(), **extra)` with
  the codes of § 5.10, each `extra` keyword copied as a key of the envelope's `error` object; any
  other exception from an action shall become `FUSION_ERROR` carrying the
  exception text and type name (§ 5.10).
  - _Acceptance_: Unit tests: `ActionError("NOT_FOUND", "m", job_id="j1")` yields
    `error.job_id == "j1"`; an action raising `RuntimeError("x")` returns `FUSION_ERROR` with
    `x` and `RuntimeError` in the message; every code reachable without Fusion is produced by at
    least one test.

### Undo grouping, change summary, Ctx
- **REQ-CORE-026**: For `write`/`destructive` calls with an open document and `undo_group=True`,
  the pipeline shall run `PTransaction.Start "De-Fusion <tool>.<action>"` before,
  `PTransaction.Commit` after, and `PTransaction.Abort` on exception before returning the error; if
  Start raises, or Commit raises (the result is still returned, with a logged warning), the call
  shall proceed ungrouped and the envelope shall carry a top-level `"undo_grouped": false` beside
  `changes` (present only when false); a raising Abort shall keep and return the original error
  (§ 5.5, § 5.7).
  - _Acceptance_: Unit tests with the fake `executeTextCommand` assert the command strings for
    success, failure (Abort issued, error returned; Abort raising still returns the original error),
    Start failure and Commit failure (`undo_grouped: false`; the key is absent otherwise);
    `undo_group=False` issues none.
- **REQ-CORE-027**: For `write`/`destructive` calls with an active design, `changes` shall hold
  `[before, after]` pairs for `bodies` (count over all components), `timeline` (count, null in
  direct mode) and `bbox` (union of visible body boxes in output length units, null on failure or above 500 bodies),
  omitting unchanged keys (§ 5.8).
  - _Acceptance_: Unit test adding a fake body yields `changes.bodies == [0, 1]` and a bbox pair;
    a no-op write yields `changes == {}`; a bbox exception yields null, not an error; a fake design of 501 bodies yields null with no
    `boundingBox` reads.
- **REQ-CORE-028**: `Ctx` shall provide `app`, `ui`, `document`, `design`, `require_design()`
  (`NO_DESIGN`), `require_parametric()` (`WRONG_DESIGN_TYPE`), `component(ref=None)`, `ref`, `refs`,
  `length_out`, `angle_out`, `area_out`, `volume_out`, `mass_out`, `page`, `text_command`
  (`TEXT_COMMAND_FAILED` with command and Fusion message), `attr_set`/`attr_get` on attribute group
  `defusion` (`attr_set` stores `json.dumps(value)`, `attr_get` returns the parsed value or `None`
  when absent), and `fail` (§ 5.9).
  - _Acceptance_: Unit tests call each helper against the fake design and assert values and error
    codes; `attr_set(e, "k", {"a": 1})` then `attr_get(e, "k")` returns `{"a": 1}`; a missing key
    returns `None`.

### Configuration and profiles
- **REQ-CORE-029**: Config shall be read with `tomllib` at `start()` from `<config dir>/config.toml`
  (`~/.defusion`, Windows `%USERPROFILE%\.defusion`) with keys `port`, `token`, `log_level`,
  `default_profile`, `[units]` and `[profiles]` and built-in defaults for missing keys; env
  `DEFUSION_CONFIG_DIR`, when set, replaces the config dir (used by tests); a missing file uses
  defaults. Any config error (unparseable file, a key invalid per the § 6.1 table, invalid profile,
  blank token) shall be logged and the server shall start with only `server.status` and
  `server.job_status` allowed, every other call returning `NOT_ALLOWED` naming the error, and
  `server.status` showing `config.error`; no built-in-default fallback applies on error. The log
  level shall be applied again after the load, replacing the handler (§ 3.1, § 6.1).
  - _Acceptance_: Unit tests with a temp config dir cover missing, valid, unparseable, invalid-key,
    invalid-profile and blank-token files and assert the effective config, that only the two server
    actions are allowed, the `NOT_ALLOWED` text and `config.error`; `DEFUSION_CONFIG_DIR` is honoured.
- **REQ-CORE-030**: The built-in profiles `full`, `readonly`, `3dprinting`, `metalworking` shall
  have the items of § 6.2; `[profiles]` entries shall add to or override them; a profile named
  `all`, an access class or a tool name shall be rejected as invalid (§ 6.2, § 6.3).
  - _Acceptance_: Unit tests resolve each built-in profile against a registry of test actions; a
    user override replaces the built-in; a profile named `model` is a config error.
- **REQ-CORE-031**: Profile items `[-]selector` shall be matched as `all`, access class, tool
  name, `tool.pattern` (fnmatch on action names), then profile name spliced in place recursively;
  the running set starts empty if the first expanded item adds or full if it removes; items apply
  left to right; `-<profile>`, a cycle, or a selector matching no action shall raise
  `INVALID_PROFILE` (§ 6.3); `load()` checks syntax, names and cycles, and a selector matching
  no action is checked in `start()` after `defusion.tools` is imported, as a config error.
  - _Acceptance_: Unit tests: `3dprinting,readonly`, `readonly`, `readonly,cam` match the § 6.3
    examples; `-readonly`, a cycle and `model.nosuch*` each raise naming the item.
- **REQ-CORE-032**: The query `/mcp?profile=<items>` shall select the request's profile (no query →
  `default_profile`), resolution shall be cached per distinct query string, an empty `profile` value, a blank item or a repeated `profile` key and any
  `INVALID_PROFILE` shall fail every MCP request on that URL with a JSON-RPC error naming the bad item
  and how to fix it, and
  `server.status` and `server.job_status` shall be allowed whenever any action is allowed (§ 6.4).
  - _Acceptance_: In-process SDK client tests with `?profile=readonly`, no query and
    `?profile=nosuch`, `?profile=` and `?profile=a&profile=b` assert the allowed set, the cache hit
    and the JSON-RPC error text; `?profile=cam`
    still lists `server` with `status` and `job_status`.
- **REQ-CORE-033**: An SDK `ServerMiddleware` shall filter `tools/list` to tools with at least one
  allowed action, rewrite each tool's `action` enum and description to the allowed names, and set
  annotations `readOnlyHint` (all allowed are read), `destructiveHint` (any is destructive),
  `idempotentHint=false`, `openWorldHint=false`; `tools/call` shall enforce the same allowed set
  (§ 6.5).
  - _Acceptance_: In-process `tools/list` under `readonly` lists every tool with only read actions
    and `readOnlyHint: true`; calling a hidden write action returns `NOT_ALLOWED`.

### Auth, units, logging
- **REQ-CORE-034**: With a token (config `token`, else env `DEFUSION_TOKEN`), an ASGI middleware
  shall require `Authorization: Bearer <token>` on every HTTP request using a constant-time compare
  and answer anything else with 401 `{"error": "unauthorized", "hint": <remedy>}`; with no token no auth shall apply
  (§ 7); the middleware shall compare the UTF-8 bytes of the header value and token, answer any
  header decode failure with 401, and the 401 body shall say to send `Authorization: Bearer <token>`;
  a blank or whitespace-only token from config or env is a config error (REQ-CORE-029).
  - _Acceptance_: In-process tests: no header / wrong token / non-UTF-8 header → 401 JSON; a blank
    token → config error; correct token → 200; config
    token wins over env; no token → requests succeed without a header; the code uses
    `hmac.compare_digest`.
- **REQ-CORE-035**: Input lengths and angles shall be evaluated per § 8: numbers get the config
  `[units]` default unit (never the per-call output `units`), strings are used as written, expressions are checked with
  `unitsManager.isValidExpression(expr, "cm"|"rad")` and evaluated with `evaluateExpression`
  (invalid → `INVALID_PARAMS` naming the parameter); without an active design a fixed table for
  `mm cm m in ft um` and `deg rad` shall be used, other units → `INVALID_PARAMS`.
  - _Acceptance_: Unit tests with and without a fake design: `5` with per-call `{"length": "in"}`
    still evaluates to 0.5 cm; `"2 ft"` → 60.96 cm without a design;
    `"3 furlong"` → `INVALID_PARAMS` naming the parameter.
- **REQ-CORE-036**: Outputs (length, area, volume, angle, mass) shall be in the call's output units:
  defaults from config `[units]`, overridden per call by top-level `units`; accepted lengths
  `mm cm m in ft um`, angles `deg rad`; mass `kg`; area/volume = length unit squared/cubed; the
  envelope's `units` field states the units used (§ 8).
  - _Acceptance_: Unit tests: `length_out(2.54)` with `{"length": "in"}` → 1.0 and envelope units
    `in`; `{"length": "furlong"}` → `INVALID_PARAMS`.
- **REQ-CORE-037**: Logging shall go to `<config dir>/defusion.log`, rotating at 1 MiB × 3 files,
  level from `log_level` (default `INFO`), and every action call shall log tool, action, duration
  and outcome code (§ 3.6).
  - _Acceptance_: Unit test with a temp config dir: a call writes one line containing tool, action,
    a duration and `ok` or the error code; the handler is a `RotatingFileHandler(maxBytes=1048576,
    backupCount=3)`.

### Installer, live harness, README
- **REQ-CORE-038**: `install.py` (any Python ≥ 3.10) shall locate Fusion's newest bundled Python
  (macOS and Windows paths of § 9, newest build directory by modification time, `--fusion-python`
  override, exit 2 listing searched paths when not found), delete `De-Fusion/lib`, run
  `<fusion python> -m pip install --upgrade --no-compile --target De-Fusion/lib -r
  requirements.lock`, and create exactly one link (macOS symlink, Windows directory junction) at
  Fusion's AddIns `De-Fusion` path, keeping a correct existing link and stopping with exit 3 and a message
  naming the path and saying to remove it if anything else is there; a non-zero pip exit shall print
  pip's output, exit 1 and perform no later step. Flags and exit codes are those of § 9: `--fusion-python
  PATH`, `--uninstall`, `--help`; exit 0 success, 1 pip failure, 2 Fusion Python not
  found, 3 link conflict (§ 9 steps 1–3).
  - _Acceptance_: Unit tests with temp dirs: the newest of two fake builds is chosen; none found →
    exit 2 with paths; the pip command line is exact; an existing correct link is kept; a regular
    directory at the link path stops the installer with the message; a failing fake pip exits 1 and
    creates no link; `--help` lists the flags.
- **REQ-CORE-039**: `install.py` shall write `config.toml` with the § 6.1 content if absent, print,
  with the configured port, the `mcp.json` entry `{"mcpServers": {"de-fusion": {"type": "http", "url":
  "http://127.0.0.1:<port>/mcp"}}}` plus a `headers` line when a token is configured and how to add
  `?profile=`; `--uninstall` shall remove the link and `lib/` and keep `config.toml` (§ 9 steps
  4–5).
  - _Acceptance_: Unit tests: an existing config is not overwritten; output contains the JSON and,
    with a token, `Authorization: Bearer`; `--uninstall` leaves only `config.toml`.
- **REQ-CORE-040**: `scripts/live_check.py [--profile P] [--port N] [--only tool[.action]]` shall
  connect to the running add-in on `--port` (default: the config `port`, 8642) with the SDK client,
  reading the token only from env `DEFUSION_TOKEN`; it shall create a new scratch design document on
  its own `full` profile session and run each
  tool module's `LIVE_SCENARIOS` entries `(name, "tool.action", async fn(session))` passing the SDK
  `ClientSession` on the `--profile` session (`--only tool` selects a module, `--only tool.action` the entries with
  that tag); it shall expose async context manager `open_session(profile=None)` for scenarios that
  need another profile, print one `PASS`/`FAIL` line per scenario, close the scratch document without
  saving on the `full` session and exit 1 on any failure, 2 when the server is unreachable. README.md documents
  install, the `mcp.json` entry, the config file, profiles and selectors, the token and units
  (§ 10).
  - _Acceptance_: `DEFUSION_TOKEN=t0k python scripts/live_check.py --only server` against the running add-in prints
    PASS lines and exits 0; `--only server.status` runs only scenarios tagged `server.status`; a
    scenario forced to fail exits 1; README.md contains sections for
    install, mcp.json, config, profiles, token and units.

### Framework contract used by tool sets
- **REQ-CORE-041**: An action declared `main_thread=False` shall run on the HTTP worker thread
  without the dispatcher and without a time limit (it runs to completion), with a `Ctx` whose `app`, `ui`, `document` and `design` are `None`, no
  undo group and no change summary; declaring it on a non-`read` action or with a Ref-typed
  parameter shall raise at decoration time (§ 5.1, § 5.3).
  - _Acceptance_: Unit test: with the dispatcher's handler held so the queue never drains, a
    `main_thread=False` action returns within 1 s and its recorded thread is not the dispatcher
    thread; a slow `main_thread=False` action returns its result after exceeding `timeout`;
    `main_thread=False` on a `write` action raises.
- **REQ-CORE-042**: `Ctx` shall expose `allowed` (frozenset of `"tool.action"` names allowed for
  the request), `profile_items` (expanded profile items), `units` (`{"length", "angle"}` output unit
  names of the call), and `design` taken from
  `document.products.itemByProductType("DesignProductType")` rather than `app.activeProduct`
  (§ 5.9).
  - _Acceptance_: Unit tests: under `?profile=readonly` `ctx.allowed` contains `server.status` and
    no write action; per-call `{"length": "in"}` gives `ctx.units == {"length": "in", "angle":
    "deg"}`; a fake document whose `activeProduct` is a CAM product still yields `ctx.design`.
- **REQ-CORE-043**: `actions.call_inline(ctx, tool, action, params)` shall validate params, check
  confirm, resolve Refs and call the other action's function with the same `ctx`, opening no undo
  group, taking no change snapshot and not dispatching; it shall raise `NOT_ALLOWED` when
  `"tool.action"` is not in `ctx.allowed` and `UNKNOWN_ACTION` when unregistered (§ 5.11).
  - _Acceptance_: Unit test: an outer write action calling `call_inline` on an inner write action
    records exactly one `PTransaction.Start`/`Commit` pair and returns the inner raw result; with the
    inner action outside `ctx.allowed` it raises `NOT_ALLOWED`.
- **REQ-CORE-044**: Ref types `MeshBody`, `JointOrigin`, `RigidGroup`, `ContactSet`, `Entity`,
  `CamSetup`, `CamOperation` and the `TimelineItem` forms shall resolve per § 5.4: `Entity` accepts
  any entity class including `MeshBody`; `ContactSet` raises `UNSUPPORTED` when `design` has no
  `contactSets`; `CamSetup`/`CamOperation` resolve by name (operation optionally scoped by `setup`)
  or token within the document's CAM product and accept their own output refs; `TimelineItem`
  resolves a token or name via the entity's `timelineObject` and `{"index": n}` via
  `design.timeline.item(n)`.
  - _Acceptance_: Unit tests against fakes: `Entity` resolves a mesh body and a sketch by name;
    `ContactSet` without `contactSets` → `UNSUPPORTED`; `{"type": "Operation", "name": "Face1",
    "setup": "Setup1"}` resolves to the fake operation; a document without a CAM product →
    `NOT_FOUND`; `{"index": 2}` returns timeline item 2 and `{"index": 99}` → `NOT_FOUND`.
- **REQ-CORE-045**: `Point2` shall accept `[x, y]` Lengths (sketch space) or `[x, y, z]` (model
  space, converted with the action's `sketch` parameter's `modelToSketchSpace` and projected to
  z = 0) and resolve to a sketch-space `Point3D` in cm; a 3-item value in an action without a
  `sketch` parameter shall fail with `INVALID_PARAMS` (§ 5.4).
  - _Acceptance_: Unit tests: `[10, 5]` → `Point3D(1.0, 0.5, 0)`; `[0, 0, 5]` on a fake sketch
    whose plane is offset 5 mm along Z → `Point3D(0, 0, 0)`; `[1, 2, 3]` without a `sketch`
    parameter → `INVALID_PARAMS`.
- **REQ-CORE-046**: Union annotations shall validate with pydantic and resolve only the matching
  Ref-typed branch, and Ref-typed fields inside pydantic-model parameters (and lists of them) shall
  be resolved recursively before the function runs (§ 5.4).
  - _Acceptance_: Unit tests: `int | TimelineItem` passes `3` through as `int` and resolves
    `{"index": 3}` to the timeline item; a model param with a `list[Body]` field arrives with fake
    bodies.
- **REQ-CORE-047**: `Plane` and `Axis` origin names shall resolve against the action's `component`
  parameter when declared and set, else `design.activeComponent` (§ 5.4).
  - _Acceptance_: Unit test: `"XY"` with `component` set to a fake sub-component returns that
    component's `xYConstructionPlane`; without it, the active component's.
- **REQ-CORE-048**: The dispatcher's main-thread handler shall not be re-entrant: while an item is
  running (including during `adsk.doEvents()`) a further custom-event delivery shall return
  immediately without draining (§ 4).
  - _Acceptance_: Unit test: an item whose function invokes the handler again (simulating
    `doEvents`) does not run the next queued item until it finishes; both complete in order.
- **REQ-CORE-049**: Core shall expose the module accessors of § 12 (`dispatch.queue_depth`,
  `dispatch.running_count`, `dispatch.get_job`, `config.CONFIG_DIR`, `config.CONFIG_PATH`,
  `config.settings`, `config.load_error`, `auth.token`, `server.state`), none of which touches
  `adsk`.
  - _Acceptance_: Unit tests call each accessor with fakes from a non-dispatcher thread and assert
    values (one queued item → `queue_depth() == 1`; broken config → `load_error()` non-empty;
    token configured → `auth.token()` equals it; after `start()` → `state() == "running"`).

## Non-Functional Requirements
- **NFR-CORE-001**: The server shall bind only to 127.0.0.1; `script` actions shall be excluded by
  the default profile; destructive actions shall need `confirm`. `?profile=` and `confirm` are
  chosen by the client and do not restrict a local caller; the token is the only access control
  (§ 11).
  - _Acceptance_: `lsof -iTCP:8642 -sTCP:LISTEN` shows `127.0.0.1` only; the default config's
    resolved set contains no `script.*` action.
- **NFR-CORE-002**: All framework code shall be unit-testable without Fusion using
  `tests/fakes/adsk`; `pytest -q` shall pass on a machine without Fusion (§ 10).
  - _Acceptance_: `pytest -q` passes with `tests/fakes` on the path and no Fusion install.
- **NFR-CORE-003**: Third-party dependencies shall be only `mcp==2.3.0` and `uvicorn` as direct
  pins in `requirements.in`, fully pinned in `requirements.lock` for Fusion's bundled Python, and
  installed only into `De-Fusion/lib` (gitignored).
  - _Acceptance_: `requirements.in` has exactly those two lines; every non-blank line of
    `requirements.lock` matches `name==version` optionally followed by ` ; <marker>`; `git check-ignore De-Fusion/lib` succeeds.
- **NFR-CORE-004**: No source code from other Fusion MCP servers shall be copied (§ 1).
  - _Acceptance_: Code review of the diff finds no copied blocks; specs cite only the Fusion API.
- **NFR-CORE-005**: The add-in shall not block Fusion's UI: `run()` returns in under 1 s and the
  main-thread handler runs only queued items.
  - _Acceptance_: Live: Fusion's UI stays responsive during startup; a unit test times `start()`
    under 1 s with fakes.
