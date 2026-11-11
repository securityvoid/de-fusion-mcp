# tasks.md — docs/specs/cam/
> De-Fusion MCP — `cam` tool: Manufacture setups, operations, toolpath generation and post processing (`adsk.cam`)

Legend: `- [ ]` open, `- [x]` done. Each task names the requirements it satisfies, its output files
and the exact commands that verify it. Run commands from the repository root. Phase 1 waits on the
core framework (docs/specs/core/tasks.md Phase 2): `@action` (with `timeout` and `undo_group`),
`Ctx`, the § 5.4 types, `ctx.page`, the dispatcher job registry and `tests/fakes/adsk`.

## Phase 1 — Product access, refs and fakes
- [x] **TASK-CAM-001** [REQ-CAM-001, REQ-CAM-002, REQ-CAM-010]: Create `cam.py` with `_product`,
  `_cam` (§ 2), the CAM output refs `_setup_ref`/`_op_ref` (§ 3) and the shared helpers
  (`_type_name`, `_state`, `_tool_out`, `_param_out`, `_set_param`, `_walk`, `_locations`,
  `_basename`, `_lib_find`); create the `adsk.cam` fake (§ 5.1); unit tests.
  - _Reuse_: core `Ctx.document`, `Ctx.ui`, `Ctx.fail` with `candidates`, `Ctx.length_out`; core
    `CamSetup`/`CamOperation` types; core fake `adsk` package
  - _Output_: `De-Fusion/defusion/tools/cam.py`, `tests/fakes/adsk/cam.py`, `tests/test_cam.py`
  - _Verify_: `pytest -q tests/test_cam.py -k "cam_product or refs"`

## Phase 2 — Actions
- [x] **TASK-CAM-002** [REQ-CAM-003, REQ-CAM-004, REQ-CAM-005, REQ-CAM-010]: Implement
  `list_setups`, `list_operations`, `operation_info` (§ 4.1–4.3) with unit tests.
  - _Reuse_: `_cam(create=False)`, `_setup_ref`, `_op_ref`, `_tool_out`, `_param_out`, `_state`
    from TASK-CAM-001; core `ctx.page`
  - _Output_: `De-Fusion/defusion/tools/cam.py`, `tests/test_cam.py`
  - _Verify_: `pytest -q tests/test_cam.py -k "list_setups or list_operations or operation_info"`
- [x] **TASK-CAM-003** [REQ-CAM-007, REQ-CAM-010]: Implement `create_setup` (§ 4.5) including
  machine lookup, the stock table and `parameters`, with unit tests.
  - _Reuse_: `_cam(create=True)`, `_set_param`, `_locations` from TASK-CAM-001; core `Length.expr`
  - _Output_: `De-Fusion/defusion/tools/cam.py`, `tests/test_cam.py`
  - _Verify_: `pytest -q tests/test_cam.py -k create_setup`
- [x] **TASK-CAM-004** [REQ-CAM-008, REQ-CAM-010]: Implement `create_operation` (§ 4.6) with
  `ToolSpec`/`_find_tool`, strategy check and `parameters`, with unit tests.
  - _Reuse_: `_cam`, `_op_ref`, `_lib_find`, `_set_param`, `_tool_out` from TASK-CAM-001
  - _Output_: `De-Fusion/defusion/tools/cam.py`, `tests/test_cam.py`
  - _Verify_: `pytest -q tests/test_cam.py -k create_operation`
- [x] **TASK-CAM-005** [REQ-CAM-009, REQ-CAM-010]: Implement `generate_toolpath` (§ 4.7) with the
  completion-probe wait loop that stops at its deadline and returns `completed: false`, declared
  `timeout=600, undo_group=False`, with unit tests (fake future that never completes, short deadline).
  - _Reuse_: `_cam`, `_op_ref`, `_state` from TASK-CAM-001; core dispatcher job
    registry for the over-timeout path
  - _Output_: `De-Fusion/defusion/tools/cam.py`, `tests/test_cam.py`
  - _Verify_: `pytest -q tests/test_cam.py -k generate_toolpath`
- [x] **TASK-CAM-006** [REQ-CAM-006, REQ-CAM-010]: Implement `post_process` (§ 4.4; `access="write"`,
  `program_name` checked with the core shared file-name rule), file and library routes, and write the test post `tests/fixtures/posts/defusion_test.cps` (§ 5.2); unit
  tests.
  - _Reuse_: `_cam(create=False)`, `_op_ref`, `_lib_find` from TASK-CAM-001; core shared
    file-name rule
  - _Output_: `De-Fusion/defusion/tools/cam.py`, `tests/fixtures/posts/defusion_test.cps`,
    `tests/test_cam.py`
  - _Verify_: `pytest -q tests/test_cam.py -k post_process && pytest -q tests/test_cam.py`

## Phase 3 — Live verification
- [x] **TASK-CAM-007** [REQ-CAM-001, REQ-CAM-002, REQ-CAM-003, REQ-CAM-004, REQ-CAM-005,
  REQ-CAM-006, REQ-CAM-007, REQ-CAM-008, REQ-CAM-009, REQ-CAM-010]: Add `LIVE_SCENARIOS` per
  design § 5.2 and run them against the running add-in; correct any API mismatch found
  (parameter names, library URL walking, NC program parameters, future completion property) in
  `cam.py` and its fake until all pass. Waits on the core live harness and on `sketch.create`,
  `sketch.rectangle` and `model.extrude` (DEP-CAM-002).
  - _Reuse_: core `scripts/live_check.py` harness and `live_check.call`; `sketch.create`,
    `sketch.rectangle`, `model.extrude` for the fixture box
  - _Output_: `De-Fusion/defusion/tools/cam.py`, `tests/fakes/adsk/cam.py`
  - _Verify_: `python scripts/live_check.py --only cam.list_setups`,
    `python scripts/live_check.py --only cam.create_setup`,
    `python scripts/live_check.py --only cam.create_operation`,
    `python scripts/live_check.py --only cam.list_operations`,
    `python scripts/live_check.py --only cam.operation_info`,
    `python scripts/live_check.py --only cam.generate_toolpath`,
    `python scripts/live_check.py --only cam.post_process`, then
    `python scripts/live_check.py --only cam` exits 0 and `pytest -q tests/test_cam.py` passes.
