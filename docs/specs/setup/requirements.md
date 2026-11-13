# requirements.md — docs/specs/setup/
> De-Fusion MCP — in-Fusion setup dialog: config, auth and MCP client installation

## Overview
A "De-Fusion Setup" command in Fusion's add-in toolbar panel opens one dialog that writes
`~/.defusion/config.toml` (port, token, folder) and adds or removes the `de-fusion` MCP server entry
in the config files of MCP clients whose config file exists. Client logic is a pure-Python, table-driven module
`De-Fusion/defusion/clients.py` (no `adsk` import); the dialog is `De-Fusion/defusion/setup_ui.py`.
Section references (§) point to `docs/specs/setup/design.md`. A brew cask that installs the files
and link is a separate spec and out of scope.

## Operator ask
2026-11-12: "an in-Fusion button/dialog to set up the MCP: click a few buttons to install into MCP
clients; include auth option; automatic as possible"

## Dependencies
- **DEP-SETUP-001**: Core set `docs/specs/core/` (add-in entry `De-Fusion.py`, `config.py` parsing and
  limits, `guard.norm_folder`, `auth` token rules, `tests/fakes/adsk`, installer url rules in `install.py`).

## Functional Requirements
- **REQ-SETUP-001**: The add-in shall add a "De-Fusion Setup" command button to the Utilities >
  Add-Ins toolbar panel at `run()` and remove the control and the command definition at `stop()`;
  handler references shall live in a module-level list. A failure to create the button shall be
  logged and shall not stop the server from starting.
  - _Acceptance_: With the fake `adsk`, `run()` leaves one control in the panel and one command
    definition; `stop()` leaves none; a second `run()`/`stop()` cycle also leaves none; an exception
    from the UI layer in `run()` still calls `server.start()`.
- **REQ-SETUP-002**: The dialog shall offer a port (default from the existing config or 8642), an
  optional folder limit (empty = none), an auth dropdown (`Generate token`, `Enter my own`, `None`),
  a masked token field used only for `Enter my own`, one ticked checkbox per detected client (undetected and unsupported clients are not listed; when none is
  detected the dialog shows no client checkboxes and OK is disabled), a mode (Apply default, Remove). The default auth is `Enter my own` prefilled with the existing config.toml token when there
  is one, else `Generate token`. Port and folder shall be validated with the core rules (port integer
  1024-65535; folder through `guard.norm_folder`), the own token shall match
  `^[A-Za-z0-9\-._~+/]+=*$`, and own token and folder length shall not exceed 256 (token) and 512 (folder) characters; an
  invalid value shall disable OK. In Remove mode config planning and port, folder and token validation are skipped; OK is still disabled with no client detected.
  - _Acceptance_: With the fake dialog, port `80`, port `abc`, folder `/`, own tokens `"   "`,
    `" abc"`, `"a\nb"`, `"a\"b"`, and a token or folder one character over the limit each disable
    OK; own token `abc-123_`
    enables it; defaults enable it; only detected clients appear, all ticked, and an undetected client has no checkbox; with none detected there are no client checkboxes and OK is disabled; with an
    existing config token the default is `Enter my own` with that token and Apply leaves it unchanged,
    with none it is `Generate token`; in Remove mode port `abc` keeps OK enabled, but with none detected OK is disabled.
- **REQ-SETUP-003**: Apply shall write `~/.defusion/config.toml` (directory from
  `config.CONFIG_DIR`): set `port`, set `folder` or remove it when empty, set `token` or remove it
  for `None`; every other key, table and comment shall be preserved; a missing file shall be created
  from `config.DEFAULT_TOML`; a file written with a literal token shall be mode 0600 (an existing mode is tightened to `mode & ~0o077`), otherwise its existing mode is kept; the write shall be atomic; identical planned bytes shall be reported `unchanged` with no write and no backup. The
  result shall re-parse through `config._parse` before replacing the file. A symlinked file or parent directory is skipped as in REQ-SETUP-005.
  - _Acceptance_: A config with `log_level`, `[units]`, `[profiles]` and comments keeps them
    byte-for-byte outside the edited lines; `None` removes a prior `token`; an existing 0644 config.toml receiving a token becomes 0600 and
    stays 0644 without one; a second identical Apply is `unchanged`; an invalid existing config.toml
    is not modified and is reported with its reason and the action to take.
- **REQ-SETUP-004**: `clients.py` shall define a client table with these supported clients: Claude
  Code, Codex CLI, Gemini CLI, Cursor, VS Code Copilot, Windsurf/Devin, Zed, Cline (VS Code), Cline
  CLI, Claude Desktop, each with config path per platform (macOS and Windows; `$COPILOT_HOME` and
  `$CLINE_MCP_SETTINGS_PATH` honoured; an empty override is ignored, a non-absolute override or a
  missing `APPDATA` on Windows makes the client undetected and not listed; the reason is visible only through `clients.plan`), top-level key, entry builder and token mode. Detection shall
  be true only when that config file exists.
  - _Acceptance_: With a temp `HOME`, creating `~/.cursor/mcp.json` makes only Cursor detected (an
    empty `~/.cursor/` does not); env
    overrides move the Copilot and Cline CLI paths; `COPILOT_HOME=""` uses the default path;
    `CLINE_MCP_SETTINGS_PATH="rel/x.json"` is not listed, `clients.plan` skips it with a reason and creates no file; the table has 10 rows.
