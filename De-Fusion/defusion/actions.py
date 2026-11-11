"""@action registration, the tool surface, help and the call pipeline (design § 5.1-§ 5.8, § 5.11)."""
import contextlib
import importlib
import inspect
import logging
import re
import sys
import time
import types as pytypes
import typing
from dataclasses import dataclass
from typing import Annotated, Any, Callable, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, ValidationError, create_model

from . import dispatch, results, types
from .context import Ctx
from .errors import (
    CONFIRM_REQUIRED,
    INVALID_PARAMS,
    NOT_ALLOWED,
    UNKNOWN_ACTION,
    ActionError,
    from_exception,
)
from .units import OutputUnits

log = logging.getLogger("defusion")

TOOLS = ("document", "sketch", "model", "assembly", "history", "inspect", "view", "cam", "script", "server")
ACCESS = ("read", "write", "destructive")
_SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")
_REQUIRED = object()


class ActionDefinitionError(ValueError):
    """An @action declaration that breaks § 5.1 (raised at decoration/import time)."""


@dataclass
class Action:
    tool: str
    name: str
    fn: Callable
    access: str
    timeout: float
    undo_group: bool
    main_thread: bool
    summary: str
    help: str
    model: type  # pydantic model of the parameters (extra="forbid")
    params: tuple  # parameter names after ctx (plus "confirm" for destructive actions)

    @property
    def key(self):
        return "%s.%s" % (self.tool, self.name)

    def input_schema(self):
        return self.model.model_json_schema()


class Registry:
    def __init__(self):
        self._actions = {}
        self._tool_summaries = {}

    def add(self, action, module_summary=None):
        if action.key in self._actions:
            raise ActionDefinitionError("duplicate action %s" % action.key)
        self._actions[action.key] = action
        if module_summary:
            self._tool_summaries.setdefault(action.tool, module_summary)

    def get(self, tool, name):
        return self._actions.get("%s.%s" % (tool, name))

    def for_tool(self, tool):
        return sorted((a for a in self._actions.values() if a.tool == tool), key=lambda a: a.name)

    def all(self):
        return sorted(self._actions.values(), key=lambda a: (a.tool, a.name))

    def tool_summary(self, tool):
        return self._tool_summaries.get(tool)

    def __iter__(self):
        return iter(self.all())

    def __len__(self):
        return len(self._actions)


_registry = Registry()


def registry():
    """The active registry (the real one, or the isolated one inside ``isolated_registry``)."""
    return _registry


@contextlib.contextmanager
def isolated_registry():
    """A fresh empty registry for the duration of the block (tests)."""
    global _registry
    saved, _registry = _registry, Registry()
    try:
        yield _registry
    finally:
        _registry = saved


# --- annotation check ---------------------------------------------------------------------------


def _check_annotation(ann, where):
    origin = typing.get_origin(ann)
    if origin is Annotated:
        base, *meta = typing.get_args(ann)
        if any(isinstance(m, BeforeValidator) for m in meta):  # a types.py type
            return
        return _check_annotation(base, where)
    if origin in (typing.Union, pytypes.UnionType):
        for a in typing.get_args(ann):
            _check_annotation(a, where)
        return
    if origin is Literal:
        return
    if origin is list:
        (item,) = typing.get_args(ann) or (None,)
        if item is None:
            raise ActionDefinitionError("%s: use list[X], not bare list" % where)
        return _check_annotation(item, where)
    if origin is dict:
        if typing.get_args(ann) not in ((str, Any), (str, str), (str, int), (str, float), (str, bool)):
            raise ActionDefinitionError("%s: only dict[str, Any] or dict[str, <scalar>] is allowed" % where)
        return
    if ann is type(None) or ann in (str, int, float, bool):
        return
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        return
    raise ActionDefinitionError("%s: unsupported annotation %r" % (where, ann))


def _module_summary(fn):
    mod = sys.modules.get(fn.__module__)
    doc = inspect.getdoc(mod) if mod is not None else None
    return doc.splitlines()[0].strip() if doc else None


