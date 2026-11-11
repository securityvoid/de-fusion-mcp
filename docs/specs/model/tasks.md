# tasks.md — docs/specs/model/
> De-Fusion MCP — `model` tool: features, primitives, surfaces, sheet metal, construction geometry, parameters, appearance and design settings

## Legend
- `[ ]` not started, `[x]` done.
- `[REQ-...]` requirements the task satisfies; § refers to `docs/specs/model/design.md`.
- _Output_: files created or changed. _Verify_: exact commands; all must pass.
- All commands run from the repository root. Unit tests use `tests/fakes/adsk` plus
  `tests/model_fakes.py` (no Fusion).
- Live tasks run on this Mac with Fusion running the De-Fusion add-in; the agent runs the commands
  itself.
- Every task waits on the core framework (docs/specs/core/tasks.md Phase 2, TASK-CORE-004 to
  TASK-CORE-012); the `Entity` type is core design § 5.4 (DEP-MDL-001, satisfied). Live tasks also wait on
  TASK-CORE-021 (live harness), TASK-CORE-022 (add-in running) and the sketch set's `create`,
  `circle`, `rectangle`, `line` actions (DEP-MDL-002).

## Phase 1 — Shared code and fakes
- [x] **TASK-MDL-001** [REQ-MDL-044, REQ-MDL-046]: Shared helpers and model fakes
  - Implement every helper of § 3.1 and the feature result of § 3.2 in `model_common.py`; create
    `model.py` importing the `model_*` modules and setting `LIVE_SCENARIOS`; create
    `tests/model_fakes.py` per § 14.1 (recording feature collections, removable members).
  - _Output_: `De-Fusion/defusion/tools/model.py`, `De-Fusion/defusion/tools/model_common.py`,
    `tests/model_fakes.py`, `tests/test_model_common.py`
  - _Reuse_: core `Ctx`, `ctx.fail`, `ctx.ref`, core enum `Operation`; `tests/fakes/adsk` (extended, not copied).
  - _Verify_: `pytest -q tests/test_model_common.py`

## Phase 2 — Actions (unit-tested)
- [x] **TASK-MDL-002** [REQ-MDL-001, REQ-MDL-002, REQ-MDL-003, REQ-MDL-004, REQ-MDL-044]: extrude, revolve, sweep, loft
  - Implement § 4.1–§ 4.4 in `model_features.py` with tests for each extent/option, required
    parameter errors and participant-body rules.
  - _Output_: `De-Fusion/defusion/tools/model_features.py`, `tests/test_model_features.py`
  - _Reuse_: `model_common` helpers (`coll`, `owner`, `participants`, `finish`, `expect`).
  - _Verify_: `pytest -q tests/test_model_features.py -k "extrude or revolve or sweep or loft"`
- [x] **TASK-MDL-003** [REQ-MDL-005, REQ-MDL-006, REQ-MDL-007, REQ-MDL-008, REQ-MDL-009, REQ-MDL-010, REQ-MDL-016, REQ-MDL-017, REQ-MDL-018, REQ-MDL-019, REQ-MDL-020, REQ-MDL-021, REQ-MDL-022]: Edge, face and body features
  - Implement § 4.5–§ 4.10 and § 4.12–§ 4.18 (fillet, chamfer incl. `createInput` fallback, shell,
    mirror, hole, patterns, draft, split_body, split_face, offset_faces, scale, move incl.
    `createInput` fallback, combine, rename).
    Tests: pattern totals (`quantity_one * quantity_two`, `circ_pattern` `quantity`) of 501 return
    `INVALID_PARAMS`, `30 x 20` returns `INVALID_PARAMS`, 500 succeeds.
  - _Output_: `De-Fusion/defusion/tools/model_features.py`, `tests/test_model_features.py`
  - _Reuse_: `model_common` helpers (`same_owner`, `uniform`, `coll`, `finish`, `require_api`).
  - _Verify_: `pytest -q tests/test_model_features.py -k "fillet or chamfer or shell or mirror or hole or pattern or draft or split or offset or scale or move or combine or rename"`
