# tasks.md — docs/specs/server/
> De-Fusion MCP — `server` tool

Legend: `- [ ]` open, `- [x]` done. Each task names its requirements, outputs, reuse and verify
commands. All commands run from the repository root. All tasks wait on the core framework
(docs/specs/core/tasks.md Phase 2), including the design § 2 interfaces. Live verifies need Fusion
running with the add-in loaded. Before each live verify, load the new code: with `restart_addin`
working, call `server.restart_addin`; otherwise relaunch Fusion with
`osascript -e 'quit app "Autodesk Fusion"'` then `open -a "Autodesk Fusion"` (the manifest's
`runOnStartup` loads De-Fusion). Then poll `server.status` until it answers.

## Phase 1: Observability

- [x] **TASK-SRV-001** [REQ-SRV-001, REQ-SRV-002, REQ-SRV-003, REQ-SRV-007]: Implement `status` and `job_status`
  - _Output_: `De-Fusion/defusion/tools/server.py` (load-time values, `status`, `job_status`,
    `LIVE_SCENARIOS` entries `status_fields`, `status_profile`, `job_unknown`),
    `tests/test_server_tool.py` (design § 8.1 tests 1–3 and 6)
  - _Reuse_: design § 2 core interfaces; `ActionError` (core design § 5.10).
  - _Verify_: `pytest -q tests/test_server_tool.py -k "status or profile"`;
    `python scripts/live_check.py --only server.status`;
    `python scripts/live_check.py --only server.job_status`

## Phase 2: Control

- [x] **TASK-SRV-002** [REQ-SRV-004, REQ-SRV-005]: Implement `restart_addin`
  - The running add-in does not yet contain this code: load it by relaunching Fusion (legend) before
    the first live verify; later reloads use `restart_addin` itself.
  - _Output_: design § 5 in `De-Fusion/defusion/tools/server.py`, `LIVE_SCENARIOS` entry `restart`,
    design § 8.1 test 4 in `tests/test_server_tool.py`
  - _Reuse_: core custom-event pattern of `dispatch.py` (core design § 4); `logging`.
  - _Verify_: `pytest -q tests/test_server_tool.py -k restart`;
    `python scripts/live_check.py --only server.restart_addin`

- [x] **TASK-SRV-003** [REQ-SRV-006]: Implement `run_text_command`
  - _Output_: design § 6 in `De-Fusion/defusion/tools/server.py`, `LIVE_SCENARIOS` entry
    `text_command`, design § 8.1 test 5 in `tests/test_server_tool.py`
  - _Reuse_: `ctx.text_command` (core design § 5.9).
  - _Verify_: `pytest -q tests/test_server_tool.py -k text_command`;
    `python scripts/live_check.py --only server.run_text_command`

## Phase 3: Full live run

- [x] **TASK-SRV-004** [REQ-SRV-001, REQ-SRV-002, REQ-SRV-003, REQ-SRV-004, REQ-SRV-005, REQ-SRV-006, REQ-SRV-007]: Run the whole server suite against the reloaded add-in
  - Run all unit tests and all five design § 8.2 scenarios (`restart` last). Fix any failure in the
    owning task's code.
  - _Output_: none beyond fixes in `De-Fusion/defusion/tools/server.py`
  - _Reuse_: `scripts/live_check.py` (core design § 10).
  - _Verify_: `pytest -q tests/test_server_tool.py`; `python scripts/live_check.py --only server`
    prints PASS for all five scenarios and exits 0