def action(tool, name=None, access=_REQUIRED, timeout=30, undo_group=True, main_thread=True):
    """Register a function as the action ``tool.name``. See design § 5.1."""
    if tool not in TOOLS:
        raise ActionDefinitionError("unknown tool %r; tools are %s" % (tool, " ".join(TOOLS)))
    if access is _REQUIRED or access not in ACCESS:
        raise ActionDefinitionError("access must be one of %s, got %r" % (" ".join(ACCESS), access))
    if not main_thread and access != "read":
        raise ActionDefinitionError("main_thread=False is only allowed on read actions")

    def decorate(fn):
        aname = name or fn.__name__
        where = "%s.%s" % (tool, aname)
        if not _SNAKE.match(aname) or aname == "help":
            raise ActionDefinitionError("%s: action name must be snake_case and not 'help'" % where)
        doc = inspect.getdoc(fn)
        if not doc:
            raise ActionDefinitionError("%s: needs a docstring (first line is the summary)" % where)
        sig = inspect.signature(fn)
        params = list(sig.parameters.values())
        if not params or params[0].name != "ctx":
            raise ActionDefinitionError("%s: first parameter must be ctx" % where)
        hints = typing.get_type_hints(fn, include_extras=True)
        fields = {}
        for p in params[1:]:
            pw = "%s parameter %r" % (where, p.name)
            if p.kind in (p.VAR_KEYWORD, p.VAR_POSITIONAL):
                raise ActionDefinitionError("%s: *args/**kwargs are not allowed" % pw)
            if not _SNAKE.match(p.name):
                raise ActionDefinitionError("%s: parameter names must be snake_case" % pw)
            if p.name not in hints:
                raise ActionDefinitionError("%s: missing annotation" % pw)
            _check_annotation(hints[p.name], pw)
            if not main_thread and types.uses_refs(hints[p.name]):
                raise ActionDefinitionError("%s: main_thread=False actions cannot take Ref types" % pw)
            if p.name == "confirm" and access == "destructive":
                raise ActionDefinitionError("%s: 'confirm' is added by the framework" % pw)
            fields[p.name] = (hints[p.name], ... if p.default is p.empty else p.default)
        if access == "destructive":
            fields["confirm"] = (bool, False)
        model = create_model(
            "%s_%s_params" % (tool, aname), __config__=ConfigDict(extra="forbid"), **fields
        )
        act = Action(
            tool=tool, name=aname, fn=fn, access=access, timeout=timeout, undo_group=undo_group,
            main_thread=main_thread, summary=doc.splitlines()[0].strip(), help=doc, model=model,
            params=tuple(fields),
        )
        _registry.add(act, _module_summary(fn))
        return fn

    return decorate


# --- tool surface (§ 5.2) and help (§ 5.6) -----------------------------------------------------------


def _allowed_actions(tool, allowed):
    return [a for a in _registry.for_tool(tool) if a.key in allowed]


def build_tool(tool, allowed):
    """The SDK tool definition for ``tool`` restricted to ``allowed`` (a set of "tool.action"),
    or ``None`` when no action of the tool is allowed."""
    acts = _allowed_actions(tool, allowed)
    if not acts:
        return None
    names = [a.name for a in acts]
    summary = _registry.tool_summary(tool) or "De-Fusion %s tools." % tool
    return {
        "name": tool,
        "description": '%s Actions: %s. Call action="help" with params {"action": "<name>"} for its '
        "parameters." % (summary, ", ".join(names)),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["help"] + names},
                "params": {"type": "object", "default": {}},
                "units": {
                    "type": "object",
                    "properties": {"length": {"type": "string"}, "angle": {"type": "string"}},
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": all(a.access == "read" for a in acts),
            "destructiveHint": any(a.access == "destructive" for a in acts),
            "idempotentHint": False,
            "openWorldHint": False,
        },
    }


