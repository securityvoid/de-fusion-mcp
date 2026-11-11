# design.md — docs/specs/script/
> De-Fusion MCP — `script` tool (`De-Fusion/defusion/tools/script.py`)

## 1. Overview
Seven actions on tool `script`. Everything shared (schema, validation, `confirm`, main-thread
dispatch, undo grouping, change summary, envelope, paging, errors, profiles) is the core framework
(core design § 5). The default profile `["all", "-script"]` (core design § 6.1) hides this tool;
`full`, `all` or `script` selectors expose it.

| Action | Access | Timeout | Undo group | Main thread |
|---|---|---|---|---|
| `api_docs` | read | 60 s | n/a | yes |
| `design_guide` | read | 30 s | n/a | yes |
| `list_scripts` | read | 30 s | n/a | yes |
| `load_script` | read | 30 s | n/a | yes |
| `save_script` | write | 30 s | `undo_group=False` | yes |
| `delete_script` | destructive | 30 s | `undo_group=False` | yes |
| `execute_python` | destructive | 60 s | default (on) | yes |

Core names used by this module: `defusion.config.CONFIG_DIR` (`pathlib.Path` of the core design
§ 6.1 config dir), `ActionError` (core design § 5.10), `Ctx` (core design § 5.9).

## 2. Module layout
`De-Fusion/defusion/tools/script.py` contains, in order:
1. Constants: `SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")`, `MAX_SCRIPT_BYTES = 60_000`,
   `MAX_SESSIONS = 32`, `MAX_STREAM_CHARS = 16_384`, `MAX_REPR_CHARS = 8_192`,
   `EXACT_RE = re.compile(r"^adsk\.(core|fusion|cam)\.[A-Za-z_]\w*(\.[A-Za-z_]\w*)?$")`.
2. API index helpers (§ 3): `_Entry` dataclass, `_INDEX: list[_Entry] | None = None`,
   `_build_index()`, `_score(entry, tokens)`, `_search(query, module)`, `_resolve_exact(name)`.
3. Script store helpers (§ 5): `_scripts_dir()`, `_script_path(ctx, name)`, `_summary(src)`.
4. Execution helpers (§ 6): `_SESSIONS: collections.OrderedDict[str, dict]`, `_session(ctx, id, reset)`,
   `_json_safe(ctx, value, depth=0)`, `_clip(text)`.
5. The seven `@action` functions.
6. `LIVE_SCENARIOS` (§ 8.2).

The guide file is `De-Fusion/defusion/guide.md` (§ 4), located with
`Path(__file__).resolve().parent.parent / "guide.md"`.

## 3. api_docs
```python
@action("script", access="read", timeout=60)
def api_docs(ctx: Ctx, query: str, module: Literal["core", "fusion", "cam"] | None = None,
             offset: int = 0, limit: int = 100) -> dict:
    """Search the live Fusion Python API (adsk.core/fusion/cam) or look up one exact name."""
```
### 3.1 Index
Built on first call and cached in `_INDEX` for the life of the module (a `server.restart_addin`
rebuilds it). For each module name `adsk.core`, `adsk.fusion`, `adsk.cam`: `importlib.import_module`;
an `ImportError` skips that module. For each `(name, obj)` in `vars(module).items()` with
`inspect.isclass(obj)`, `not name.startswith("_")` and `obj.__module__.startswith("adsk.")`:
- one class entry: `name = f"{modname}.{name}"`, `kind = "class"`,
  `signature = f"{cls.__name__}({', '.join(b.__name__ for b in cls.__bases__)})"`.
- one member entry per `(mname, attr)` in `vars(cls).items()` (own members only; inherited members
  are reachable by exact lookup) with `not mname.startswith("_")`:

| `attr` | kind | signature |
|---|---|---|
| `property` | `property` | `mname: <fget return annotation or "">` plus ` (read-only)` when `attr.fset is None` |
| `staticmethod` / `classmethod` | `staticmethod` | `mname<inspect.signature(attr.__func__)>` |
| function | `method` | `mname<inspect.signature(attr)>` with the leading `self` parameter removed |
| `int` | `constant` | `mname = <value>` |
| anything else | skipped | |

