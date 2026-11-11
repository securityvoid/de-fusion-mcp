# tasks.md — docs/specs/sketch/
> De-Fusion MCP — `sketch` tool

## Legend
- `[ ]` not started, `[x]` done.
- `[REQ-...]` requirements the task satisfies; § refers to `docs/specs/sketch/design.md`, "core §"
  to `docs/specs/core/design.md`.
- _Output_: files created or changed. _Verify_: exact commands; all must pass.
- All commands run from the repository root. Unit tests use `tests/fakes/adsk` plus
  `tests/fakes/sketch.py` (no Fusion).
- Live verification runs on this Mac against the running add-in (Fusion installed, De-Fusion
  loaded); the agent runs the commands itself.
- Phases 1–2 wait on the core framework (core TASK-CORE-003 to TASK-CORE-012). Phase 3 also waits
  on core TASK-CORE-021 and TASK-CORE-022 (live harness, add-in running) and on model
  TASK-MDL-002 (`model.extrude`) and TASK-MDL-009 (`model.construction_plane`).

## Phase 1 — Foundation and read actions
- [x] **TASK-SK-001** [REQ-SK-020, REQ-SK-021]: Module skeleton, shared helpers and sketch fakes
  - Create `De-Fusion/defusion/tools/sketch.py` with `xy`, `check_owned`,
    `kind`, the § 2.4 type map, `curve_summary`, `draw_result`, `fully_constrained` (§ 2), an empty
    `LIVE_SCENARIOS = []`, and add `sketch` to the imports in `defusion/tools/__init__.py` if absent.
  - Create `tests/fakes/sketch.py` per § 10.1 (all fakes listed there).
  - Tests: output conversion in `in`;
    `check_owned` rejects a curve of another sketch with `INVALID_PARAMS` naming the param.
  - _Reuse_: core `@action`, `Length`/`Point2`/`SketchCurve`/`SketchPoint` types, `Ctx.ref`, `Ctx.length_out`, `Ctx.fail` and the core fake `adsk` package
  - _Output_: `De-Fusion/defusion/tools/sketch.py`, `tests/fakes/sketch.py`, `tests/test_sketch.py`
  - _Verify_: `pytest -q tests/test_sketch.py -k "helpers"`
- [x] **TASK-SK-002** [REQ-SK-001, REQ-SK-002]: `list` and `info` with `describe`
  - Implement `list_sketches` (action `list`, § 3.1) and `describe` + `info` (§ 3.2) with every
    constraint and dimension class mapping, `replayable: false` for unknown classes and foreign
    entities, and section paging.
  - Tests: root + child component sketches, `component` filter, paging; a fake sketch holding one of
    each curve type, each constraint class and each dimension class maps to the § 3.2 keys;
    `section: "curves", limit: 2` paging; driven dimension with no parameter; a fake sketch of 300
    curves and 300 constraints with default `info` args returns under 64 KiB or the
    `RESULT_TOO_LARGE` hint names `section`.
  - _Reuse_: § 2 helpers from TASK-SK-001; core `Ctx.page`, `Ctx.area_out`, `Ctx.angle_out`
  - _Output_: `De-Fusion/defusion/tools/sketch.py`, `tests/test_sketch.py`
  - _Verify_: `pytest -q tests/test_sketch.py -k "list or info or describe"`

## Phase 2 — Write actions
- [x] **TASK-SK-003** [REQ-SK-003, REQ-SK-019]: `create` and `delete`
  - Implement `create_sketch` + `create` (§ 4.1) incl. non-planar face check and occurrence context,
    and `delete` (§ 4.2).
  - Tests: origin plane name in a given component, construction plane, planar face, cylindrical
    face → `INVALID_PARAMS`, `name` applied; `delete` calls `deleteMe` and returns the prior Ref;
    `deleteMe` returning false → `FUSION_ERROR`.
  - _Reuse_: core `Plane`/`Component` resolution and `Ctx.component`; § 2 helpers
  - _Output_: `De-Fusion/defusion/tools/sketch.py`, `tests/test_sketch.py`
  - _Verify_: `pytest -q tests/test_sketch.py -k "create or delete"`
