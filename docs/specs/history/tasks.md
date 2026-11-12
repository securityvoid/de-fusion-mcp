# tasks.md — docs/specs/history/
> De-Fusion MCP — `history` tool: timeline, snapshots, guarded undo, recipes

## Legend
- `[ ]` not started, `[x]` done.
- `[REQ-...]` requirements the task satisfies; § refers to `docs/specs/history/design.md`, "core §"
  to `docs/specs/core/design.md`.
- _Output_: files created or changed. _Verify_: exact commands; all must pass.
- All commands run from the repository root. Unit tests use `tests/fakes/adsk` (no Fusion).
- Live tasks run on this Mac with Fusion running and the De-Fusion add-in loaded.
- Phase 1 waits on the core framework (docs/specs/core/tasks.md Phase 2, TASK-CORE-004 to
  TASK-CORE-012), including core `TimelineItem` resolution and unions (core § 5.1, § 5.4), `Ctx`
  `allowed`/`units`/`attr_get` (core § 5.9) and `actions.call_inline` (core § 5.11). Phase 2 waits
  on the live harness (TASK-CORE-021) and on the sketch, model, document, inspect, assembly and
  script tool modules named in § 9.2.

## Phase 1 — Actions with unit tests
- [x] **TASK-HIS-001** [REQ-HIS-021]: Module skeleton, shared helpers, history fakes
  - Create `history.py` with § 2 helpers (`_item`, `_item_json`, `_with_collapsed`, `_timeline`,
    `_snapshots`, `_snapshot`, `_snapshot_json`). Extend `tests/fakes/adsk` with the § 9.1 timeline,
    group, snapshot and command-definition fakes.
  - _Reuse_: core `@action`, `Ctx.require_parametric`, `Ctx.ref`, `Ctx.page`, `ActionError`, core `TimelineItem` resolution (core § 5.4) and the core fake `adsk` package
  - _Output_: `De-Fusion/defusion/tools/history.py`, `tests/fakes/adsk/`, `tests/test_history.py`
  - _Verify_: `pytest -q tests/test_history.py -k "helpers or design_type or unsupported"`
- [x] **TASK-HIS-002** [REQ-HIS-001, REQ-HIS-002]: list and snapshot_list
  - § 3.1 (`list_items` registered as `list`, group insertion, marker/count) and § 3.2; `snapshot_list` builds summaries only for the requested page slice (test: 500 fake snapshots, `limit=10` builds 10).
  - _Reuse_: `_timeline`, `_item_json`, `_snapshots`, `_snapshot_json` from TASK-HIS-001; core `Ctx.page`
  - _Output_: `De-Fusion/defusion/tools/history.py`, `tests/test_history.py`
  - _Verify_: `pytest -q tests/test_history.py -k "list"`
- [x] **TASK-HIS-003** [REQ-HIS-003, REQ-HIS-004, REQ-HIS-005, REQ-HIS-006]: set_marker, reorder, set_suppressed, rename
  - § 4.1–4.4, including the expanded-group retry (§ 2.3) and the out-of-range and dependency
    errors.
  - _Reuse_: `_item`, `_item_json`, `_with_collapsed` from TASK-HIS-001; core `TimelineItem` resolution
  - _Output_: `De-Fusion/defusion/tools/history.py`, `tests/test_history.py`
  - _Verify_: `pytest -q tests/test_history.py -k "marker or reorder or suppress or rename"`
- [x] **TASK-HIS-004** [REQ-HIS-007, REQ-HIS-008, REQ-HIS-009]: group_add, group_delete, delete_after_marker
  - § 4.5, § 5.1, § 5.2; destructive actions tested with and without `confirm`.
  - _Reuse_: `_item`, `_item_json` from TASK-HIS-001; core `confirm` handling
  - _Output_: `De-Fusion/defusion/tools/history.py`, `tests/test_history.py`
  - _Verify_: `pytest -q tests/test_history.py -k "group or after_marker"`
- [x] **TASK-HIS-005** [REQ-HIS-010, REQ-HIS-011, REQ-HIS-012, REQ-HIS-013]: Snapshot actions
  - § 4.6, § 4.7, § 5.3, § 5.4, including index/name addressing and `AMBIGUOUS`.
  - _Reuse_: `_snapshots`, `_snapshot`, `_snapshot_json` from TASK-HIS-001
  - _Output_: `De-Fusion/defusion/tools/history.py`, `tests/test_history.py`
  - _Verify_: `pytest -q tests/test_history.py -k snapshot`