- [x] **TASK-MDL-004** [REQ-MDL-011]: Threads
  - Implement § 4.11 (`thread_query` levels, `list_threads`, `thread` with default internal flag,
    designation and class; left-hand `UNSUPPORTED`).
  - _Output_: `De-Fusion/defusion/tools/model_features.py`, `tests/test_model_features.py`
  - _Reuse_: `model_common` (`coll`, `finish`, `require_api`); core `ctx.page`.
  - _Verify_: `pytest -q tests/test_model_features.py -k thread`
- [x] **TASK-MDL-005** [REQ-MDL-012, REQ-MDL-013, REQ-MDL-014, REQ-MDL-015]: Coil
  - Implement § 5 in `model_coil.py`: validation (§ 5.3), text-command sequence (§ 5.4), cleanup on
    failure, participant roll, naming, request attribute with the exact format of § 5.6, result
    `parameters`. Tests assert the exact `executeTextCommand` list for every `coil_type` and
    every `section`, the absence of `CoilTaperAngle` for `spiral`, and the exact `req` dict passed to `ctx.attr_set`.
  - _Output_: `De-Fusion/defusion/tools/model_coil.py`, `tests/test_model_coil.py`
  - _Reuse_: `model_common` (`activated`, `origin_plane`, `positive`, `finish`); core `ctx.text_command`, `ctx.attr_set`, `ctx.require_parametric`.
  - _Verify_: `pytest -q tests/test_model_coil.py`
- [x] **TASK-MDL-006** [REQ-MDL-023, REQ-MDL-044]: Primitives
  - Implement § 6 (cylinder, sphere, torus) in `model_primitives.py`.
  - _Output_: `De-Fusion/defusion/tools/model_primitives.py`, `tests/test_model_primitives.py`
  - _Reuse_: extrude/revolve input helpers from `model_features.py`; `model_common.origin_plane`.
  - _Verify_: `pytest -q tests/test_model_primitives.py`
- [x] **TASK-MDL-007** [REQ-MDL-024, REQ-MDL-025]: Surfaces
  - Implement § 7 in `model_surfaces.py`.
  - _Output_: `De-Fusion/defusion/tools/model_surfaces.py`, `tests/test_model_surfaces.py`
  - _Reuse_: `model_common` (`same_owner`, `expect`, `cells_hint`, `require_api`).
  - _Verify_: `pytest -q tests/test_model_surfaces.py`
- [x] **TASK-MDL-008** [REQ-MDL-026, REQ-MDL-027, REQ-MDL-046]: Sheet metal
  - Implement § 8 in `model_sheet.py` with one `UNSUPPORTED` test per detected member.
  - _Output_: `De-Fusion/defusion/tools/model_sheet.py`, `tests/test_model_sheet.py`
  - _Reuse_: `model_common.require_api`, `finish`.
  - _Verify_: `pytest -q tests/test_model_sheet.py`
- [x] **TASK-MDL-009** [REQ-MDL-028, REQ-MDL-029, REQ-MDL-030]: Construction geometry and UCS
  - Implement § 9 in `model_construct.py`.
  - _Output_: `De-Fusion/defusion/tools/model_construct.py`, `tests/test_model_construct.py`
  - _Reuse_: `model_common` (`expect`, `require_api`); core `Plane`/`Axis` resolution.
  - _Verify_: `pytest -q tests/test_model_construct.py`
- [x] **TASK-MDL-010** [REQ-MDL-031, REQ-MDL-032, REQ-MDL-033]: Parameters
  - Implement § 10 in `model_params.py`.
  - _Output_: `De-Fusion/defusion/tools/model_params.py`, `tests/test_model_params.py`
  - _Reuse_: core `ctx.page`, `ctx.length_out`, `ctx.angle_out`.
  - _Verify_: `pytest -q tests/test_model_params.py`
