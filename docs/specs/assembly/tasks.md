# tasks.md — docs/specs/assembly/
> De-Fusion MCP — `assembly` tool: components, occurrences, joints, as-built joints, rigid groups and contact sets

Legend: `- [ ]` open, `- [x]` done. Each task names the requirements it satisfies, its output files
and the exact commands that verify it. Run commands from the repository root. Phase 1 waits on the
core framework (docs/specs/core/tasks.md Phase 2): `@action`, `Ctx`, the § 5.4 types, `ctx.page`
and `tests/fakes/adsk`. Every action calls `ctx.require_design()` first (design § 1).

## Phase 1 — Module, fakes and read actions
- [x] **TASK-ASM-001** [REQ-ASM-001, REQ-ASM-002, REQ-ASM-003, REQ-ASM-011]: Create
  `assembly.py` with the § 2 definitions and the read actions `list_components`,
  `list_occurrences`, `contact_get`; extend the fakes with the § 4.1 classes they need; add their
  unit tests.
  - _Reuse_: core `@action`, `Ctx.page`, `Ctx.ref`, `Ctx.length_out` and the core fake `adsk` package
  - _Output_: `De-Fusion/defusion/tools/assembly.py`, `tests/fakes/adsk/fusion.py`,
    `tests/test_assembly.py`
  - _Verify_: `pytest -q tests/test_assembly.py -k "list_components or list_occurrences or contact_get"`

## Phase 2 — Write actions
- [x] **TASK-ASM-002** [REQ-ASM-004, REQ-ASM-011]: Implement `create_component` (§ 3.4) with unit
  tests for empty, named, parented, with bodies, `bodies: []` and `UNSUPPORTED`.
  - _Reuse_: core Ref resolution of `Body`/`Occurrence` and undo grouping; `_occ_out` from TASK-ASM-001
  - _Output_: `De-Fusion/defusion/tools/assembly.py`, `tests/test_assembly.py`
  - _Verify_: `pytest -q tests/test_assembly.py -k create_component`
- [x] **TASK-ASM-003** [REQ-ASM-005, REQ-ASM-011]: Implement `JointGeo` resolution `_geometry`
  (§ 3.5) with unit tests for every field (incl. `joint_origin` passed through without a
  `createBy*` call), keypoint mapping and every proxy-rule branch.
  - _Reuse_: core Ref resolution of `Face`/`Edge`/`Vertex`/`SketchCurve`/`SketchPoint`/`JointOrigin`/`Occurrence`; `ctx.fail` with `candidates`
  - _Output_: `De-Fusion/defusion/tools/assembly.py`, `tests/fakes/adsk/fusion.py`,
    `tests/test_assembly.py`
  - _Verify_: `pytest -q tests/test_assembly.py -k geometry`
- [x] **TASK-ASM-004** [REQ-ASM-006, REQ-ASM-007, REQ-ASM-011]: Implement `joint` (§ 3.6) with
  `_set_motion`, `_apply_limits` (§ 3.7) and `_joint_out`; unit tests for all seven motion calls,
  offset/angle/flip, `second_axis` misuse, same-occurrence error and the limits table.
  - _Reuse_: `_geometry` from TASK-ASM-003; core `Length`/`Angle` `.value_input()`, `.cm`, `.rad`
  - _Output_: `De-Fusion/defusion/tools/assembly.py`, `tests/test_assembly.py`
  - _Verify_: `pytest -q tests/test_assembly.py -k "joint or limits"`
- [x] **TASK-ASM-005** [REQ-ASM-007, REQ-ASM-008, REQ-ASM-009, REQ-ASM-011]: Implement
  `as_built_joint` (§ 3.8) reusing `_set_motion`/`_apply_limits` (incl. the `joint_origin`
  `UNSUPPORTED` branch), and `rigid_group` (§ 3.9), with unit tests.
  - _Reuse_: `_geometry`, `_set_motion`, `_apply_limits`, `_joint_out` from TASK-ASM-003/004
  - _Output_: `De-Fusion/defusion/tools/assembly.py`, `tests/test_assembly.py`
  - _Verify_: `pytest -q tests/test_assembly.py -k "as_built_joint or rigid_group"`
- [x] **TASK-ASM-006** [REQ-ASM-010, REQ-ASM-011]: Implement `contact_set` (§ 3.10) with unit tests
  for the three modes, create, suppress, create `UNSUPPORTED` and the empty-call error.
  - _Reuse_: `contact_get` analysis-mode helper from TASK-ASM-001; core `ContactSet` Ref resolution
  - _Output_: `De-Fusion/defusion/tools/assembly.py`, `tests/test_assembly.py`
  - _Verify_: `pytest -q tests/test_assembly.py -k contact_set && pytest -q tests/test_assembly.py`

## Phase 3 — Live verification
- [x] **TASK-ASM-007** [REQ-ASM-001, REQ-ASM-002, REQ-ASM-003, REQ-ASM-004, REQ-ASM-005,
  REQ-ASM-006, REQ-ASM-007, REQ-ASM-008, REQ-ASM-009, REQ-ASM-010, REQ-ASM-011]: Add
  `LIVE_SCENARIOS` per design § 4.2 and run them against the running add-in; fix any API mismatch in
  `assembly.py` (e.g. contact-set member attribute, keypoint rules) until all pass. Waits on the
  core live harness (`scripts/live_check.py`) and on `sketch.create`, `sketch.rectangle` and
  `model.extrude` (DEP-ASM-002), and on `script.execute_python` for the joint-origin fixture.
  - _Reuse_: core `scripts/live_check.py` harness and `live_check.call`; `sketch.create`,
    `sketch.rectangle`, `model.extrude` for fixture boxes
  - _Output_: `De-Fusion/defusion/tools/assembly.py`
  - _Verify_: `python scripts/live_check.py --only assembly.create_component`,
    `python scripts/live_check.py --only assembly.list_components`,
    `python scripts/live_check.py --only assembly.list_occurrences`,
    `python scripts/live_check.py --profile full --only assembly.joint`,
    `python scripts/live_check.py --only assembly.as_built_joint`,
    `python scripts/live_check.py --only assembly.rigid_group`,
    `python scripts/live_check.py --only assembly.contact_get`,
    `python scripts/live_check.py --only assembly.contact_set`, then
    `python scripts/live_check.py --profile full --only assembly` exits 0.