- [x] **TASK-HIS-006** [REQ-HIS-014]: Guarded undo
  - § 4.8 with `undo_group=False`; tests: plain undo, parametric→direct flip triggers one redo and
    `WRONG_DESIGN_TYPE`, direct design allowed (fake scripts `Transaction.Undo`/`Transaction.Redo`).
  - _Reuse_: core `Ctx.require_design`, `Ctx.ui`; command-definition fakes from TASK-HIS-001
  - _Output_: `De-Fusion/defusion/tools/history.py`, `tests/test_history.py`
  - _Verify_: `pytest -q tests/test_history.py -k undo`
- [x] **TASK-HIS-007** [REQ-HIS-015, REQ-HIS-016, REQ-HIS-018]: dump_recipe core, sketches and unknown steps
  - § 6, § 7.1, § 7.2, § 7.5, § 7.6: paging by timeline index, before/after roll, created-body
    names, `suppressed`, marker restored after a raising item, groups skipped, sketch create/replay
    pair using the sketch set's describe function, unknown fallback with `parameters` and `reason`,
    `model.parameter_create` steps on the first page in dependency order; a marker restore that raises or does not take effect returns `FUSION_ERROR` "marker restore failed: at <pos>, expected <saved>" (test: fake timeline whose `markerPosition` setter ignores the restore); a 21-item timeline with large sketches at the default `limit` returns a page under 64 KiB with `next_offset` set, not `RESULT_TOO_LARGE`.
  - _Reuse_: `_with_collapsed`, `_item_json` from TASK-HIS-001; the sketch set's importable describe function; core `Ctx.page`, `Ctx.attr_get`
  - _Output_: `De-Fusion/defusion/tools/history.py`, `tests/test_history.py`
  - _Verify_: `pytest -q tests/test_history.py -k "dump and (sketch or unknown or marker or page)"`
- [x] **TASK-HIS-008** [REQ-HIS-015, REQ-HIS-016]: Feature mapping
  - Every § 7.3 row with its conditions, reference forms, profile matching, the `MoveFeature`
    decomposition and `L`/`A`/`N` (`p.expression`, fixed-value fallback when a model parameter is
    referenced); a parametrised test dumps one fake of each row and validates each step's `params` against the
    registered `(tool, action)` pydantic model (update § 6/§ 7.3 names to the sketch/model sets'
    names if validation fails).
  - _Reuse_: `_unknown`, value formatting and reference helpers from TASK-HIS-007; core `Operation` mapping in `types.py`; action registry pydantic models
  - _Output_: `De-Fusion/defusion/tools/history.py`, `tests/test_history.py`
  - _Verify_: `pytest -q tests/test_history.py -k "dump and feature"`
- [x] **TASK-HIS-009** [REQ-HIS-017]: Coil dump
  - § 7.4: `defusion.request/1` params verbatim with token-free Refs, missing or malformed request
    → unknown, base sketch skipped, staleness by parameter role with `current` expressions.
  - _Reuse_: `_unknown`, value formatting from TASK-HIS-007; core `Ctx.attr_get` (core § 5.9)
  - _Output_: `De-Fusion/defusion/tools/history.py`, `tests/test_history.py`
  - _Verify_: `pytest -q tests/test_history.py -k "dump and coil"`
- [x] **TASK-HIS-010** [REQ-HIS-019, REQ-HIS-020]: apply_recipe_step
  - § 8: key checks, a non-`sketch`/`model` tool or a `destructive` target returns `INVALID_PARAMS` "<tool>.<action> cannot be a recipe step" (tests: `document.export` and `cam.post_process` steps run nothing), `call_inline`, `NOT_ALLOWED` under a profile without the
    target action, `INVALID_PARAMS` with schema, renames of entity and bodies, `suppressed`,
    mismatch warning, `stale` echo.
  - _Reuse_: core `actions.call_inline` (core § 5.11); `_all_bodies` from TASK-HIS-007
  - _Output_: `De-Fusion/defusion/tools/history.py`, `tests/test_history.py`
  - _Verify_: `pytest -q tests/test_history.py -k apply`

