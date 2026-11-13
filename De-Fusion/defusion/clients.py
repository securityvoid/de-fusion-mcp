"""MCP client config files: client table, detection, plan/apply with backup and atomic write (setup design § 2, § 3).

Stdlib only, so everything here is unit-tested without Fusion."""
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from . import config
from .guard import norm_folder

NAME = "de-fusion"
TOML_NAME = "defusion"
MAX_BYTES = 10 * 1024 * 1024
MCP_REMOTE = "mcp-remote@0.16.6"  # exact pinned version; the maintainer bumps it by editing this constant
MAX_TOKEN = 256
MAX_FOLDER = 512
ENV_VAR = "DEFUSION_TOKEN"
_WRITES = ("add", "update", "remove")


@dataclass(frozen=True)
class Platform:
    win: bool
    env: object  # mapping of environment variables
    home: Path

    @property
    def appdata(self):
        """``%APPDATA%`` as a Path, or a skip reason when missing or not absolute."""
        v = self.env.get("APPDATA") or ""
        return Path(v) if v and Path(v).is_absolute() else "APPDATA is not set; set it or add the entry manually"


def platform(env=None, home=None):
    return Platform(sys.platform == "win32", os.environ if env is None else env, Path.home() if home is None else Path(home))


@dataclass(frozen=True)
class Client:
    id: str
    label: str
    terminal: bool  # True -> env-var mode (DEFUSION_TOKEN), False -> literal token
    key: str  # top-level key holding the servers
    fmt: str  # "json" | "toml"
    path: Callable  # (Platform) -> Path, or a skip reason string
    build: Callable  # (url, token | None) -> entry dict


@dataclass
class Source:
    path: object  # Path, or None when the path itself is a skip reason
    data: object  # bytes, or None when the file is absent
    skip: object  # skip reason or None
    base: object  # directory above which symlinks are not checked


@dataclass
class Plan:
    path: object
    action: str  # add | update | remove | unchanged | not-present | skip
    reason: object
    original: object  # bytes or None
    new: object  # bytes or None
    mode: int
    base: object = None


@dataclass
class Result:
    status: str  # ok | unchanged | not-present | skipped
    reason: object
    backup: object


# --- paths and entries ---------------------------------------------------------------------------
def _home(*parts):
    return lambda p: p.home.joinpath(*parts)


def _roaming(win_parts, mac_parts):
    def path(p):
        if not p.win:
            return p.home.joinpath(*mac_parts)
        base = p.appdata
        return base.joinpath(*win_parts) if isinstance(base, Path) else base

    return path


def _override(var, default, name=None):
    """``$var`` (a directory holding ``name``, or the file itself when ``name`` is None), else ``default``."""

    def path(p):
        v = p.env.get(var) or ""
        if not v:
            return default(p)
        if not Path(v).is_absolute():
            return "%s is not an absolute path; set it to an absolute path or unset it" % var
        return Path(v) / name if name else Path(v)

    return path


def _hdr(auth):
    return {"headers": {"Authorization": auth}} if auth else {}


def _env_hdr(token):
    return _hdr(token and "Bearer ${%s}" % ENV_VAR)


def _lit_hdr(token):
    return _hdr(token and "Bearer " + token)


def _desktop(url, token):
    args = ["-y", MCP_REMOTE, url, "--allow-http", "--transport", "http-only"]
    if token:
        args += ["--header", "Authorization:${AUTH_HEADER}"]
    entry = {"command": "npx", "args": args}
    if token:
        entry["env"] = {"AUTH_HEADER": "Bearer " + token}
    return entry


_CLINE = ("Code", "User", "globalStorage", "saoudrizwan.claude-dev", "settings", "cline_mcp_settings.json")

