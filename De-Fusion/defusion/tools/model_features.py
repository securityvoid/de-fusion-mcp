"""Solid features: extrude, revolve, sweep, loft, fillet, chamfer, shell, patterns, threads and more."""
from typing import Literal

import adsk.core
import adsk.fusion

from ..actions import action
from ..errors import FUSION_ERROR, INVALID_PARAMS, UNSUPPORTED
from ..types import (
    Angle, Axis, Body, Edge, Entity, Expr, Face, Length, Operation, Plane, Point3, Profile, SketchPoint,
    adsk_enum,
)
from .model_common import (
    coll, expect, finish, kind, owner, participants, positive, require_api, same, same_owner, uniform, val, vi,
)

PATTERN_CAP = 500
THREAD_TYPE_HINTS = 20

SHAPE_END_KINDS = ("BRepFace", "BRepBody", "BRepVertex", "ConstructionPlane", "ConstructionPoint")


def _first(x):
    """A resolved ``Profile`` may be one profile or a collection of them."""
    return x.item(0) if isinstance(x, adsk.core.ObjectCollection) else x


def check_participants(ctx, operation, participant_bodies):
    if participant_bodies is not None and operation in ("new_body", "new_component"):
        ctx.fail(INVALID_PARAMS, "participant_bodies applies only to join, cut and intersect")


def _direction(flip):
    d = adsk.fusion.ExtentDirections
    return d.NegativeExtentDirection if flip else d.PositiveExtentDirection


def _need(ctx, value, name, why):
    if value is None:
        ctx.fail(INVALID_PARAMS, "%s is required for %s" % (name, why))


def build_extrude(ctx, profile, *, extent="distance", distance=None, distance_two=None, to_entity=None,
                  to_offset=0, flip=False, full_length=True, taper_angle=0, taper_angle_two=0,
                  operation="new_body", participant_bodies=None):
    """The extrude of design § 4.1 (shared with the primitives); returns the feature."""
    check_participants(ctx, operation, participant_bodies)
    if extent == "to_object":
        _need(ctx, to_entity, "to_entity", "extent to_object")
        expect(ctx, [to_entity], SHAPE_END_KINDS, "to_entity")
    else:
        _need(ctx, distance, "distance", "extent %s" % extent)
        positive(ctx, distance, "distance")
        if extent == "two_sided":
            _need(ctx, distance_two, "distance_two", "extent two_sided")
            positive(ctx, distance_two, "distance_two")
    comp = owner(_first(profile))
    ext = comp.features.extrudeFeatures
    inp = ext.createInput(profile, adsk_enum(operation))
    taper = vi(ctx, taper_angle, "angle")
    if extent == "distance":
        inp.setOneSideExtent(adsk.fusion.DistanceExtentDefinition.create(vi(ctx, distance)), _direction(flip), taper)
    elif extent == "to_object":
        inp.setOneSideExtent(
            adsk.fusion.ToEntityExtentDefinition.create(to_entity, False, vi(ctx, to_offset)), _direction(flip), taper)
    elif extent == "symmetric":
        inp.setSymmetricExtent(vi(ctx, distance), full_length, taper)
    else:
        inp.setTwoSidesExtent(
            adsk.fusion.DistanceExtentDefinition.create(vi(ctx, distance)),
            adsk.fusion.DistanceExtentDefinition.create(vi(ctx, distance_two)),
            taper, vi(ctx, taper_angle_two, "angle"))
    participants(ctx, inp, participant_bodies)
    return ext.add(inp)