- [x] **TASK-HIS-013** [REQ-HIS-023, REQ-HIS-024]: Sketch-line axis and profile recovery
  - § 7.7: `SketchLine` case in `_axis_form`; `_profile_ref(profile)` on `inspect.profile_index`
    (remove `_profile_index`; unmatched → `_unnamed`); `_read_extrude`/`_read_revolve` call it.
    Tests: axis found / occurrence context /
    not found; profile one sketch (int, list), two sketches, unmatched → `unknown`; every emitted
    step validates against the registered `model.extrude`/`model.revolve` models.
  - Waits on TASK-CORE-032 and TASK-INS-009.
  - _Reuse_: `_axis_form`, `_unnamed`, `_name_ref`, `_guarded` in `history.py`; `inspect.profile_index` (TASK-INS-009)
  - _Output_: `De-Fusion/defusion/tools/history.py`, `tests/test_history.py`
  - _Verify_: `pytest -q tests/test_history.py -k "dump and (axis or profile)"`

## Phase 2 — Live verification
- [x] **TASK-HIS-011** [REQ-HIS-001, REQ-HIS-002, REQ-HIS-003, REQ-HIS-004, REQ-HIS-005, REQ-HIS-006, REQ-HIS-007, REQ-HIS-008, REQ-HIS-009, REQ-HIS-010, REQ-HIS-011, REQ-HIS-012, REQ-HIS-013, REQ-HIS-014, REQ-HIS-017, REQ-HIS-021, REQ-HIS-022]: Live timeline, snapshot, undo and coil-probe scenarios
  - Add the § 9.2 `LIVE_SCENARIOS` entries tagged `history.list` through `history.undo`, plus
    `coil_probe`, in the core § 10 tuple format. Run
    `coil_probe`; when the printed roles do not match § 7.4's substrings, update § 7.4 and the code
    and rerun TASK-HIS-009's tests. Fix any Fusion behaviour that differs from § 3–5 in code and
    design.
  - _Reuse_: sketch, model, assembly and script tool actions over the live harness (TASK-CORE-021)
  - _Output_: `De-Fusion/defusion/tools/history.py`, `docs/specs/history/design.md`
  - _Verify_: `python scripts/live_check.py --profile full --only history.<action>` for each of
    `list`, `set_marker`, `reorder`, `set_suppressed`, `rename`, `group_add`, `group_delete`,
    `delete_after_marker`, `snapshot_list`, `snapshot_add`, `snapshot_rename`,
    `snapshot_revert_pending`, `snapshot_delete`, `undo`; and `coil_probe` output read from
    `python scripts/live_check.py --profile full --only history.dump_recipe`
- [x] **TASK-HIS-012** [REQ-HIS-015, REQ-HIS-016, REQ-HIS-017, REQ-HIS-019, REQ-HIS-020, REQ-HIS-022]: Live recipe round trips
  - Add `recipe_part`, `apply_recipe_step` and one `recipe_coil_<coil_type>` scenario per remaining
    `model.coil` `coil_type` (§ 9.2); fix mapping or replay defects until body count, volume and
    bbox match.
  - _Reuse_: `dump_recipe`, `apply_recipe_step`; `document.create`/`document.close`; `inspect.list_bodies`, `inspect.physical_properties`, `inspect.bounds`; live harness (TASK-CORE-021)
  - _Output_: `De-Fusion/defusion/tools/history.py`
  - _Verify_: `python scripts/live_check.py --profile full --only history.dump_recipe`,
    `python scripts/live_check.py --profile full --only history.apply_recipe_step`,
    `python scripts/live_check.py --profile full --only history` (all PASS, exit 0) and
    `pytest -q tests/test_history.py`
- [x] **TASK-HIS-014** [REQ-HIS-023, REQ-HIS-024, REQ-HIS-022]: Live axis-line and multi-sketch profile round trips
  - Add the `recipe_axis_line` and `recipe_profiles` scenarios (§ 9.2, tag `history.dump_recipe`);
    fix mapping or replay defects until body count, volume and bbox match.
  - Waits on TASK-HIS-013, TASK-INS-009 and TASK-CORE-032.
  - _Reuse_: `recipe_part` comparison helper; `sketch.line`, `sketch.rectangle`, `model.revolve`, `script.execute_python`, `inspect.object_info`; live harness (TASK-CORE-021)
  - _Output_: `De-Fusion/defusion/tools/history.py`
  - _Verify_: `python scripts/live_check.py --profile full --only history.dump_recipe` (all PASS, exit 0) and `pytest -q tests/test_history.py`