CLIENTS = (
    Client("claude-code", "Claude Code", True, "mcpServers", "json", _home(".claude.json"),
           lambda u, t: {"type": "http", "url": u, **_env_hdr(t)}),
    Client("codex", "Codex CLI", True, "mcp_servers", "toml", _home(".codex", "config.toml"),
           lambda u, t: {"url": u, **({"bearer_token_env_var": ENV_VAR} if t else {})}),
    Client("gemini", "Gemini CLI", True, "mcpServers", "json", _home(".gemini", "settings.json"),
           lambda u, t: {"httpUrl": u, **_env_hdr(t)}),
    Client("cursor", "Cursor", False, "mcpServers", "json", _home(".cursor", "mcp.json"),
           lambda u, t: {"url": u, **_lit_hdr(t)}),
    Client("copilot", "VS Code Copilot", False, "mcpServers", "json",
           _override("COPILOT_HOME", _home(".copilot", "mcp-config.json"), "mcp-config.json"),
           lambda u, t: {"type": "http", "url": u, **_lit_hdr(t), "tools": ["*"]}),
    Client("windsurf", "Windsurf/Devin", False, "mcpServers", "json",
           _roaming(("devin", "mcp_config.json"), (".config", "devin", "mcp_config.json")),
           lambda u, t: {"serverUrl": u, **_lit_hdr(t)}),
    Client("zed", "Zed", False, "context_servers", "json",
           _roaming(("Zed", "settings.json"), (".config", "zed", "settings.json")),
           lambda u, t: {"url": u, **_lit_hdr(t)}),
    Client("cline", "Cline (VS Code)", False, "mcpServers", "json",
           _roaming(_CLINE, ("Library", "Application Support") + _CLINE),
           lambda u, t: {"type": "streamableHttp", "url": u, **_lit_hdr(t), "disabled": False}),
    Client("cline-cli", "Cline CLI", False, "mcpServers", "json",
           _override("CLINE_MCP_SETTINGS_PATH", _home(".cline", "data", "settings", "cline_mcp_settings.json")),
           lambda u, t: {"type": "streamableHttp", "url": u, **_lit_hdr(t), "disabled": False}),
    Client("claude-desktop", "Claude Desktop", False, "mcpServers", "json",
           _roaming(("Claude", "claude_desktop_config.json"),
                    ("Library", "Application Support", "Claude", "claude_desktop_config.json")),
           _desktop),
)


def build_url(port, folder):
    url = "http://127.0.0.1:%d/mcp" % port
    f = norm_folder(folder) if folder else None
    return url + "?folder=" + quote(f, safe="") if f else url


# --- reading and detection -----------------------------------------------------------------------
def _fix(path, what):
    return "%s: %s; fix the permission problem and reopen the dialog" % (path, what)


def read_path(path, base):
    """Source for ``path``; symlinks between ``base`` and the file, an over-10 MB file and OSErrors are skips."""
    try:
        p = path
        while p != base and p != p.parent:
            if p.is_symlink():
                return Source(path, None, "%s: is a symlink; add the entry manually" % path, base)
            p = p.parent
        if not path.exists():
            return Source(path, None, None, base)
        if path.stat().st_size > MAX_BYTES:
            return Source(path, None, "%s: larger than 10 MB; add the entry manually" % path, base)
        return Source(path, path.read_bytes(), None, base)
    except OSError as exc:
        return Source(path, None, _fix(path, exc.strerror), base)


def _base(path, plat):
    """The nearest of home / APPDATA above ``path``, else its own directory (env overrides)."""
    roots = [plat.home] + ([plat.appdata] if isinstance(plat.appdata, Path) else [])
    return next((r for r in roots if r in path.parents), path.parent)


def detect(c, plat=None):
    """True when the client's resolved config file exists."""
    plat = plat or platform()
    path = c.path(plat)
    return isinstance(path, Path) and path.exists()


def read(c, plat=None):
    plat = plat or platform()
    path = c.path(plat)
    if not isinstance(path, Path):
        return Source(None, None, path, None)
    return read_path(path, _base(path, plat))


def read_config():
    return read_path(config.CONFIG_PATH, config.CONFIG_PATH.parent.parent)


# --- planning ------------------------------------------------------------------------------------
class _Skip(Exception):
    pass


class _Dup(ValueError):
    pass


