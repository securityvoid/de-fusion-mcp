# tasks.md — docs/specs/setup/

Legend: `- [ ]` open, `- [x]` done. Run every command from the repository root. Unit tests use
`tests/fakes/adsk`. Design references are to `docs/specs/setup/design.md`. All tasks wait on the core
set (`docs/specs/core/tasks.md`).

## Phase 1 — Client logic (pure Python)
- [x] **TASK-SETUP-001** [REQ-SETUP-004, REQ-SETUP-005, REQ-SETUP-006, REQ-SETUP-008, REQ-SETUP-012]: JSON clients, table, backup, atomic write
  - Create `De-Fusion/defusion/clients.py` with § 2 interface (action `replace` renamed `update`; no UNSUPPORTED label): `Client` table (all ten rows; Codex
    row declared, TOML in TASK-SETUP-002), `detect`, `build_url`, `plan`, `apply`,
    `remove_plan`, JSON merge/remove, backup, atomic write, permissions, invalid-file skip.
  - Create `tests/test_clients.py`: the REQ-SETUP-004, -005, -006, -008 and -012 acceptance cases and
    design § 6 for JSON clients.
  - _Output_: `De-Fusion/defusion/clients.py`, `tests/test_clients.py`
  - _Reuse_: `guard.norm_folder`, `urllib.parse.quote`, stdlib `json`, `tempfile`, `shutil`.
  - _Verify_: `pytest -q tests/test_clients.py -k "not codex and not config" && ! grep -n adsk De-Fusion/defusion/clients.py && ! grep -nE '"replace"|UNSUPPORTED' De-Fusion/defusion/clients.py`
- [x] **TASK-SETUP-002** [REQ-SETUP-006, REQ-SETUP-007, REQ-SETUP-005, REQ-SETUP-008]: Codex TOML
  - Implement the § 2.3 text edit with `tomllib` verification in `clients.py` for plan and remove (action `replace` renamed `update`).
  - Tests: the REQ-SETUP-005 to -008 acceptance cases and design § 6 for Codex.
  - _Output_: `De-Fusion/defusion/clients.py`, `tests/test_clients.py`
  - _Reuse_: `tomllib`, TASK-SETUP-001 backup/atomic helpers.
  - _Verify_: `pytest -q tests/test_clients.py -k codex && ! grep -nE '"replace"|UNSUPPORTED' De-Fusion/defusion/clients.py`
- [x] **TASK-SETUP-003** [REQ-SETUP-003]: `config.toml` editing
  - Implement `plan_config(port, folder, token, src)` per § 2.5, `src` the cached `config.toml` bytes.
  - Tests: the REQ-SETUP-003 acceptance cases and design § 6.
  - _Output_: `De-Fusion/defusion/clients.py`, `tests/test_clients.py`
  - _Reuse_: `config.DEFAULT_TOML`, `config._parse`, `guard.norm_folder`.
  - _Verify_: `pytest -q tests/test_clients.py -k config`

## Phase 2 — Dialog and wiring
- [x] **TASK-SETUP-004** [REQ-SETUP-002, REQ-SETUP-009, REQ-SETUP-010, REQ-SETUP-012]: Dialog
  - Create `De-Fusion/defusion/setup_ui.py` per § 4 with `add()` / `remove()` (detected clients only, OK disabled on invalid input or no client; no UNSUPPORTED or `not detected` labels); extend
    `tests/fakes/adsk/core.py` with command, toolbar-panel, command-input and drop-down fakes
    (append only); create `tests/test_setup_ui.py`.
  - Tests: the REQ-SETUP-002, -009, -010 and -012 acceptance cases and design § 6.
  - _Output_: `De-Fusion/defusion/setup_ui.py`, `tests/fakes/adsk/core.py`, `tests/test_setup_ui.py`
  - _Reuse_: `clients.py` (TASK-SETUP-001..003), `secrets.token_urlsafe`, `shutil.which`.
  - _Verify_: `pytest -q tests/test_setup_ui.py && pytest -q tests/test_fakes.py && ! grep -n 'not detected' De-Fusion/defusion/setup_ui.py`
- [x] **TASK-SETUP-005** [REQ-SETUP-001, REQ-SETUP-012]: Add-in wiring and panel id
  - Wire `run()`/`stop()` per § 5. With Fusion running and the add-in loaded, check the Add-Ins
    panel id with a one-off `ui.allToolbarPanels` listing via the add-in script runner, record the
    verified id in § 4 and set `PANEL_ID`. Tests: `run()` still starts the server when `add()` raises;
    `stop()` removes the control.
  - _Output_: `De-Fusion/De-Fusion.py`, `De-Fusion/defusion/setup_ui.py`, `tests/test_setup_ui.py`, `docs/specs/setup/design.md` (§ 4 panel id only)
  - _Reuse_: `tests/test_entry.py` patterns for `run`/`stop`.
  - _Verify_: `pytest -q tests/test_setup_ui.py tests/test_entry.py && grep -n "SolidScriptsAddinsPanel\|PANEL_ID" De-Fusion/defusion/setup_ui.py`