@action("model", access="write")
def extrude(ctx, profile: Profile, extent: Literal["distance", "to_object", "symmetric", "two_sided"] = "distance",
            distance: Length | None = None, distance_two: Length | None = None,
            to_entity: Entity | None = None, to_offset: Length = 0, flip: bool = False,
            full_length: bool = True, taper_angle: Angle = 0, taper_angle_two: Angle = 0,
            operation: Operation = "new_body", participant_bodies: list[Body] | None = None,
            name: str | None = None) -> dict:
    """Extrude a sketch profile by a distance, to an object, symmetrically or on two sides."""
    ctx.require_design()
    feat = build_extrude(
        ctx, profile, extent=extent, distance=distance, distance_two=distance_two, to_entity=to_entity,
        to_offset=to_offset, flip=flip, full_length=full_length, taper_angle=taper_angle,
        taper_angle_two=taper_angle_two, operation=operation, participant_bodies=participant_bodies)
    return finish(ctx, feat, name)


def build_revolve(ctx, profile, axis, *, extent="angle", angle="360 deg", angle_two=None,
                  operation="new_body", participant_bodies=None):
    """The revolve of design § 4.2 (shared with the primitives); returns the feature."""
    check_participants(ctx, operation, participant_bodies)
    positive(ctx, val(ctx, angle, "angle"), "angle")
    if extent == "two_sided":
        _need(ctx, angle_two, "angle_two", "extent two_sided")
    rev = owner(_first(profile)).features.revolveFeatures
    inp = rev.createInput(profile, axis, adsk_enum(operation))
    if extent == "two_sided":
        inp.setTwoSideAngleExtent(vi(ctx, angle, "angle"), vi(ctx, angle_two, "angle"))
    else:
        inp.setAngleExtent(extent == "symmetric", vi(ctx, angle, "angle"))
    participants(ctx, inp, participant_bodies)
    return rev.add(inp)


@action("model", access="write")
def revolve(ctx, profile: Profile, axis: Axis, extent: Literal["angle", "symmetric", "two_sided"] = "angle",
            angle: Angle = "360 deg", angle_two: Angle | None = None, operation: Operation = "new_body",
            participant_bodies: list[Body] | None = None, name: str | None = None) -> dict:
    """Revolve a sketch profile around an axis."""
    ctx.require_design()
    feat = build_revolve(ctx, profile, axis, extent=extent, angle=angle, angle_two=angle_two,
                         operation=operation, participant_bodies=participant_bodies)
    return finish(ctx, feat, name)


@action("model", access="write")
def sweep(ctx, profile: Profile, path: list[Entity], chain: bool = True,
          guide_rail: list[Entity] | None = None, twist_angle: Angle = 0,
          orientation: Literal["perpendicular", "parallel"] = "perpendicular",
          operation: Operation = "new_body", participant_bodies: list[Body] | None = None,
          name: str | None = None) -> dict:
    """Sweep a profile along a path, optionally with a guide rail and twist."""
    ctx.require_design()
    check_participants(ctx, operation, participant_bodies)
    expect(ctx, path, ("SketchCurve", "BRepEdge"), "path")
    if guide_rail is not None:
        expect(ctx, guide_rail, ("SketchCurve", "BRepEdge"), "guide_rail")
    comp = owner(_first(profile))
    sw = comp.features.sweepFeatures
    inp = sw.createInput(profile, comp.features.createPath(coll(path), chain), adsk_enum(operation))
    o = adsk.fusion.SweepOrientationTypes
    inp.orientation = o.PerpendicularOrientationType if orientation == "perpendicular" else o.ParallelOrientationType
    if guide_rail is not None:
        inp.guideRail = comp.features.createPath(coll(guide_rail), chain)
    if val(ctx, twist_angle, "angle").number != 0:
        inp.twistAngle = vi(ctx, twist_angle, "angle")
    participants(ctx, inp, participant_bodies)
    return finish(ctx, sw.add(inp), name)


