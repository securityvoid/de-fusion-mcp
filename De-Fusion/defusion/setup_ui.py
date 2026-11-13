"""The "De-Fusion Setup" command: one dialog that writes config.toml and the MCP client entries (setup design § 4).

All handlers run on Fusion's main thread (command events). ``adsk`` is imported inside ``add()`` so tests import
this module with the fake."""
import logging
import re
import secrets
import shutil
import tomllib
from dataclasses import dataclass

from . import clients, config
from .guard import norm_folder

log = logging.getLogger("defusion")

PANEL_ID = "SolidScriptsAddinsPanel"  # verified in Fusion 2026-11-12 (design §4)
CMD_ID = "defusion_setup"
RESULT_ID = "defusion_setup_result"
AUTH = ("Generate token", "Enter my own", "None")
MODES = ("Apply", "Remove")
TOKEN_RE = r"[A-Za-z0-9\-._~+/]+=*"
RESTART = ("Port and token changes apply after De-Fusion restarts: "
           "Utilities > Add-Ins > De-Fusion > Stop, then Run, or restart Fusion")
CODEX_WARN = "Codex ignores the De-Fusion server until DEFUSION_TOKEN is set in its environment."
NPX_WARN = "Claude Desktop needs Node.js (npx) and it was not found on PATH; install Node.js."

_handlers = []  # every created handler, so Fusion's weak references stay alive


@dataclass
class Session:
    """What one dialog session read and generated; the dialog plans from it and never re-reads."""

    config_src: object
    found: list
    sources: dict
    token: str


@dataclass
class Results:
    lines: list
    backups: list
    export: str
    token: str
    warnings: str


_session = None
_result = None


def _handler(base, fn):
    class H(base):
        def notify(self, args):
            try:
                fn(args)
            except Exception:
                log.exception("De-Fusion Setup handler failed")

    h = H()
    _handlers.append(h)
    return h


def _ui():
    import adsk.core

    return adsk.core.Application.get().userInterface


def add():
    import adsk.core

    ui = _ui()
    panel = ui.allToolbarPanels.itemById(PANEL_ID)
    if panel is None:
        log.error("De-Fusion Setup button skipped: toolbar panel %s not found", PANEL_ID)
        return
    remove()
    setup = ui.commandDefinitions.addButtonDefinition(CMD_ID, "De-Fusion Setup", "Configure De-Fusion and MCP clients", "")
    setup.commandCreated.add(_handler(adsk.core.CommandCreatedEventHandler, _created))
    result = ui.commandDefinitions.addButtonDefinition(RESULT_ID, "De-Fusion Setup Result", "", "")
    result.commandCreated.add(_handler(adsk.core.CommandCreatedEventHandler, _result_created))
    panel.controls.addCommand(setup)


def remove():
    ui = _ui()
    panel = ui.allToolbarPanels.itemById(PANEL_ID)
    control = panel.controls.itemById(CMD_ID) if panel is not None else None
    if control is not None:
        control.deleteMe()
    for cmd_id in (CMD_ID, RESULT_ID):
        definition = ui.commandDefinitions.itemById(cmd_id)
        if definition is not None:
            definition.deleteMe()
    _handlers.clear()


# --- dialog --------------------------------------------------------------------------------------
def _created(args):
    import adsk.core

    global _session
    cmd = args.command
    src = clients.read_config()
    cfg = None
    try:
        parsed, errors = config._parse(tomllib.loads((src.data or b"").decode("utf-8")))
        cfg = None if errors else parsed
    except ValueError:
        pass
    found = [c for c in clients.CLIENTS if clients.detect(c)]
    _session = Session(src, found, {c.id: clients.read(c) for c in found}, secrets.token_urlsafe(32))
    port = cfg.port if cfg else config.DEFAULT_PORT
    folder = cfg.folder if cfg and cfg.folder else ""
    own = cfg.token if cfg and cfg.token else ""
    inputs = cmd.commandInputs
    inputs.addStringValueInput("port", "Port", str(port))
    inputs.addStringValueInput("folder", "Folder limit (empty = none)", folder)
    auth = inputs.addDropDownCommandInput("auth", "Auth", adsk.core.DropDownStyles.TextListDropDownStyle)
    for name in AUTH:
        auth.listItems.add(name, name == (AUTH[1] if own else AUTH[0]))
    inputs.addStringValueInput("own_token", "Token", own).isPassword = True
    inputs.addTextBoxCommandInput("gen_token", "Generated token", _session.token, 1, True)
    for c in found:
        inputs.addBoolValueInput("client_" + c.id, c.label, True, "", True)
    inputs.addTextBoxCommandInput("export", "Export", "", 2, True)
    inputs.addTextBoxCommandInput("warnings", "Warnings", "", 3, True)
    mode = inputs.addDropDownCommandInput("mode", "Mode", adsk.core.DropDownStyles.TextListDropDownStyle)
    for name in MODES:
        mode.listItems.add(name, name == MODES[0])
    cmd.inputChanged.add(_handler(adsk.core.InputChangedEventHandler, lambda a: _refresh(a.inputs, cmd)))
    cmd.execute.add(_handler(adsk.core.CommandEventHandler, lambda a: _execute(cmd.commandInputs)))
    _refresh(inputs, cmd)