- [x] **TASK-SK-004** [REQ-SK-004, REQ-SK-005, REQ-SK-006, REQ-SK-009, REQ-SK-021]: `rectangle`, `circle`, `line`, `polygon`
  - Implement §§ 5.1, 5.2, 5.3, 5.6 with `construction` and compute deferral (§ 2.6).
  - Tests: exact Fusion call per mode; unused-parameter and missing-parameter `INVALID_PARAMS`;
    radius vs diameter exclusivity; zero-size rectangle; polyline point sharing and `closed`;
    consecutive equal points; polygon vertices for inscribed/circumscribed with rotation 0 and 15
    deg, equal constraints count `sides-1`; `construction: true` sets `isConstruction`.
  - _Reuse_: `draw_result`, `curve_summary` from TASK-SK-001; the § 5.3 polyline helper is reused by `polygon`
  - _Output_: `De-Fusion/defusion/tools/sketch.py`, `tests/test_sketch.py`
  - _Verify_: `pytest -q tests/test_sketch.py -k "rectangle or circle or line or polygon"`
- [x] **TASK-SK-005** [REQ-SK-007, REQ-SK-008]: `arc` and `spline`
  - Implement §§ 5.4, 5.5 incl. `UNSUPPORTED` branches for control-point splines.
  - Tests: both arc modes with exact calls and radians; sweep 0 and 360 rejected; fit spline closed;
    control spline degree 3 with 3 points → `INVALID_PARAMS`; closed control spline whose
    `isClosed` setter raises → `UNSUPPORTED`; missing `sketchControlPointSplines` → `UNSUPPORTED`.
  - _Reuse_: `draw_result` from TASK-SK-001
  - _Output_: `De-Fusion/defusion/tools/sketch.py`, `tests/test_sketch.py`
  - _Verify_: `pytest -q tests/test_sketch.py -k "arc or spline"`
- [x] **TASK-SK-006** [REQ-SK-010, REQ-SK-011, REQ-SK-012]: `constraint`, `auto_constrain`, `dimension`
  - Implement §§ 6.1–6.3 incl. default text points (angular: `T = X + 5 mm · unit(u1 + u2)`, § 6.3) and the angular quadrant rule.
  - Tests: each constraint type with each accepted input form → exact `gc` call; one rejected form
    per type; `fix` sets `isFixed`; `curvature` without `addCurvature` → `UNSUPPORTED`;
    `auto_constrain` result mapping for all three options, `None` result → `FUSION_ERROR`, missing
    API → `UNSUPPORTED`; each dimension type → exact `sd` call and orientation; `value`/`angle`
    misuse, `driven` with value → `INVALID_PARAMS`; expression assignment; parallel lines for
    angular → `INVALID_PARAMS`; lines crossing at both midpoints → `T` 5 mm from `X`, driving value in the quadrant containing `T`.
  - _Reuse_: `check_owned`, `kind`, `fully_constrained` from TASK-SK-001
  - _Output_: `De-Fusion/defusion/tools/sketch.py`, `tests/test_sketch.py`
  - _Verify_: `pytest -q tests/test_sketch.py -k "constraint or dimension"`
- [x] **TASK-SK-007** [REQ-SK-013, REQ-SK-014, REQ-SK-015, REQ-SK-016, REQ-SK-017]: `offset`, `trim`, `extend`, `project`, `set_construction`
  - Implement §§ 7.1–7.5.
  - Tests: offset call with direction point and cm distance, `distance <= 0`; trim/extend pass
    `createConstraints=True` and return remaining curves; project per entity kind, `linked: false`
    with and without `project2`, `cut_edges` with non-body input → `INVALID_PARAMS`, `intersect`
    collection, own-sketch curves rejected, no entities rejected; `set_construction` both values.
  - _Reuse_: `check_owned`, `draw_result` from TASK-SK-001; core `Edge`/`Face`/`Body` resolution
  - _Output_: `De-Fusion/defusion/tools/sketch.py`, `tests/test_sketch.py`
  - _Verify_: `pytest -q tests/test_sketch.py -k "offset or trim or extend or project or set_construction"`
