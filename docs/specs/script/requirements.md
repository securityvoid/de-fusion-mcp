# requirements.md — docs/specs/script/
> De-Fusion MCP — `script` tool: API documentation lookup, modelling guide, saved scripts and Python execution

## Overview
The `script` tool is the escape hatch and reference desk of De-Fusion. It lets an agent search the
live Fusion Python API, read a modelling guide written for agents, keep reusable Python snippets
under the config directory, and run arbitrary Python inside Fusion with a persistent namespace.
All actions live in `De-Fusion/defusion/tools/script.py`. The default profile (core design § 6.1)
excludes the whole tool; it is enabled with a profile such as `full` or `?profile=all`.

## Operator ask
> 2026-11-10: "comprehensive Fusion MCP server ... union of the four servers"

## Dependencies
- **DEP-SCR-001**: The core set (`docs/specs/core/`) supplies the action framework, call pipeline,
  dispatcher and jobs, undo grouping, results envelope, paging, errors and profiles. This set
  specifies actions only.

## Functional Requirements

### A. API documentation

- **REQ-SCR-001**: The script tool shall provide a `read` action `api_docs` that, given a free-text
  `query`, searches the classes and members of the live `adsk.core`, `adsk.fusion` and `adsk.cam`
  modules by introspection and returns a paged, score-ordered list of matches, each with its dotted
  name, kind, signature and docstring summary, optionally restricted to one module.
  - _Acceptance_: Live, `api_docs {"query": "extrude input"}` returns `total > 0` and an item named
    `adsk.fusion.ExtrudeFeatures.createInput` within the first 10 items; with `"module": "cam"` no
    item name starts with `adsk.fusion.` or `adsk.core.`; `offset`/`limit` page per core design § 5.5.

- **REQ-SCR-002**: When `query` is an exact dotted name of an `adsk` class or member, `api_docs`
  shall return that one entry with its full docstring and signature (and, for a class, its paged
  member list); an exact-looking name that does not resolve shall fail `NOT_FOUND` with up to five
  search suggestions as hints.
  - _Acceptance_: Live, `api_docs {"query": "adsk.fusion.ExtrudeFeatures.createInput"}` returns
    `match: "exact"`, `kind: "method"` and a non-empty `doc`; `adsk.fusion.Sketch` returns
    `kind: "class"` with `members.total > 0`; `adsk.fusion.NoSuchThing` returns `NOT_FOUND`.

### B. Modelling guide

- **REQ-SCR-003**: The script tool shall provide a `read` action `design_guide` that returns the
  contents of `De-Fusion/defusion/guide.md`, a modelling guide for agents written for this project
  with the sections listed in design § 4, containing no third-party text, under 24 KiB, and naming
  only `tool.action` pairs that exist in the action registry.
  - _Acceptance_: `design_guide {}` returns `guide` equal to the file text and `bytes < 24576`; a
    unit test finds every design § 4 heading in the file and every backticked `tool.action` in it
    registered.

### C. Saved scripts

- **REQ-SCR-004**: The script tool shall provide `read` actions `list_scripts` (paged list of saved
  scripts with name, size, modification time and summary) and `load_script` (returns a saved
  script's code), both over `<config dir>/scripts`.
  - _Acceptance_: After `save_script {"name": "box", "code": "\"\"\"Make a box.\"\"\"\nx = 1\n"}`,
    `list_scripts` contains `{"name": "box", "summary": "Make a box.", ...}` and `load_script
    {"name": "box"}` returns the same code; `load_script {"name": "missing"}` returns `NOT_FOUND`.

- **REQ-SCR-005**: The script tool shall provide a `write` action `save_script` that stores a new
  script of at most 60 000 UTF-8 bytes under `<config dir>/scripts/<name>.py`, creating the
  directory when absent, and refuses to replace an existing script.
  - _Acceptance_: Saving a new name returns `{"script": {"name": ..., "bytes": ...}}` and the file
    exists; saving the same name again returns `INVALID_PARAMS` with a hint naming
    `script.delete_script`; 60 001 bytes of code returns `INVALID_PARAMS`.

- **REQ-SCR-006**: The script tool shall provide a `destructive` action `delete_script` that
  removes one saved script.
  - _Acceptance_: Without `confirm` the call returns `CONFIRM_REQUIRED` and the file remains; with
    `confirm: true` it returns `{"deleted": "<name>"}` and the file is gone; a missing name returns
    `NOT_FOUND`.

- **REQ-SCR-007**: Every saved-script action shall accept only names matching
  the core `FileName` rule (an optional trailing `.py` is stripped first) and shall only
  touch regular files directly inside `<config dir>/scripts`, never following symlinks.
  - _Acceptance_: Names `../x`, `a/b`, `.hidden` and `""` each return `INVALID_PARAMS`
    with no file-system change; a symlink `<config dir>/scripts/evil.py` is not listed and
    `load_script {"name": "evil"}` returns `NOT_FOUND`.

### D. Python execution

- **REQ-SCR-008**: The script tool shall provide a `destructive` action `execute_python` that runs
  `code` on Fusion's main thread with `adsk`, `app`, `ui`, `design` and `root` preloaded, captures
  stdout and stderr, and returns the value of a trailing expression statement (REPL-style) as
  JSON-safe data: JSON values as-is, Fusion entities as Refs (core design § 5.4), anything else as
  a truncated `repr`.
  - _Acceptance_: Live, `execute_python {"code": "print('hi')\nroot.name", "confirm": true}` returns
    `stdout: "hi\n"` and `value` equal to the root component name; code ending in
    `root.bRepBodies.item(0)` after creating a body returns a Ref with `type: "BRepBody"`
    whose `token` resolves through `design.findEntityByToken`; code ending in an assignment returns `value: null`.

- **REQ-SCR-009**: `execute_python` shall keep one persistent namespace per `session_id`
  (default `"default"`), refresh the preloaded names on every call, clear the namespace when
  `reset` is true, and hold at most 32 sessions, evicting the least recently used.
  - _Acceptance_: `x = 41` then `x + 1` in the same session returns `42`; in another `session_id`
    `x` raises `NameError`; with `reset: true` `x` is undefined; the 33rd session evicts the least
    recently used one (unit test).

- **REQ-SCR-010**: `execute_python` shall report a syntax error as `INVALID_PARAMS` and a runtime
  exception as `FUSION_ERROR` carrying the exception type, message, traceback and the output
  captured so far, with partial model changes rolled back by the core undo group (core design
  § 5.7); a call exceeding its 60 s timeout shall follow core job semantics (core design § 4).
  - _Acceptance_: `code: "def ("` returns `INVALID_PARAMS`; code that creates a sketch then raises
    returns `FUSION_ERROR` with `error.stdout`, `error.traceback`, and the sketch count unchanged;
    live, `import time; time.sleep(65); 7` returns `{"status": "running", "job_id": ...}` and
    `server.job_status` later reports `state: "done"` with `result.value == 7`.
