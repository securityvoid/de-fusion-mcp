# requirements.md — docs/specs/cam/
> De-Fusion MCP — `cam` tool: Manufacture setups, operations, toolpath generation and post processing (`adsk.cam`)

## Overview
The `cam` tool drives Fusion's Manufacture product of the active document: listing setups and
operations, reading operation parameters, tools and toolpath state, creating setups (milling,
turning, additive) and operations (strategy + tool from a library or the document), generating
toolpaths with job semantics, and post processing to NC files. It obtains `adsk.cam.CAM` from the
document's products and never requires the user to switch to the Manufacture workspace. All
actions are `@action("cam", ...)` functions in `De-Fusion/defusion/tools/cam.py`; schema,
validation, Ref resolution, units, dispatch and jobs, undo grouping, results, errors, paging,
help and profiles come from the core framework. Section references (core §) point to
`docs/specs/core/design.md`; (§) alone points to this set's design.md.

## Operator ask
2026-11-10: "comprehensive Fusion MCP server ... union of the four servers"

## Dependencies
- **DEP-CAM-001**: Core set `docs/specs/core/` (action framework, dispatcher and job registry,
  Ref types, units, results, errors, paging, undo grouping, fake `adsk` package, live harness).
  Satisfied by core: `ctx.design` is the Design product even while Manufacture is active (core
  § 5.9), `CamSetup`/`CamOperation` resolve setups and operations (core § 5.4), and the dispatcher
  is not re-entrant during `adsk.doEvents()` (core § 4).
- **DEP-CAM-002**: The `sketch` and `model` tool sets provide `sketch.create`, `sketch.rectangle`
  and `model.extrude`, which the live scenarios use to build the fixture box. Only TASK-CAM-007 waits on them.

## Functional Requirements
- **REQ-CAM-001**: Every cam action shall obtain the CAM product through
  `document.products.itemByProductType("CAMProductType")` without a user workspace switch; read
  actions shall treat a missing CAM product as "no setups"; write actions shall create it by
  activating the Manufacture workspace transiently and restoring the previous workspace. No
  document → `NO_DOCUMENT`; a document without a Design product → `NO_DESIGN`; `adsk.cam`
  missing or the product not obtainable → `UNSUPPORTED` (§ 2).
  - _Acceptance_: Live: in a new design never opened in Manufacture, `list_setups` returns `total`
    0 and `create_setup` then succeeds. Unit (fake `adsk`): a read with no CAM product activates no
    workspace; a write activates `CAMEnvironment` once and re-activates the previous workspace,
    also when product lookup still fails (`UNSUPPORTED`); no document returns `NO_DOCUMENT`; a
    document without a Design product returns `NO_DESIGN`.
- **REQ-CAM-002**: Setups and operations shall be referenced through the core `CamSetup` /
  `CamOperation` types (core § 5.4: a name string, `{"name", "setup"?}`, a token, or the `{"type",
  "name", "setup"}` object every cam result emits), with `NOT_FOUND` and `AMBIGUOUS` listing
  candidates (§ 3).
  - _Acceptance_: The `operation` object returned by `create_operation` passed unchanged to
    `operation_info` resolves; an unknown setup name returns `NOT_FOUND`; two operations with the same name in different setups, referenced
    without `setup`, return `AMBIGUOUS` with two candidates.
- **REQ-CAM-003**: The cam tool shall provide `list_setups` (read), paged, each setup with its
  Ref, type, model Refs, operation count, whether all toolpaths are valid and the stock mode (§ 4.1).
  - _Acceptance_: After one milling `create_setup`, `list_setups` returns `total` 1 with `type`
    `"milling"`, the fixture body among `models` and `operations` 0.
- **REQ-CAM-004**: The cam tool shall provide `list_operations` (read), paged, over one setup or
  all setups, each with its Ref, strategy, tool summary and toolpath state (§ 4.2).
  - _Acceptance_: After `create_operation` (`face`), `list_operations` for the setup returns one
    item with `strategy` `"face"`, a `tool` with `number` and `description`, and
    `has_toolpath: false`.