- **REQ-SETUP-005**: Merge shall add or update only the `de-fusion` entry (Codex: `defusion`) under
  the client's key, preserving every other key and, for JSON, key order; for `~/.claude.json` only
  top-level `mcpServers` shall change. Before any write of an existing file a backup
  `<file>.defusion-bak-<UTC yyyymmddThhmmssZ>` shall be created; writes shall be atomic (temp file in
  the same directory + `os.replace`); an existing file's permissions shall be preserved unless the write contains a literal token, which
  sets `mode & ~0o077` (a new file 0600); identical planned bytes shall be `unchanged` with no write
  and no backup; a file over 10 MB, a symlink as the file or any directory between the home, `APPDATA` or env-override base and the file (never replaced),
  any OSError in read, backup, makedirs or write (never a write without a completed backup; other
  clients continue and the result screen is still shown), and JSON with a duplicate key shall each be
  skipped with a reason; missing parent directories of a ticked client shall be
  created. A file that cannot be read as valid strict JSON (or TOML for Codex), or whose top-level
  value or target key has the wrong type, shall be skipped with a reported reason and never
  overwritten.
  - _Acceptance_: For each client, a fixture with extra servers and unrelated keys round-trips with
    only the `de-fusion` entry added; merging twice yields identical content; the backup holds the
    original bytes; a fixture with invalid JSON, a JSONC comment (Zed), `mcpServers` as a list, or
    invalid TOML is byte-identical afterwards and reported skipped with a reason; a duplicate-key
    fixture, a fixture of 10 MB + 1 byte (a file of exactly 10 MB is accepted), a symlinked file or parent directory (link and target intact) and a raising
    `shutil.copy2` or `os.makedirs` are skipped with a reason, byte-identical, no backup or temp file;
    a second merge is `unchanged` with one backup in total; a 0644 file keeps 0644 without a token and
    becomes 0600 with a literal token; new token files are 0600.
- **REQ-SETUP-006**: The entry shall use url `http://127.0.0.1:<port>/mcp` plus `?folder=<quote(norm_folder, safe="")>`
  when a folder is set (the `install.py` rule; no profile query) and these shapes. Terminal clients
  use the env-var reference `DEFUSION_TOKEN`: Claude Code `{"type":"http","url","headers":{"Authorization":"Bearer ${DEFUSION_TOKEN}"}}`;
  Codex `[mcp_servers.defusion]` with `url` and `bearer_token_env_var = "DEFUSION_TOKEN"`; Gemini CLI
  `mcpServers.de-fusion` `{"httpUrl","headers":{"Authorization":"Bearer ${DEFUSION_TOKEN}"}}`
  (subject to REQ-SETUP-011). GUI clients receive the literal token: Cursor `{"url","headers"}`;
  VS Code Copilot `{"type":"http","url","headers","tools":["*"]}`; Windsurf/Devin `{"serverUrl","headers"}`;
  Zed under `context_servers` `{"url","headers"}`; Cline (both) `{"type":"streamableHttp","url","headers","disabled":false}`;
  Claude Desktop (exact pinned `mcp-remote@0.16.6`, a constant in `clients.py`) `{"command":"npx","args":["-y","mcp-remote@0.16.6","<url>","--allow-http","--transport","http-only","--header","Authorization:${AUTH_HEADER}"],"env":{"AUTH_HEADER":"Bearer <token>"}}`.
  With auth `None` no `headers`, `Authorization`, `--header`/`env` or token is written for any client.
  - _Acceptance_: Per-client golden entries for token and no-token cases; Codex TOML contains
    `bearer_token_env_var` and no token text; Cursor/Copilot/Windsurf/Zed/Cline entries contain
    the literal token and no `${`; the Desktop entry contains the literal token in `env.AUTH_HEADER`,
    `${AUTH_HEADER}` only in its args, no `${DEFUSION_TOKEN}`, and `args[1]` matches
    `mcp-remote@\d+\.\d+\.\d+`; no literal-mode entry contains `${DEFUSION_TOKEN}`; a folder of `My Project/Parts` yields `?folder=My%20Project%2FParts`;
    no-auth entries contain no `Authorization`.
- **REQ-SETUP-007**: For Codex, merge and remove shall edit the TOML text, replacing or inserting
  only the `[mcp_servers.defusion]` table and its sub-tables, and shall verify the new text parses
  with `tomllib` and that every other top-level table is unchanged before replacing the file.
  - _Acceptance_: A config with other `[mcp_servers.*]` tables, comments and a `[mcp_servers.defusion.env]`
    sub-table keeps everything else byte-identical after merge and after remove; a result that fails
    to parse leaves the file untouched and reports the reason.
