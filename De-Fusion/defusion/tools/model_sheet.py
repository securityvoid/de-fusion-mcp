"""Sheet metal: flange, bend (Fold), unfold and flat pattern (design § 8). Most members are preview API."""
from typing import Literal

import adsk.fusion

from ..actions import action
from ..errors import INVALID_PARAMS, NOT_FOUND, UNSUPPORTED
from ..types import Angle, Edge, Face, Length, Profile, SketchCurve, Value
from .model_common import expect, finish, owner, require_api, vi
from .model_features import _first

POSITIONS = {"inside": "Inside", "outside": "Outside", "adjacent": "Adjacent", "tangent": "Tangent"}
LINE_POSITIONS = {"center": "Center", "start": "Start", "end": "End"}


def _sheet_body(ctx, entity, param):
    body = entity.body
    if hasattr(body, "isSheetMetal") and not body.isSheetMetal:
        ctx.fail(INVALID_PARAMS, "%s must belong to a sheet metal body" % param)


def _enum(ctx, cls, member):
    """``adsk.fusion.<cls>.<member>`` or ``UNSUPPORTED`` naming the missing member."""
    value = getattr(getattr(adsk.fusion, cls, None), member, None)
    if value is None:
        ctx.fail(UNSUPPORTED, "%s.%s is not available in this Fusion build" % (cls, member),
                 hints=["this API member is missing from the installed Fusion version"])
    return value


def _activate_rule(ctx, comp, rule):
    found = None
    for lib in ("librarySheetMetalRules", "designSheetMetalRules"):
        rules = getattr(ctx.design, lib, None)
        found = found or (rules.itemByName(rule) if rules is not None else None)
    if found is None:
        ctx.fail(NOT_FOUND, "sheet metal rule %r not found" % rule)
    require_api(ctx, comp, "activeSheetMetalRule", "Component.activeSheetMetalRule")
    comp.activeSheetMetalRule = found


@action("model", access="write", timeout=60)
def sheet_flange(ctx, edges: list[Edge] | None = None, profile: Profile | None = None,
                 height: Length | None = None, angle: Angle = "90 deg",
                 bend_position: Literal["inside", "outside", "adjacent", "tangent"] | None = None,
                 rule: str | None = None, name: str | None = None) -> dict:
    """Create a base flange from a profile, or an edge flange on sheet metal edges."""
    ctx.require_design()
    if (edges is None) == (profile is None):
        ctx.fail(INVALID_PARAMS, "give exactly one of edges or profile")
    if profile is not None:
        if height is not None or bend_position is not None or isinstance(angle, Value):
            ctx.fail(INVALID_PARAMS, "height, angle and bend_position apply only to an edge flange")
        comp = owner(_first(profile))
    else:
        if height is None:
            ctx.fail(INVALID_PARAMS, "height is required for an edge flange")
        for e in edges:
            _sheet_body(ctx, e, "edges")
        comp = owner(edges[0])
    require_api(ctx, comp.features, "flangeFeatures", "flangeFeatures")
    ff = comp.features.flangeFeatures
    if rule is not None:
        _activate_rule(ctx, comp, rule)
    if profile is not None:
        require_api(ctx, ff, "createBaseFlangeInput", "FlangeFeatures.createBaseFlangeInput")
        inp = ff.createBaseFlangeInput([profile])
    else:
        require_api(ctx, ff, "createEdgeFlangeInput", "FlangeFeatures.createEdgeFlangeInput")
        position = None if bend_position is None else _enum(
            ctx, "BendPositionTypes", POSITIONS[bend_position] + "BendPositionType")
        inp = ff.createEdgeFlangeInput(list(edges), vi(ctx, height))
        inp.angle = vi(ctx, angle, "angle")
        if position is not None:
            inp.bendPositionType = position
    return finish(ctx, ff.add(inp), name)


@action("model", access="write", timeout=60)
def sheet_bend(ctx, stationary_face: Face, lines: list[SketchCurve], angle: Angle = "90 deg",
               line_position: Literal["center", "start", "end"] = "center", bend_relief: bool = True,
               name: str | None = None) -> dict:
    """Fold a sheet metal body along sketch lines (Fusion's Fold)."""
    ctx.require_design()
    _sheet_body(ctx, stationary_face, "stationary_face")
    expect(ctx, lines, ("SketchLine",), "lines")
    comp = owner(stationary_face)
    require_api(ctx, comp.features, "foldFeatures", "features.foldFeatures")
    pos = _enum(ctx, "FoldBendLinePositionTypes", LINE_POSITIONS[line_position] + "FoldBendLinePositionType")
    ff = comp.features.foldFeatures
    inp = ff.createInput(stationary_face)
    for line in lines:
        inp.bendLines.add(line, vi(ctx, angle, "angle"), pos, bend_relief)
    return finish(ctx, ff.add(inp), name)


@action("model", access="write", timeout=60)
def sheet_unfold(ctx, stationary_face: Face, bend_faces: list[Face] | None = None,
                 name: str | None = None) -> dict:
    """Unfold all bends, or the given bend faces, of a sheet metal body."""
    ctx.require_design()
    _sheet_body(ctx, stationary_face, "stationary_face")
    comp = owner(stationary_face)
    require_api(ctx, comp.features, "unfoldFeatures", "features.unfoldFeatures")
    uf = comp.features.unfoldFeatures
    require_api(ctx, uf, "createInput", "UnfoldFeatures.createInput")
    inp = uf.createInput(stationary_face)
    if bend_faces is None:
        inp.isUnfoldAllBends = True
    else:
        inp.bendFaces = list(bend_faces)
    return finish(ctx, uf.add(inp), name)


@action("model", access="write", timeout=60)
def sheet_flat_pattern(ctx, stationary_face: Face) -> dict:
    """Create the flat pattern of a sheet metal component."""
    ctx.require_design()
    _sheet_body(ctx, stationary_face, "stationary_face")
    comp = owner(stationary_face)
    require_api(ctx, comp, "createFlatPattern", "Component.createFlatPattern")
    if getattr(comp, "flatPattern", None) is not None:
        ctx.fail(INVALID_PARAMS, "component already has a flat pattern")
    fp = comp.createFlatPattern(stationary_face)
    box = fp.flatBody.boundingBox
    lo, hi = box.minPoint, box.maxPoint
    return {"flat_pattern": ctx.ref(fp), "flat_body": ctx.ref(fp.flatBody),
            "size": [ctx.length_out(b - a) for a, b in ((lo.x, hi.x), (lo.y, hi.y), (lo.z, hi.z))]}