- [x] **TASK-SK-008** [REQ-SK-018]: `replay` and `replay_into`
  - Implement `SketchDescription` and § 8 exactly (point reuse via `pmap`, origin mapping, three-
    point arcs, skip rules, compute deferral, fixed last, description units).
  - Tests: shared endpoints reuse SketchPoints (no duplicate points); origin maps to
    `originPoint`; non-replayable curve and its dependent constraint land in `skipped`; a Fusion
    exception in one constraint is skipped with its message; units `in` description converts to
    cm; `sketch` and `plane` both/neither → `INVALID_PARAMS`; `describe` → `replay_into` →
    `describe` on fakes gives equal curves, constraints and dimensions (ignoring `ref`).
  - _Reuse_: `create_sketch` (TASK-SK-003), `describe` (TASK-SK-002), the § 6.1/§ 6.3 constraint and dimension call tables (TASK-SK-006)
  - _Output_: `De-Fusion/defusion/tools/sketch.py`, `tests/test_sketch.py`
  - _Verify_: `pytest -q tests/test_sketch.py -k "replay"`
- [x] **TASK-SK-011** [REQ-SK-023, REQ-SK-024, REQ-SK-025, REQ-SK-026, REQ-SK-021]: `ellipse`, `slot`, `point`, `text`
  - Implement §§ 5.7–5.10 incl. `add_ellipse` and `add_text` as module functions, the alignment
    maps and the `UNSUPPORTED` branches; extend `tests/fakes/sketch.py` per § 10.1 (ellipses,
    texts, `sketchPoints.add`).
  - Tests: ellipse degenerate inputs → `INVALID_PARAMS` and exact `sketchEllipses.add` call; slot
    curve calls, shared points, 4 tangent + 1 equal constraint for both modes, `overall` too short
    and width 0 → `INVALID_PARAMS`; point list; text each mode's input calls, default box corner,
    parameter misuse per mode, missing `createInput2` and missing `setAsFitOnPath` →
    `UNSUPPORTED`.
  - _Reuse_: `draw_result`, `check_owned`, § 2.6 deferral from TASK-SK-001
  - _Output_: `De-Fusion/defusion/tools/sketch.py`, `tests/fakes/sketch.py`, `tests/test_sketch.py`
  - _Verify_: `pytest -q tests/test_sketch.py -k "ellipse or slot or point or text"`
- [x] **TASK-SK-012** [REQ-SK-027, REQ-SK-028, REQ-SK-029]: `fillet`, `delete_constraint`, `delete_dimension`
  - Implement §§ 7.6, 6.4, 6.5 and the § 2.9 `constraint_type`/`dimension_type` helpers (move the
    class tables of TASK-SK-002 into them).
  - Tests: fillet default end selection, explicit points, misuse → `INVALID_PARAMS`, exact
    `addFillet` call; delete duplicate/out-of-range index and `isDeletable` false →
    `INVALID_PARAMS` with nothing deleted, descending order, `deleteMe` false → `FUSION_ERROR`,
    result types and parameter name.
  - _Reuse_: `check_owned`, `fully_constrained` (TASK-SK-001); `describe` class tables (TASK-SK-002)
  - _Output_: `De-Fusion/defusion/tools/sketch.py`, `tests/test_sketch.py`
  - _Verify_: `pytest -q tests/test_sketch.py -k "fillet or delete_constraint or delete_dimension"`
- [x] **TASK-SK-013** [REQ-SK-030, REQ-SK-031]: `mirror` and `pattern`
  - Implement `reflect` (§ 2.8), § 7.7 using `build_curve` (§ 8.2) and § 7.8.
  - Tests: reflection math; mirror of a chain shares points, points on the line reused without
    symmetry, arc and ellipse mirrored, unsupported curve type and non-line mirror line →
    `INVALID_PARAMS`, symmetry and equal call counts, `constrain: false`; pattern matrices for
    rectangular with `direction` 30 deg and circular 360 / 90 deg totals, parameter misuse per type,
    501 instances → `INVALID_PARAMS`, missing `Sketch.copy` → `UNSUPPORTED`.
  - _Reuse_: `build_curve` (TASK-SK-008), `add_ellipse` (TASK-SK-011), `draw_result` (TASK-SK-001)
  - _Output_: `De-Fusion/defusion/tools/sketch.py`, `tests/test_sketch.py`
  - _Verify_: `pytest -q tests/test_sketch.py -k "mirror or pattern or reflect"`
