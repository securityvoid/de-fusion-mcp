"""Coil creation by driving Fusion's Coil command with text commands (design § 5)."""
from typing import Literal

import adsk.core
import adsk.fusion

from ..actions import action
from ..errors import FUSION_ERROR, INVALID_PARAMS, UNSUPPORTED, ActionError
from ..types import Angle, Body, Component, Expr, Length, Plane
from .model_common import activated, finish, origin_plane, positive, resolve_plane, val

CoilType = Literal["revolution_height", "revolution_pitch", "height_pitch", "spiral"]
CoilSection = Literal["circular", "square", "triangular_external", "triangular_internal"]
CoilSectionPosition = Literal["inside", "on_center", "outside"]

REQUIRED = {
    "revolution_height": ("revolutions", "height"),
    "revolution_pitch": ("revolutions", "pitch"),
    "height_pitch": ("height", "pitch"),
    "spiral": ("revolutions", "pitch"),
}
SIZE_TYPE = {
    "revolution_height": "infoRevolutionAndHeight", "revolution_pitch": "infoRevolutionAndPitch",
    "height_pitch": "infoHeightAndPitch", "spiral": "infoSpiral",
}
SECTION_TYPE = {
    "circular": "infoCircular", "square": "infoRectanglar", "triangular_external": "infoTrianglar",
    "triangular_internal": "infoTrianglar2",
}
POSITION = {"inside": "infoInside", "on_center": "infoOnCenter", "outside": "infoOutside"}
BOOLEAN = {"new_body": "infoNewBodyType", "join": "infoJoinType", "cut": "infoCutType",
           "intersect": "infoIntersectType"}
SIZE_COMMANDS = (("revolutions", "CoilRevolutions"), ("height", "CoilHeight"), ("pitch", "CoilPitch"))
ORIGIN_PLANES = (("XY", "xYConstructionPlane"), ("XZ", "xZConstructionPlane"), ("YZ", "yZConstructionPlane"))
HALF_PI = 1.5707963267948966


def _validate(ctx, coil_type, sizes, diameter, section_size, taper, operation, participant_bodies):
    for param in ("revolutions", "height", "pitch"):
        needed = param in REQUIRED[coil_type]
        if needed and sizes[param] is None:
            ctx.fail(INVALID_PARAMS, "%s is required for coil_type %s" % (param, coil_type))
        if not needed and sizes[param] is not None:
            ctx.fail(INVALID_PARAMS, "%s is not used by coil_type %s" % (param, coil_type))
    positive(ctx, diameter, "diameter")
    positive(ctx, section_size, "section_size")
    for param, v in sizes.items():
        if v is not None:
            positive(ctx, v, param)
    if sizes["pitch"] is not None and section_size.number > sizes["pitch"].number:
        ctx.fail(INVALID_PARAMS, "section_size must not exceed pitch")
    if coil_type == "spiral":
        if taper.number != 0:
            ctx.fail(INVALID_PARAMS, "spiral coils have no taper")
    elif not -HALF_PI < taper.number < HALF_PI:
        ctx.fail(INVALID_PARAMS, "taper_angle must be between -90 and 90 deg")
    if participant_bodies is not None:
        if operation == "new_body":
            ctx.fail(INVALID_PARAMS, "participant_bodies applies only to join, cut and intersect")
        if not hasattr(adsk.fusion.CoilFeature, "participantBodies"):
            ctx.fail(UNSUPPORTED, "CoilFeature.participantBodies is not available in this Fusion build")


def _commands(coil_type, section, section_position, sizes, taper, operation, clockwise, section_size):
    cmds = [
        "Commands.SetString infoSizeType %s" % SIZE_TYPE[coil_type],
        "Commands.SetString infoSectionType %s" % SECTION_TYPE[section],
        "Commands.SetString infoSectionPosition %s" % POSITION[section_position],
        "Commands.SetDouble SectionSize %r" % float(section_size.number),
    ]
    for param, command in SIZE_COMMANDS:
        if sizes[param] is not None:
            cmds.append("Commands.SetDouble %s %r" % (command, float(sizes[param].number)))
    if coil_type != "spiral":
        cmds.append("Commands.SetDouble CoilTaperAngle %r" % float(taper.number))
    cmds.append("Commands.SetString infoBooleanType %s" % BOOLEAN[operation])
    if clockwise:
        cmds.append("Commands.SetBool CoilFlipRotation 1")
    return cmds