@action("model", access="write")
def loft(ctx, sections: list[Profile], rails: list[Entity] | None = None, centerline: Entity | None = None,
         solid: bool = True, closed: bool = False, operation: Operation = "new_body",
         participant_bodies: list[Body] | None = None, name: str | None = None) -> dict:
    """Loft between two or more profiles, optionally along rails or a centerline."""
    ctx.require_design()
    check_participants(ctx, operation, participant_bodies)
    if len(sections) < 2:
        ctx.fail(INVALID_PARAMS, "loft needs at least 2 sections")
    if rails is not None and centerline is not None:
        ctx.fail(INVALID_PARAMS, "give rails or centerline, not both")
    if rails is not None:
        expect(ctx, rails, ("SketchCurve", "BRepEdge"), "rails")
    if centerline is not None:
        expect(ctx, [centerline], ("SketchCurve", "BRepEdge"), "centerline")
    lf = owner(_first(sections[0])).features.loftFeatures
    inp = lf.createInput(adsk_enum(operation))
    for s in sections:
        inp.loftSections.add(s)
    for r in rails or ():
        inp.centerLineOrRails.addRail(r)
    if centerline is not None:
        inp.centerLineOrRails.addCenterLine(centerline)
    inp.isSolid = solid
    inp.isClosed = closed
    participants(ctx, inp, participant_bodies)
    return finish(ctx, lf.add(inp), name)


# --- edge, face and body features -----------------------------------------------------------------


@action("model", access="write")
def fillet(ctx, edges: list[Edge], radius: Length, tangent_chain: bool = True, name: str | None = None) -> dict:
    """Fillet edges with a constant radius."""
    ctx.require_design()
    positive(ctx, radius, "radius")
    ff = same_owner(ctx, edges, "edges").features.filletFeatures
    inp = ff.createInput()
    target = inp.edgeSetInputs if hasattr(inp, "edgeSetInputs") else inp
    target.addConstantRadiusEdgeSet(coll(edges), vi(ctx, radius), tangent_chain)
    return finish(ctx, ff.add(inp), name)


@action("model", access="write")
def chamfer(ctx, edges: list[Edge],
            chamfer_type: Literal["equal_distance", "two_distances", "distance_angle"] = "equal_distance",
            distance: Length = "1 mm", distance_two: Length | None = None, angle: Angle | None = None,
            flip: bool = False, tangent_chain: bool = True, name: str | None = None) -> dict:
    """Chamfer edges with an equal distance, two distances or a distance and an angle."""
    ctx.require_design()
    positive(ctx, val(ctx, distance, "length"), "distance")
    if chamfer_type == "two_distances":
        _need(ctx, distance_two, "distance_two", "chamfer_type two_distances")
        positive(ctx, distance_two, "distance_two")
    if chamfer_type == "distance_angle":
        _need(ctx, angle, "angle", "chamfer_type distance_angle")
        if not 0 < angle.rad < 1.5707963267948966:
            ctx.fail(INVALID_PARAMS, "angle must be between 0 and 90 deg")
    cf = same_owner(ctx, edges, "edges").features.chamferFeatures
    d = vi(ctx, distance)
    if hasattr(cf, "createInput2"):
        inp = cf.createInput2()
        sets = inp.chamferEdgeSets
        if chamfer_type == "equal_distance":
            sets.addEqualDistanceChamferEdgeSet(coll(edges), d, tangent_chain)
        elif chamfer_type == "two_distances":
            sets.addTwoDistancesChamferEdgeSet(coll(edges), d, vi(ctx, distance_two), flip, tangent_chain)
        else:
            sets.addDistanceAndAngleChamferEdgeSet(coll(edges), d, vi(ctx, angle, "angle"), flip, tangent_chain)
    else:  # older builds: flip is ignored
        inp = cf.createInput(coll(edges), tangent_chain)
        if chamfer_type == "equal_distance":
            inp.setToEqualDistance(d)
        elif chamfer_type == "two_distances":
            inp.setToTwoDistances(d, vi(ctx, distance_two))
        else:
            inp.setToDistanceAndAngle(d, vi(ctx, angle, "angle"))
    return finish(ctx, cf.add(inp), name)