- **REQ-SETUP-008**: A "Remove" action shall delete only the `de-fusion` (Codex `defusion`) entry from
  each ticked client, after a backup, leaving other content unchanged; a client without the entry
  shall be reported "not-present" with no write and no backup; Remove shall not touch `config.toml` and skips config planning and port, folder and token validation.
  - _Acceptance_: Merge then remove restores the original parsed content for every client; remove on
    a file without the entry leaves it byte-identical and creates no backup file.
- **REQ-SETUP-009**: Apply and Remove shall act on the files as read when the dialog opened: each file (config.toml and each
  client file) is read and planned once per dialog session (bytes cached), and a file
  changed since the dialog opened is skipped with `changed since the dialog opened; reopen the dialog`. Execute shall apply config.toml
  first; in Apply mode a skipped or failed config.toml plan, whatever the auth choice, writes no client and reports each
  ticked client `skipped: config.toml not updated; fix config.toml and Apply again`. After the action a result screen, a second
  command dialog with read-only text boxes (not a `messageBox`), shall list one `<label>: <status word>` line per client (`ok` /
  `skipped: <reason>` / `not-present` / `unchanged`), the backup path, the export line, the token and
  the warnings, and the note that port and token changes apply after Utilities > Add-Ins >
  De-Fusion > Stop then Run, or a Fusion restart. Every skip message shall name the
  item and the action to take.
  - _Acceptance_: With the fake dialog, the result dialog's read-only inputs hold one line per ticked client (`config.toml` line first) with the exact wording
    `ok` / `skipped: <reason>` / `not-present` / `unchanged`, the backup path, the export line and the
    restart note (contains `Add-Ins`, not `restart_addin`); a file mutated after the dialog opened is `skipped:
    changed since the dialog opened; reopen the dialog`, keeps the mutated bytes and has no backup; with an invalid existing
    config.toml and any auth choice (including `None`) every ticked client file is byte-identical, has no backup and is
    reported skipped; a raising client does not stop the next client or the result dialog; 20 input
    changes cause one read of config.toml and one per client.
- **REQ-SETUP-010**: Generated tokens shall be `secrets.token_urlsafe(32)`, generated once per dialog
  session and shown unmasked in a read-only box. In Apply mode, when a terminal client (Claude Code, Codex CLI,
  Gemini CLI) is ticked and auth is not `None`, the `export` and `token` boxes of the dialog and result screens shall show the line
  `export DEFUSION_TOKEN=<token>` in a copyable read-only box and shall warn that Codex ignores the
  server until the variable is set. De-Fusion shall never edit a shell profile. In Apply mode, when Claude Desktop
  is ticked and `npx` is not on `PATH` (`shutil.which`), the dialog shall warn that it needs Node.js.
  - _Acceptance_: Two dialog sessions produce different 43-character tokens, the same token across
    refreshes of one session; the export line appears only in Apply mode with a ticked terminal client and auth
    other than `None` (Remove mode shows no export line, token or Codex warning); the npx warning appears only in Apply mode with Claude Desktop ticked and `which` returning
    `None`; no code path writes `.zshrc`, `.bashrc`, `.profile` or the registry.
- **REQ-SETUP-011**: The Gemini CLI header env expansion (`${DEFUSION_TOKEN}` in `headers`) shall be
  tested against a stub MCP server with the real Gemini CLI. If it expands, the env-var form stays;
  if not, Gemini shall switch to a literal token in the file (GUI-mode shape) and design § 3.3 shall
  record the result.
  - _Acceptance_: design § 3.3 records `expands` or `literal fallback` with the Gemini CLI version
    and the stub's observed Authorization header; the Gemini row and golden tests match the record.
- **REQ-SETUP-012**: All dialog and command handlers shall run on the Fusion main thread; the server
  thread shall never call UI APIs. `setup_ui.py` shall be imported lazily from `run()` so a UI
  failure cannot block the server, and `clients.py` shall import nothing from `adsk`.
  - _Acceptance_: `grep -n "adsk" De-Fusion/defusion/clients.py` finds nothing; `grep -rn "setup_ui"
    De-Fusion/defusion/server.py De-Fusion/defusion/dispatch.py` finds nothing; importing `clients`
    succeeds with `adsk` absent from `sys.modules`.
- **REQ-SETUP-013**: Paths marked inferred (Windows Gemini, Windows Cline, Cline CLI, Zed, Windsurf/Devin Windows) shall be tested as far as
  the machine allows and the outcome (confirmed, not testable) recorded per client in design § 3.2.
  - _Acceptance_: design § 3.2 has a status for each inferred path; none says "unverified" without
    "not testable here".
- **REQ-SETUP-014**: README.md shall have a "Setup dialog" section (button location, what Apply
  writes, token placement per client type, Remove, restart note, the `export DEFUSION_TOKEN` step and
  the Codex warning, unsupported clients (README only; not shown in the dialog), Claude Desktop needing Node.js), and design § 6 shall give
  an install-time smoke note.
  - _Acceptance_: `tests/test_readme.py` finds the section containing `DEFUSION_TOKEN`, `Remove`
    and `restart`; design § 6 lists the smoke steps.
