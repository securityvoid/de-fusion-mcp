"""Surface features: patch, stitch, thicken, ruled surface and trim (design § 7)."""
from typing import Literal

import adsk.fusion

from ..actions import action
from ..errors import INVALID_PARAMS, UNSUPPORTED
from ..types import Angle, Body, Entity, Face, Length, Operation, adsk_enum
from .model_common import cells_hint, coll, expect, finish, owner, require_api, same_owner, val, vi


@action("model", access="write")
def surface_patch(ctx, boundary: list[Entity], chain: bool = True, name: str | None = None) -> dict:
    """Fill a closed boundary of edges or sketch curves with a patch surface."""
    ctx.require_design()
    expect(ctx, boundary, ("BRepEdge", "SketchCurve"), "boundary")
    comp = same_owner(ctx, boundary, "boundary")
    path = comp.features.createPath(coll(boundary), chain)
    pf = comp.features.patchFeatures
    feat = pf.add(pf.createInput(path, adsk_enum("new_body")))
    return finish(ctx, feat, name)


@action("model", access="write")
def surface_stitch(ctx, bodies: list[Body], tolerance: Length = "0.01 mm",
                   operation: Literal["new_body", "new_component"] = "new_body", name: str | None = None) -> dict:
    """Stitch surface bodies together."""
    ctx.require_design()
    if any(b.isSolid for b in bodies):
        ctx.fail(INVALID_PARAMS, "bodies must be surface bodies")
    comp = same_owner(ctx, bodies, "bodies")
    sf = comp.features.stitchFeatures
    feat = sf.add(sf.createInput(coll(bodies), vi(ctx, tolerance), adsk_enum(operation)))
    return finish(ctx, feat, name)


@action("model", access="write")
def surface_thicken(ctx, faces: list[Face], thickness: Length, symmetric: bool = False,
                    operation: Operation = "new_body", chain: bool = True, name: str | None = None) -> dict:
    """Thicken faces or surface bodies into a solid."""
    ctx.require_design()
    if val(ctx, thickness, "length").number == 0:
        ctx.fail(INVALID_PARAMS, "thickness must not be 0")
    comp = same_owner(ctx, faces, "faces")
    tf = comp.features.thickenFeatures
    feat = tf.add(tf.createInput(coll(faces), vi(ctx, thickness), symmetric, adsk_enum(operation), chain))
    return finish(ctx, feat, name)


@action("model", access="write")
def surface_ruled(ctx, edges: list[Entity], distance: Length, angle: Angle = 0,
                  ruled_type: Literal["normal", "tangent"] = "normal", chain: bool = True,
                  name: str | None = None) -> dict:
    """Create a ruled surface from the edges of a surface body."""
    ctx.require_design()
    expect(ctx, edges, ("BRepEdge",), "edges")
    comp = same_owner(ctx, edges, "edges")
    require_api(ctx, comp.features, "ruledSurfaceFeatures", "RuledSurfaceFeatures")
    rf = comp.features.ruledSurfaceFeatures
    require_api(ctx, rf, "createInput", "RuledSurfaceFeatures.createInput")
    profile = comp.createBRepEdgeProfile(coll(edges), chain)
    t = adsk.fusion.RuledSurfaceTypes
    kind = t.NormalRuledSurfaceType if ruled_type == "normal" else t.TangentRuledSurfaceType
    try:
        inp = rf.createInput(profile, vi(ctx, distance), vi(ctx, angle, "angle"), kind)
    except TypeError:
        ctx.fail(UNSUPPORTED, "RuledSurfaceFeatures.createInput signature differs in this Fusion build")
    return finish(ctx, rf.add(inp), name)


@action("model", access="write")
def surface_trim(ctx, tool: Entity, remove_cells: list[int], name: str | None = None) -> dict:
    """Trim a surface with a tool, removing the chosen cells."""
    ctx.require_design()
    expect(ctx, [tool], ("BRepBody", "BRepFace", "ConstructionPlane", "SketchCurve"), "tool")
    tf = owner(tool).features.trimFeatures
    inp = tf.createInput(tool)
    cells = inp.bRepCells
    if not remove_cells or len(set(remove_cells)) != len(remove_cells) or any(
            not 0 <= i < cells.count for i in remove_cells):
        ctx.fail(INVALID_PARAMS, "remove_cells must be unique indexes between 0 and %d" % (cells.count - 1),
                 cells_hint(ctx, cells))
    for i in remove_cells:
        cells.item(i).isSelected = True
    return finish(ctx, tf.add(inp), name)