def help_result(tool, allowed, params):
    """Result of ``action="help"``: the allowed action list, or one action's schema."""
    if not isinstance(params, dict) or set(params) - {"action"}:
        raise ActionError(INVALID_PARAMS, 'help takes {} or {"action": "<name>"}')
    if "action" not in params:
        return {
            "tool": tool,
            "actions": [
                {"name": a.name, "summary": a.summary, "access": a.access}
                for a in _allowed_actions(tool, allowed)
            ],
        }
    x = params["action"]
    if not isinstance(x, str):
        raise ActionError(INVALID_PARAMS, "action must be a string")
    key = "%s.%s" % (tool, x)
    if key not in allowed:
        raise ActionError(
            NOT_ALLOWED,
            "%s is not allowed by this connection's profile" % key,
            hints=[a.name for a in _allowed_actions(tool, allowed)],
        )
    act = _registry.get(tool, x)
    if act is None:
        raise ActionError(
            UNKNOWN_ACTION, "no action %s" % key, hints=[a.name for a in _allowed_actions(tool, allowed)]
        )
    return {
        "name": act.name,
        "summary": act.summary,
        "help": act.help,
        "access": act.access,
        "timeout": act.timeout,
        "input_schema": act.input_schema(),
    }


def import_tool_modules(package, names):
    """Import ``package.<name>`` for each name; raise if a module registered no action or lacks a
    docstring whose first line is a full sentence (it becomes the tool description)."""
    for name in names:
        module = importlib.import_module("%s.%s" % (package, name))
        prefix = module.__name__ + "_"  # an index module (model.py) imports model_* modules
        if not any(a.fn.__module__ == module.__name__ or a.fn.__module__.startswith(prefix) for a in _registry):
            raise RuntimeError("tool module %s registered no action" % module.__name__)
        doc = (module.__doc__ or "").strip().splitlines()
        if not doc or not doc[0].endswith("."):
            raise RuntimeError(
                "tool module %s needs a docstring whose first line is a full sentence ending in a period"
                % module.__name__
            )


# --- call pipeline (§ 5.3) -------------------------------------------------------------------------------


@dataclass
class _Outcome:
    result: Any
    changes: Any
    undo_grouped: bool


def _validate(act, params):
    """§ 5.3 step 4 for params, then step 5 (confirm). Returns the validated model."""
    if not isinstance(params, dict):
        raise ActionError(INVALID_PARAMS, "params must be an object")
    try:
        validated = act.model.model_validate(params)
    except ValidationError as exc:
        hints = [
            "%s: %s" % (".".join(str(x) for x in e["loc"]), e["msg"]) if e["loc"] else e["msg"]
            for e in exc.errors()
        ]
        raise ActionError(
            INVALID_PARAMS, "invalid params for %s" % act.key, hints=hints, schema=act.input_schema()
        ) from None
    if act.access == "destructive" and params.get("confirm") is not True:
        raise ActionError(
            CONFIRM_REQUIRED,
            "%s deletes or discards data; repeat the call with params.confirm = true" % act.key,
            hints=['add "confirm": true to params'],
        )
    return validated


def _lookup(tool, name, allowed):
    key = "%s.%s" % (tool, name)
    if key not in allowed:
        raise ActionError(
            NOT_ALLOWED,
            "%s is not allowed by this connection's profile" % key,
            hints=[a.name for a in _allowed_actions(tool, allowed)],
        )
    act = _registry.get(tool, name)
    if act is None:
        raise ActionError(
            UNKNOWN_ACTION, "no action %s" % key, hints=[a.name for a in _allowed_actions(tool, allowed)]
        )
    return act


def _transaction(ctx, command):
    ctx.app.executeTextCommand(command)


