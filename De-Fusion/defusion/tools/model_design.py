"""Design settings, queries and destructive actions: units, configurations, attributes, visibility,
selection sets, design type/intent, recompute, remove_body and delete_entities (design § 12, § 13)."""
from typing import Literal

import adsk.core
import adsk.fusion

from ..actions import action
from ..errors import INVALID_PARAMS
from ..types import Body, Entity
from .common import _enum_name
from .model_common import require_api

LENGTH_UNITS = {"mm": "Millimeter", "cm": "Centimeter", "m": "Meter", "in": "Inch", "ft": "Foot"}
VALUE_LIMIT = 2000


def _distance_units():
    return getattr(adsk.fusion, "DistanceUnits", None) or adsk.core.DistanceUnits


def _unit_name(display):
    units = _distance_units()
    for short, long in LENGTH_UNITS.items():
        if display == getattr(units, long + "DistanceUnits", None):
            return short
    return None


def _short(obj):
    return obj.objectType.split("::")[-1]


def _row(r):
    return None if r is None else {"id": r.id, "name": r.name}


# --- units -----------------------------------------------------------------------------------


@action("model", access="read")
def units_get(ctx) -> dict:
    """The design's default and display length units, and this call's output units."""
    design = ctx.require_design()
    return {
        "default_length_units": design.unitsManager.defaultLengthUnits,
        "display_length_units": _unit_name(design.fusionUnitsManager.distanceDisplayUnits),
        "output_units": dict(ctx.units),
    }


@action("model", access="write")
def units_set(ctx, length: Literal["mm", "cm", "m", "in", "ft"]) -> dict:
    """Set the design's display length unit."""
    design = ctx.require_design()
    design.fusionUnitsManager.distanceDisplayUnits = getattr(_distance_units(), LENGTH_UNITS[length] + "DistanceUnits")
    return {"display_length_units": _unit_name(design.fusionUnitsManager.distanceDisplayUnits)}


# --- configurations --------------------------------------------------------------------------


@action("model", access="read")
def config_inspect(ctx, offset: int = 0, limit: int = 100) -> dict:
    """Inspect the configuration table of a configured design."""
    design = ctx.require_design()
    require_api(ctx, design, "configurationTopTable", "Design.configurationTopTable")
    table = design.configurationTopTable
    if table is None:
        return {"configured": False, "active_row": None, "columns": [], **ctx.page([], offset, limit)}
    columns = [{"title": c.title, "type": _short(c)} for c in table.columns]
    return {"configured": True, "active_row": _row(table.activeRow), "columns": columns,
            **ctx.page(list(table.rows), offset, limit, build=_row)}


@action("model", access="write")
def create_configured_design(ctx) -> dict:
    """Convert the design to a configured design."""
    design = ctx.require_design()
    require_api(ctx, design, "createConfiguredDesign", "Design.createConfiguredDesign")
    if design.isConfiguredDesign:
        ctx.fail(INVALID_PARAMS, "design is already configured")
    table = design.createConfiguredDesign()
    return {"configured": True, "rows": table.rows.count}


# --- attributes and tokens -------------------------------------------------------------------


@action("model", access="read")
def find_attributes(ctx, group: str = "defusion", attribute: str = "", offset: int = 0, limit: int = 100) -> dict:
    """Find attributes by group and name (an empty name matches any), with their parent entity."""
    design = ctx.require_design()

    def build(a):
        out = {"group": a.groupName, "name": a.name, "value": a.value[:VALUE_LIMIT],
               "parent": None if a.parent is None else ctx.ref(a.parent)}
        if len(a.value) > VALUE_LIMIT:
            out["truncated"] = True
        return out

    return ctx.page(list(design.findAttributes(group, attribute)), offset, limit, build=build)


@action("model", access="read")
def find_by_token(ctx, token: str) -> dict:
    """The entities for an entity token (empty when none match)."""
    design = ctx.require_design()
    return {"entities": ctx.refs(list(design.findEntityByToken(token)))}


# --- visibility ------------------------------------------------------------------------------


def _flags(ctx, design):
    require_api(ctx, design, "objectVisibility", "Design.objectVisibility")
    ov = design.objectVisibility
    return {n: getattr(ov, n) for n in dir(ov) if n.startswith("is") and isinstance(getattr(ov, n, None), bool)}


def _visibility(ctx, design, targets):
    if targets is None:
        return {"flags": _flags(ctx, design)}
    return {"entities": [
        {"target": ctx.ref(t), "light_bulb": getattr(t, "isLightBulbOn", None), "visible": getattr(t, "isVisible", None)}
        for t in targets]}


@action("model", access="read")
def visibility_get(ctx, targets: list[Entity] | None = None) -> dict:
    """Light bulb and visibility of entities, or the design's visibility flags when no targets are given."""
    return _visibility(ctx, ctx.require_design(), targets)