@action("model", access="write")
def shell(ctx, thickness: Length, faces: list[Face] | None = None, bodies: list[Body] | None = None,
          direction: Literal["inside", "outside", "both"] = "inside", tangent_chain: bool = True,
          name: str | None = None) -> dict:
    """Shell a body: remove faces and hollow it, or hollow a closed body."""
    ctx.require_design()
    if (faces is None) == (bodies is None):
        ctx.fail(INVALID_PARAMS, "give exactly one of faces or bodies")
    positive(ctx, thickness, "thickness")
    items = faces if faces is not None else bodies
    sf = same_owner(ctx, items, "faces" if faces is not None else "bodies").features.shellFeatures
    inp = sf.createInput(coll(items), tangent_chain)
    if direction in ("inside", "both"):
        inp.insideThickness = vi(ctx, thickness)
    if direction in ("outside", "both"):
        inp.outsideThickness = vi(ctx, thickness)
    return finish(ctx, sf.add(inp), name)


@action("model", access="write")
def mirror(ctx, entities: list[Entity], plane: Plane, combine: bool = False, name: str | None = None) -> dict:
    """Mirror bodies or features across a plane."""
    ctx.require_design()
    group = uniform(ctx, entities, ("bodies", "features"), "entities")
    if group == "features":
        ctx.require_parametric()
        if combine:
            ctx.fail(INVALID_PARAMS, "combine applies only to bodies")
    mf = same_owner(ctx, entities, "entities").features.mirrorFeatures
    inp = mf.createInput(coll(entities), plane)
    if group == "bodies":
        inp.isCombine = combine
    return finish(ctx, mf.add(inp), name)


@action("model", access="write")
def hole(ctx, hole_type: Literal["simple", "counterbore", "countersink"] = "simple", diameter: Length = "5 mm",
         depth: Length | None = None, through_all: bool = False,
         face: Face | None = None, point: Point3 | None = None, sketch_points: list[SketchPoint] | None = None,
         counterbore_diameter: Length | None = None, counterbore_depth: Length | None = None,
         countersink_diameter: Length | None = None, countersink_angle: Angle = "90 deg",
         tip_angle: Angle = "118 deg", participant_bodies: list[Body] | None = None,
         name: str | None = None) -> dict:
    """Create a simple, counterbore or countersink hole at a point on a face or at sketch points."""
    ctx.require_design()
    by_face = face is not None or point is not None
    if by_face == (sketch_points is not None):
        ctx.fail(INVALID_PARAMS, "give either face and point, or sketch_points")
    if by_face and (face is None or point is None):
        ctx.fail(INVALID_PARAMS, "face and point go together")
    if (depth is None) == (not through_all):
        ctx.fail(INVALID_PARAMS, "give exactly one of depth or through_all: true")
    d = val(ctx, diameter, "length")
    positive(ctx, d, "diameter")
    if depth is not None:
        positive(ctx, depth, "depth")
    if hole_type == "counterbore":
        _need(ctx, counterbore_diameter, "counterbore_diameter", "hole_type counterbore")
        _need(ctx, counterbore_depth, "counterbore_depth", "hole_type counterbore")
        if counterbore_diameter.number <= d.number:
            ctx.fail(INVALID_PARAMS, "counterbore_diameter must be > diameter")
        positive(ctx, counterbore_depth, "counterbore_depth")
    if hole_type == "countersink":
        _need(ctx, countersink_diameter, "countersink_diameter", "hole_type countersink")
        if countersink_diameter.number <= d.number:
            ctx.fail(INVALID_PARAMS, "countersink_diameter must be > diameter")
    if by_face and face.geometry.surfaceType != adsk.core.SurfaceTypes.PlaneSurfaceType:
        ctx.fail(INVALID_PARAMS, "face must be planar")
    hf = (owner(face) if by_face else owner(sketch_points[0])).features.holeFeatures
    if hole_type == "simple":
        inp = hf.createSimpleInput(vi(ctx, diameter))
    elif hole_type == "counterbore":
        inp = hf.createCounterboreInput(vi(ctx, diameter), vi(ctx, counterbore_diameter), vi(ctx, counterbore_depth))
    else:
        inp = hf.createCountersinkInput(vi(ctx, diameter), vi(ctx, countersink_diameter),
                                        vi(ctx, countersink_angle, "angle"))
    if by_face:
        inp.setPositionByPoint(face, point)
    else:
        inp.setPositionBySketchPoints(coll(sketch_points))
    if through_all:
        inp.setAllExtent(adsk.fusion.ExtentDirections.PositiveExtentDirection)
    else:
        inp.setDistanceExtent(vi(ctx, depth))
    inp.tipAngle = vi(ctx, tip_angle, "angle")
    participants(ctx, inp, participant_bodies)
    return finish(ctx, hf.add(inp), name)


