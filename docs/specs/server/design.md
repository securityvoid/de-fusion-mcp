# design.md — docs/specs/server/
> De-Fusion MCP — `server` tool (`De-Fusion/defusion/tools/server.py`)

## 1. Overview
| Action | Access | Main thread | Undo group | Timeout |
|---|---|---|---|---|
| `status` | read | no (`main_thread=False`) | n/a | 30 s |
| `job_status` | read | no (`main_thread=False`) | n/a | 30 s |
| `restart_addin` | write | yes | `undo_group=False` | 30 s |
| `run_text_command` | destructive | yes | `undo_group=False` | 30 s |

Everything shared is the core framework (core design § 5). `status` and `job_status` are always
allowed when any action is (core design § 6.4); `restart_addin` and `run_text_command` follow the
profile. The default profile `["all", "-script"]` includes all four. After any config error only
`status` and `job_status` are allowed (core design § 6.1) and `status` reports `config.error`.
`restart_addin` declares `edits_document="none"` (core design § 5.13 table); `run_text_command`
keeps the default `"active"`, so it is guarded like any edit of the active document and accepts
`preserve_read_only`.

## 2. Core interfaces used
This module reads core state through these names, all provided by core (core design § 5.1,
§ 5.9, § 12).

| Name | Core section | Meaning |
|---|---|---|
| `@action(..., main_thread=False)` | § 5.1 | pipeline calls the function directly on the request thread with a `Ctx` whose `app`, `ui`, `document`, `design` are `None`; no undo group, no change summary |
| `ctx.profile_items: list[str]` | § 5.9 | the request's expanded profile items (query items, or `default_profile`) |
| `ctx.allowed: frozenset[str]` | § 5.9 | the request's allowed `"tool.action"` strings |
| `defusion.__version__` | § 3.5 | package version |
| `defusion.server.state() -> str` | § 12 | `starting`, `running` or `failed` |
| `defusion.config.CONFIG_PATH: Path` | § 12 | `<config dir>/config.toml` |
| `defusion.config.settings()` | § 12 | loaded config with `.port: int` |
| `defusion.config.load_error() -> str \| None` | § 12 | any config error recorded at start |
| `defusion.auth.token() -> str \| None` | § 12 | effective token, `None` when auth is off |
| `defusion.dispatch.queue_depth() -> int` | § 12 | items in state `queued` |
| `defusion.dispatch.running_count() -> int` | § 12 | jobs in state `running` |
| `defusion.dispatch.get_job(job_id) -> dict \| None` | § 12 | registry entry `{state, result \| error, started, finished}` (epoch seconds) |
| `ctx.text_command(cmd)` | § 5.9 | `app.executeTextCommand` with `TEXT_COMMAND_FAILED` mapping |