- [x] **TASK-SK-014** [REQ-SK-032, REQ-SK-002, REQ-SK-018]: `info`/`replay` for ellipses, texts and owned curves
  - Extend `describe` (§ 3.2: ellipse keys, `owner`, `owned`, `texts` section) with
    `owned_curves` (§ 2.8), and `replay_into` (§ 8.2 steps 3–5, 7–8: ellipse, owned-curve mapping,
    texts, skip rules, `texts` count).
  - Tests: `describe` of an ellipse with and without axis lines, each text definition class, box
    corners of a 30 deg text, `owned` constraints; `replay_into` maps owned curves without drawing
    them, skips `owned` constraints silently, records an unmapped path text as skipped; `describe`
    → `replay_into` → `describe` equality including texts and a standalone point.
  - _Reuse_: `add_ellipse`, `add_text` (TASK-SK-011); `describe`, `replay_into` (TASK-SK-002,
    TASK-SK-008)
  - _Output_: `De-Fusion/defusion/tools/sketch.py`, `tests/fakes/sketch.py`, `tests/test_sketch.py`
  - _Verify_: `pytest -q tests/test_sketch.py -k "info or describe or replay"`

## Phase 3 — Live verification
- [x] **TASK-SK-009** [REQ-SK-022]: Live scenarios
  - Fill `LIVE_SCENARIOS` with one `(action_name, "sketch.<action_name>", async fn(session))` per
    action per § 10.2 (fixtures exactly as listed there), each asserting
    the acceptance values of its requirement through `info`.
  - Test `test_live_scenarios_cover_actions`: the scenario names equal the set of registered
    `sketch` action names (28) and each tag equals `"sketch." + name`.
  - _Reuse_: core § 10 `LIVE_SCENARIOS` format and `live_check.call` (TASK-CORE-021); `model.extrude`,
    `model.construction_plane` for fixtures
  - _Output_: `De-Fusion/defusion/tools/sketch.py`, `tests/test_sketch.py`
  - _Verify_: `pytest -q tests/test_sketch.py -k live_scenarios`
- [x] **TASK-SK-010** [REQ-SK-001, REQ-SK-002, REQ-SK-003, REQ-SK-004, REQ-SK-005, REQ-SK-006, REQ-SK-007, REQ-SK-008, REQ-SK-009, REQ-SK-010, REQ-SK-011, REQ-SK-012, REQ-SK-013, REQ-SK-014, REQ-SK-015, REQ-SK-016, REQ-SK-017, REQ-SK-018, REQ-SK-019, REQ-SK-020, REQ-SK-021, REQ-SK-022, REQ-SK-023, REQ-SK-024, REQ-SK-025, REQ-SK-026, REQ-SK-027, REQ-SK-028, REQ-SK-029, REQ-SK-030, REQ-SK-031, REQ-SK-032]: Run live scenarios in Fusion
  - Reload the add-in with `server.restart_addin` (or restart Fusion) so the new module loads, then
    run each action's scenario; fix code (not scenarios' expected values) until all pass. If the
    angled-text round trip shows Fusion rotating box text about a point other than the box centre,
    or `pattern` copies land in model space, correct `describe`/`add_text` or the § 7.8 matrix.
  - _Reuse_: `scripts/live_check.py` (core TASK-CORE-021); `server.restart_addin`
  - _Output_: `De-Fusion/defusion/tools/sketch.py` (fixes only)
  - _Verify_: `for a in list info create delete rectangle circle line arc spline polygon ellipse slot point text fillet delete_constraint delete_dimension mirror pattern constraint dimension offset trim extend project set_construction replay; do python scripts/live_check.py --only sketch.$a || exit 1; done` (every sketch scenario except `sketch.auto_constrain`)
  - _Note_: `sketch.auto_constrain` is excluded: Fusion answers "AutoConstrain is not available since user is restricted" on this licence-restricted account.
