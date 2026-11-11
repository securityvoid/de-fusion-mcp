"""Primitives: cylinder, sphere and torus as a sketch plus a real extrude or revolve feature (design § 6)."""
import math

import adsk.core

from ..actions import action
from ..errors import INVALID_PARAMS
from ..types import Body, Component, Length, Operation, Plane
from .model_common import origin_plane, positive, resolve_plane, val
from .model_features import build_extrude, build_revolve, check_participants


def _setup(ctx, center, component, operation, participant_bodies, **lengths):
    """Validate everything before any change; returns ``(comp, cx, cy, lengths)`` (cm)."""
    ctx.require_design()
    if len(center) != 2:
        ctx.fail(INVALID_PARAMS, "center needs 2 values [x, y]")
    vals = {k: val(ctx, v, "length") for k, v in lengths.items()}
    for k, v in vals.items():
        positive(ctx, v, k)
    cx, cy = (val(ctx, c, "length") for c in center)
    check_participants(ctx, operation, participant_bodies)
    comp = ctx.component(component)
    return comp, cx.number, cy.number, vals


def _sketch(comp, plane_in, ctx, kind_name, name):
    sk = comp.sketches.add(origin_plane(comp, resolve_plane(ctx, plane_in, comp)))
    sk.name = "%s sketch" % (name or kind_name)
    return sk


def _done(ctx, feat, sk, name):
    if name:
        feat.name = name
        for b in feat.bodies:
            b.name = name
    return {"feature": ctx.ref(feat), "bodies": [ctx.ref(b) for b in feat.bodies], "sketch": ctx.ref(sk)}


@action("model", access="write")
def cylinder(ctx, diameter: Length, height: Length, plane: Plane = "XY", center: list[Length] = [0, 0],
             operation: Operation = "new_body", participant_bodies: list[Body] | None = None,
             component: Component | None = None, name: str | None = None) -> dict:
    """Create a cylinder: a circle sketch extruded by height."""
    comp, cx, cy, v = _setup(ctx, center, component, operation, participant_bodies,
                             diameter=diameter, height=height)
    sk = _sketch(comp, plane, ctx, "Cylinder", name)
    sk.sketchCurves.sketchCircles.addByCenterRadius(adsk.core.Point3D.create(cx, cy, 0), v["diameter"].number / 2)
    feat = build_extrude(ctx, sk.profiles.item(0), distance=v["height"], operation=operation,
                         participant_bodies=participant_bodies)
    return _done(ctx, feat, sk, name)


@action("model", access="write")
def sphere(ctx, diameter: Length, plane: Plane = "XY", center: list[Length] = [0, 0],
           operation: Operation = "new_body", participant_bodies: list[Body] | None = None,
           component: Component | None = None, name: str | None = None) -> dict:
    """Create a sphere: a half-disc sketch revolved 360 degrees about its diameter."""
    comp, cx, cy, v = _setup(ctx, center, component, operation, participant_bodies,
                             diameter=diameter)
    sk = _sketch(comp, plane, ctx, "Sphere", name)
    r = v["diameter"].number / 2
    arc = sk.sketchCurves.sketchArcs.addByCenterStartSweep(
        adsk.core.Point3D.create(cx, cy, 0), adsk.core.Point3D.create(cx + r, cy, 0), math.pi)
    axis = sk.sketchCurves.sketchLines.addByTwoPoints(arc.startSketchPoint, arc.endSketchPoint)
    feat = build_revolve(ctx, sk.profiles.item(0), axis, operation=operation, participant_bodies=participant_bodies)
    return _done(ctx, feat, sk, name)


@action("model", access="write")
def torus(ctx, major_diameter: Length, minor_diameter: Length, plane: Plane = "XY",
          center: list[Length] = [0, 0], operation: Operation = "new_body",
          participant_bodies: list[Body] | None = None, component: Component | None = None,
          name: str | None = None) -> dict:
    """Create a torus: a circle revolved 360 degrees about a construction line (the sketch Y direction)."""
    comp, cx, cy, v = _setup(ctx, center, component, operation, participant_bodies,
                             major_diameter=major_diameter, minor_diameter=minor_diameter)
    major, minor = v["major_diameter"].number, v["minor_diameter"].number
    if minor >= major:
        ctx.fail(INVALID_PARAMS, "minor_diameter must be < major_diameter")
    sk = _sketch(comp, plane, ctx, "Torus", name)
    pt = adsk.core.Point3D.create
    axis = sk.sketchCurves.sketchLines.addByTwoPoints(pt(cx, cy - major / 2, 0), pt(cx, cy + major / 2, 0))
    axis.isConstruction = True
    sk.sketchCurves.sketchCircles.addByCenterRadius(pt(cx + major / 2, cy, 0), minor / 2)
    feat = build_revolve(ctx, sk.profiles.item(0), axis, operation=operation, participant_bodies=participant_bodies)
    return _done(ctx, feat, sk, name)