def _pairs(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise _Dup(k)
        d[k] = v
    return d


def _nonfinite(name):
    raise ValueError("%s is not valid JSON" % name)


def _json_edit(c, entry, src, remove):
    """(new bytes | None, entry existed) for a JSON client file."""
    path, orig = src.path, src.data
    text = ""
    data = {}
    if orig is not None:
        try:
            text = orig.decode("utf-8")
            data = json.loads(text, object_pairs_hook=_pairs, parse_constant=_nonfinite)
        except _Dup as exc:
            raise _Skip('%s: duplicate key "%s"; remove the duplicate or add the entry manually' % (path, exc))
        except ValueError as exc:
            if c.id == "zed":
                raise _Skip("%s: contains comments or is not strict JSON; add the entry manually" % path)
            raise _Skip("%s: not strict JSON (%s); fix the file or add the entry manually" % (path, exc))
    section = data.get(c.key, {}) if isinstance(data, dict) else None
    if not isinstance(section, dict):
        raise _Skip('%s: top-level value or "%s" has the wrong type; fix the file or add the entry manually' % (path, c.key))
    existed = NAME in section
    if remove:
        if not existed:
            return None, False
        del section[NAME]
    else:
        section[NAME] = entry
        data[c.key] = section
    m = re.search(r"^[ \t]+\S", text, re.M)
    indent = 4 if m and m.group().startswith("    ") else 2
    out = json.dumps(data, indent=indent, ensure_ascii=False)
    return (out + ("\n" if not text or text.endswith("\n") else "")).encode("utf-8"), existed


_OURS = re.compile(r"^\[\s*mcp_servers\.%s(\.[^\]]+)?\s*\]" % TOML_NAME)


def _toml_block(entry):
    return "[mcp_servers.%s]\n%s\n" % (TOML_NAME, "\n".join("%s = %s" % (k, json.dumps(v)) for k, v in entry.items()))


def _toml_strip(text):
    """``text`` without the defusion table and its sub-tables."""
    keep, skipping = [], False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("["):
            skipping = bool(_OURS.match(line))
        if not skipping:
            keep.append(line)
    return "".join(keep)


def _others(data):
    """``data`` without mcp_servers.defusion (and an emptied mcp_servers)."""
    servers = dict(data.get("mcp_servers", {}))
    servers.pop(TOML_NAME, None)
    rest = {k: v for k, v in data.items() if k != "mcp_servers"}
    return {**rest, "mcp_servers": servers} if servers else rest


def _toml_edit(entry, src, remove):
    path = src.path
    try:
        text = src.data.decode("utf-8") if src.data is not None else ""
        old = tomllib.loads(text)
    except ValueError as exc:
        raise _Skip("%s: not valid TOML (%s); fix the file or add the entry manually" % (path, exc))
    stripped = _toml_strip(text)
    existed = stripped != text
    new = stripped
    if remove:
        if not existed:
            return None, False
    else:
        if new and not new.endswith("\n"):
            new += "\n"
        if new and not new.endswith("\n\n"):
            new += "\n"
        new += _toml_block(entry)
    try:
        new_data = tomllib.loads(new)
        ok = _others(old) == _others(new_data) and (remove or new_data["mcp_servers"][TOML_NAME]["url"] == entry["url"])
    except tomllib.TOMLDecodeError as exc:
        raise _Skip("%s: not valid TOML (%s); fix the file or add the entry manually" % (path, exc))
    if not ok:
        raise _Skip("%s: edit would change other tables; add the entry manually" % path)
    return new.encode("utf-8"), existed


def _mode(path, literal):
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        mode = 0o644
    return mode & ~0o077 if literal else mode


def _plan(c, url, token, src, remove):
    entry = None if remove else c.build(url, token)
    if src.skip:
        return _skip(src, src.skip)
    try:
        new, existed = _toml_edit(entry, src, remove) if c.fmt == "toml" else _json_edit(c, entry, src, remove)
    except _Skip as exc:
        return _skip(src, str(exc))
    mode = _mode(src.path, bool(token) and not c.terminal)
    if new is None:
        action = "not-present"
    elif new == src.data:
        action = "unchanged"
    else:
        action = "remove" if remove else "update" if existed else "add"
    return Plan(src.path, action, None, src.data, new, mode, src.base)


def plan(c, url, token, src):
    return _plan(c, url, token, src, False)


def remove_plan(c, src):
    return _plan(c, None, None, src, True)


def _skip(src, reason):
    return Plan(src.path, "skip", reason, None, None, 0o644, src.base)


def plan_config(port, folder, token, src):
    """Plan for ``config.toml``; ``folder`` is the entered text ('' for none), ``token`` None for no auth."""
    path = src.path
    folder = norm_folder(folder) if folder else None
    want = {"port": port, "folder": folder, "token": token}
    if src.skip:
        return _skip(src, src.skip)

    def skip(err):
        return _skip(src, "config.toml: %s; fix the file or delete it, then Apply again" % err)

    try:
        text = src.data.decode("utf-8") if src.data is not None else config.DEFAULT_TOML
    except UnicodeDecodeError:
        return skip("not valid UTF-8")

    def check(t):
        try:
            _, errors = config._parse(tomllib.loads(t))
        except ValueError as exc:
            return str(exc)
        return "; ".join(errors)

    err = check(text)
    if err:
        return skip(err)
    lines = text.splitlines(keepends=True)
    for key, value in want.items():
        end = next((i for i, l in enumerate(lines) if l.lstrip().startswith("[")), len(lines))
        at = next((i for i in range(end) if re.match(r"%s\s*=" % key, lines[i])), None)
        if at is not None and value is not None and _same(lines[at], key, value):
            continue
        if at is not None:
            del lines[at]
        if value is not None:
            if at is None:
                last = [i for i in range(end) if re.match(r"[A-Za-z_][\w-]*\s*=", lines[i])]
                at = last[-1] + 1 if last else 0
                if at and not lines[at - 1].endswith("\n"):
                    lines[at - 1] += "\n"
            lines.insert(at, "%s = %s\n" % (key, json.dumps(value)))
    new = "".join(lines)
    err = check(new)
    if err:
        return skip(err)
    raw = new.encode("utf-8")
    action = "unchanged" if raw == src.data else "add" if src.data is None else "update"
    return Plan(path, action, None, src.data, raw, _mode(path, token is not None), src.base)


def _same(line, key, value):
    """True when the assignment ``line`` already holds ``value`` (so its spacing and comment stay)."""
    try:
        return tomllib.loads(line).get(key) == value
    except tomllib.TOMLDecodeError:
        return False


# --- applying ------------------------------------------------------------------------------------
def _backup(path):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest, n = Path("%s.defusion-bak-%s" % (path, stamp)), 0
    while dest.exists():
        n += 1
        dest = Path("%s.defusion-bak-%s-%d" % (path, stamp, n))
    shutil.copy2(path, dest)
    return dest


def apply(p):
    """Write ``p`` (backup, then atomic replace) when it is an add, update or remove; nothing otherwise."""
    if p.action == "skip":
        return Result("skipped", p.reason, None)
    if p.action not in _WRITES:
        return Result(p.action, None, None)
    cur = read_path(p.path, p.base)
    if cur.skip:
        return Result("skipped", cur.skip, None)
    if cur.data != p.original:
        return Result("skipped", "%s: changed since the dialog opened; reopen the dialog" % p.path, None)
    backup = tmp = None
    try:
        os.makedirs(p.path.parent, exist_ok=True)
        if p.original is not None:
            backup = _backup(p.path)
        fd, tmp = tempfile.mkstemp(dir=p.path.parent)
        with os.fdopen(fd, "wb") as f:
            f.write(p.new)
        os.chmod(tmp, p.mode)
        os.replace(tmp, p.path)
        tmp = None
    except OSError as exc:
        return Result("skipped", "%s: %s; fix the permission or space problem and Apply again" % (p.path, exc.strerror), backup)
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return Result("ok", None, backup)
