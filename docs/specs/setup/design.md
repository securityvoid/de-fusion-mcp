# design.md — docs/specs/setup/
> De-Fusion MCP — setup dialog

## 1. Overview
Two new modules in `De-Fusion/defusion/`:
- `clients.py` — stdlib-only, no `adsk`. Client table, detection, entry builders, merge/remove,
  backup, atomic write, `config.toml` editing. Everything the dialog applies goes through it, so
  every behaviour is unit-tested without Fusion.
- `setup_ui.py` — the Fusion command, dialog and handlers. Imported lazily by `De-Fusion.py`
  `run()` inside `try/except` (REQ-SETUP-012); `stop()` removes the UI.

Reused: `config.CONFIG_DIR/CONFIG_PATH/DEFAULT_TOML/_parse` (parse + validate), `guard.norm_folder`,
`install.py` url rule (re-implemented in `clients.build_url`, 3 lines; `install.py` is not importable
from the add-in), `tests/fakes/adsk`. No changes to server, dispatch or any tool module.

Files touched: `De-Fusion/defusion/clients.py`, `De-Fusion/defusion/setup_ui.py`, `De-Fusion/De-Fusion.py`,
`tests/test_clients.py`, `tests/test_setup_ui.py`, `tests/fakes/adsk/core.py` (append-only fakes),
`README.md` ("Setup dialog" section), `tests/test_readme.py` (section check).

## 2. `clients.py` interface
```python
@dataclass(frozen=True)
class Client:
    id: str            # "claude-code", "codex", "gemini", "cursor", "copilot", "windsurf",
                       # "zed", "cline", "cline-cli", "claude-desktop"
    label: str
    terminal: bool     # True -> env-var mode (DEFUSION_TOKEN), False -> literal token
    key: str           # top-level key: mcpServers | context_servers | mcp_servers (codex, entry name defusion)
    fmt: str           # "json" | "toml"
    path: Callable[[Platform], Path | str]    # Path, or a skip reason (§ 2.6)
    build: Callable[[str, str | None], dict]  # (url, token|None) -> entry dict
CLIENTS: tuple[Client, ...]
def detect(c) -> bool
def build_url(port, folder) -> str
def read(c) -> Source                 # bytes | None (absent) | skip reason; once per dialog session (§ 4)
def plan(c, url, token, src) -> Plan # Plan(path, action, reason, original, new, mode)
                                     # action: add|update|remove|unchanged|not-present|skip
def apply(plan) -> Result            # backup + atomic write; Result(status, reason, backup)
                                     # status: ok|unchanged|not-present|skipped
def remove_plan(c, src) -> Plan
def plan_config(port, folder, token, src) -> Plan   # config.toml; src = cached bytes like plan
MAX_BYTES = 10 * 1024 * 1024         # larger files are skipped (§ 2.6)
MCP_REMOTE = "mcp-remote@0.16.6"     # exact pinned version; the maintainer bumps it by editing this constant
```
`Platform` is `sys.platform` + env (`HOME`, `APPDATA`, `COPILOT_HOME`, `CLINE_MCP_SETTINGS_PATH`)
read through one `env` parameter defaulting to `os.environ`/`Path.home()` so tests inject a temp home.
`Platform` is `sys.platform` plus the env mapping; any platform other than `win32` uses the macOS column.
Entry name constant `NAME = "de-fusion"`; Codex `TOML_NAME = "defusion"`. Setup-defined limits (core has
none): `MAX_TOKEN = 256`, `MAX_FOLDER = 512` characters.