def _value(inputs, input_id):
    return inputs.itemById(input_id).value


def _selected(inputs, input_id):
    return inputs.itemById(input_id).selectedItem.name


def _validate(inputs):
    """(valid, port, folder, token) for Apply mode; token is None for auth None."""
    bad = (False, None, None, None)
    port, folder = _value(inputs, "port"), _value(inputs, "folder").strip()
    if not re.fullmatch("[0-9]{1,5}", port) or not 1024 <= int(port) <= 65535:
        return bad
    if folder and (norm_folder(folder) is None or len(folder) > clients.MAX_FOLDER):
        return bad
    auth, token = _selected(inputs, "auth"), None
    if auth == AUTH[1]:
        token = _value(inputs, "own_token")
        if not re.fullmatch(TOKEN_RE, token) or len(token) > clients.MAX_TOKEN:
            return bad
    if auth == AUTH[0]:
        token = _session.token
    return True, int(port), folder, token


def _ticked(inputs):
    return [c for c in _session.found if _value(inputs, "client_" + c.id)]


def _plans(inputs):
    """(valid, config plan | None, [(client, plan)], token) for the current inputs, from the session cache."""
    if _selected(inputs, "mode") == MODES[1]:
        return True, None, [(c, clients.remove_plan(c, _session.sources[c.id])) for c in _ticked(inputs)], None
    valid, port, folder, token = _validate(inputs)
    if not valid:
        return False, None, [], None
    url = clients.build_url(port, folder)
    cfg_plan = clients.plan_config(port, folder, token, _session.config_src)
    return True, cfg_plan, [(c, clients.plan(c, url, token, _session.sources[c.id])) for c in _ticked(inputs)], token


def _extras(ticked, token):
    """(export line, warnings) shown in Apply mode only."""
    terminal = any(c.terminal for c in ticked)
    export = "export DEFUSION_TOKEN=%s" % token if terminal and token else ""
    warnings = []
    if export and any(c.id == "codex" for c in ticked):
        warnings.append(CODEX_WARN)
    if any(c.id == "claude-desktop" for c in ticked) and shutil.which("npx") is None:
        warnings.append(NPX_WARN)
    return export, "\n".join(warnings)


def _refresh(inputs, cmd):
    remove = _selected(inputs, "mode") == MODES[1]
    auth = _selected(inputs, "auth")
    inputs.itemById("own_token").isVisible = not remove and auth == AUTH[1]
    inputs.itemById("gen_token").isVisible = not remove and auth == AUTH[0]
    cmd.okButtonText = "Remove" if remove else "Apply"
    valid, token = True, None
    if not remove:
        valid, _, _, token = _validate(inputs)
    export, warnings = ("", "") if remove or not valid else _extras(_ticked(inputs), token)
    cmd.isOKButtonEnabled = valid and bool(_session.found)
    inputs.itemById("export").text = export
    inputs.itemById("warnings").text = warnings


def _safe_apply(p):
    try:
        return clients.apply(p)
    except Exception as exc:
        return clients.Result("skipped", "%s: %s; Apply again" % (p.path, exc), None)


def _execute(inputs):
    global _result
    valid, cfg_plan, items, token = _plans(inputs)
    if not valid:
        return
    remove = cfg_plan is None
    lines, backups = [], []

    def record(label, r):
        lines.append("%s: %s" % (label, "skipped: %s" % r.reason if r.status == "skipped" else r.status))
        if r.backup:
            backups.append("backup: %s" % r.backup)

    blocked = False
    if cfg_plan:
        r = _safe_apply(cfg_plan)
        record("config.toml", r)
        blocked = r.status == "skipped"
    for c, p in items:
        if blocked:
            lines.append("%s: skipped: config.toml not updated; fix config.toml and Apply again" % c.label)
        else:
            record(c.label, _safe_apply(p))
    export, warnings = ("", "") if remove else _extras(_ticked(inputs), token)
    _result = Results(lines, backups, export, token or "", warnings)
    _ui().commandDefinitions.itemById(RESULT_ID).execute()


def _result_created(args):
    import adsk.core

    cmd = args.command
    cmd.isCancelButtonVisible = False
    inputs = cmd.commandInputs
    r = _result
    inputs.addTextBoxCommandInput("results", "Result", "\n".join(r.lines + r.backups + ["", RESTART]), 8, True)
    for input_id, name, text in (("export", "Export", r.export), ("token", "Token", r.token), ("warnings", "Warnings", r.warnings)):
        if text:
            inputs.addTextBoxCommandInput(input_id, name, text, 2, True)