def _run_on_main_thread(act, validated, allowed, profile_items, output_units):
    """The closure dispatched to Fusion's main thread (§ 5.3 step 6)."""
    import adsk.core

    ctx = Ctx.for_app(adsk.core.Application.get(), allowed, profile_items, output_units)
    kwargs = types.resolve_params(ctx, act, validated)
    kwargs.pop("confirm", None)
    writes = act.access != "read"
    before = results.snapshot(ctx.design, output_units) if writes and ctx.design is not None else None
    grouped = writes and act.undo_group and ctx.document is not None
    undo_grouped, started = True, False
    if grouped:
        try:
            _transaction(ctx, 'PTransaction.Start "De-Fusion %s"' % act.key)
            started = True
        except Exception as exc:
            undo_grouped = False
            log.warning("undo grouping unavailable for %s: %s", act.key, exc)
    try:
        result = act.fn(ctx, **kwargs)
    except Exception:
        if started:
            try:
                _transaction(ctx, "PTransaction.Abort")
            except Exception as exc:
                log.warning("PTransaction.Abort failed for %s: %s", act.key, exc)
        raise
    if started:
        try:
            _transaction(ctx, "PTransaction.Commit")
        except Exception as exc:
            undo_grouped = False
            log.warning("PTransaction.Commit failed for %s: %s", act.key, exc)
    changes = None
    if writes:
        changes = {}
        if before is not None:
            try:
                changes = results.diff(before, results.snapshot(ctx.design, output_units))
            except RuntimeError:  # the action closed the document, so its design no longer exists
                pass
    return _Outcome(result, changes, undo_grouped)


def _call(tool, args, allowed, profile_items, default_units, note):
    action_name = args.get("action")
    if not isinstance(action_name, str):
        raise ActionError(INVALID_PARAMS, "action must be a string")
    visible = any(a.key in allowed for a in _registry.for_tool(tool))
    if action_name == "help":
        if not visible:
            raise ActionError(NOT_ALLOWED, "tool %s is not allowed by this connection's profile" % tool)
        out = OutputUnits(**default_units.as_dict())
        return results.shape(tool, "help", help_result(tool, allowed, args.get("params") or {}), out.as_dict())
    try:
        act = _lookup(tool, action_name, allowed)
    except ActionError as exc:
        if exc.code == NOT_ALLOWED and note:
            exc.message = "%s (%s)" % (exc.message, note)
        raise
    out = OutputUnits.for_call(args.get("units"), default_units.as_dict())
    params = args.get("params")
    validated = _validate(act, {} if params is None else params)
    if not act.main_thread:
        ctx = Ctx.offline(allowed, profile_items, out)
        kwargs = types.resolve_params(ctx, act, validated)
        kwargs.pop("confirm", None)
        return results.shape(tool, action_name, act.fn(ctx, **kwargs), out.as_dict())
    outcome = dispatch.run(
        lambda: _run_on_main_thread(act, validated, allowed, profile_items, out),
        act.timeout,
        label=act.key,
        job_value=lambda o: o.result,
    )
    if isinstance(outcome, _Outcome):
        return results.shape(
            tool, action_name, outcome.result, out.as_dict(), outcome.changes, outcome.undo_grouped
        )
    return results.shape(tool, action_name, outcome, out.as_dict())  # {"status": "running", ...}


def call(tool, args, allowed, profile_items, output_units=None, *, denied_note=None):
    """Run ``tools/call`` of ``tool`` with ``args = {action, params, units}``; returns a CallToolResult.

    ``allowed`` is the request's set of "tool.action"; ``output_units`` the configured default
    OutputUnits; ``denied_note`` is appended to a NOT_ALLOWED message (config error text)."""
    started = time.monotonic()
    default_units = output_units or OutputUnits.for_call()
    action_name = args.get("action") if isinstance(args, dict) else None
    try:
        result = _call(tool, args, allowed, profile_items, default_units, denied_note)
        outcome = "ok" if not result.is_error else result.structured_content["error"]["code"]
    except Exception as exc:
        err = from_exception(exc)
        outcome = err.code
        result = results.error_result(tool, str(action_name), err)
        if err.code == "FUSION_ERROR":
            log.exception("%s.%s raised", tool, action_name)
    log.info(
        "call tool=%s action=%s duration_ms=%d outcome=%s",
        tool, action_name, (time.monotonic() - started) * 1000, outcome,
    )
    return result


def call_inline(ctx, tool, action_name, params):
    """Run another registered action inside the current main-thread call (§ 5.11).

    Opens no undo group, takes no change snapshot and does not dispatch; returns the raw result."""
    act = _lookup(tool, action_name, ctx.allowed)
    validated = _validate(act, params)
    kwargs = types.resolve_params(ctx, act, validated)
    kwargs.pop("confirm", None)
    return act.fn(ctx, **kwargs)