def _pattern_group(ctx, entities):
    group = uniform(ctx, entities, ("bodies", "features", "faces"), "entities")
    if group == "features":
        ctx.require_parametric()
    return group


def _count(ctx, q, param, minimum):
    n = val(ctx, q, "expr").number
    if n != int(n) or n < minimum:
        ctx.fail(INVALID_PARAMS, "%s must be an integer >= %d" % (param, minimum))
    return int(n)


def _cap(ctx, total):
    if total > PATTERN_CAP:
        ctx.fail(INVALID_PARAMS, "pattern is capped at %d instances; split it" % PATTERN_CAP)


def _pattern_result(ctx, feat, name):
    extra = {"pattern_count": feat.patternElements.count} if hasattr(feat, "patternElements") else {}
    return finish(ctx, feat, name, **extra)


@action("model", access="write")
def rect_pattern(ctx, entities: list[Entity], direction_one: Axis, quantity_one: Expr, distance_one: Length,
                 direction_two: Axis | None = None, quantity_two: Expr = 1, distance_two: Length | None = None,
                 distance_type: Literal["spacing", "extent"] = "spacing", symmetric: bool = False,
                 name: str | None = None) -> dict:
    """Pattern bodies, features or faces in one or two directions."""
    ctx.require_design()
    _pattern_group(ctx, entities)
    q1 = _count(ctx, quantity_one, "quantity_one", 2)
    q2 = _count(ctx, quantity_two, "quantity_two", 1)
    if direction_two is not None:
        _need(ctx, distance_two, "distance_two", "direction_two")
    _cap(ctx, q1 * q2)
    pf = same_owner(ctx, entities, "entities").features.rectangularPatternFeatures
    t = adsk.fusion.PatternDistanceType
    inp = pf.createInput(
        coll(entities), direction_one, vi(ctx, quantity_one, "expr"), vi(ctx, distance_one),
        t.SpacingPatternDistanceType if distance_type == "spacing" else t.ExtentPatternDistanceType)
    if direction_two is not None:
        inp.setDirectionTwo(direction_two, vi(ctx, quantity_two, "expr"), vi(ctx, distance_two))
    inp.isSymmetricInDirectionOne = symmetric
    if direction_two is not None:
        inp.isSymmetricInDirectionTwo = symmetric
    return _pattern_result(ctx, pf.add(inp), name)


@action("model", access="write")
def circ_pattern(ctx, entities: list[Entity], axis: Axis, quantity: Expr, total_angle: Angle = "360 deg",
                 symmetric: bool = False, name: str | None = None) -> dict:
    """Pattern bodies, features or faces around an axis."""
    ctx.require_design()
    _pattern_group(ctx, entities)
    _cap(ctx, _count(ctx, quantity, "quantity", 2))
    pf = same_owner(ctx, entities, "entities").features.circularPatternFeatures
    inp = pf.createInput(coll(entities), axis)
    inp.quantity = vi(ctx, quantity, "expr")
    inp.totalAngle = vi(ctx, total_angle, "angle")
    inp.isSymmetric = symmetric
    return _pattern_result(ctx, pf.add(inp), name)


