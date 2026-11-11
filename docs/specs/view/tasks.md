# tasks.md — docs/specs/view/
> De-Fusion MCP — `view` tool

## Legend
- `[ ]` not started, `[x]` done.
- `[REQ-...]` requirements the task satisfies; § refers to `docs/specs/view/design.md`, "core §"
  to `docs/specs/core/design.md`.
- _Output_: files created or changed. _Verify_: exact commands; all must pass.
- All commands run from the repository root. Unit tests use `tests/fakes/adsk` (no Fusion); fakes
  are extended additively.
- Live tasks run on this Mac with Fusion installed and the De-Fusion add-in running.
- Every task waits on the core framework (docs/specs/core/tasks.md Phase 2).

## Phase 1 — Actions
- [x] **TASK-VIEW-001** [REQ-VIEW-001]: Module and literals
  - Create `view.py` with the § 1 literals, maps and constants.
  - _Reuse_: core `errors.ActionError`.
  - _Output_: `De-Fusion/defusion/tools/view.py`, `tests/test_view.py`
  - _Verify_: `pytest -q tests/test_view.py -k literals`
- [x] **TASK-VIEW-002** [REQ-VIEW-005, REQ-VIEW-006]: get_camera and set_camera
  - Implement `_unit_names`, `_camera_dict`, `_apply_view`, `_restore`, `get_camera` (§ 2.5),
    `CameraSnapshot` and `set_camera` (§ 2.6) with orbit/pan/zoom math and restore on failure.
  - _Reuse_: core `Ctx.length_out`/`angle_out`, `Length`, `Angle` types; fakes of TASK-CORE-003.
  - _Output_: `De-Fusion/defusion/tools/view.py`, `tests/fakes/adsk/core.py`, `tests/test_view.py`
  - _Verify_: `pytest -q tests/test_view.py -k "camera"`
- [x] **TASK-VIEW-003** [REQ-VIEW-001, REQ-VIEW-002, REQ-VIEW-003]: capture
  - Implement `_save_png` with the § 2.3 temp directory, option feature detection;
    `capture` (§ 2.1) with the § 2.2 restore.
  - _Reuse_: `_apply_view`, `_restore`, `_camera_dict` (TASK-VIEW-002); core `Image`.
  - _Output_: `De-Fusion/defusion/tools/view.py`, `tests/fakes/adsk/core.py`, `tests/test_view.py`
  - _Verify_: `pytest -q tests/test_view.py -k capture`
- [x] **TASK-VIEW-004** [REQ-VIEW-004, REQ-VIEW-002, REQ-VIEW-003]: view_sheet
  - Implement § 2.4 (four ortho fitted tiles returned as four ImageContent items, restore).
  - _Reuse_: `_save_png` (TASK-VIEW-003), `_apply_view`, `_restore`.
  - _Output_: `De-Fusion/defusion/tools/view.py`, `tests/test_view.py`
  - _Verify_: `pytest -q tests/test_view.py -k sheet`

## Phase 2 — Live
- [x] **TASK-VIEW-005** [REQ-VIEW-008]: LIVE_SCENARIOS
  - Add `_box` and the § 3.2 scenarios to `view.py`. Waits on sketch TASK-SK-003 and
    TASK-SK-004 (`sketch.create`, `sketch.rectangle`), model TASK-MDL-002 (`model.extrude`)
    (DEP-VIEW-002) and core TASK-CORE-021.
  - _Reuse_: core `live_check.call`.
  - _Output_: `De-Fusion/defusion/tools/view.py`
  - _Verify_: `pytest -q tests/test_view.py` and
    `python -c "import sys; sys.path[:0]=['De-Fusion','tests/fakes']; from defusion.tools import view; assert len(view.LIVE_SCENARIOS) == 7 and {t for _, t, _ in view.LIVE_SCENARIOS} == {'view.capture', 'view.view_sheet', 'view.get_camera', 'view.set_camera'}"`
- [x] **TASK-VIEW-006** [REQ-VIEW-001, REQ-VIEW-002, REQ-VIEW-003, REQ-VIEW-004, REQ-VIEW-005, REQ-VIEW-006, REQ-VIEW-008]: Live verification
  - Reload the add-in (`server.restart_addin`), run the view scenarios and fix failures until all
    pass. Open two representative captures (default and `view_sheet`) with the Read tool to
    confirm the box is visible and the tiles show the expected views.
  - _Reuse_: TASK-VIEW-005 scenarios; `server.restart_addin`.
  - _Output_: `De-Fusion/defusion/tools/view.py`
  - _Verify_: `python scripts/live_check.py --only view`