def _create(ctx, comp, pl, name, cx, cy, diameter, cmds):
    """Steps 1-6 of design § 5.4; returns ``(coil, sketch)``. Cleans up and re-raises on failure."""
    coils = comp.features.coilFeatures
    before = coils.count
    sk = None
    try:
        sk = comp.sketches.add(pl)
        sk.name = "%s base" % (name or "Coil")
        circle = sk.sketchCurves.sketchCircles.addByCenterRadius(
            adsk.core.Point3D.create(cx.number, cy.number, 0), diameter.number / 2)
        ctx.ui.activeSelections.clear()
        ctx.ui.activeSelections.add(circle)
        ctx.text_command("Commands.Start Coil")
        for c in cmds:
            ctx.text_command(c)
        ctx.text_command("NuCommands.CommitCmd")
        if coils.count != before + 1:
            ctx.fail(FUSION_ERROR, "coil not created")
    except Exception as exc:
        for cleanup in (ctx.ui.terminateActiveCommand, (sk.deleteMe if sk is not None else None)):
            if cleanup is not None:
                try:
                    cleanup()
                except Exception:
                    pass
        if isinstance(exc, ActionError):
            raise
        raise ActionError(FUSION_ERROR, "coil not created: %s" % exc) from exc
    return coils.item(before), sk


def _request(ctx, comp, pl, passed_component, params):
    plane = next((n for n, attr in ORIGIN_PLANES if getattr(comp, attr).entityToken == pl.entityToken), None)
    refs = params["participant_bodies"]
    return {
        "format": "defusion.request/1", "tool": "model", "action": "coil",
        "params": {
            "plane": plane or ctx.ref(pl),
            "center": [params["cx"].expr, params["cy"].expr],
            "diameter": params["diameter"].expr,
            "coil_type": params["coil_type"],
            "revolutions": _expr(params["revolutions"]), "height": _expr(params["height"]),
            "pitch": _expr(params["pitch"]),
            "taper_angle": params["taper"].expr,
            "section": params["section"], "section_position": params["section_position"],
            "section_size": params["section_size"].expr,
            "clockwise": params["clockwise"], "operation": params["operation"],
            "participant_bodies": None if refs is None else [ctx.ref(b) for b in refs],
            "component": ctx.ref(comp) if passed_component is not None else None,
            "name": params["name"],
        },
    }


def _expr(v):
    return None if v is None else v.expr


@action("model", access="write", timeout=60)
def coil(ctx, diameter: Length, plane: Plane = "XY", center: list[Length] = [0, 0],
         coil_type: CoilType = "revolution_height", revolutions: Expr | None = None,
         height: Length | None = None, pitch: Length | None = None, taper_angle: Angle = 0,
         section: CoilSection = "circular", section_position: CoilSectionPosition = "on_center",
         section_size: Length = "1 mm", clockwise: bool = False,
         operation: Literal["new_body", "join", "cut", "intersect"] = "new_body",
         participant_bodies: list[Body] | None = None, component: Component | None = None,
         name: str | None = None) -> dict:
    """Create a coil (helix or spiral) by driving Fusion's Coil command; parametric designs only."""
    ctx.require_parametric()
    design = ctx.design
    if len(center) != 2:
        ctx.fail(INVALID_PARAMS, "center needs 2 values [x, y]")
    diameter = val(ctx, diameter, "length")
    taper = val(ctx, taper_angle, "angle")
    section_size = val(ctx, section_size, "length")
    sizes = {"revolutions": None if revolutions is None else val(ctx, revolutions, "expr"),
             "height": None if height is None else val(ctx, height, "length"),
             "pitch": None if pitch is None else val(ctx, pitch, "length")}
    cx, cy = (val(ctx, c, "length") for c in center)
    _validate(ctx, coil_type, sizes, diameter, section_size, taper, operation, participant_bodies)
    comp = ctx.component(component)
    pl = origin_plane(comp, resolve_plane(ctx, plane, comp))
    cmds = _commands(coil_type, section, section_position, sizes, taper, operation, clockwise, section_size)
    with activated(ctx, comp):
        feat, sk = _create(ctx, comp, pl, name, cx, cy, diameter, cmds)
    if participant_bodies is not None:
        feat.timelineObject.rollTo(True)
        try:
            feat.participantBodies = list(participant_bodies)
        finally:
            design.timeline.moveToEnd()
    if name:
        feat.name = name
    request = _request(ctx, comp, pl, component, {
        "cx": cx, "cy": cy, "diameter": diameter, "coil_type": coil_type, **sizes, "taper": taper,
        "section": section, "section_position": section_position, "section_size": section_size,
        "clockwise": clockwise, "operation": operation, "participant_bodies": participant_bodies,
        "name": name})
    ctx.attr_set(feat, "request", request)
    made = [
        {"name": p.name, "expression": p.expression, "role": p.role}
        for p in design.allParameters
        if isinstance(p, adsk.fusion.ModelParameter) and p.createdBy.entityToken == feat.entityToken
    ]
    return finish(ctx, feat, None, sketch=ctx.ref(sk), parameters=made)