@action("model", access="write")
def draft(ctx, faces: list[Face], pull_plane: Plane, angle: Angle, angle_two: Angle | None = None,
          flip: bool = False, tangent_chain: bool = True, name: str | None = None) -> dict:
    """Apply a draft angle to faces relative to a pull plane."""
    ctx.require_design()
    df = same_owner(ctx, faces, "faces").features.draftFeatures
    inp = df.createInput(list(faces), pull_plane, tangent_chain)
    if angle_two is None:
        inp.setSingleAngle(False, vi(ctx, angle, "angle"))
    else:
        inp.setTwoAngles(vi(ctx, angle, "angle"), vi(ctx, angle_two, "angle"))
    inp.isDirectionFlipped = flip
    return finish(ctx, df.add(inp), name)


SPLIT_TOOLS = ("BRepBody", "BRepFace", "ConstructionPlane", "SketchCurve")


@action("model", access="write")
def split_body(ctx, bodies: list[Body], tool: Entity, extend_tool: bool = True, name: str | None = None) -> dict:
    """Split bodies with a body, face, plane or sketch curve."""
    ctx.require_design()
    expect(ctx, [tool], SPLIT_TOOLS, "tool")
    sf = same_owner(ctx, bodies, "bodies").features.splitBodyFeatures
    inp = sf.createInput(coll(bodies), tool, extend_tool)
    return finish(ctx, sf.add(inp), name)


@action("model", access="write")
def split_face(ctx, faces: list[Face], tool: Entity, extend_tool: bool = True, name: str | None = None) -> dict:
    """Split faces with a body, face, plane or sketch curve."""
    ctx.require_design()
    expect(ctx, [tool], SPLIT_TOOLS, "tool")
    sf = same_owner(ctx, faces, "faces").features.splitFaceFeatures
    inp = sf.createInput(coll(faces), tool, extend_tool)
    return finish(ctx, sf.add(inp), name)


@action("model", access="write")
def offset_faces(ctx, faces: list[Face], distance: Length, name: str | None = None) -> dict:
    """Offset faces by a signed distance."""
    ctx.require_design()
    if distance.number == 0:
        ctx.fail(INVALID_PARAMS, "distance must not be 0")
    of = same_owner(ctx, faces, "faces").features.offsetFacesFeatures
    require_api(ctx, of, "createInput", "OffsetFacesFeatures.createInput")
    inp = of.createInput(list(faces), vi(ctx, distance))
    return finish(ctx, of.add(inp), name)


@action("model", access="write")
def scale(ctx, entities: list[Entity], factor: Expr = 1, factors: list[Expr] | None = None,
          base_point: Entity | None = None, name: str | None = None) -> dict:
    """Scale bodies or sketches uniformly, or bodies non-uniformly with three factors."""
    ctx.require_design()
    expect(ctx, entities, ("BRepBody", "Sketch"), "entities")
    if factors is not None:
        if len(factors) != 3:
            ctx.fail(INVALID_PARAMS, "factors needs exactly 3 values")
        if any(kind(e) != "BRepBody" for e in entities):
            ctx.fail(INVALID_PARAMS, "factors applies only to bodies")
    for label, f in [("factor", factor)] + [("factors", f) for f in factors or ()]:
        positive(ctx, val(ctx, f, "expr"), label)
    if base_point is not None:
        expect(ctx, [base_point], ("BRepVertex", "SketchPoint", "ConstructionPoint"), "base_point")
    comp = same_owner(ctx, entities, "entities")
    sf = comp.features.scaleFeatures
    inp = sf.createInput(coll(entities), base_point if base_point is not None else comp.originConstructionPoint,
                         vi(ctx, factor, "expr"))
    if factors is not None:
        inp.setToNonUniform(*[vi(ctx, f, "expr") for f in factors])
    return finish(ctx, sf.add(inp), name)


def _axis_line(axis):
    """(origin Point3D, direction Vector3D) of a construction axis, linear edge or sketch line."""
    k = kind(axis)
    if k == "ConstructionAxis":
        g = axis.geometry
        return g.origin, g.direction
    g = axis.geometry if k == "BRepEdge" else axis.worldGeometry
    s, e = g.startPoint, g.endPoint
    v = adsk.core.Vector3D.create(e.x - s.x, e.y - s.y, e.z - s.z)
    v.normalize()
    return s, v


