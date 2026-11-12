# tasks.md — docs/specs/inspect/
> De-Fusion MCP — `inspect` tool

## Legend
- `[ ]` not started, `[x]` done.
- `[REQ-...]` requirements the task satisfies; § refers to `docs/specs/inspect/design.md`, "core §"
  to `docs/specs/core/design.md`.
- _Output_: files created or changed. _Verify_: exact commands; all must pass.
- All commands run from the repository root. Unit tests use `tests/fakes/adsk` (no Fusion); fakes
  are extended additively.
- Live tasks run on this Mac with Fusion installed and the De-Fusion add-in running.
- Every task waits on the core framework (docs/specs/core/tasks.md Phase 2); `Entity` is core
  § 5.4 (DEP-INS-002, satisfied).

## Phase 1 — Read actions
- [x] **TASK-INS-001** [REQ-INS-001, REQ-INS-002, REQ-INS-015]: Module, helpers, summary, object_info
  - Create `inspect.py` with the § 1 literals and helpers (`_pt`, `_vec`, `_bbox`, `_bodies`,
    `_surface`, `_curve`, `_index_of`), `summary` (§ 2.1) and `object_info` (§ 2.2, every table row).
  - _Reuse_: core `Ctx` helpers (`ref`, `length_out`, `area_out`, `volume_out`, `mass_out`, `page`), core types and fakes (TASK-CORE-003).
  - _Output_: `De-Fusion/defusion/tools/inspect.py`, `tests/fakes/adsk/fusion.py`,
    `tests/test_inspect.py`
  - _Verify_: `pytest -q tests/test_inspect.py -k "summary or object_info or helpers"`
- [x] **TASK-INS-002** [REQ-INS-003, REQ-INS-004, REQ-INS-005, REQ-INS-015]: list_bodies, list_faces, list_edges
  - Implement § 2.3–2.5 with page-slice row building; surface and curve type maps.
  - _Reuse_: `_bodies`, `_surface`, `_curve`, `_pt`, `_vec` (TASK-INS-001); `ctx.page`.
  - _Output_: `De-Fusion/defusion/tools/inspect.py`, `tests/fakes/adsk/fusion.py`,
    `tests/test_inspect.py`
  - _Verify_: `pytest -q tests/test_inspect.py -k "list_bodies or list_faces or list_edges"`
- [x] **TASK-INS-003** [REQ-INS-006, REQ-INS-007]: geometry, bounds
  - Implement § 2.6 (face/edge/vertex/body detail, adjacency) and § 2.7 (default and `precise`).
  - _Reuse_: `_surface`, `_curve`, `_index_of`, `_bbox` (TASK-INS-001).
  - _Output_: `De-Fusion/defusion/tools/inspect.py`, `tests/fakes/adsk/core.py`,
    `tests/fakes/adsk/fusion.py`, `tests/test_inspect.py`
  - _Verify_: `pytest -q tests/test_inspect.py -k "geometry or bounds"`
- [x] **TASK-INS-004** [REQ-INS-008, REQ-INS-009, REQ-INS-010]: measure_distance, measure_angle, physical_properties
  - Implement § 2.8–2.10, including totals, density/moment unit conversion and the
    `physicalProperties` fallback.
  - _Reuse_: `_pt`, `_vec` (TASK-INS-001); core `Ctx` unit converters.
  - _Output_: `De-Fusion/defusion/tools/inspect.py`, `tests/fakes/adsk/core.py`,
    `tests/fakes/adsk/fusion.py`, `tests/test_inspect.py`
  - _Verify_: `pytest -q tests/test_inspect.py -k "measure or physical"`
- [x] **TASK-INS-005** [REQ-INS-011, REQ-INS-013, REQ-INS-014]: interference, get_selection, section_analysis
  - Implement § 2.11, § 2.13 and § 2.14 with the `UNSUPPORTED` feature detection; `interference` without `entities` returns
    `INVALID_PARAMS` (count and hint to pass `entities`) above 200 bodies.
  - _Reuse_: `_bodies`, `_bbox` (TASK-INS-001); core `Plane` and `Length` types.
  - _Output_: `De-Fusion/defusion/tools/inspect.py`, `tests/fakes/adsk/core.py`,
    `tests/fakes/adsk/fusion.py`, `tests/test_inspect.py`
  - _Verify_: `pytest -q tests/test_inspect.py -k "interference or selection or section"`