`inspect.signature` raising `ValueError`/`TypeError` → signature `mname(...)`. Annotations are
rendered with `inspect.formatannotation`; string annotations (Fusion's SWIG wrappers quote them)
are shown without quotes.

`_Entry` fields: `name`, `module` (`core|fusion|cam`), `cls_lower`, `member_lower` (`""` for class
entries), `kind`, `signature`, `summary` (first non-empty line of `inspect.getdoc(attr)` or `""`,
cut to 200 chars), `summary_lower`, `obj` (the class or attribute, for § 3.3).

The index only reads Python objects; it calls no Fusion API method. It still runs on the main
thread (core design § 3.4: non-main threads never touch `adsk`).

### 3.2 Search (`match: "search"`)
`tokens = [t for t in re.split(r"[\s.,()]+", query.lower()) if t and t != "adsk"]`; empty →
`INVALID_PARAMS` ("query is empty"). For each entry (filtered to `module` when given), every token
must hit or the entry is excluded; score is the sum over tokens of the first rule that applies:

| Rule | Points |
|---|---|
| token == `member_lower` | 8 |
| token is a substring of `member_lower` | 4 |
| token == `cls_lower` | 6 |
| token is a substring of `cls_lower` | 3 |
| token is a substring of `summary_lower` | 1 |

Sort by score descending, then `len(name)`, then `name`. Return
`{"match": "search", "query": query, **ctx.page(items, offset, limit)}` with items
`{"name", "kind", "signature", "summary", "score"}`.

### 3.3 Exact lookup (`match: "exact"`)
Used when `EXACT_RE.match(query.strip())`. Resolution: import the module, `getattr` the class
(must be a class), and for a member use `inspect.getattr_static(cls, member)` (searches the MRO, so
inherited members such as `isValid` resolve). Found → 
```json
{"match": "exact", "name": "adsk.fusion.ExtrudeFeatures.createInput", "kind": "method",
 "signature": "createInput(profile, operation)", "doc": "<full inspect.getdoc text, cut to 16 KiB>",
 "members": {"items": [...], "total": 0, "offset": 0, "next_offset": null}}
```
`members` appears only for classes: the class's own member entries (§ 3.1 item shape, without
`score`) sorted by name, paged with `offset`/`limit`. Not found → `NOT_FOUND`
("<query> is not in the live adsk API") with hints = names of the top 5 § 3.2 results for the same
query, and the `module` argument is ignored for exact names.

### 3.4 Gotchas
- `adsk.cam` exists in every current Fusion build; the `ImportError` skip covers a build without it.
- Fusion regenerates `adsk` wrappers per release; results reflect the running build, which is the
  point of introspection.

## 4. design_guide
```python
@action("script", access="read")
def design_guide(ctx: Ctx) -> dict:
    """Return the De-Fusion modelling guide for agents (read before building models)."""
```
Reads `guide.md` as UTF-8 on each call and returns `{"guide": <text>, "bytes": <utf-8 length>}`.
Missing file → `NOT_FOUND` ("guide.md missing from the add-in; reinstall").

### 4.1 guide.md content
Written fresh for this project by the implementer from Fusion API knowledge and the registered
actions' help text. No text copied from Autodesk documentation, other MCP servers or any other
third party. Under 24 KiB. Second person, imperative, short paragraphs and bullet lists, one worked
call example (JSON) per section where useful. Every reference to an action is written as a
backticked `tool.action` pair (for example `` `sketch.create` ``) and must name a registered action
(enforced by § 8.1 test 7). Required headings, exactly, in this order:

1. `# De-Fusion modelling guide`
2. `## 1. Calling De-Fusion` — tool + `action` + `params`; `help`; `confirm` for destructive
   actions; profiles; the result envelope and `changes`; error codes and hints; `running` results
   and `server.job_status`.
3. `## 2. Units` — bare numbers are mm/deg by default; unit expressions (`"0.5 in"`); parameter
   expressions (`"wall*2"`); per-call output units.
4. `## 3. Components first` — one component per physical part, created before its bodies; active
   component; components versus occurrences; why (joints, export per part, BOM, copies).
5. `## 4. Parameters` — create user parameters for driving dimensions; naming; use them in
   expressions; changing one rebuilds dependent features.
6. `## 5. Sketch to feature` — pick plane or planar face; create sketch; draw; constrain and
   dimension; check profiles; create the feature; verify.
7. `## 6. Profiles` — how profiles are indexed; list them before extruding; several indices in one
   call; inner loops and holes; profiles change when the sketch changes, so list again.
