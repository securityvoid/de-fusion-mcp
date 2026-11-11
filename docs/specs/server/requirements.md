# requirements.md — docs/specs/server/
> De-Fusion MCP — `server` tool: status, job polling, add-in restart and Fusion text commands

## Overview
The `server` tool reports on the De-Fusion runtime, lets a client poll long-running jobs, restarts
the add-in so code and config changes apply, and runs raw Fusion text commands. All actions live in
`De-Fusion/defusion/tools/server.py`. `status` and `job_status` never need Fusion's main thread, so
they answer even when the dispatcher queue is stuck.

## Operator ask
> 2026-11-10: "comprehensive Fusion MCP server ... union of the four servers"

## Dependencies
- **DEP-SRV-001**: The core set (`docs/specs/core/`) supplies the action framework, call pipeline,
  dispatcher and job registry, config, auth, profiles, results envelope and errors. This set
  specifies actions only, plus the core interfaces it reads (design § 2).

## Functional Requirements

### A. Observability

- **REQ-SRV-001**: The server tool shall provide a `read` action `status` that returns the
  De-Fusion version, Fusion version, server state, start time, uptime, a per-load instance id, the
  port and MCP URL, whether auth is on, the config path, whether the config file exists and the
  config error if any, the request's resolved profile items and allowed action count, the
  dispatcher queue depth and the number of running jobs.
  - _Acceptance_: Live, `status {}` returns every field of design § 3 with the right types;
    `server.version` equals `defusion.__version__`; `http.port` equals the config port;
    `profile.allowed_actions` is smaller with `?profile=readonly` than with `?profile=full`.

- **REQ-SRV-002**: `status` and `job_status` shall run on the HTTP request thread without
  dispatching to Fusion's main thread and without calling any `adsk` API, using values cached at
  load time for Fusion-derived fields.
  - _Acceptance_: A unit test with a dispatcher whose queue is never drained returns `status`
    within 1 s showing `dispatcher.queue_depth == 1`, and makes no call to the fake
    `adsk.core.Application`.

- **REQ-SRV-003**: The server tool shall provide a `read` action `job_status` that returns the
  state (`running`, `done`, `failed`), result or error, start and finish times of a job created by a
  core dispatcher timeout (core design § 4), including a `failed` job with a `RESULT_TOO_LARGE`
  error when its result exceeded the size limit, and `NOT_FOUND` for an unknown or evicted id.
  - _Acceptance_: Unit: a job in the registry is returned with its stored result once done;
    a 65,537-byte job result shows `failed` with `RESULT_TOO_LARGE`;
  `job_status {"job_id": "nope"}` returns `NOT_FOUND`. Live: covered by the script set's
    `execute_job` scenario.

### B. Control

- **REQ-SRV-004**: The server tool shall provide a `write` action `restart_addin` (no undo group)
  that locates this add-in through `app.scripts`, returns immediately, and about 750 ms later stops
  the add-in, removes every `defusion` module from `sys.modules` and runs the add-in again, so code
  and config changes apply; it shall fail `UNSUPPORTED` when `app.scripts` is absent and `NOT_FOUND`
  when the add-in cannot be located, without scheduling anything.
  - _Acceptance_: Live, `restart_addin {}` returns `{"scheduled": true, ...}`; within 30 s
    `status` answers again with a different `server.instance` and a later `server.started`.

- **REQ-SRV-005**: A second `restart_addin` while one is pending shall not schedule another, and
  every failure inside the scheduled restart shall be logged and never raised into Fusion.
  - _Acceptance_: Unit: two calls before the timer fires create one custom event and the second
    returns `already_scheduled: true`; a fake `Script.stop` that raises is logged and `Script.run`
    is still attempted.

- **REQ-SRV-006**: The server tool shall provide a `destructive` action `run_text_command` (no undo
  group) that runs one Fusion text command through `ctx.text_command` and returns its output.
  - _Acceptance_: Without `confirm` it returns `CONFIRM_REQUIRED` and runs nothing; with `confirm`,
    live `PTransaction.Start "De-Fusion live"` then `PTransaction.Abort` both return
    `{"command": ..., "output": <string>}`; unit: a fake `executeTextCommand` that raises yields
    `TEXT_COMMAND_FAILED`; an empty command yields `INVALID_PARAMS`.

- **REQ-SRV-007**: `status` and `job_status` shall be callable under every profile that allows at
  least one action (core design § 6.4) and under a config error (core design § 6.1), and the other
  server actions only when the profile selects them and the config has no error.
  - _Acceptance_: MCP-surface test with `?profile=cam`: `tools/list` contains `server` with action
    enum `["help", "status", "job_status"]`, and calling `restart_addin` returns `NOT_ALLOWED`.