### 2.1 Plan / apply split
`plan()` computes the new bytes from `src` without writing. When the new bytes equal `original` the action is `unchanged`.
`apply(plan)` writes (and backs up) only for `add`/`update`/`remove`; `skip`, `unchanged` and
`not-present` write nothing and create no backup, so only planned changes are written. Apply
re-reads the file and refuses if its bytes differ from the plan's `original`: `skipped: <path>:
changed since the dialog opened; reopen the dialog`. Result lines: § 4 Template.

### 2.2 JSON clients
`json.loads` (strict) with an `object_pairs_hook` that builds a dict (keeps order) and raises on a
duplicate key: skip `<path>: duplicate key "<k>"; remove the duplicate or add the entry manually`.
Invalid JSON: `<path>: not strict JSON (<error>); fix the file or add the entry manually`.
Top-level must be an object and `data[key]` absent or an object, else
`<path>: top-level value or "<key>" has the wrong type; fix the file or add the entry manually`. Set `data[key]["de-fusion"] = entry`
(update keeps position, add appends), `json.dumps(data, indent=<4 if the first indented line of the original starts with 4 spaces, else 2>,
ensure_ascii=False)` + trailing newline if the original had one; a new file uses indent 2 and a trailing newline. `~/.claude.json` is loaded and
dumped whole but only `data["mcpServers"]` is assigned; the JSON round-trip preserves all other
values. Zed `settings.json` containing comments fails strict parse → skip `<path>: contains comments or is
not strict JSON; add the entry manually`.

### 2.3 Codex TOML
Text edit. Find table headers with `^\[\s*mcp_servers\.defusion(\.[^\]]+)?\s*\]` through the next
header line not matching; remove those blocks (and `[mcp_servers.defusion.env]` etc.), then always append
the new block at the end of the text, preceded by one blank line unless the text is empty or already ends with a blank line:
```
[mcp_servers.defusion]
url = "http://127.0.0.1:8642/mcp"
bearer_token_env_var = "DEFUSION_TOKEN"
```
(no `bearer_token_env_var` for auth None). Validation: `tomllib.loads(new)` succeeds, `old_data`
with the `mcp_servers.defusion` key removed equals `new_data` likewise, and the new
`mcp_servers.defusion.url` equals the url. Else skip `<path>: not valid TOML (<error>); fix the file or add the entry manually` (or, for a failed
verification, `<path>: edit would change other tables; add the entry manually`). Live-tested facts: plain http
loopback works; if `DEFUSION_TOKEN` is unset Codex silently skips the server (hence the warning,
REQ-SETUP-010).

### 2.4 Backup, atomic write, permissions
- Symlink: if the file or any directory component between the base (`Path.home()`, `APPDATA` or the
  env override's directory) and the file is a symlink (`config.toml` included), it is skipped, never
  replaced: `<path>: is a symlink; add the entry manually`.
- Backup: `shutil.copy2(path, f"{path}.defusion-bak-{utcnow:%Y%m%dT%H%M%SZ}")` before writing an
  existing file; if a same-name backup exists, append `-1`, `-2`...
- Write: `tempfile.mkstemp(dir=path.parent)`, write bytes, `os.chmod(tmp, mode)` where `mode` is
  the existing file's `stat.S_IMODE` (new file: `0o644`); when the written content contains a literal
  token (any file, `config.toml` included) `mode &= ~0o077`, so a new token file is `0o600` and an
  existing `0o644` one becomes `0o600`; without a literal token the existing mode is kept. Then
  `os.replace`. On any exception the temp file is removed and the original is untouched.
- Missing parent directories are created with `os.makedirs(exist_ok=True)` only for ticked clients.
- Any `OSError` in read, backup, `makedirs` or write makes `apply` return
  `Result("skipped", "<path>: <strerror>; fix the permission or space problem and Apply again", None)`;
  nothing is written unless the backup of an existing file completed.

### 2.5 `config.toml` editing
Line-based, top-level region only (lines before the first `[table]` header): replace the first
`^port\s*=` / `^token\s*=` / `^folder\s*=` line (also a commented `# token = ...` template line is
left alone), else insert before the first table header (after the last top-level assignment).
Removing deletes the assignment line. Values written with `json.dumps` (valid TOML basic strings
for these ASCII/UTF-8 values): port as an int, folder as the `norm_folder` string, token as entered or generated. A missing file starts from `config.DEFAULT_TOML`. Before replacing:
`tomllib.loads` must parse and `config._parse` must return no errors; an existing file that already
fails either check is skipped (never overwritten) with `config.toml: <error>; fix the file or delete
it, then Apply again`. Mode, symlink and OSError rules: § 2.4.

### 2.6 Env paths, size bound
An empty env override (`COPILOT_HOME`, `CLINE_MCP_SETTINGS_PATH`) is ignored (default path used). A
non-absolute override: `path()` returns the reason `<VAR> is not an absolute path; set it to an
absolute path or unset it`; a missing or non-absolute `APPDATA` on Windows: `APPDATA is not set;
set it or add the entry manually`. Such a client is undetected and its plan is `skip` with that
reason; nothing is created. A client is detected only when its config file (the resolved path, including an env override) exists; its parent directory is not considered. Such a client does not appear in the dialog; the reason is visible only through `clients.plan`. `clients.plan`/`apply` called directly for a valid client whose file is absent still add the entry and create the file and its missing parent directories (§ 2.4); the dialog never does. `read()` skips a file over `MAX_BYTES` (`<path>: larger than 10 MB; add
the entry manually`).