8. `## 7. Refs` — Ref forms; feed result Refs back as inputs; prefer tokens over names; `AMBIGUOUS`;
   re-read Refs after timeline edits because tokens can change.
9. `## 8. Timeline and rollback` — parametric versus direct design; timeline marker; roll back to
   edit an earlier feature and roll forward; suppress; feature health warnings.
10. `## 9. Undo` — each write call is one undo step; `undo_grouped: false`; how to undo with the
    history tool.
11. `## 10. Coils and threads` — when to use a coil body versus a thread feature; modelled versus
    cosmetic threads for 3D printing; check the action's `help` for its parameters.
12. `## 11. Verifying work` — inspect and measure after each step; screenshots via the view tool;
    read the change summary.
13. `## 12. Escape hatch` — when to use `script.execute_python`; look up API names with
    `script.api_docs`; sessions; saving reusable snippets with `script.save_script`.

## 5. Saved scripts
### 5.1 Helpers
- `_scripts_dir()` → `config.CONFIG_DIR / "scripts"` (not created by reads).
- `_script_path(ctx, name)`: strip one trailing `.py`; validate the result as a core `FileName` (core design § 5.4; failure →
  `INVALID_PARAMS`); return `_scripts_dir() / f"{name}.py"`. `FileName` has no `/`, `\` or `..`,
  so the path is always a direct child.
- A path is a *saved script* when `not path.is_symlink() and path.is_file()`.
- `_summary(src)`: `ast.get_docstring(ast.parse(src))` first line; on `SyntaxError` or no docstring,
  the first line starting with `#` with leading `#` and spaces removed; else `""`. Cut to 120 chars.

### 5.2 list_scripts
```python
@action("script", access="read")
def list_scripts(ctx: Ctx, offset: int = 0, limit: int = 100) -> dict:
    """List saved scripts in <config dir>/scripts."""
```
Directory missing → empty page. Otherwise `sorted(dir.glob("*.py"))`, keeping saved scripts whose
stem is a valid `FileName`. Returns `ctx.page(paths, offset, limit, build=fn)` (core design § 5.9), so
only the requested slice is read. `fn(path)` → `{"name": stem, "bytes": st_size, "modified": <UTC
ISO 8601, seconds, "Z" suffix>, "summary": _summary(text)}`.

### 5.3 load_script
```python
@action("script", access="read")
def load_script(ctx: Ctx, name: str) -> dict:
    """Return the code of a saved script."""
```
Not a saved script → `NOT_FOUND` ("no saved script <name>") with hint `script.list_scripts`.
Returns `{"name": name, "code": <utf-8 text>}`.