@action("model", access="write")
def visibility_set(ctx, targets: list[Entity] | None = None, visible: bool | None = None,
                   flag: str | None = None, value: bool | None = None) -> dict:
    """Show or hide entities (targets + visible) or set a design visibility flag (flag + value)."""
    design = ctx.require_design()
    by_target = targets is not None and visible is not None and flag is None and value is None
    by_flag = flag is not None and value is not None and targets is None and visible is None
    if by_target == by_flag:
        ctx.fail(INVALID_PARAMS, "give exactly one of (targets and visible) or (flag and value)")
    if by_flag:
        flags = _flags(ctx, design)
        if flag not in flags:
            ctx.fail(INVALID_PARAMS, "unknown flag %r" % flag, hints=sorted(flags))
        setattr(design.objectVisibility, flag, value)
        return {"flags": _flags(ctx, design)}
    attrs = []
    for t in targets:
        attr = next((a for a in ("isLightBulbOn", "isVisible") if hasattr(t, a)), None)
        if attr is None:
            ctx.fail(INVALID_PARAMS, "%s has no visibility to set" % ctx.ref(t)["name"])
        attrs.append(attr)
    for t, attr in zip(targets, attrs):
        setattr(t, attr, visible)
    return _visibility(ctx, design, targets)


# --- selection sets --------------------------------------------------------------------------


@action("model", access="read")
def selection_sets_list(ctx, offset: int = 0, limit: int = 100) -> dict:
    """List selection sets with their entities."""
    design = ctx.require_design()
    require_api(ctx, design, "selectionSets", "Design.selectionSets")

    def build(s):
        ents = list(s.entities)
        return {"name": s.name, "count": len(ents), "entities": ctx.refs(ents)}

    return ctx.page(list(design.selectionSets), offset, limit, build=build)


@action("model", access="write")
def selection_set_create(ctx, name: str, entities: list[Entity]) -> dict:
    """Create a named selection set."""
    design = ctx.require_design()
    require_api(ctx, design, "selectionSets", "Design.selectionSets")
    design.selectionSets.add(list(entities), name)
    return {"name": name, "count": len(entities)}


# --- design type, intent, recompute ----------------------------------------------------------


@action("model", access="destructive")
def set_design_type(ctx, design_type: Literal["parametric", "direct"]) -> dict:
    """Switch between parametric and direct modeling; switching to direct discards the timeline."""
    design = ctx.require_design()
    t = adsk.fusion.DesignTypes
    target = t.ParametricDesignType if design_type == "parametric" else t.DirectDesignType
    changed = design.designType != target
    if changed:
        design.designType = target
    return {"design_type": design_type, "changed": changed}


@action("model", access="write")
def set_design_intent(ctx, intent: Literal["part", "assembly", "hybrid"]) -> dict:
    """Set the design intent (part, assembly or hybrid)."""
    design = ctx.require_design()
    require_api(ctx, design, "designIntent", "Design.designIntent")
    require_api(ctx, adsk.fusion, "DesignIntentTypes", "DesignIntentTypes")
    target = getattr(adsk.fusion.DesignIntentTypes, intent.capitalize() + "DesignIntentType")
    changed = design.designIntent != target
    design.designIntent = target
    return {"design_intent": intent, "changed": changed}


@action("model", access="write")
def activate_root(ctx) -> dict:
    """Make the root component the active one."""
    design = ctx.require_design()
    design.activateRootComponent()
    return {"active_component": ctx.ref(design.activeComponent)}


@action("model", access="write", timeout=300)
def compute_all(ctx) -> dict:
    """Recompute the design and list the timeline items that are not healthy."""
    design = ctx.require_design()
    design.computeAll()
    unhealthy = []
    if design.designType == adsk.fusion.DesignTypes.ParametricDesignType:
        healthy = adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState
        for i in range(design.timeline.count):
            item = design.timeline.item(i)
            if item.healthState != healthy:
                unhealthy.append({"item": ctx.ref(item.entity),
                                  "health": _enum_name(adsk.fusion.FeatureHealthStates, item.healthState)})
    return {"timeline_health": unhealthy}


@action("model", access="write")
def set_modeling_in_assembly(ctx, enabled: bool) -> dict:
    """Enable or disable modeling in assembly context."""
    design = ctx.require_design()
    require_api(ctx, design, "isModelingInAssemblyEnabled", "Design.isModelingInAssemblyEnabled")
    design.isModelingInAssemblyEnabled = enabled
    return {"enabled": design.isModelingInAssemblyEnabled}


# --- destructive -----------------------------------------------------------------------------


@action("model", access="destructive")
def remove_body(ctx, body: Body) -> dict:
    """Remove a body (a Remove feature in a parametric design, a delete in a direct one)."""
    design = ctx.require_design()
    removed = ctx.ref(body)
    if design.designType == adsk.fusion.DesignTypes.ParametricDesignType:
        feat = body.parentComponent.features.removeFeatures.add(body)
        return {"removed": removed, "feature": ctx.ref(feat)}
    body.deleteMe()
    return {"removed": removed, "feature": None}


@action("model", access="destructive")
def delete_entities(ctx, targets: list[Entity]) -> dict:
    """Delete entities one by one, reporting which could not be deleted."""
    ctx.require_design()
    refs = [ctx.ref(t) for t in targets]
    deleted, failed = [], []
    for t, ref in zip(targets, refs):
        if getattr(t, "isValid", True) is False:
            deleted.append(ref)
        elif not hasattr(t, "deleteMe"):
            failed.append({"target": ref, "reason": "not deletable"})
        else:
            try:
                ok = t.deleteMe()
            except Exception as exc:
                failed.append({"target": ref, "reason": str(exc)})
                continue
            if ok is False:
                failed.append({"target": ref, "reason": "deleteMe returned False"})
            else:
                deleted.append(ref)
    return {"deleted": deleted, "failed": failed}