@action("model", access="write")
def move(ctx, bodies: list[Body], translate: Point3 | None = None, rotate_axis: Axis | None = None,
         angle: Angle | None = None, name: str | None = None) -> dict:
    """Move bodies by a translation, a rotation about an axis, or both (rotation first)."""
    ctx.require_design()
    if (rotate_axis is None) != (angle is None):
        ctx.fail(INVALID_PARAMS, "rotate_axis and angle go together")
    if translate is None and rotate_axis is None:
        ctx.fail(INVALID_PARAMS, "give translate, or rotate_axis with angle")
    mf = same_owner(ctx, bodies, "bodies").features.moveFeatures
    m = adsk.core.Matrix3D.create()
    if rotate_axis is not None:
        origin, direction = _axis_line(rotate_axis)
        m.setToRotation(angle.rad, direction, origin)
    if translate is not None:
        t = adsk.core.Matrix3D.create()
        t.translation = adsk.core.Vector3D.create(translate.x, translate.y, translate.z)
        m.transformBy(t)
    if hasattr(mf, "createInput2"):
        inp = mf.createInput2(coll(bodies))
        inp.defineAsFreeMove(m)
    else:
        inp = mf.createInput(coll(bodies), m)
    return finish(ctx, mf.add(inp), name)


@action("model", access="write")
def combine(ctx, target: Body, tools: list[Body], operation: Literal["join", "cut", "intersect"] = "join",
            keep_tools: bool = False, name: str | None = None) -> dict:
    """Combine a target body with tool bodies (join, cut or intersect)."""
    ctx.require_design()
    if any(same(target, t) for t in tools):
        ctx.fail(INVALID_PARAMS, "target must not be one of tools")
    cf = same_owner(ctx, [target] + list(tools), "bodies").features.combineFeatures
    inp = cf.createInput(target, coll(tools))
    inp.operation = adsk_enum(operation)
    inp.isKeepToolBodies = keep_tools
    inp.isNewComponent = False
    return finish(ctx, cf.add(inp), name)


@action("model", access="write")
def rename(ctx, target: Entity, name: str) -> dict:
    """Rename a body, feature, sketch, component or other named entity."""
    ctx.require_design()
    if not name.strip():
        ctx.fail(INVALID_PARAMS, "name must not be empty")
    k = kind(target)
    if k == "Occurrence":
        ctx.fail(INVALID_PARAMS, "occurrences cannot be renamed", hints=["rename its component instead"])
    if not hasattr(target, "name"):
        ctx.fail(INVALID_PARAMS, "%s has no name to change" % k)
    previous = target.name
    try:
        target.name = name
    except Exception as exc:
        if "read-only" in str(exc).lower() or "read only" in str(exc).lower():
            ctx.fail(INVALID_PARAMS, "%s name is read-only" % k)
        ctx.fail(FUSION_ERROR, "rename failed: %s" % exc)
    return {"target": ctx.ref(target), "previous": previous, "name": target.name}


# --- threads -----------------------------------------------------------------------------------


def _thread_query(ctx):
    return ctx.require_design().rootComponent.features.threadFeatures.threadDataQuery


@action("model", access="read")
def thread_query(ctx, thread_type: str | None = None, size: str | None = None, designation: str | None = None,
                 internal: bool = False, offset: int = 0, limit: int = 100) -> dict:
    """List thread types, then the sizes, designations or classes of one type, one level per call."""
    q = _thread_query(ctx)
    types = list(q.allThreadTypes)
    if thread_type is None:
        level, items = "types", types
    else:
        if thread_type not in types:
            ctx.fail(INVALID_PARAMS, "unknown thread_type %r" % thread_type, hints=types[:THREAD_TYPE_HINTS])
        if designation is not None:
            level, items = "classes", q.allClasses(internal, thread_type, designation)
        elif size is not None:
            level, items = "designations", q.allDesignations(thread_type, size)
        else:
            level, items = "sizes", q.allSizes(thread_type)
    return {"level": level, **ctx.page(list(items), offset, limit)}