### 5.4 save_script
```python
@action("script", access="write", undo_group=False)
def save_script(ctx: Ctx, name: str, code: str) -> dict:
    """Save Python code as a new named script. Refuses to replace an existing one."""
```
- `len(code.encode("utf-8")) > MAX_SCRIPT_BYTES` → `INVALID_PARAMS` ("script is N bytes; limit
  60000").
- `path.exists() or path.is_symlink()` → `INVALID_PARAMS` ("script <name> exists") with hint
  "delete it first with script.delete_script (confirm: true)". Replacing would discard user data,
  which only a destructive action may do.
- `mkdir(parents=True, exist_ok=True)`; write with `open(path, "x", encoding="utf-8", newline="")`
  (exclusive create; a race loser gets `FileExistsError`, mapped to the same `INVALID_PARAMS`).
- Code is not syntax-checked (work in progress may be saved).
- Returns `{"script": {"name": name, "bytes": n}}`.

### 5.5 delete_script
```python
@action("script", access="destructive", undo_group=False)
def delete_script(ctx: Ctx, name: str) -> dict:
    """Delete a saved script."""
```
Not a saved script → `NOT_FOUND`. `path.unlink()`. Returns `{"deleted": name}`.

## 6. execute_python
```python
@action("script", access="destructive", timeout=60)
def execute_python(ctx: Ctx, code: str, session_id: str = "default", reset: bool = False) -> dict:
    """Run Python in Fusion's main thread; the last expression's value is returned.

    Preloaded: adsk, app, ui, design, root. Variables persist per session_id. stdout/stderr are
    captured. Blocks Fusion while running; a call longer than 60 s continues as a job
    (server.job_status). An endless loop cannot be cancelled. Do not call adsk.doEvents()."""
```
Steps:
1. `SESSION_RE.fullmatch(session_id)` failure → `INVALID_PARAMS`.
2. Parse: `tree = ast.parse(code, "<execute_python>", "exec")`. `SyntaxError e` →
   `INVALID_PARAMS` (`f"SyntaxError: {e.msg} (line {e.lineno})"`), before touching the session.
   If `tree.body` ends with an `ast.Expr`, pop it into `ast.Expression(last.value)`; compile body
   (`"exec"`) and expression (`"eval"`) with filename `<execute_python>`.
3. `_session(ctx, session_id, reset)`: `reset` → `_SESSIONS.pop(session_id, None)`. Get or create
   `{"__name__": "__defusion__", "__builtins__": builtins}`; `move_to_end`; while
   `len(_SESSIONS) > MAX_SESSIONS`: `popitem(last=False)`. Then (every call) import
   `adsk.core, adsk.fusion, adsk.cam` and set `adsk`, `app = ctx.app`, `ui = ctx.ui`,
   `design = ctx.design`, `root = ctx.design.rootComponent if ctx.design else None`, overwriting
   user variables of those names.
4. `out, err = io.StringIO(), io.StringIO()`; `t0 = time.monotonic()`; inside
   `contextlib.redirect_stdout(out), contextlib.redirect_stderr(err)`: `exec(body, ns)`, then
   `value = eval(expr, ns)` when there is an expression, else `None`. When `value is not None`,
   `ns["_"] = value`.
5. `except (Exception, SystemExit) as e` → raise
   `ActionError("FUSION_ERROR", f"{type(e).__name__}: {e}", exception_type=type(e).__name__,
   traceback=<traceback.format_exception text keeping only frames whose filename is
   "<execute_python>", clipped>, stdout=_clip(out), stderr=_clip(err))`. The core undo group
   aborts, rolling back partial model changes (core design § 5.7). The session keeps any names
   bound before the exception.
6. Return
```json
{"session_id": "default", "value": <_json_safe>, "stdout": "...", "stderr": "...", "duration_ms": 12}
```

`_clip(s)`: text longer than `MAX_STREAM_CHARS` keeps the last `MAX_STREAM_CHARS` characters,
prefixed with `"[truncated N chars]\n"`.

`_json_safe(ctx, v, depth)`:
| Value | Output |
|---|---|
| `None`, `bool`, `int`, `str` | as-is |
| `float` | as-is if finite, else `str(v)` |
| `list`/`tuple`/`set` | list of converted items; over 1000 items → first 1000 plus a final `"[+N more]"` string |
| `dict` with all `str` keys | dict of converted values (same 1000 cap on keys) |
| has a non-empty string `entityToken` attribute (`getattr` guarded by `try`) | `ctx.ref(v)` |
| anything else, or `depth > 8` | `{"repr": repr(v)[:MAX_REPR_CHARS]}` plus `"type": v.objectType` when that attribute exists |

### 6.1 Gotchas
- `redirect_stdout` swaps the process-wide `sys.stdout`; prints from other threads during the call
  are captured too. Core logging uses its own file handler, so logs are unaffected.
- The code runs inside the core dispatcher handler; `adsk.doEvents()` can re-enter the dispatcher.
  The help text says not to call it.
- Code that switches or closes the active document is outside the undo group of the document it
  started in; it still runs, without rollback guarantees.
- Sessions live in module memory; `server.restart_addin` clears them.
- After a timeout the call returns a core job (core design § 4); the job result is the § 6 return
  dict.

## 7. Errors (action-specific)
| Action | Condition | Code |
|---|---|---|
| `api_docs` | empty query | `INVALID_PARAMS` |
| `api_docs` | exact name not in the API | `NOT_FOUND` |
| `design_guide` | `guide.md` missing | `NOT_FOUND` |
| script store | bad name | `INVALID_PARAMS` |
| `load_script`, `delete_script` | no such saved script | `NOT_FOUND` |
| `save_script` | too large, or exists | `INVALID_PARAMS` |
| `execute_python` | bad `session_id`, syntax error | `INVALID_PARAMS` |
| `execute_python` | exception raised by the code | `FUSION_ERROR` with `error.exception_type`, `error.traceback`, `error.stdout`, `error.stderr` |

The extra `error.*` keys are `ActionError` keyword extras, which the core envelope copies into
`error` (core design § 5.5, § 5.10).

## 8. Testability
### 8.1 Unit (`tests/test_script.py`, fake adsk, no Fusion)
Fakes added to `tests/fakes/adsk`: `adsk.fusion.ExtrudeFeatures` with a documented method
`createInput(self, profile, operation)` and property `count`; `adsk.fusion.ExtrudeFeatureInput`;
`adsk.fusion.Sketch`; `adsk.core.Base` with property `isValid`; `adsk.cam.CAM`. `CONFIG_DIR` is
monkeypatched to `tmp_path`. Actions are called through the core call pipeline as core tests do.
Test functions are named `test_api_docs_*` (1–2), `test_scripts_*` (3), `test_execute_*` (4–6)
and `test_guide_*` (7).
1. `api_docs` search: `"extrude input"` ranks `adsk.fusion.ExtrudeFeatures.createInput` first;
   `module="cam"` excludes fusion names; paging fields; empty query → `INVALID_PARAMS`.
2. `api_docs` exact: method, class (with `members`), inherited `adsk.fusion.Sketch.isValid`,
   unknown → `NOT_FOUND` with hints; signature has no `self`.
3. Script store: save/list/load round trip; summary from docstring and from `#` comment; exists →
   `INVALID_PARAMS`; 60 001 bytes → `INVALID_PARAMS`; each bad name in REQ-SCR-007 rejected; a
   symlink is neither listed nor loadable; delete without confirm → `CONFIRM_REQUIRED`; delete
   missing → `NOT_FOUND`.
4. `execute_python` value: trailing expression, trailing assignment (`null`), `print` captured,
   stderr captured, `_` set, non-JSON object → `{"repr": ...}`, NaN → `"nan"`, a fake entity with
   `entityToken` → Ref, 1500-item list capped.
5. Sessions: persistence, isolation, `reset`, LRU eviction at 33, preloaded names refreshed after
   the user rebinds `app`.
6. Errors: syntax error → `INVALID_PARAMS`; `raise ValueError("x")` after `print("a")` →
   `FUSION_ERROR` with `exception_type == "ValueError"`, `stdout == "a\n"`, traceback naming
   `<execute_python>`; `SystemExit` caught; long stdout clipped with marker.
7. Guide: `design_guide` returns the file text and `bytes < 24576`; every § 4.1 heading is present
   in order; every backticked `` `word.word` `` whose first word is a tool name is a registered
   `(tool, action)`.

### 8.2 Live (`LIVE_SCENARIOS` in `script.py`; run with `--profile full`)
Each entry is a core § 10 tuple `(name, "script.<action>", async fn(session))` tagged with the
action in the Action column, so `--only script.<action>` selects it; calls go through
`scripts/live_check.call(session, "script", action, params)`; destructive calls (`execute_python`,
`delete_script`) pass `"confirm": true`.

| Scenario | Action | Steps | Pass when |
|---|---|---|---|
| `api_docs_search` | `api_docs` | as REQ-SCR-001 acceptance | as REQ-SCR-001 acceptance |
| `api_docs_exact` | `api_docs` | exact `adsk.fusion.ExtrudeFeatures.createInput`, `adsk.fusion.Sketch`, `adsk.fusion.NoSuchThing` | as REQ-SCR-002 acceptance |
| `api_docs_cam` | `api_docs` | `{"query": "setup", "module": "cam"}` | `total > 0`, all names start `adsk.cam.` |
| `design_guide` | `design_guide` | `design_guide {}` | as REQ-SCR-003 acceptance |
| `scripts_roundtrip` | `save_script` | save `livecheck_tmp`, list, load, delete (confirm) | each step as REQ-SCR-004..006; file gone at end |
| `execute_value` | `execute_python` | as REQ-SCR-008 acceptance | as REQ-SCR-008 acceptance |
| `execute_ref` | `execute_python` | create a box body with the API in code ending `root.bRepBodies.item(root.bRepBodies.count-1)` | `value.type == "BRepBody"`, `value.token` non-empty, and a follow-up `len(design.findEntityByToken(<token>))` returns 1 |
| `execute_session` | `execute_python` | as REQ-SCR-009 acceptance | as REQ-SCR-009 acceptance |
| `execute_rollback` | `execute_python` | as REQ-SCR-010 acceptance | as REQ-SCR-010 acceptance |
| `execute_job` | `execute_python` | `import time; time.sleep(65); 7` | result `status == "running"`; `server.job_status {"job_id": <job_id>}` reaches `state == "done"` with `result.value == 7` within 30 s |