- [x] **TASK-INS-006** [REQ-INS-012]: compare_meshes
  - Implement § 2.12: `_triangles` (feature-detected accessors, proxy transform rule),
    `_sample_points`, grid `_deviation`, `_stats`, the action.
  - _Reuse_: `_pt`, `_bbox` (TASK-INS-001); `math` and stdlib only (no numpy).
  - _Output_: `De-Fusion/defusion/tools/inspect.py`, `tests/fakes/adsk/fusion.py`,
    `tests/test_inspect.py`
  - _Verify_: `pytest -q tests/test_inspect.py -k "deviation or stats or sample or compare_meshes"`

- [x] **TASK-INS-009** [REQ-INS-017, REQ-INS-015]: object_info profiles and axis
  - § 2.2.1: public `profile_index`, `profile_loops`; extend `object_info` for `ExtrudeFeature` and
    `RevolveFeature` (`profiles`, revolve `axis`); extend the fakes per § 3.1 additively; tests per
    § 3.1; curve records tagged with profile position and loop index, returned via the core paging helper (`ctx.page` with `build`, so only the requested slice is built; `limit` default 25, `offset`),
    spline/ellipse sampled at a fixed 16 points; tests: every REQ-INS-017 acceptance case.
  - _Reuse_: `_pt`, `_vec`, `_index_of`, `_surface`-style per-key guard in `object_info` (TASK-INS-001); `ctx.length_out`
  - _Output_: `De-Fusion/defusion/tools/inspect.py`, `tests/fakes/adsk/core.py`, `tests/fakes/adsk/fusion.py`, `tests/test_inspect.py`
  - _Verify_: `pytest -q tests/test_inspect.py -k "object_info or profile"`

## Phase 2 — Live
- [x] **TASK-INS-007** [REQ-INS-016]: LIVE_SCENARIOS
  - Add the § 3.2 scenarios (boxes via `live_check.box`, core design § 10) to `inspect.py`. Waits on the `sketch`, `model` and
    `document` tool modules (DEP-INS-003) and core TASK-CORE-021.
  - _Reuse_: core `scripts/live_check.call` (TASK-CORE-021); `sketch.create`, `sketch.rectangle`,
    `model.extrude`, `document.export`, `document.import_mesh`.
  - _Output_: `De-Fusion/defusion/tools/inspect.py`
  - _Verify_: `pytest -q tests/test_inspect.py` and
    `python -c "import sys; sys.path[:0]=['De-Fusion','tests/fakes']; from defusion.tools import inspect; assert len(inspect.LIVE_SCENARIOS) == 14"`
- [x] **TASK-INS-008** [REQ-INS-001, REQ-INS-002, REQ-INS-003, REQ-INS-004, REQ-INS-005, REQ-INS-006, REQ-INS-007, REQ-INS-008, REQ-INS-009, REQ-INS-010, REQ-INS-011, REQ-INS-012, REQ-INS-013, REQ-INS-014, REQ-INS-015, REQ-INS-016]: Live verification
  - Reload the add-in (`server.restart_addin`), run the inspect scenarios, fix failures until all
    pass. A `section_analysis` `UNSUPPORTED` result is a pass for that scenario. As a one-off
    diagnostic, list the design's analysis API names with `script.execute_python` under
    `--profile full` (`print([n for n in dir(design) if 'nalys' in n])`) and report them to the
    operator; this is not a verify condition.
  - _Reuse_: TASK-INS-007 scenarios; `server.restart_addin`; `script.execute_python` for API probing.
  - _Output_: `De-Fusion/defusion/tools/inspect.py`
  - _Verify_: `python scripts/live_check.py --only inspect`
- [x] **TASK-INS-010** [REQ-INS-017, REQ-INS-016]: Live profiles and axis
  - Waits on TASK-INS-009 and TASK-CORE-032. Extend the `object_info` scenario per § 3.2 (the scenario count stays 14); fix any Fusion difference from § 2.2.1 in code and design.
  - _Reuse_: `live_check.box`, `sketch.create`, `sketch.line`, `sketch.rectangle`, `model.extrude`, `model.revolve`, `model.construction_plane`
  - _Output_: `De-Fusion/defusion/tools/inspect.py`, `docs/specs/inspect/design.md`
  - _Verify_: `python scripts/live_check.py --only inspect.object_info` and `python scripts/live_check.py --only inspect`