- **REQ-CAM-005**: The cam tool shall provide `operation_info` (read) for one operation (strategy,
  setup, tool, toolpath status, parameters) or one setup (parameters), with parameters paged and
  optionally filtered by exact names (§ 4.3).
  - _Acceptance_: `operation_info` on the face operation returns `toolpath` with `has_toolpath`,
    `valid`, `state`, and `parameters.items` each with `name`, `title`, `expression`;
    `names ["tool_number"]` returns exactly that parameter; an unknown name returns
    `INVALID_PARAMS`; `setup` instead of `operation` returns the setup's parameters.
- **REQ-CAM-006**: The cam tool shall provide `post_process` (write, timeout 120 s) that posts a
  setup or a list of operations with either a `.cps` file path (executed as code) or a post-library
  name to an absolute output folder and returns the written NC file paths; operations without a valid
  toolpath are rejected (§ 4.4).
  - _Acceptance_: After `generate_toolpath`, `post_process` with the test post
    `tests/fixtures/posts/defusion_test.cps` writes at least one file into the given folder and
    lists it in `files`; with the library name `fanuc` it also lists a file; before generation it
    returns `INVALID_PARAMS` naming the operation; a relative folder returns `INVALID_PARAMS`;
    `program_name`, including the defaulted setup name, must satisfy the core file-name rule (`"../x"` returns `INVALID_PARAMS` and
    the folder is unchanged); under a readonly profile it returns `NOT_ALLOWED` and the fake
    `cam.postProcess` is never called.
- **REQ-CAM-007**: The cam tool shall provide `create_setup` (write) for `milling`, `turning` or
  `additive` (additive feature-detected, needing a machine from the machine library), with model
  bodies/occurrences, name, typed stock mode and offsets, and arbitrary setup parameters by name
  and expression (used for WCS); an unknown parameter name rejects the whole call (§ 4.5).
  - _Acceptance_: A milling setup with `stock_mode` `relative_box` and `stock_offset_sides`
    `"1 mm"` reads back `job_stockMode` `'default'` and `job_stockOffsetSides` 1 mm via
    `operation_info`; `parameters {"wcs_nope": "1"}` returns `INVALID_PARAMS` and `list_setups`
    count is unchanged; no models returns `INVALID_PARAMS`; `additive` without `machine` returns
    `INVALID_PARAMS`.
- **REQ-CAM-008**: The cam tool shall provide `create_operation` (write) from a strategy name
  compatible with the setup, a tool chosen from a tool library (by name or URL) or the document
  tool library by number, description or index, an optional name and parameters by name and
  expression (§ 4.6).
  - _Acceptance_: `create_operation {"setup": S, "strategy": "face", "tool": {"library": L,
    "index": 0}}` returns an operation Ref and tool summary; strategy `"nope"` returns
    `INVALID_PARAMS` whose hints include `face`; library `"__none__"` returns `NOT_FOUND` whose
    hints list library names.
- **REQ-CAM-009**: The cam tool shall provide `generate_toolpath` (write, timeout 600 s, not
  undo-grouped) for all operations, one setup or listed operations, optionally skipping valid
  ones, waiting for completion by default up to a 570 s deadline, after which it returns
  `completed: false` with the operation states (§ 4.7).
  - _Acceptance_: `generate_toolpath {"operations": [op]}` returns `completed: true` and the
    operation with `toolpath_valid: true`; `wait: false` returns `completed: false` with the operation states read at
    return time, without blocking; both `setup` and `operations` given returns `INVALID_PARAMS`; with a fake future that never
    completes and a short injected deadline the call returns `completed: false`.
- **REQ-CAM-010**: The cam tool module shall carry unit tests against the fake `adsk` package for
  every action and error path above, and `LIVE_SCENARIOS` covering every action against running
  Fusion (§ 5).
  - _Acceptance_: `pytest -q tests/test_cam.py` passes; `python scripts/live_check.py --only cam`
    prints PASS for every scenario in § 5.2 and exits 0.
