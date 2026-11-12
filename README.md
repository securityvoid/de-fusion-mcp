# De-Fusion MCP

An add-in that lets an AI agent drive Autodesk Fusion over MCP. It de-fuses the gap between "I want a
bracket" and "why is this sketch under-constrained again?". Terrible puns are included at no extra cost.

De-Fusion runs inside Fusion and serves MCP over HTTP on `127.0.0.1` only. Tools: `sketch`, `model`,
`assembly`, `history`, `inspect`, `view`, `document`, `cam`, `script` and `server`. Each tool takes an
`action` and a `params` object; `action: "help"` lists the actions and shows their schemas. The agent
can also read the modelling guide with `script.design_guide`.

## Install

You need Python 3.10 or newer outside Fusion, and Fusion installed (macOS or Windows).

```
python install.py
```

The installer finds Fusion's bundled Python, installs the pinned libraries from `requirements.lock`
into `De-Fusion/lib` (so the binary wheels match Fusion), links `De-Fusion/` into Fusion's AddIns
folder, writes a default `config.toml` if you have none, and prints the `mcp.json` entry.

- `--fusion-python PATH` uses that Python instead of the newest one it finds.
- `--uninstall` removes the link and `lib/`. Your `config.toml` stays put, because we are not monsters.
- `--folder "My Project/Parts"` prints the url with `?folder=My%20Project%2FParts` (see
  [Folder limit](#folder-limit)). It only changes the printed url; it does not write `config.toml`.
- Exit codes: 0 success, 1 pip failed, 2 Fusion Python not found, 3 something else already lives at
  the link path, 4 invalid `--folder` (checked before anything else).

Then restart Fusion. The add-in starts with Fusion and shows up once in Scripts and Add-Ins as
`De-Fusion`. Do not also add the repo folder with the "+" button; one fusion is plenty, and two
copies would fight over the port.

## mcp.json

Add the entry the installer printed to your MCP client. With the default port:

```json
{
  "mcpServers": {
    "de-fusion": {
      "type": "http",
      "url": "http://127.0.0.1:8642/mcp"
    }
  }
}
```

If you set a token, the entry also carries `"headers": {"Authorization": "Bearer <token>"}`. Append
`?profile=<items>` to the URL to pick a profile for that client.

## Config

`~/.defusion/config.toml` (Windows: `%USERPROFILE%\.defusion\config.toml`). Changes apply on the next
start; run `server.restart_addin` or restart Fusion.

```toml
port = 8642
# token = "..."
log_level = "INFO"
default_profile = ["all", "-script"]
# folder = "My Project/Parts"    # optional default folder limit

[units]
length = "mm"
angle = "deg"

[profiles]
# my-print = ["3dprinting", "-model.coil"]
```

| Key | Default | Valid |
|---|---|---|
| `port` | `8642` | 1024-65535 |
| `token` | none | any non-blank string |
| `log_level` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `default_profile` | `["all", "-script"]` | profile items (see below) |
| `folder` | none | a project-qualified path such as `My Project/Parts` (see [Folder limit](#folder-limit)) |
| `units.length` | `mm` | `mm`, `cm`, `m`, `in`, `ft`, `um` |
| `units.angle` | `deg` | `deg`, `rad` |
| `profiles.<name>` | none | profile items |

A broken config fails closed: the server still starts, but only `server.status` and
`server.job_status` work, and `server.status` shows the config error.

## Profiles

A profile is the set of actions a client may see and call. The default is `default_profile`; a client
overrides it with `/mcp?profile=<items>` (comma-separated, URL-encoded).

| Profile | Items |
|---|---|
| `full` | `all` |
| `readonly` | `-write`, `-destructive` |
| `3dprinting` | `all`, `-cam`, `-model.sheet_*`, `-script` |
| `metalworking` | `all`, `-script` |

An item is `[-]selector`, where the selector is one of:

1. `all`: every action.
2. `read`, `write` or `destructive`: every action of that access class.
3. A tool name: every action of that tool.
4. `tool.pattern`: action names of that tool matching a shell-style pattern (`model.sheet_*`, `model.coil`).
5. A profile name: replaced by that profile's items.

Items apply left to right. If the first item adds, you start from nothing; if it removes, you start
from everything. A selector that matches no action is an error, so typos get caught early.

- `?profile=3dprinting,readonly` is the 3dprinting set minus every write.
- `?profile=readonly` is everything minus every write.
- `?profile=readonly,cam` is read-only plus all CAM actions.

## Token

Set `token` in `config.toml`, or `DEFUSION_TOKEN` in the environment, and every HTTP request must send
`Authorization: Bearer <token>`. Anything else gets a 401. With no token there is no authentication.

Be clear about what protects what. The token is the only access control. The `?profile=` query and
the `confirm` flag are chosen by the client, so they shape what a well-behaved agent sees and
prevent accidents, but they do not restrict a local caller who simply asks for something else. The
server listens on `127.0.0.1` only, but any program on your machine can reach it; set a token if
that matters to you.

## Units

Inputs: a bare number uses the `[units]` defaults from your config (mm and degrees out of the box).
A string is used as written, so `"0.5 in"`, `"90 deg"` and parameter expressions such as `"wall*2"`
all work. Fusion keeps parameter references in the timeline.

Outputs: every length, area, volume, angle and mass comes back in the call's output units, which
default to your config and are named in the result's `units` field. Pass a top-level `units`
(`{"length": "in"}`) to change them for one call. This changes outputs only; it never changes how
inputs are read. Mass is always `kg`; areas and volumes are the length unit squared or cubed.

## Bulk entries

The document actions that work on files take a list of 1 to 50 entries instead of a single value:
`export`, `import` and `import_mesh` take `paths`; `open`, `datafile_refresh`, `datafile_copy`,
`datafile_move` and `datafile_delete` take `files`; `datafile_set` takes `files` of `{file, name}`;
`save`, `set_read_only` and `close` take `documents`; `save_as` takes `documents` of
`{document, name}`. Every other parameter is shared by all entries. The old singular names (`path`,
`file`, `document`, and `name` on `save_as`/`datafile_set`) are rejected with `INVALID_PARAMS`.

```json
{"action": "open", "params": {"files": ["My Project/Bracket", "My Project/Lid"]}}
```

The result is `{"results": [...], "ok": n, "failed": n}`, one record per entry in input order, each
with `ok` and either `result` or `error`. One bad entry does not sink the others; the batch keeps
going like a good dad joke after the first groan. Entries run one at a time, so a later entry sees
what an earlier one did. `close` and `datafile_delete` still need `confirm: true`, once per call.

## Read-only documents

A write to a saved document that is read-only makes it editable first (Fusion's Make Editable),
then does the edit. The envelope's `made_editable` lists the documents that got unlocked. Pass
`params.preserve_read_only: true` to refuse instead: the call fails with `READ_ONLY` and nothing is
touched. Never-saved documents are always editable.

`document.save` and `document.save_as` make the saved file read-only again after saving (Fusion's
Make Read-Only), and each entry result reports `read_only`. Pass `read_only: false` to skip the
lock. `document.set_read_only` locks or unlocks explicitly. Fusion applies these changes through the
cloud, so De-Fusion checks a fresh copy of the file up to 10 times, 1 s apart; if the change never
shows up the entry fails with `READ_ONLY_NOT_APPLIED` (the save itself still happened). Locking
keeps an account's editable-document count down, which matters on plans that cap it.

## Folder limit

Add `?folder=<Project/Path>` (URL-encoded) to the url, or set `folder = "My Project/Parts"` in
`config.toml` as the default, to keep a client inside one folder of the active hub:

```
http://127.0.0.1:8642/mcp?folder=My%20Project%2FParts&profile=3dprinting
```

`python install.py --folder "My Project/Parts"` prints a url with it already set. With a limit:

- every cloud file or folder a document action addresses must be inside the folder or one of its
  subfolders (`My Project/PartsX` is not inside `My Project/Parts`; segments compare exactly);
- `save`, `save_as` and `set_read_only` only touch files inside it, and `save_as` defaults its
  `folder` to the limit;
- every edit of a saved open document checks that the document's file is inside it.

Anything outside fails with `NOT_ALLOWED`. Never-saved documents and read-only queries on open
documents are not limited. A request cannot clear a config `folder`; an empty value or a repeated
`folder` key fails every request on that url.

Like `?profile=`, this is a guardrail for a cooperative client, not access control:
`script.execute_python` and `server.run_text_command` can still reach any file. Fences make good
neighbours, but they are not locks.

Live checks (`scripts/live_check.py`, run against a running add-in) save scratch files in the active
project's root, so a live run needs the config `folder` unset.
