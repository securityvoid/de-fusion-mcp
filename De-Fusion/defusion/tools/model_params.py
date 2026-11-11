"""Design parameters: list, create, modify and delete (design § 10)."""
import fnmatch
import re
from typing import Literal

import adsk.core
import adsk.fusion
from pydantic import BaseModel

from ..actions import action
from ..errors import INVALID_PARAMS
from ..types import Entity

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ParamEdit(BaseModel, extra="forbid"):
    name: str
    expression: str


def _value(ctx, p):
    um = ctx.design.unitsManager
    if p.unit:
        if um.isValidExpression("1 " + p.unit, "cm"):
            return ctx.length_out(p.value)
        if um.isValidExpression("1 " + p.unit, "rad"):
            return ctx.angle_out(p.value)
    return p.value


def _item(ctx, p):
    created = getattr(p, "createdBy", None)
    return {
        "name": p.name,
        "kind": "user" if isinstance(p, adsk.fusion.UserParameter) else "model",
        "expression": p.expression,
        "unit": p.unit,
        "value": _value(ctx, p),
        "comment": p.comment,
        "created_by": None if created is None else ctx.ref(created),
        "role": getattr(p, "role", None) or None,
        "is_favorite": bool(getattr(p, "isFavorite", False)),
    }


@action("model", access="read")
def parameter_list(ctx, kind: Literal["all", "user", "model"] = "all", name: str | None = None,
                   created_by: Entity | None = None, offset: int = 0, limit: int = 100) -> dict:
    """List user and model parameters, optionally filtered by name pattern or creating feature."""
    design = ctx.require_design()
    if kind == "user":
        params = list(design.userParameters)
    elif kind == "model":
        params = [p for p in design.allParameters if isinstance(p, adsk.fusion.ModelParameter)]
    else:
        params = list(design.allParameters)
    if name is not None:
        params = [p for p in params if fnmatch.fnmatchcase(p.name, name)]
    if created_by is not None:
        params = [p for p in params if isinstance(p, adsk.fusion.ModelParameter)
                  and getattr(p, "createdBy", None) is not None
                  and p.createdBy.entityToken == created_by.entityToken]
    return ctx.page(params, offset, limit, build=lambda p: _item(ctx, p))


@action("model", access="write")
def parameter_create(ctx, name: str, expression: str, unit: str | None = None, comment: str = "") -> dict:
    """Create a user parameter; unit defaults to the design length unit, "" means unitless."""
    design = ctx.require_design()
    if not NAME.match(name):
        ctx.fail(INVALID_PARAMS, "name %r must match [A-Za-z_][A-Za-z0-9_]*" % name)
    if design.allParameters.itemByName(name) is not None:
        ctx.fail(INVALID_PARAMS, "parameter %r already exists" % name)
    if unit is None:
        unit = design.unitsManager.defaultLengthUnits
    if not design.unitsManager.isValidExpression(expression, unit):
        ctx.fail(INVALID_PARAMS, "expression %r is not valid for unit %r" % (expression, unit))
    p = design.userParameters.add(name, adsk.core.ValueInput.createByString(expression), unit, comment)
    return {"parameter": _item(ctx, p)}


@action("model", access="write")
def parameter_modify(ctx, edits: list[ParamEdit]) -> dict:
    """Change the expressions of existing parameters in one recompute where the API allows."""
    design = ctx.require_design()
    problems, params, seen = [], [], set()
    for e in edits:
        p = design.allParameters.itemByName(e.name)
        if e.name in seen:
            problems.append("%s: listed more than once" % e.name)
        elif p is None:
            problems.append("%s: no such parameter" % e.name)
        elif not design.unitsManager.isValidExpression(e.expression, p.unit):
            problems.append("%s: invalid expression %r" % (e.name, e.expression))
        seen.add(e.name)
        params.append(p)
    if problems:
        ctx.fail(INVALID_PARAMS, "bad edits: " + "; ".join(problems), hints=problems)
    if hasattr(design, "modifyParameters"):
        design.modifyParameters(params, [adsk.core.ValueInput.createByString(e.expression) for e in edits])
    else:
        for p, e in zip(params, edits):
            p.expression = e.expression
    return {"parameters": [_item(ctx, p) for p in params]}


@action("model", access="destructive")
def parameter_delete(ctx, names: list[str]) -> dict:
    """Delete user parameters; ones still in use are reported as failed."""
    design = ctx.require_design()
    found = {n: design.userParameters.itemByName(n) for n in names}
    bad = [n for n, p in found.items() if p is None]
    if bad:
        ctx.fail(INVALID_PARAMS, "not user parameters: " + ", ".join(bad))
    deleted, failed = [], []
    for n, p in found.items():
        if p.deleteMe():
            deleted.append(n)
        else:
            failed.append({"name": n, "reason": "in use by other parameters or features"})
    return {"deleted": deleted, "failed": failed}