- [x] **TASK-MDL-011** [REQ-MDL-034, REQ-MDL-035]: Materials and appearances
  - Implement § 11 in `model_appearance.py`.
  - _Output_: `De-Fusion/defusion/tools/model_appearance.py`, `tests/test_model_appearance.py`
  - _Reuse_: core `ctx.page`; one lookup function shared by materials and appearances.
  - _Verify_: `pytest -q tests/test_model_appearance.py`
- [x] **TASK-MDL-012** [REQ-MDL-036, REQ-MDL-037, REQ-MDL-038, REQ-MDL-039, REQ-MDL-040, REQ-MDL-041, REQ-MDL-042, REQ-MDL-043]: Design settings and destructive actions
  - Implement § 12, § 12.1 and § 13 in `model_design.py`.
  - _Output_: `De-Fusion/defusion/tools/model_design.py`, `tests/test_model_design.py`
  - _Reuse_: core `ctx.page`, `ctx.ref`; `model_common.require_api`.
  - _Verify_: `pytest -q tests/test_model_design.py`

## Phase 3 — Registration and live verification
- [x] **TASK-MDL-013** [REQ-MDL-045]: Registration check
  - Test that importing `defusion.tools.model` registers exactly the 63 actions with the access
    classes and timeouts of § 2.1, every sheet metal action starts with `sheet_`, and `box` /
    `delete_all` are absent.
  - _Output_: `tests/test_model_registration.py`
  - _Reuse_: the core action registry introspection used by core registration tests.
  - _Verify_: `pytest -q tests/test_model_registration.py && pytest -q tests/test_model_*.py`
- [x] **TASK-MDL-014** [REQ-MDL-001, REQ-MDL-002, REQ-MDL-003, REQ-MDL-004, REQ-MDL-005, REQ-MDL-006, REQ-MDL-007, REQ-MDL-008, REQ-MDL-009, REQ-MDL-010, REQ-MDL-011, REQ-MDL-012, REQ-MDL-013, REQ-MDL-015, REQ-MDL-016, REQ-MDL-017, REQ-MDL-018, REQ-MDL-019, REQ-MDL-020, REQ-MDL-021, REQ-MDL-022, REQ-MDL-023, REQ-MDL-024, REQ-MDL-025, REQ-MDL-026, REQ-MDL-027, REQ-MDL-028, REQ-MDL-029, REQ-MDL-030, REQ-MDL-031, REQ-MDL-032, REQ-MDL-033, REQ-MDL-034, REQ-MDL-035, REQ-MDL-036, REQ-MDL-037, REQ-MDL-038, REQ-MDL-039, REQ-MDL-040, REQ-MDL-041, REQ-MDL-042, REQ-MDL-043, REQ-MDL-044, REQ-MDL-047]: Live scenarios
  - Implement every scenario of § 14.2 in `model_live.py` in the listed order, then run them against
    the running add-in (reload it first with `server.restart_addin` so new code loads; `set_design_type` calls pass
    `confirm: true`). Fix code
    until all pass; when a feature-detected member is present but its live call fails, fix the call
    against the installed Fusion API (record the observed signature in design.md and the
    changelog).
  - _Output_: `De-Fusion/defusion/tools/model_live.py`
  - _Reuse_: core `scripts/live_check.py` client; `sketch` tool actions for input geometry.
  - _Verify_: `python scripts/live_check.py --only model.coil` then `for a in extrude revolve sweep loft fillet chamfer shell mirror hole rect_pattern circ_pattern thread_query thread list_threads coil draft split_body split_face offset_faces scale move combine rename cylinder sphere torus surface_patch surface_stitch surface_thicken surface_ruled surface_trim sheet_flange construction_plane construction_axis ucs parameter_create material_assign appearance_set color_set units_set find_attributes find_by_token visibility_set selection_set_create set_design_intent set_modeling_in_assembly activate_root compute_all remove_body delete_entities set_design_type; do python scripts/live_check.py --only model.$a || exit 1; done` (every model scenario except `model.create_configured_design`)
  - _Note_: `model.create_configured_design` is excluded: Fusion answers "Cannot create configuration top table as the user is restricted" on this licence-restricted account.
