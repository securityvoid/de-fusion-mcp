# tasks.md — docs/specs/script/
> De-Fusion MCP — `script` tool

Legend: `- [ ]` open, `- [x]` done. Each task names its requirements, outputs and verify commands.
All commands run from the repository root. All tasks wait on the core framework
(docs/specs/core/tasks.md Phase 2). Live verifies need Fusion running with the add-in loaded; before each
live verify, load the new code with `server.restart_addin` (docs/specs/server TASK-SRV-002; before it exists,
relaunch Fusion as in docs/specs/server/tasks.md) and wait
until `server.status` answers.

## Phase 1: Reference actions

- [x] **TASK-SCR-001** [REQ-SCR-001, REQ-SCR-002]: Implement `api_docs` (index, search, exact lookup)
  - _Output_: `De-Fusion/defusion/tools/script.py` (constants, § 3 helpers, `api_docs`),
    `tests/fakes/adsk` additions from design § 8.1, `tests/test_script.py` (design § 8.1 tests 1–2),
    `LIVE_SCENARIOS` entries `api_docs_search`, `api_docs_exact`, `api_docs_cam` (design § 8.2)
  - _Reuse_: core `ctx.page`, `ActionError`, `@action` (core design § 5); stdlib `inspect`, `importlib`.
  - _Verify_: `pytest -q tests/test_script.py -k api_docs`;
    `python scripts/live_check.py --profile full --only script.api_docs`

- [x] **TASK-SCR-002** [REQ-SCR-003]: Write `guide.md` and implement `design_guide`
  - Waits on the tasks that register actions (each other set's Phase 1/2 tasks, not its live-verification
    phases), because the guide may only name registered `tool.action` pairs.
  - Write the guide fresh per design § 4.1 (headings exactly as listed, under 24 KiB, no
    third-party text); read each referenced action's `help` before naming it.
  - _Output_: `De-Fusion/defusion/guide.md`, `design_guide` in `De-Fusion/defusion/tools/script.py`,
    design § 8.1 test 7 in `tests/test_script.py`,
    `LIVE_SCENARIOS` entry `design_guide`
  - _Reuse_: core action registry for the name check (core design § 5.1).
  - _Verify_: `pytest -q tests/test_script.py -k guide`;
    `python scripts/live_check.py --profile full --only script.design_guide`

## Phase 2: Saved scripts

- [x] **TASK-SCR-003** [REQ-SCR-004, REQ-SCR-005, REQ-SCR-006, REQ-SCR-007]: Implement
  `list_scripts`, `load_script`, `save_script`, `delete_script`
  - _Output_: design § 5 in `De-Fusion/defusion/tools/script.py`; design § 8.1 test 3 in
    `tests/test_script.py`, `LIVE_SCENARIOS` entry `scripts_roundtrip`
  - _Reuse_: `config.CONFIG_DIR` (core design § 6.1), `ctx.page`; stdlib `ast.get_docstring`.
  - _Verify_: `pytest -q tests/test_script.py -k scripts`;
    `python scripts/live_check.py --profile full --only script.save_script`

## Phase 3: Execution

- [x] **TASK-SCR-004** [REQ-SCR-008, REQ-SCR-009, REQ-SCR-010]: Implement `execute_python`
  - _Output_: design § 6 in `De-Fusion/defusion/tools/script.py`; design § 8.1 tests 4–6 in
    `tests/test_script.py`, `LIVE_SCENARIOS` entries `execute_*` (design § 8.2)
  - _Reuse_: core undo group, dispatcher jobs and `ctx.ref` (core design § 4, § 5.4, § 5.7); stdlib `ast`, `contextlib.redirect_stdout`.
  - _Verify_: `pytest -q tests/test_script.py -k execute`;
    `python scripts/live_check.py --profile full --only script.execute_python`

## Phase 4: Full live run

- [x] **TASK-SCR-005** [REQ-SCR-001, REQ-SCR-002, REQ-SCR-003, REQ-SCR-004, REQ-SCR-005, REQ-SCR-006, REQ-SCR-007, REQ-SCR-008, REQ-SCR-009, REQ-SCR-010]: Run the whole script suite against the reloaded add-in
  - Call `server.restart_addin`, wait until `server.status` answers, then run every unit test and
    all ten design § 8.2 scenarios. `execute_job` blocks Fusion for 65 s by design. Fix any failure
    in the owning task's code.
  - _Output_: none beyond fixes in `De-Fusion/defusion/tools/script.py`
  - _Reuse_: `scripts/live_check.py` (core design § 10) and `server.restart_addin`.
  - _Verify_: `pytest -q tests/test_script.py`; `python scripts/live_check.py --profile full --only script`
    prints PASS for all ten scenarios and exits 0
