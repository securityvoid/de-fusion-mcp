"""Construction geometry and user coordinate systems (design § 9)."""
from typing import Literal

import adsk.core
import adsk.fusion

from ..actions import action
from ..errors import INVALID_PARAMS
from ..types import Angle, Axis, Component, Edge, Entity, Expr, Face, Length, Plane
from .model_common import expect, origin_plane, require_api, val, vi

POINT_KINDS = ("BRepVertex", "SketchPoint", "ConstructionPoint")
PLANE_NEEDS = {
    "offset": ("plane",), "angle": ("axis", "plane"), "midplane": ("plane", "plane_two"),
    "three_points": ("points",), "tangent": ("face", "plane"), "along_path": ("path",),
}
AXIS_NEEDS = {
    "two_planes": ("plane", "plane_two"), "two_points": ("points",), "edge": ("edge",),
    "circular_face": ("face",), "normal_to_face": ("face", "point"),
}


def _need(ctx, method, needs, given):
    missing = [n for n in needs if given[n] is None]
    if missing:
        ctx.fail(INVALID_PARAMS, "method %s needs %s" % (method, ", ".join(missing)))


def _points(ctx, points, count):
    if len(points) != count:
        ctx.fail(INVALID_PARAMS, "points needs exactly %d entries" % count)
    expect(ctx, points, POINT_KINDS, "points")
    return points


def _round_face(ctx, face):
    st = face.geometry.surfaceType
    if st not in (adsk.core.SurfaceTypes.CylinderSurfaceType, adsk.core.SurfaceTypes.ConeSurfaceType):
        ctx.fail(INVALID_PARAMS, "face must be cylindrical or conical")


@action("model", access="write")
def construction_plane(ctx, method: Literal["offset", "angle", "midplane", "three_points", "tangent", "along_path"],
                       plane: Plane | None = None, offset: Length = 0, axis: Axis | None = None,
                       angle: Angle = 0, plane_two: Plane | None = None, points: list[Entity] | None = None,
                       face: Face | None = None, path: Entity | None = None, position: Expr = 0.5,
                       component: Component | None = None, name: str | None = None) -> dict:
    """Create a construction plane by offset, angle, midplane, three points, tangent or position along a path."""
    ctx.require_parametric()
    _need(ctx, method, PLANE_NEEDS[method], dict(plane=plane, axis=axis, plane_two=plane_two, points=points,
                                                 face=face, path=path))
    comp = ctx.component(component)
    cp = comp.constructionPlanes
    if method == "three_points":
        _points(ctx, points, 3)
    elif method == "tangent":
        _round_face(ctx, face)
    elif method == "along_path":
        expect(ctx, [path], ("BRepEdge", "SketchCurve"), "path")
        if not 0 <= val(ctx, position, "expr").number <= 1:
            ctx.fail(INVALID_PARAMS, "position must be between 0 and 1")
    inp = cp.createInput()
    if method == "offset":
        inp.setByOffset(origin_plane(comp, plane), vi(ctx, offset))
    elif method == "angle":
        inp.setByAngle(axis, vi(ctx, angle, "angle"), origin_plane(comp, plane))
    elif method == "midplane":
        inp.setByTwoPlanes(origin_plane(comp, plane), origin_plane(comp, plane_two))
    elif method == "three_points":
        inp.setByThreePoints(*points)
    elif method == "tangent":
        inp.setByTangent(face, vi(ctx, angle, "angle"), origin_plane(comp, plane))
    else:
        inp.setByDistanceOnPath(path, vi(ctx, position, "expr"))
    obj = cp.add(inp)
    if name:
        obj.name = name
    return {"plane": ctx.ref(obj)}


@action("model", access="write")
def construction_axis(ctx, method: Literal["two_planes", "two_points", "edge", "circular_face", "normal_to_face"],
                      plane: Plane | None = None, plane_two: Plane | None = None, points: list[Entity] | None = None,
                      edge: Edge | None = None, face: Face | None = None, point: Entity | None = None,
                      component: Component | None = None, name: str | None = None) -> dict:
    """Create a construction axis by two planes, two points, an edge, a circular face or normal to a face."""
    ctx.require_parametric()
    _need(ctx, method, AXIS_NEEDS[method], dict(plane=plane, plane_two=plane_two, points=points, edge=edge,
                                                face=face, point=point))
    comp = ctx.component(component)
    ca = comp.constructionAxes
    if method == "two_points":
        _points(ctx, points, 2)
    elif method == "edge":
        if not isinstance(edge.geometry, adsk.core.Line3D):
            ctx.fail(INVALID_PARAMS, "edge must be linear")
    elif method == "circular_face":
        _round_face(ctx, face)
    elif method == "normal_to_face":
        expect(ctx, [point], POINT_KINDS, "point")
    inp = ca.createInput()
    if method == "two_planes":
        inp.setByTwoPlanes(origin_plane(comp, plane), origin_plane(comp, plane_two))
    elif method == "two_points":
        inp.setByTwoPoints(*points)
    elif method == "edge":
        inp.setByEdge(edge)
    elif method == "circular_face":
        inp.setByCircularFace(face)
    else:
        inp.setByNormalToFaceAtPoint(face, point)
    obj = ca.add(inp)
    if name:
        obj.name = name
    return {"axis": ctx.ref(obj)}


@action("model", access="write")
def ucs(ctx, origin: Entity, angle_x: Angle = 0, angle_y: Angle = 0, angle_z: Angle = 0,
        component: Component | None = None, name: str | None = None) -> dict:
    """Create a user coordinate system at a point (preview API)."""
    ctx.require_design()
    comp = ctx.component(component)
    require_api(ctx, comp, "userCoordinateSystems", "Component.userCoordinateSystems")
    expect(ctx, [origin], POINT_KINDS, "origin")
    coll = comp.userCoordinateSystems
    inp = coll.createInput(adsk.fusion.UserCoordinateSystemGeometry.createByPoint(origin))
    for attr, value in (("angleX", angle_x), ("angleY", angle_y), ("angleZ", angle_z)):
        v = val(ctx, value, "angle")
        if v.number != 0:
            require_api(ctx, inp, attr, "UserCoordinateSystemInput." + attr)
            setattr(inp, attr, v.value_input())
    obj = coll.add(inp)
    if name:
        obj.name = name
    return {"ucs": ctx.ref(obj)}