## Phase 3 — Confirmations and docs
- [x] **TASK-SETUP-006** [REQ-SETUP-013]: Inferred paths
  - Test the § 3.2 paths as far as the machine allows. Fill § 3.2 values; no "filled by" text remains.
  - _Output_: `docs/specs/setup/design.md` (§ 3.2 values)
  - _Reuse_: `clients.plan`/`apply` against a temp `HOME`.
  - _Verify_: `! grep -qE "filled by" docs/specs/setup/design.md && pytest -q tests/test_clients.py`
- [x] **TASK-SETUP-007** [REQ-SETUP-014]: README
  - Add the README "Setup dialog" section (REQ-SETUP-014) and a `tests/test_readme.py` check for it, asserting the restart note names
    "Add-Ins" and not `restart_addin`.
  - _Output_: `README.md`, `tests/test_readme.py`
  - _Reuse_: `tests/test_readme.py` section helper.
  - _Verify_: `pytest -q tests/test_readme.py && pytest -q`

## Phase 4 — Literal tokens, auth off by default
- [x] **TASK-SETUP-008** [REQ-SETUP-002, REQ-SETUP-004, REQ-SETUP-005, REQ-SETUP-006, REQ-SETUP-009, REQ-SETUP-010, REQ-SETUP-014]: Literal token for every client
  - `clients.py`: drop `Client.terminal`, `ENV_VAR`, the env-var header builder and the `bearer_token_env_var` entry; Claude Code and Gemini build `headers.Authorization = "Bearer <token>"`, Codex builds `http_headers` (§ 2.3); the literal-token mode (0600) applies to every client; `tests/test_clients.py` runs all ten client ids: an existing 0644 file becomes 0600 after applying a token plan and stays 0644 for an auth-`None` plan.
  - `setup_ui.py`: default auth `None` when `config.toml` has no token; remove the `export` box, `CODEX_WARN` and the `terminal` logic; the `token` box shows in Apply mode with auth not `None`.
  - `README.md`: Setup dialog section describes literal tokens, no `export DEFUSION_TOKEN`; update `tests/test_clients.py`, `tests/test_setup_ui.py`, `tests/test_readme.py` to REQ-SETUP-002, -006, -010, -014 acceptance.
  - _Output_: `De-Fusion/defusion/clients.py`, `De-Fusion/defusion/setup_ui.py`, `README.md`, `tests/test_clients.py`, `tests/test_setup_ui.py`, `tests/test_readme.py`
  - _Reuse_: `clients._mode` (0600 for token writes), `tests/test_clients.py` and `tests/test_setup_ui.py` fixtures.
  - _Verify_: `pytest -q && ! grep -rnE "bearer_token_env_var|\.terminal|CODEX_WARN|export DEFUSION_TOKEN" De-Fusion README.md`

## Phase 5 — Claude Code config directories
- [x] **TASK-SETUP-009** [REQ-SETUP-015]: Detect Claude Code directories
  - `clients.py`: add `claude_dirs` per § 2.7. `setup_ui.py`: append `claude_dirs()` to the detected list.
  - Tests: the REQ-SETUP-015 acceptance case in `tests/test_clients.py` and `tests/test_setup_ui.py`; the dialog test fake rejects any input id outside `[A-Za-z0-9_-]` like Fusion, so a dotted directory name fails; README Setup dialog section names the extra `Claude Code (~/<dir>)` entries, checked in `tests/test_readme.py` (the section contains `Claude Code (~/`).
  - _Output_: `De-Fusion/defusion/clients.py`, `De-Fusion/defusion/setup_ui.py`, `README.md`, `tests/test_clients.py`, `tests/test_setup_ui.py`, `tests/test_readme.py`
  - _Reuse_: the `claude-code` row's `build`, `clients.read`/`plan`/`apply`.
  - _Verify_: `pytest -q`

## Phase 6 — Client files read at Apply
- [x] **TASK-SETUP-010** [REQ-SETUP-009]: Read client files when Execute runs
  - `setup_ui.py`: drop `Session.sources` and the per-client read in `_created`; `_plans` calls `clients.read(c)` for each ticked client (only `config.toml` stays cached).
  - Tests: in `tests/test_setup_ui.py`, a client file mutated after the dialog opened is `ok`, keeps the mutation and gains the entry; opening the dialog and 20 input changes read only `config.toml`, Execute reads the ticked client files; `test_changed_since_dialog_opened` for `config.toml` stays; `clients.read` raising for the first ticked client gives `skipped: ...; Apply again` for it, the next client `ok` and the result dialog still runs; a ticked client file rewritten after Execute's read and before `apply` (monkeypatch `clients.apply` to write first) is skipped, keeps the new bytes and has no backup.
  - `_plans` returns `[(Client, Callable[[], Plan])]`; Execute calls each thunk inside the per-client catch, and an exception from the read or plan gives `<label>: skipped: <error>; Apply again`.
  - _Output_: `De-Fusion/defusion/setup_ui.py`, `tests/test_setup_ui.py`
  - _Reuse_: `clients.read`, `clients.plan`, `clients.apply`.
  - _Verify_: `pytest -q`

## Operator verification (not tasks)
Click-through in Fusion per design § 6 install-time smoke: button appears in Add-Ins panel, dialog
one real client connects after restart, Remove removes it.