@action("model", access="read")
def list_threads(ctx, offset: int = 0, limit: int = 100) -> dict:
    """List the thread features of every component."""
    design = ctx.require_design()
    comps, seen = [], set()
    for c in [design.rootComponent] + list(design.allComponents):
        if c.entityToken not in seen:
            seen.add(c.entityToken)
            comps.append(c)

    def row(feat):
        info = feat.threadInfo
        return {
            "feature": ctx.ref(feat),
            "thread_type": getattr(info, "threadType", None),
            "designation": getattr(info, "threadDesignation", None),
            "thread_class": getattr(info, "threadClass", None),
            "internal": getattr(info, "isInternal", None),
            "modeled": feat.isModeled,
            "full_length": feat.isFullLength,
            "right_handed": getattr(info, "isRightHanded", None),
            "face_count": feat.faces.count,
        }

    feats = []
    for c in comps:
        tf = c.features.threadFeatures
        feats.extend(tf.item(i) for i in range(tf.count))
    return ctx.page(feats, offset, limit, row)


def _is_internal(face):
    """True when the face normal points toward the cylinder axis (a hole)."""
    cyl, p = face.geometry, face.pointOnFace
    a = cyl.axis
    t = (p.x - cyl.origin.x) * a.x + (p.y - cyl.origin.y) * a.y + (p.z - cyl.origin.z) * a.z
    to_axis = (cyl.origin.x + t * a.x - p.x, cyl.origin.y + t * a.y - p.y, cyl.origin.z + t * a.z - p.z)
    _, n = face.evaluator.getNormalAtPoint(p)
    return n.x * to_axis[0] + n.y * to_axis[1] + n.z * to_axis[2] > 0


@action("model", access="write", timeout=60)
def thread(ctx, faces: list[Face], thread_type: str = "ISO Metric profile", designation: str | None = None,
           thread_class: str | None = None, internal: bool | None = None, modeled: bool = False,
           full_length: bool = True, length: Length | None = None, offset: Length = 0,
           right_handed: bool = True, name: str | None = None) -> dict:
    """Add a cosmetic or modeled thread to cylindrical faces; designation and class default to a fit."""
    ctx.require_design()
    cyl = adsk.core.SurfaceTypes.CylinderSurfaceType
    if any(f.geometry.surfaceType != cyl for f in faces):
        ctx.fail(INVALID_PARAMS, "faces must be cylindrical")
    if not full_length:
        _need(ctx, length, "length", "full_length false")
    tf = same_owner(ctx, faces, "faces").features.threadFeatures
    if internal is None:
        internal = _is_internal(faces[0])
    q = tf.threadDataQuery
    recommended = None
    if designation is None:
        diameter = 2 * faces[0].geometry.radius
        ok, designation, recommended = q.recommendThreadData(diameter, internal, thread_type)
        if not ok:
            ctx.fail(INVALID_PARAMS, "no standard thread fits diameter %s" % ctx.length_out(diameter))
    if thread_class is None:
        thread_class = recommended or q.allClasses(internal, thread_type, designation)[0]
    info = tf.createThreadInfo(internal, thread_type, designation, thread_class)
    if hasattr(info, "isRightHanded"):
        info.isRightHanded = right_handed
    elif not right_handed:
        ctx.fail(UNSUPPORTED, "left-hand threads are not available in this Fusion build")
    inp = tf.createInput(coll(faces), info)
    inp.isModeled = modeled
    inp.isFullLength = full_length
    if not full_length:
        inp.threadLength = vi(ctx, length)
        inp.threadOffset = vi(ctx, offset)
    return finish(ctx, tf.add(inp), name, designation=designation, thread_class=thread_class, internal=internal)
