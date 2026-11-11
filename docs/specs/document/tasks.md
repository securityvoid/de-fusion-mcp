# tasks.md — docs/specs/document/

Legend: `- [ ]` open, `- [x]` done. Run every command from the repository root. Unit tests use
`tests/fakes/adsk`. Live verifies need Fusion running with the De-Fusion add-in loaded (core
TASK-CORE-022) and run `scripts/live_check.py` (core TASK-CORE-021). All tasks wait on the core
framework (docs/specs/core/tasks.md Phase 2, TASK-CORE-004 to TASK-CORE-012). Design references
are to `docs/specs/document/design.md`.

## Phase 1 — Fakes and helpers
- [x] **TASK-DOC-001** [REQ-DOC-021, REQ-DOC-022]: Fake adsk document/data classes and shared helpers
  - Add the § 10.1 fake classes and enums to `tests/fakes/adsk/core.py` and
    `tests/fakes/adsk/fusion.py` (append; do not change existing fakes' behaviour).
  - Create `De-Fusion/defusion/tools/document.py` with the module docstring of § 1, the § 2 helpers
    (`_doc`, `_folder`, `_file`, `_hub`, `_folder_path`, `_file_path`, `_file_ref`, `_doc_summary`,
    `_folder_summary`, `_file_summary`, `_file_props`, `_need`, `_abs_path`), an empty
    `LIVE_SCENARIOS = []`. Tests import the module directly; it joins `defusion/tools/__init__.py`
    in TASK-DOC-002 (core rejects a tool module that registers no action).
  - Tests for the helpers in `tests/test_document.py`: selector match order, `NO_DOCUMENT`,
    `NOT_FOUND`, `AMBIGUOUS` with candidates, id vs path addressing, path segment error message,
    upload-pending `file.id` null, `_abs_path` errors.
  - _Output_: `tests/fakes/adsk/core.py`, `tests/fakes/adsk/fusion.py`,
    `De-Fusion/defusion/tools/document.py`, `tests/test_document.py`
  - _Reuse_: core `ctx.fail`, `ctx.ref`, core fakes in `tests/fakes/adsk` (TASK-CORE-003).
  - _Verify_: `pytest -q tests/test_document.py -k helpers && pytest -q tests/test_fakes.py`

## Phase 2 — Actions (unit-tested)
- [x] **TASK-DOC-002** [REQ-DOC-002, REQ-DOC-003, REQ-DOC-004, REQ-DOC-021]: Cloud navigation reads
  - Implement `list_hubs`, `list_projects`, `list_folders`, `resolve_folder`, `datafile_get`
    (design § 5.2–5.4) with § 3 access and timeouts and `undo_group=False`. Import `document` from
    `De-Fusion/defusion/tools/__init__.py` if not already imported.
  - _Output_: `De-Fusion/defusion/tools/document.py`, `De-Fusion/defusion/tools/__init__.py`,
    `tests/test_document.py`
  - _Reuse_: § 2 helpers (TASK-DOC-001), core `ctx.page`, `@action`.
  - _Verify_: `pytest -q tests/test_document.py -k "hubs or projects or folder or datafile_get"`
- [x] **TASK-DOC-003** [REQ-DOC-001, REQ-DOC-005, REQ-DOC-007, REQ-DOC-008, REQ-DOC-009, REQ-DOC-012, REQ-DOC-019, REQ-DOC-021]: Open-document lifecycle
  - Implement `list_open`, `list_references`, `create`, `open` (as `open_`), `activate`,
    `set_name`, `close` (design § 5.1, 5.5, 6.1–6.3, 6.6, 7.1).
  - _Output_: `De-Fusion/defusion/tools/document.py`, `tests/test_document.py`
  - _Reuse_: § 2 helpers, core `ctx.page`, `@action`.
  - _Verify_: `pytest -q tests/test_document.py -k "list_open or references or create or open or activate or set_name or close"`
- [x] **TASK-DOC-004** [REQ-DOC-010, REQ-DOC-011, REQ-DOC-014, REQ-DOC-013]: Save, save as, read-only, references update
  - Implement `save`, `save_as`, `set_read_only`, `advance_to_latest`, `update_all_references`
    (design § 6.4, 6.5, 6.7, 6.8). Tests: `save_as` name `../x` returns `INVALID_PARAMS`.
  - _Output_: `De-Fusion/defusion/tools/document.py`, `tests/test_document.py`
  - _Reuse_: § 2 helpers, core `ctx.text_command`.
  - _Verify_: `pytest -q tests/test_document.py -k "save or read_only or advance or update_all"`
- [x] **TASK-DOC-005** [REQ-DOC-015, REQ-DOC-016, REQ-DOC-020]: Data file changes
  - Implement `datafile_set`, `datafile_refresh`, `datafile_copy`, `datafile_move`,
    `datafile_delete` (design § 6.9, 6.10, 7.2). Tests: `datafile_set` name `../x` returns `INVALID_PARAMS`; a licence-restricted export returns `UNSUPPORTED`.
  - _Output_: `De-Fusion/defusion/tools/document.py`, `tests/test_document.py`
  - _Reuse_: § 2 helpers (`_file`, `_folder`, `_file_props`).
  - _Verify_: `pytest -q tests/test_document.py -k "datafile"`
- [x] **TASK-DOC-006** [REQ-DOC-006]: Export
  - Implement `export` (design § 5.6, § 8) including keyword `create*ExportOptions` calls, mesh
    options, the existing-file `INVALID_PARAMS` check (every `one_file_per_body` file, before any write), and
    `one_file_per_body` file listing.
  - _Output_: `De-Fusion/defusion/tools/document.py`, `tests/test_document.py`
  - _Reuse_: § 2 helpers (`_abs_path`, `_need`), core `Component`/`Body` Refs and `ctx.ref`.
  - _Verify_: `pytest -q tests/test_document.py -k export`
- [x] **TASK-DOC-007** [REQ-DOC-017, REQ-DOC-018]: Import and mesh import
  - Implement `import` (as `import_`) and `import_mesh` (design § 6.11, 6.12, § 9).
  - _Output_: `De-Fusion/defusion/tools/document.py`, `tests/test_document.py`
  - _Reuse_: § 2 helpers, core `Plane`/`Component` Refs, `ctx.length_out`.
  - _Verify_: `pytest -q tests/test_document.py -k import`
- [x] **TASK-DOC-008** [REQ-DOC-022]: Cross-cutting checks
  - Tests: every action registered with the § 3 access and timeout (via `help`), no `PTransaction`
    text command recorded for any action, and each § 4 member removed from the fake yields
    `UNSUPPORTED`.
  - _Output_: `tests/test_document.py`
  - _Reuse_: core `help` result (`actions.help_result`) and the fake `executeTextCommand` call log.
  - _Verify_: `pytest -q tests/test_document.py`

## Phase 3 — Live verification
- [x] **TASK-DOC-009** [REQ-DOC-001, REQ-DOC-002, REQ-DOC-003, REQ-DOC-004, REQ-DOC-005, REQ-DOC-007, REQ-DOC-008, REQ-DOC-009, REQ-DOC-010, REQ-DOC-011, REQ-DOC-012, REQ-DOC-013, REQ-DOC-014, REQ-DOC-015, REQ-DOC-016, REQ-DOC-019, REQ-DOC-020, REQ-DOC-021]: Live scenarios for documents and data files
  - Add to `LIVE_SCENARIOS` every § 10.2 scenario except `export`, `export_usdz`, `import`,
    `import_mesh`, `roundtrip`; reload the add-in (`server.restart_addin`) before running.
    Fix code until all pass.
  - Waits on the actions `live_check.box` (core design § 10) calls, used by `save`: `sketch.create`
    (TASK-SK-003), `sketch.rectangle` (TASK-SK-004), `model.extrude` (TASK-MDL-002).
  - _Output_: `De-Fusion/defusion/tools/document.py`
  - _Reuse_: `scripts/live_check.py` (TASK-CORE-021), `server.restart_addin` (server set).
  - _Verify_: `python scripts/live_check.py --only document.list_open && python scripts/live_check.py --only document.list_hubs && python scripts/live_check.py --only document.list_projects && python scripts/live_check.py --only document.resolve_folder && python scripts/live_check.py --only document.list_folders && python scripts/live_check.py --only document.create && python scripts/live_check.py --only document.activate && python scripts/live_check.py --only document.set_name && python scripts/live_check.py --only document.save_as && python scripts/live_check.py --only document.save && python scripts/live_check.py --only document.datafile_get && python scripts/live_check.py --only document.close && python scripts/live_check.py --only document.open && python scripts/live_check.py --only document.set_read_only && python scripts/live_check.py --only document.datafile_set && python scripts/live_check.py --only document.datafile_refresh && python scripts/live_check.py --only document.datafile_copy && python scripts/live_check.py --only document.datafile_move && python scripts/live_check.py --only document.list_references && python scripts/live_check.py --only document.advance_to_latest && python scripts/live_check.py --only document.update_all_references && python scripts/live_check.py --only document.datafile_delete`
- [x] **TASK-DOC-010** [REQ-DOC-006, REQ-DOC-017, REQ-DOC-018, REQ-DOC-022]: Live scenarios for export and import
  - Waits on `sketch.create` (TASK-SK-003), `sketch.rectangle` (TASK-SK-004) and `model.extrude`
    (TASK-MDL-002), which `live_check.box` (core design § 10) calls.
  - Add the `export`, `export_usdz`, `import`, `import_mesh`, `roundtrip` scenarios; reload the
    add-in; fix code until all pass; then run the whole set.
  - _Output_: `De-Fusion/defusion/tools/document.py`
  - _Reuse_: `scripts/live_check.py`, `server.restart_addin`, `live_check.box` (core design § 10).
  - _Verify_: `python scripts/live_check.py --only document.export && python scripts/live_check.py --only document.import && python scripts/live_check.py --only document.import_mesh && python scripts/live_check.py --only document && pytest -q tests/test_document.py`