## 3. status
```python
@action("server", access="read", main_thread=False)
def status(ctx: Ctx) -> dict:
    """Report De-Fusion and Fusion versions, port, auth, config, profile, queue and uptime."""
```
Module-level values set when `tools/server.py` is imported (that import happens inside core
`start()` on Fusion's main thread, core design § 3.1):
- `_STARTED = time.time()`, `_STARTED_MONO = time.monotonic()`, `_INSTANCE = uuid.uuid4().hex[:12]`.
- `_FUSION_VERSION`: `adsk.core.Application.get().version` inside `try`; `None` when `adsk` is not
  importable or the call raises.

Result (every field is computed without the main thread):
```json
{"server":   {"name": "De-Fusion", "version": "0.1.0", "state": "running",
              "started": "2026-11-10T14:03:22Z", "uptime_s": 812.4, "instance": "3f9a0c1b2d4e"},
 "fusion":   {"version": "2.0.21508"},
 "http":     {"port": 8642, "url": "http://127.0.0.1:8642/mcp", "auth": false},
 "config":   {"path": "/Users/x/.defusion/config.toml", "exists": true, "error": null},
 "profile":  {"items": ["all", "-script"], "allowed_actions": 123},
 "dispatcher": {"queue_depth": 0, "jobs_running": 0}}
```
| Field | Source |
|---|---|
| `server.version` | `defusion.__version__` |
| `server.state` | `defusion.server.state()` |
| `server.started` | `_STARTED` as UTC ISO 8601, seconds, `Z` |
| `server.uptime_s` | `round(time.monotonic() - _STARTED_MONO, 1)` |
| `server.instance` | `_INSTANCE` (changes on every module load, proving a reload) |
| `fusion.version` | `_FUSION_VERSION` |
| `http.port`, `http.url` | `config.settings().port` |
| `http.auth` | `auth.token() is not None` |
| `config.path`, `config.exists`, `config.error` | `str(CONFIG_PATH)`, `CONFIG_PATH.is_file()`, `load_error()` |
| `profile.items`, `profile.allowed_actions` | `ctx.profile_items`, `len(ctx.allowed)` (excludes the implicit `help`) |
| `dispatcher.*` | `queue_depth()`, `running_count()` |

## 4. job_status
```python
@action("server", access="read", main_thread=False)
def job_status(ctx: Ctx, job_id: str) -> dict:
    """Return the state and, when finished, the result or error of a timed-out call's job."""
```
`get_job(job_id)` is `None` → `NOT_FOUND` ("unknown job <id>") with hints "only the last 100 jobs
are kept" and "jobs do not survive server.restart_addin". Otherwise:
```json
{"job_id": "a1b2", "state": "done", "result": {...}, "started": "<ISO>", "finished": "<ISO|null>",
 "elapsed_s": 71.2}
```
`result` is present when `state == "done"`, `error` (the core error object) when `failed`, neither
while `running`. `elapsed_s` = `finished` (or now) − `started`, rounded to 0.1.

## 5. restart_addin
```python
@action("server", access="write", undo_group=False)
def restart_addin(ctx: Ctx) -> dict:
    """Restart De-Fusion about 750 ms after replying; reloads its code and config.

    The HTTP server is down for a few seconds. Poll server.status until server.instance changes.
    Calls in flight at restart end with a transport error and an unknown outcome; job ids are lost."""
```
### 5.1 Scheduling (main thread, inside the call)
1. `scripts = getattr(ctx.app, "scripts", None)`; `None` → `UNSUPPORTED` ("this Fusion build has no
   Application.scripts").
2. Locate this add-in, `addin_dir = Path(defusion.__file__).resolve().parent.parent` and
   `link_dir = Path(defusion.__file__).parent.parent` (the AddIns symlink path when loaded through
   it). Try in order, skipping a step whose method is absent or raises, and stop at the first
   non-`None` result: `scripts.itemByPath(str(link_dir))`, `scripts.itemByPath(str(addin_dir))`,
   first item of `scripts.itemsByName("De-Fusion")`. None found → `NOT_FOUND` ("De-Fusion not found
   in app.scripts").
3. `_PENDING` (module global dict or `None`) is set → return the § 5.3 result with
   `already_scheduled: true`.
4. `event_id = f"defusion.restart.{uuid.uuid4().hex}"`; `event = ctx.app.registerCustomEvent(event_id)`;
   `handler = _RestartHandler()` (subclass of `adsk.core.CustomEventHandler`); `event.add(handler)`.
   Store `_PENDING = {"event_id", "event", "handler", "script", "app": ctx.app,
   "fired": threading.Event(), "done": False}` so nothing is garbage-collected.
5. `threading.Timer(0.75, _fire).start()` with `daemon = True`.

`_fire()` (timer thread): up to 50 times: `app.fireCustomEvent(event_id)`, then
`fired.wait(0.1)`; stop when `fired` is set. Firing from a non-main thread can be dropped, hence the
retry (same rule as core design § 4). After 50 attempts it logs "restart event never delivered" and
clears `_PENDING`.

### 5.2 Restart handler (main thread)
`_RestartHandler.notify(args)`:
1. `fired.set()`; if `_PENDING["done"]` return; set `done = True`.
2. `script.stop()` — Fusion calls `De-Fusion.py` `stop()`, which runs core `stop()` (core design
   § 3.3: port released, queued items fail `NOT_EXECUTED`, jobs cleared).
3. `for n in [m for m in sys.modules if m == "defusion" or m.startswith("defusion.")]:
   del sys.modules[n]`.
4. `app.unregisterCustomEvent(event_id)`.
5. `script.run()` — Fusion runs `De-Fusion.py` `run()` again, which imports fresh `defusion`
   modules and reads `config.toml` again.
Each step is in its own `try`; an exception is logged with `logging.getLogger("defusion")` and the
next step still runs. Nothing propagates to Fusion. The handler and timer keep working after step 3
because their functions hold references to the old module's globals.

### 5.3 Result
```json
{"scheduled": true, "already_scheduled": false, "delay_ms": 750, "addin": "<script.name>",
 "instance": "<current _INSTANCE>", "hint": "poll server.status until server.instance differs"}
```

### 5.4 Gotchas
- If `script.run()` fails (for example a syntax error in edited code), the server stays down; the
  log names the error and the user restarts De-Fusion from Fusion's Scripts and Add-Ins dialog.
- `Application.scripts` with `Scripts.itemByPath`, `Scripts.itemsByName`, `Script.stop`,
  `Script.run` is the documented script-management API; the `UNSUPPORTED` path covers builds
  without it, and live scenario `restart` proves it on this machine.

## 6. run_text_command
```python
@action("server", access="destructive", undo_group=False)
def run_text_command(ctx: Ctx, command: str) -> dict:
    """Run one Fusion text command (app.executeTextCommand) and return its output.

    Text commands are unrestricted and can change or discard data; no undo group is opened."""
```
`command.strip() == ""` → `INVALID_PARAMS`. Returns `{"command": command, "output":
ctx.text_command(command)}`; `TEXT_COMMAND_FAILED` comes from `ctx.text_command`. No undo group,
because commands such as `PTransaction.*` would nest inside the framework's own transaction.
Output size is bounded by the core size budget (core design § 5.5).

## 7. Errors (action-specific)
| Action | Condition | Code |
|---|---|---|
| `job_status` | unknown or evicted id | `NOT_FOUND` |
| `restart_addin` | no `app.scripts` | `UNSUPPORTED` |
| `restart_addin` | add-in not located | `NOT_FOUND` |
| `run_text_command` | empty command | `INVALID_PARAMS` |
| `run_text_command` | Fusion raised | `TEXT_COMMAND_FAILED` |

## 8. Testability
### 8.1 Unit (`tests/test_server_tool.py`, fake adsk, no Fusion)
Test function names start `test_status_`, `test_job_status_`, `test_restart_`, `test_text_command_`
and `test_profile_`.
1. `status`: as REQ-SRV-001 and REQ-SRV-002 acceptance; a broken temp `config.toml` surfaces the config error and a write action returns `NOT_ALLOWED`.
2. `job_status`: as REQ-SRV-003 acceptance, plus running, done and failed states.
3. `restart_addin`: as REQ-SRV-004 and REQ-SRV-005 acceptance; fake `app.scripts` with `itemByPath` returning a fake `Script`, timer patched to call `_fire` immediately, fake `fireCustomEvent` invoking the handler; asserts order `stop`, purge (a sentinel `defusion.zz_test` module is gone), `unregisterCustomEvent`, `run`; `stop` raising still reaches `run`.
4. `run_text_command`: as REQ-SRV-006 acceptance.
5. Profile: as REQ-SRV-007 acceptance.

### 8.2 Live (`LIVE_SCENARIOS` in `server.py`)
Each entry is a core design § 10 tuple `(name, "tool.action", async fn(session))` using
`live_check.call`; the Action column is the tag.

| Scenario | Action | Steps | Pass when |
|---|---|---|---|
| `status_fields` | `server.status` | `status {}` | as REQ-SRV-001 acceptance |
| `status_profile` | `server.status` | `status` through `live_check.open_session("readonly")` and `open_session("full")` | as REQ-SRV-001 acceptance |
| `job_unknown` | `server.job_status` | `job_status {"job_id": "nope"}` | as REQ-SRV-003 acceptance |
| `text_command` | `server.run_text_command` | `PTransaction.Start "De-Fusion live"` then `PTransaction.Abort`, both with `confirm` | as REQ-SRV-006 acceptance |
| `restart` | `server.restart_addin` | read `server.instance`; `restart_addin {}`; poll `status` every 0.5 s for 30 s, tolerating connection errors | as REQ-SRV-004 acceptance. Runs last |