## 3. Client table
### 3.1 Entries and paths
| id | label | mode | file (macOS / Windows) | key |
|---|---|---|---|---|
| claude-code | Claude Code | env | `~/.claude.json` | `mcpServers` |
| codex | Codex CLI | env | `~/.codex/config.toml` | `mcp_servers` (name `defusion`) |
| gemini | Gemini CLI | env (§ 3.3) | `~/.gemini/settings.json` | `mcpServers` |
| cursor | Cursor | literal | `~/.cursor/mcp.json` | `mcpServers` |
| copilot | VS Code Copilot | literal | `$COPILOT_HOME/mcp-config.json`, else `~/.copilot/mcp-config.json` | `mcpServers` |
| windsurf | Windsurf/Devin | literal | `~/.config/devin/mcp_config.json` / `%AppData%\devin\mcp_config.json` | `mcpServers` |
| zed | Zed | literal | `~/.config/zed/settings.json` / `%APPDATA%\Zed\settings.json` | `context_servers` |
| cline | Cline (VS Code) | literal | `~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json` / `%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json` | `mcpServers` |
| cline-cli | Cline CLI | literal | `$CLINE_MCP_SETTINGS_PATH`, else `~/.cline/data/settings/cline_mcp_settings.json` | `mcpServers` |
| claude-desktop | Claude Desktop | literal (env block) | `~/Library/Application Support/Claude/claude_desktop_config.json` / `%APPDATA%\Claude\claude_desktop_config.json` | `mcpServers` |

`~` paths not listed per platform use `Path.home()` on both. Entry shapes: REQ-SETUP-006.

### 3.2 Inferred paths (REQ-SETUP-013)
The inferred paths (REQ-SETUP-013) are Windows Gemini, Windows Cline, Cline CLI, Zed and
Windsurf/Devin Windows, inferred from product docs. TASK-SETUP-006 tests what the machine allows and records one line per path here:
`confirmed (<how>)` or `not testable here`. Recorded values (2026-11-12, web research; no Windows machine here):
- Windows Gemini `%USERPROFILE%\.gemini\settings.json`: confirmed (Gemini CLI configuration docs, geminicli.com; `~` is the home dir).
- Windows Cline `%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json`: not testable here (third-party guides only; Cline 4.x guides call it legacy, migrated to `~/.cline/data/settings/`).
- Cline CLI `~/.cline/data/settings/cline_mcp_settings.json`: confirmed (docs.cline.bot CLI configuration); `CLINE_MCP_SETTINGS_PATH` not in official docs, third-party guides only.
- Zed `%APPDATA%\Zed\settings.json`: confirmed (Zed docs and guides; `url` + `headers` entries are remote servers over Streamable HTTP or SSE; no `source` key needed in examples).
- Windsurf/Devin Windows `%APPDATA%\devin\mcp_config.json`: not testable here (third-party guides only; official Windsurf/Devin CLI docs name `~/.codeium/windsurf/mcp_config.json`).

### 3.3 Gemini header expansion (REQ-SETUP-011)
Result: `expands`. Gemini CLI 0.68.0 (`npx -y @google/gemini-cli`, temp `HOME`, folder trusted via
`trustedFolders.json`). Method: a stdlib `http.server` stub that logs request headers and answers MCP
`initialize`; `~/.gemini/settings.json` held `httpUrl` and `headers.Authorization = "Bearer ${DEFUSION_TOKEN}"`;
`DEFUSION_TOKEN=probe gemini mcp list` reported `Connected` and the stub observed
`Authorization: Bearer probe` on every POST and GET. The env-var form stays; the Gemini row needs no change.

## 4. Dialog (`setup_ui.py`)
- Command `defusion_setup`, name "De-Fusion Setup", added to the panel resolved by
  `ui.allToolbarPanels.itemById(PANEL_ID)` (verified in Fusion 2026-11-12: panel "Add-ins" holds the button;
  id `SolidScriptsAddinsPanel`; fallback logs and skips the button). `stop()` calls
  `control.deleteMe()` and `cmd_def.deleteMe()`. `_handlers = []` module list holds every created
  handler (`CommandCreated`, `InputChanged`, `Execute`).
- Inputs: `port` (StringValueInput), `folder` (StringValueInput), `auth` (DropDownCommandInput
  TextListDropDownStyle: Generate token / Enter my own / None), `own_token` (StringValueInput with
  `isPassword = True`, visible only for Enter my own), `gen_token` (read-only TextBoxCommandInput,
  visible only for Generate token), one ticked BoolValueCommandInput per detected client only (no
  undetected or unsupported client appears; when none is detected no client checkbox is created and `isOKButtonEnabled = False`), an npx
  warning text, and a `mode` dropdown (Apply / Remove).
- Defaults: mode Apply; auth `Enter my own` prefilled with the token when `config.toml` has one
  (Apply then leaves it `unchanged`), else `Generate token`. The generated token is created once in
  `CommandCreated`.
- Each client file and `config.toml` is read once per dialog session in `CommandCreated` (`clients.read`, cached
  bytes, 10 MB bound); `InputChanged` plans from the cache and never re-reads. Execute's Apply
  re-reads per § 2.1.
- `InputChanged` plans via `clients.plan_config`/`plan` and validates port (integer 1024-65535), folder
  (`norm_folder`, at most `MAX_FOLDER`) and own token (`^[A-Za-z0-9\-._~+/]+=*$`, at most `MAX_TOKEN`);
  `isOKButtonEnabled` is true only when all are valid and a client is detected. In Remove mode
  `plan_config` and the port/folder/token validation are skipped; OK is still disabled with no client detected.
- `Execute` (OK, labelled "Apply" / "Remove" per mode), Apply mode: apply the `config.toml` plan
  first. When that plan is `skip` or its apply is `skipped`, whatever the auth choice, apply no client
  plan and report each ticked client `skipped: config.toml not updated; fix config.toml and Apply
  again`. Otherwise apply each client plan, catching any per-client exception into
  `skipped: <path>: <error>; Apply again` so the remaining clients and the result dialog still run.
  Remove mode applies client plans only. No second prompt.
- Template. Result: one line
  `<label>: <status word>` per ticked client in table order, `config.toml` first; status word is `ok`, `unchanged`, `not-present` or `skipped: <reason>`.
- Result: Execute stores a `Results` object in module variable `_result`, then calls
  `result_def.execute()` on a second command `defusion_setup_result` (definition created beside
  `defusion_setup`, deleted in `stop()`, its handler in `_handlers`; OK only, no Cancel). Its
  `CommandCreated` reads `_result` and builds read-only `TextBoxCommandInput`s: `results` (one line per ticked client, config.toml line, backup paths, and
  the restart note `Port and token changes apply after De-Fusion restarts: Utilities > Add-Ins >
  De-Fusion > Stop, then Run, or restart Fusion`), `export` (`export DEFUSION_TOKEN=<token>`, only
  in Apply mode with a ticked terminal client and auth not `None`), `token`, and `warnings` (Codex, npx; both only in Apply mode). The
  `export` line and token (Apply mode only) show in their own `export` and `token` boxes before OK. No `ui.messageBox`.
- All of it runs on the Fusion main thread (command events); nothing here is reachable from the
  uvicorn thread.

## 5. Add-in wiring
`De-Fusion.py` `run()` after `server.start()` (inside its own `try/except` logging
`"De-Fusion Setup button failed"`): `from defusion import setup_ui; setup_ui.add()`; `stop()` calls
`setup_ui.remove()` in `try/except` before `server.stop()`. `setup_ui` imports `adsk` at call time
inside `add()`, not at module top, so tests import it with the fake.

## 6. Tests and install-time smoke
- `tests/test_clients.py`: per-client cases for REQ-SETUP-001..006 acceptance plus the § 2 messages (detection by file existence only; an empty app directory is not detected);
  a 4-space and a 2-space JSON fixture keep their indent; Codex block in the middle gives exact bytes;
  a Linux-injected platform yields the macOS paths; `config.toml` input `/A/B/` gives `folder = "A/B"`;
  `adsk` not imported.
- `tests/test_setup_ui.py`: uses `tests/fakes/adsk` (extended with command, toolbar-panel, input
  and drop-down fakes); the REQ-SETUP acceptance cases through the fake, the § 4 result template for add,
  update, unchanged, not-present and skip, OK state per validation case, the no-client-detected dialog (no client checkboxes, OK disabled), undetected clients absent, and after Execute the fake records the executed result definition
  whose inputs hold the lines.
- Install-time smoke (operator): load the add-in, click "De-Fusion Setup", tick one detected
  client, Apply, confirm the backup file and the entry, restart De-Fusion, confirm the client
  connects; Remove and confirm the entry is gone.
