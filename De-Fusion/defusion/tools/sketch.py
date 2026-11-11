"""Create, read and edit Fusion sketches."""
import contextlib
import math
from typing import Annotated, Any, Literal

import adsk.core
import adsk.fusion
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .. import results
from .. import units as _units
from ..actions import action
from ..errors import (
    FUSION_ERROR,
    INVALID_PARAMS,
    RESULT_TOO_LARGE,
    UNSUPPORTED,
    ActionError,
)
from ..types import (
    Angle, Body, Component, Edge, Face, Length, Plane, Point2, Sketch, SketchCurve, SketchPoint,
    Value,
)


# --- shared definitions (design § 2) -------------------------------------------------------------


def xy(ctx, p):
    """[x, y] of a sketch-space point in the output length unit (§ 2.2)."""
    return [ctx.length_out(p.x), ctx.length_out(p.y)]


def kind(obj):
    """Fusion class name of ``obj`` (§ 2.4)."""
    return obj.objectType.split("::")[-1]


_TYPE_NAMES = {
    "SketchLine": "line",
    "SketchCircle": "circle",
    "SketchArc": "arc",
    "SketchFittedSpline": "fitted_spline",
    "SketchControlPointSpline": "control_point_spline",
    "SketchEllipse": "ellipse",
    "SketchEllipticalArc": "elliptical_arc",
    "SketchConicCurve": "conic",
    "SketchFixedSpline": "fixed_spline",
}


def type_name(obj):
    k = kind(obj)
    return _TYPE_NAMES.get(k, k)


def check_owned(ctx, sketch, entities, param):
    """Every curve/point must belong to ``sketch`` (§ 2.3); ``entities`` may be one entity or a list."""
    if not isinstance(entities, (list, tuple)):
        entities = [entities]
    for e in entities:
        if e.parentSketch.entityToken != sketch.entityToken:
            ctx.fail(
                INVALID_PARAMS, "%s: %s is not in sketch %s" % (param, e.entityToken, sketch.name)
            )


def fully_constrained(sketch):
    """§ 2.7."""
    if hasattr(sketch, "isFullyConstrained"):
        return sketch.isFullyConstrained
    return all(c.isFullyConstrained for c in sketch.sketchCurves if not c.isReference)


def curve_summary(ctx, curve):
    """{ref, type, points} of one curve (§ 2.5)."""
    t = type_name(curve)
    ref = ctx.ref

    def attr(name):
        return ref(getattr(curve, name))

    if t == "line":
        points = {"start": attr("startSketchPoint"), "end": attr("endSketchPoint")}
    elif t in ("circle", "ellipse"):
        points = {"center": attr("centerSketchPoint")}
    elif t == "arc":
        points = {
            "center": attr("centerSketchPoint"),
            "start": attr("startSketchPoint"),
            "end": attr("endSketchPoint"),
        }
    elif t == "fitted_spline":
        points = {"fit": [ref(p) for p in curve.fitPoints]}
    elif t == "control_point_spline":
        points = {"control": [ref(p) for p in curve.controlPoints]}
    else:
        points = {}
    return {"ref": ref(curve), "type": t, "points": points}


def draw_result(ctx, sketch, curves):
    """The drawing/editing result shape (§ 2.5)."""
    return {
        "sketch": ctx.ref(sketch),
        "curves": [curve_summary(ctx, c) for c in curves],
        "profiles": sketch.profiles.count,
    }


def reference_plane(sketch):
    """The sketch's construction plane, or None when it sits on a face (Fusion raises then)."""
    try:
        return sketch.referencePlane
    except RuntimeError:
        return None


def frame(ctx, sketch):
    """The sketch frame in model space (§ 3.2 ``sketch``, § 4.1)."""
    o, xd, yd = sketch.origin, sketch.xDirection, sketch.yDirection
    return {
        "origin": [ctx.length_out(o.x), ctx.length_out(o.y), ctx.length_out(o.z)],
        "x_direction": [xd.x, xd.y, xd.z],
        "y_direction": [yd.x, yd.y, yd.z],
    }


# --- read actions (design § 3) -------------------------------------------------------------------

_CONSTRAINTS = {
    "CoincidentConstraint": ("coincident", ("point", "entity")),
    "HorizontalConstraint": ("horizontal", ("line",)),
    "HorizontalPointsConstraint": ("horizontal", ("pointOne", "pointTwo")),
    "VerticalConstraint": ("vertical", ("line",)),
    "VerticalPointsConstraint": ("vertical", ("pointOne", "pointTwo")),
    "ParallelConstraint": ("parallel", ("lineOne", "lineTwo")),
    "PerpendicularConstraint": ("perpendicular", ("lineOne", "lineTwo")),
    "CollinearConstraint": ("collinear", ("lineOne", "lineTwo")),
    "TangentConstraint": ("tangent", ("curveOne", "curveTwo")),
    "EqualConstraint": ("equal", ("curveOne", "curveTwo")),
    "ConcentricConstraint": ("concentric", ("entityOne", "entityTwo")),
    "MidPointConstraint": ("midpoint", ("point", "midPointCurve")),
    "SymmetryConstraint": ("symmetry", ("entityOne", "entityTwo")),
    "SmoothConstraint": ("smooth", ("curveOne", "curveTwo")),
    "CurvatureConstraint": ("curvature", ("curveOne", "curveTwo")),
}

# Dimension class -> (type, entity attributes); ``None`` type: from the linear orientation.
_DIMENSIONS = {
    "SketchLinearDimension": (None, ("entityOne", "entityTwo")),
    "SketchRadialDimension": ("radial", ("entity",)),
    "SketchDiameterDimension": ("diameter", ("entity",)),
    "SketchAngularDimension": ("angular", ("lineOne", "lineTwo")),
    "SketchOffsetDimension": ("offset", ("line", "entityTwo")),
}


def constraint_type(c):
    """The § 3.2 ``type`` of a constraint (its class name when unknown)."""
    spec = _CONSTRAINTS.get(kind(c))
    return spec[0] if spec else kind(c)


def dimension_type(d):
    """The § 3.2 ``type`` of a dimension (its class name when unknown)."""
    k = kind(d)
    if k == "SketchLinearDimension":
        return _orientation_type(d)
    spec = _DIMENSIONS.get(k)
    return spec[0] if spec else k


def _orientation_type(d):
    o = d.orientation
    e = adsk.fusion.DimensionOrientations
    if o == e.HorizontalDimensionOrientation:
        return "horizontal"
    if o == e.VerticalDimensionOrientation:
        return "vertical"
    return "distance"


def _locate(entity, pidx, cidx):
    """("points" | "curves", index) of a sketch entity, or None when it is not in the sketch."""
    token = entity.entityToken
    if token in pidx:
        return "points", pidx[token]
    if token in cidx:
        return "curves", cidx[token]
    return None


def owned_curves(sketch):
    """{curve token: {"kind", "index", "role"}} of curves Fusion created with another object (§ 2.8)."""
    out = {}
    for i, c in enumerate(sketch.sketchCurves):
        for attr, role in (("majorAxisLine", "major"), ("minorAxisLine", "minor")):
            line = getattr(c, attr, None)
            if line is not None:
                out[line.entityToken] = {"kind": "ellipse", "index": i, "role": role}
    for t, text_ in enumerate(getattr(sketch, "sketchTexts", [])):
        d = getattr(text_, "definition", None)
        if d is not None and kind(d) == "MultiLineTextDefinition":
            for k, line in enumerate(d.rectangleLines):
                out[line.entityToken] = {"kind": "text", "index": t, "role": k}
    return out


def _owned_point_tokens(sketch, owned):
    toks = set()
    for c in sketch.sketchCurves:
        if c.entityToken in owned:
            toks.update((c.startSketchPoint.entityToken, c.endSketchPoint.entityToken))
    return toks


def _describe_constraint(i, c, pidx, cidx, owned=None, owned_pts=()):
    k = kind(c)
    spec = _CONSTRAINTS.get(k)
    if spec is None:
        return {"index": i, "type": constraint_type(c), "points": [], "curves": [], "replayable": False}
    t, attrs = constraint_type(c), spec[1]
    item = {"index": i, "type": t, "points": [], "curves": []}
    ok = True
    is_owned = False
    for a in attrs:
        entity = getattr(c, a)
        if entity.entityToken in (owned or ()) or entity.entityToken in owned_pts:
            is_owned = True
        loc = _locate(entity, pidx, cidx)
        if loc is None:
            ok = False
        else:
            item[loc[0]].append(loc[1])
    if is_owned:
        item["owned"] = True
    if k == "SymmetryConstraint":
        loc = _locate(c.symmetryLine, pidx, cidx)
        if loc is None:
            ok = False
        else:
            item["symmetry_line"] = loc[1]
    if not ok:
        item["replayable"] = False
    return item


def _dimension_values(ctx, t, d):
    """value (output units), expression, parameter name and driving flag of a dimension."""
    param = getattr(d, "parameter", None)
    if param is not None:
        raw, expr, name = param.value, param.expression, param.name
    else:
        raw, expr, name = getattr(d, "value", None), None, None
    conv = ctx.angle_out if t == "angular" else ctx.length_out
    return {
        "value": None if raw is None else conv(raw),
        "expression": expr,
        "parameter": name,
        "driving": d.isDriving,
    }


def _describe_dimension(ctx, i, d, pidx, cidx):
    k = kind(d)
    t = dimension_type(d)
    attrs = _DIMENSIONS.get(k, (None, ()))[1]
    if k not in _DIMENSIONS:
        return {"index": i, "type": t, "points": [], "curves": [], "replayable": False}
    item = {"index": i, "type": t, "points": [], "curves": []}
    ok = True
    for a in attrs:
        loc = _locate(getattr(d, a), pidx, cidx)
        if loc is None:
            ok = False
        else:
            item[loc[0]].append(loc[1])
    item.update(_dimension_values(ctx, t, d))
    tp = getattr(d, "textPosition", None)
    item["text_point"] = None if tp is None else xy(ctx, tp)
    if not ok:
        item["replayable"] = False
    return item


def _describe_curve(ctx, i, c, pidx, cidx, owned=None):
    t = type_name(c)
    item = {
        "index": i,
        "ref": ctx.ref(c),
        "type": t,
        "construction": c.isConstruction,
        "fixed": c.isFixed,
        "reference": c.isReference,
        "fully_constrained": c.isFullyConstrained,
    }

    def pi(p):
        return pidx.get(p.entityToken)

    if owned and c.entityToken in owned:
        item["owner"] = dict(owned[c.entityToken])
    if t == "line":
        item["start"], item["end"] = pi(c.startSketchPoint), pi(c.endSketchPoint)
    elif t == "circle":
        item["center"], item["radius"] = pi(c.centerSketchPoint), ctx.length_out(c.radius)
    elif t == "arc":
        g = c.geometry
        item["center"] = pi(c.centerSketchPoint)
        item["start"], item["end"] = pi(c.startSketchPoint), pi(c.endSketchPoint)
        item["radius"] = ctx.length_out(c.radius)
        item["sweep"] = ctx.angle_out(g.endAngle - g.startAngle)
    elif t == "fitted_spline":
        item["fit_points"] = [pi(p) for p in c.fitPoints]
        item["closed"] = c.isClosed
    elif t == "control_point_spline":
        item["control_points"] = [pi(p) for p in c.controlPoints]
        item["degree"] = int(c.degree)
        item["closed"] = getattr(c, "isClosed", False)  # Fusion's control-point splines have no isClosed
    elif t == "ellipse":
        axis = c.geometry.majorAxis
        item["center"] = pi(c.centerSketchPoint)
        item["major_radius"] = ctx.length_out(c.majorAxisRadius)
        item["minor_radius"] = ctx.length_out(c.minorAxisRadius)
        item["major_axis"] = [axis.x, axis.y]
    else:
        item["replayable"] = False
        for key, attr in (("center", "centerSketchPoint"), ("start", "startSketchPoint"),
                          ("end", "endSketchPoint")):
            p = getattr(c, attr, None)
            if p is not None:
                item[key] = pi(p)
    return item


def _align_name(table, enum, value):
    for name, attr in table.items():
        if getattr(enum, attr) == value:
            return name
    return None


_TEXT_MODES = {"MultiLineTextDefinition": "box", "AlongPathTextDefinition": "along_path",
               "FitOnPathTextDefinition": "fit_on_path"}


def text_angle(t):
    """Rotation of a sketch text in radians. Fusion's ``SketchText.angle`` getter raises, so a box
    text's angle is read from the direction of its first box line (rotation is about the centre)."""
    try:
        return t.angle
    except RuntimeError:
        line = next(iter(getattr(getattr(t, "definition", None), "rectangleLines", ())), None)
        if line is None:
            return 0.0
        a, b = line.startSketchPoint.geometry, line.endSketchPoint.geometry
        return math.atan2(b.y - a.y, b.x - a.x) % (2 * math.pi)


def _text_box(ctx, definition, angle):
    """The un-rotated box [[x, y], [x, y]] of a box text (§ 3.2)."""
    pts = {}
    for line in definition.rectangleLines:
        for p in (line.startSketchPoint, line.endSketchPoint):
            pts[p.entityToken] = p.geometry
    pts = list(pts.values())
    cx, cy = sum(p.x for p in pts) / len(pts), sum(p.y for p in pts) / len(pts)
    ca, sa = math.cos(-angle), math.sin(-angle)
    rot = [(cx + (p.x - cx) * ca - (p.y - cy) * sa, cy + (p.x - cx) * sa + (p.y - cy) * ca)
           for p in pts]
    lo = adsk.core.Point3D.create(min(x for x, _ in rot), min(y for _, y in rot), 0)
    hi = adsk.core.Point3D.create(max(x for x, _ in rot), max(y for _, y in rot), 0)
    return [xy(ctx, lo), xy(ctx, hi)]


def _describe_text(ctx, i, t, cidx):
    definition = getattr(t, "definition", None)
    mode = _TEXT_MODES.get(kind(definition)) if definition is not None else None
    item = {
        "index": i,
        "ref": ctx.ref(t),
        "content": t.text,
        "height": ctx.length_out(t.height),
        "font": t.fontName,
        "angle": ctx.angle_out(text_angle(t)),
        "mode": mode,
        "box": None,
        "horizontal_align": None,
        "vertical_align": None,
        "path": None,
        "above": None,
    }
    if mode is None:
        item["replayable"] = False
        return item
    if mode != "fit_on_path":
        item["horizontal_align"] = _align_name(
            _H_ALIGN, adsk.core.HorizontalAlignments, definition.horizontalAlignment)
    if mode == "box":
        item["vertical_align"] = _align_name(
            _V_ALIGN, adsk.core.VerticalAlignments, definition.verticalAlignment)
        item["box"] = _text_box(ctx, definition, text_angle(t))
    else:
        item["path"] = cidx.get(definition.path.entityToken)
        item["above"] = definition.isAbovePath
        if item["path"] is None:
            item["replayable"] = False
    return item


def describe(ctx, sketch):
    """The full description of one sketch (§ 3.2); also imported by the history tool."""
    points = list(sketch.sketchPoints)
    curves = list(sketch.sketchCurves)
    pidx = {p.entityToken: i for i, p in enumerate(points)}
    cidx = {c.entityToken: i for i, c in enumerate(curves)}
    origin_token = sketch.originPoint.entityToken
    owned = owned_curves(sketch)
    owned_pts = _owned_point_tokens(sketch, owned)
    plane = reference_plane(sketch)
    head = {
        "ref": ctx.ref(sketch),
        "component": sketch.parentComponent.name,
        "plane": None if plane is None else ctx.ref(plane),
        **frame(ctx, sketch),
        "fully_constrained": fully_constrained(sketch),
        "is_parametric": sketch.isParametric,
    }
    return {
        "sketch": head,
        "units": dict(ctx.units),
        "points": [
            {
                "index": i,
                "ref": ctx.ref(p),
                "xy": xy(ctx, p.geometry),
                "fixed": p.isFixed,
                "origin": p.entityToken == origin_token,
                "reference": p.isReference,
            }
            for i, p in enumerate(points)
        ],
        "curves": [_describe_curve(ctx, i, c, pidx, cidx, owned) for i, c in enumerate(curves)],
        "constraints": [
            _describe_constraint(i, c, pidx, cidx, owned, owned_pts)
            for i, c in enumerate(sketch.geometricConstraints)
        ],
        "dimensions": [
            _describe_dimension(ctx, i, d, pidx, cidx) for i, d in enumerate(sketch.sketchDimensions)
        ],
        "texts": [
            _describe_text(ctx, i, t, cidx) for i, t in enumerate(getattr(sketch, "sketchTexts", []))
        ],
        "profiles": [
            {
                "index": i,
                "area": ctx.area_out(p.areaProperties().area),
                "loops": p.profileLoops.count,
            }
            for i, p in enumerate(sketch.profiles)
        ],
    }


@action("sketch", access="read", name="list")
def list_sketches(
    ctx, component: Component | None = None, offset: int = 0, limit: int = 100
) -> dict:
    """List the sketches of one component or of the whole design."""
    design = ctx.require_design()
    if component is not None:
        comps = [component]
    else:
        seen, comps = set(), []
        for c in [design.rootComponent] + list(design.allComponents):
            if c.entityToken not in seen:
                seen.add(c.entityToken)
                comps.append(c)
    items = [(c, s) for c in comps for s in c.sketches]

    def build(cs):
        c, s = cs
        plane = reference_plane(s)
        return {
            "ref": ctx.ref(s),
            "component": c.name,
            "plane": None if plane is None else ctx.ref(plane),
            "profiles": s.profiles.count,
            "curves": s.sketchCurves.count,
            "fully_constrained": fully_constrained(s),
            "visible": s.isVisible,
        }

    return ctx.page(items, offset, limit, build)


@action("sketch", access="read", timeout=60)
def info(
    ctx,
    sketch: Sketch,
    section: Literal[
        "all", "curves", "points", "constraints", "dimensions", "texts", "profiles"
    ] = "all",
    offset: int = 0,
    limit: int = 100,
) -> dict:
    """Describe one sketch: frame, points, curves, constraints, dimensions, texts and profiles.

    section "all" returns everything; any other section is paged with offset and limit."""
    ctx.require_design()
    d = describe(ctx, sketch)
    if section == "all":
        try:
            results.check_result(d)
        except ActionError as exc:
            if exc.code != RESULT_TOO_LARGE:
                raise
            raise ActionError(
                RESULT_TOO_LARGE,
                exc.message,
                hints=['pass section ("curves", "points", "constraints", ...) with limit and offset'],
            ) from None
        return d
    return {
        "sketch": d["sketch"],
        "units": d["units"],
        section: ctx.page(d[section], offset, limit),
    }


# --- sketch lifecycle (design § 4) ---------------------------------------------------------------


def create_sketch(ctx, plane, component, name):
    """Add a sketch on ``plane`` in ``component`` (§ 4.1); shared with ``replay``."""
    comp = ctx.component(component)
    if isinstance(plane, adsk.fusion.BRepFace):
        if plane.geometry.surfaceType != adsk.core.SurfaceTypes.PlaneSurfaceType:
            ctx.fail(INVALID_PARAMS, "plane: face is not planar")
    occurrence = getattr(plane, "assemblyContext", None)
    if occurrence is not None and comp.entityToken == ctx.design.rootComponent.entityToken:
        sk = comp.sketches.add(plane, occurrence)
    else:
        sk = comp.sketches.add(plane)
    if name is not None:
        sk.name = name
    return sk


@action("sketch", access="write")
def create(
    ctx, plane: Plane, component: Component | None = None, name: str | None = None
) -> dict:
    """Create a sketch on an origin plane, construction plane or planar face."""
    ctx.require_design()
    sk = create_sketch(ctx, plane, component, name)
    ref_plane = reference_plane(sk)
    return {
        "sketch": ctx.ref(sk),
        "plane": None if ref_plane is None else ctx.ref(ref_plane),
        **frame(ctx, sk),
    }


@action("sketch", access="destructive")
def delete(ctx, sketch: Sketch) -> dict:
    """Delete a sketch; features that use its profiles become unhealthy."""
    ctx.require_design()
    r = ctx.ref(sketch)
    if not sketch.deleteMe():
        ctx.fail(FUSION_ERROR, "deleteMe returned false")
    return {"deleted": r}


# --- drawing actions (design § 5) ----------------------------------------------------------------

_EPS = 1e-7  # cm


@contextlib.contextmanager
def _deferred(sketch):
    """Defer sketch recompute while several curves are created (§ 2.6)."""
    sketch.isComputeDeferred = True
    try:
        yield
    finally:
        sketch.isComputeDeferred = False


def _mark(curves, construction):
    if construction:
        for c in curves:
            c.isConstruction = True


def _rad(v):
    """Radians of an Angle value; a plain number is a documented default in degrees."""
    return v.rad if isinstance(v, Value) else math.radians(v)


def _same(a, b):
    return abs(a.x - b.x) < _EPS and abs(a.y - b.y) < _EPS


def _check_mode(ctx, mode, params, required, allowed=()):
    """Mode/parameter agreement: ``required`` must be given, anything outside ``allowed`` must not."""
    missing = [n for n in required if params[n] is None]
    if missing:
        ctx.fail(INVALID_PARAMS, "mode %s needs %s" % (mode, ", ".join(missing)))
    ok = set(required) | set(allowed)
    unused = [n for n, v in params.items() if v is not None and n not in ok]
    if unused:
        ctx.fail(INVALID_PARAMS, "mode %s does not use %s" % (mode, ", ".join(unused)))


@action("sketch", access="write")
def rectangle(
    ctx,
    sketch: Sketch,
    mode: Literal["two_point", "center", "three_point"],
    p1: Point2,
    p2: Point2,
    p3: Point2 | None = None,
    construction: bool = False,
) -> dict:
    """Draw a rectangle from two corners, a center and a corner, or three points."""
    ctx.require_design()
    _check_mode(ctx, mode, {"p3": p3}, ["p3"] if mode == "three_point" else [])
    if mode == "three_point":
        if _same(p1, p2):
            ctx.fail(INVALID_PARAMS, "p1 and p2 must differ")
    elif abs(p1.x - p2.x) < _EPS or abs(p1.y - p2.y) < _EPS:
        ctx.fail(INVALID_PARAMS, "the rectangle has zero width or height")
    lines = sketch.sketchCurves.sketchLines
    with _deferred(sketch):
        if mode == "two_point":
            made = lines.addTwoPointRectangle(p1, p2)
        elif mode == "center":
            made = lines.addCenterPointRectangle(p1, p2)
        else:
            made = lines.addThreePointRectangle(p1, p2, p3)
        curves = list(made)
        _mark(curves, construction)
    return draw_result(ctx, sketch, curves)


@action("sketch", access="write")
def circle(
    ctx,
    sketch: Sketch,
    mode: Literal["center_radius", "two_point", "three_point"],
    center: Point2 | None = None,
    radius: Length | None = None,
    diameter: Length | None = None,
    p1: Point2 | None = None,
    p2: Point2 | None = None,
    p3: Point2 | None = None,
    construction: bool = False,
) -> dict:
    """Draw a circle by center and radius or diameter, two diameter ends, or three points."""
    ctx.require_design()
    given = {"center": center, "radius": radius, "diameter": diameter, "p1": p1, "p2": p2, "p3": p3}
    circles = sketch.sketchCurves.sketchCircles
    if mode == "center_radius":
        _check_mode(ctx, mode, given, ["center"], ["radius", "diameter"])
        if (radius is None) == (diameter is None):
            ctx.fail(INVALID_PARAMS, "mode center_radius needs exactly one of radius, diameter")
        r = radius.cm if radius is not None else diameter.cm / 2
        if r <= 0:
            ctx.fail(INVALID_PARAMS, "radius must be greater than 0")
        make = lambda: circles.addByCenterRadius(center, r)  # noqa: E731
    elif mode == "two_point":
        _check_mode(ctx, mode, given, ["p1", "p2"])
        if _same(p1, p2):
            ctx.fail(INVALID_PARAMS, "p1 and p2 must differ")
        make = lambda: circles.addByTwoPoints(p1, p2)  # noqa: E731
    else:
        _check_mode(ctx, mode, given, ["p1", "p2", "p3"])
        make = lambda: circles.addByThreePoints(p1, p2, p3)  # noqa: E731
    c = make()
    _mark([c], construction)
    return draw_result(ctx, sketch, [c])


def _polyline(sketch, pts, closed):
    """Lines through ``pts`` sharing end points (§ 5.3); ``closed`` adds the closing segment."""
    lines = sketch.sketchCurves.sketchLines
    made = [lines.addByTwoPoints(pts[0], pts[1])]
    for p in pts[2:]:
        made.append(lines.addByTwoPoints(made[-1].endSketchPoint, p))
    if closed:
        made.append(lines.addByTwoPoints(made[-1].endSketchPoint, made[0].startSketchPoint))
    return made


@action("sketch", access="write")
def line(
    ctx,
    sketch: Sketch,
    points: Annotated[list[Point2], Field(min_length=2)],
    closed: bool = False,
    construction: bool = False,
) -> dict:
    """Draw a chain of connected lines through the points, optionally closed."""
    ctx.require_design()
    if closed and len(points) < 3:
        ctx.fail(INVALID_PARAMS, "closed needs at least 3 points")
    for i in range(1, len(points)):
        if _same(points[i], points[i - 1]):
            ctx.fail(INVALID_PARAMS, "points[%d] equals points[%d]" % (i, i - 1))
    with _deferred(sketch):
        made = _polyline(sketch, points, closed)
        _mark(made, construction)
    return draw_result(ctx, sketch, made)


@action("sketch", access="write")
def polygon(
    ctx,
    sketch: Sketch,
    center: Point2,
    sides: Annotated[int, Field(ge=3, le=64)],
    radius: Length,
    mode: Literal["inscribed", "circumscribed"] = "inscribed",
    rotation: Angle = 0,
    construction: bool = False,
) -> dict:
    """Draw a regular polygon with equal sides (radius to the vertices or to the edges)."""
    ctx.require_design()
    if radius.cm <= 0:
        ctx.fail(INVALID_PARAMS, "radius must be greater than 0")
    rot = _rad(rotation)
    if mode == "inscribed":
        big_r, a0 = radius.cm, rot
    else:
        big_r, a0 = radius.cm / math.cos(math.pi / sides), rot + math.pi / sides
    pts = [
        adsk.core.Point3D.create(
            center.x + big_r * math.cos(a0 + 2 * math.pi * k / sides),
            center.y + big_r * math.sin(a0 + 2 * math.pi * k / sides),
            0,
        )
        for k in range(sides)
    ]
    with _deferred(sketch):
        made = _polyline(sketch, pts, True)
        for k in range(1, sides):
            sketch.geometricConstraints.addEqual(made[0], made[k])
        _mark(made, construction)
    return draw_result(ctx, sketch, made)


@action("sketch", access="write")
def arc(
    ctx,
    sketch: Sketch,
    mode: Literal["three_point", "center_start_sweep"],
    start: Point2,
    point: Point2 | None = None,
    end: Point2 | None = None,
    center: Point2 | None = None,
    sweep: Angle | None = None,
    construction: bool = False,
) -> dict:
    """Draw an arc through three points, or from a center, a start point and a sweep angle."""
    ctx.require_design()
    arcs = sketch.sketchCurves.sketchArcs
    given = {"point": point, "end": end, "center": center, "sweep": sweep}
    if mode == "three_point":
        _check_mode(ctx, mode, given, ["point", "end"])
        a = arcs.addByThreePoints(start, point, end)
    else:
        _check_mode(ctx, mode, given, ["center", "sweep"])
        s = _rad(sweep)
        if abs(s) < 1e-9 or abs(s) >= 2 * math.pi - 1e-9:
            ctx.fail(INVALID_PARAMS, "sweep must be non-zero and less than 360 deg in size")
        a = arcs.addByCenterStartSweep(center, start, s)
    _mark([a], construction)
    return draw_result(ctx, sketch, [a])


@action("sketch", access="write")
def spline(
    ctx,
    sketch: Sketch,
    mode: Literal["fit", "control"],
    points: Annotated[list[Point2], Field(min_length=2)],
    closed: bool = False,
    degree: Literal[3, 5] = 3,
    construction: bool = False,
) -> dict:
    """Draw a spline through fit points or from control points."""
    ctx.require_design()
    if mode == "fit":
        if closed and len(points) < 3:
            ctx.fail(INVALID_PARAMS, "a closed fit spline needs at least 3 points")
    else:
        if len(points) < degree + 1:
            ctx.fail(INVALID_PARAMS, "a degree %d control-point spline needs at least %d points"
                     % (degree, degree + 1))
        if not hasattr(sketch.sketchCurves, "sketchControlPointSplines"):
            ctx.fail(UNSUPPORTED, "control-point splines are not supported by this Fusion build")
    coll = adsk.core.ObjectCollection.create()
    for p in points:
        coll.add(p)
    if mode == "fit":
        s = sketch.sketchCurves.sketchFittedSplines.add(coll)
    else:
        s = sketch.sketchCurves.sketchControlPointSplines.add(list(points), degree)
    if closed:
        try:
            s.isClosed = True
        except Exception:
            if mode == "fit":
                raise
            ctx.fail(
                UNSUPPORTED, "closed control-point splines are not supported by this Fusion build"
            )
    _mark([s], construction)
    return draw_result(ctx, sketch, [s])


@action("sketch", access="write")
def fillet(
    ctx,
    sketch: Sketch,
    curve_one: SketchCurve,
    curve_two: SketchCurve,
    radius: Length,
    point_one: Point2 | None = None,
    point_two: Point2 | None = None,
) -> dict:
    """Add a tangent arc of the given radius between two curves, trimming both."""
    ctx.require_design()
    check_owned(ctx, sketch, [curve_one, curve_two], "curves")
    if radius.cm <= 0:
        ctx.fail(INVALID_PARAMS, "radius must be positive")
    if curve_one.entityToken == curve_two.entityToken:
        ctx.fail(INVALID_PARAMS, "curve_one and curve_two must differ")
    if (point_one is None) != (point_two is None):
        ctx.fail(INVALID_PARAMS, "give both point_one and point_two, or neither")
    if point_one is None:
        for c in (curve_one, curve_two):
            if not (hasattr(c, "startSketchPoint") and hasattr(c, "endSketchPoint")):
                ctx.fail(INVALID_PARAMS,
                         "point_one and point_two are required for %s" % type_name(c))
        ends = [(a.geometry, b.geometry)
                for a in (curve_one.startSketchPoint, curve_one.endSketchPoint)
                for b in (curve_two.startSketchPoint, curve_two.endSketchPoint)]
        point_one, point_two = min(
            ends, key=lambda ab: math.hypot(ab[0].x - ab[1].x, ab[0].y - ab[1].y))
    arc_ = sketch.sketchCurves.sketchArcs.addFillet(
        curve_one, point_one, curve_two, point_two, radius.cm)
    return draw_result(ctx, sketch, [arc_])


def _delete_items(ctx, items, indices, label):
    """Validate ``indices`` into ``items``, delete in descending order; the deleted items."""
    if len(set(indices)) != len(indices):
        ctx.fail(INVALID_PARAMS, "duplicate %s index" % label)
    count = len(items)
    for i in indices:
        if not 0 <= i < count:
            ctx.fail(INVALID_PARAMS, "%s index %d is out of range 0..%d" % (label, i, count - 1))
    picked = {i: items[i] for i in indices}
    for i, it in picked.items():
        if getattr(it, "isDeletable", True) is False:
            ctx.fail(INVALID_PARAMS, "%s %d is not deletable" % (label, i))
    return picked


def _run_deletes(ctx, picked, label):
    for i in sorted(picked, reverse=True):
        if not picked[i].deleteMe():
            ctx.fail(FUSION_ERROR, "deleting %s %d returned false" % (label, i))


@action("sketch", access="destructive")
def delete_constraint(
    ctx, sketch: Sketch, indices: Annotated[list[int], Field(min_length=1)]
) -> dict:
    """Delete geometric constraints by their info index."""
    ctx.require_design()
    items = list(sketch.geometricConstraints)
    picked = _delete_items(ctx, items, indices, "constraint")
    deleted = [{"index": i, "type": constraint_type(picked[i])} for i in sorted(picked, reverse=True)]
    _run_deletes(ctx, picked, "constraint")
    return {"sketch": ctx.ref(sketch), "deleted": deleted,
            "fully_constrained": fully_constrained(sketch)}


@action("sketch", access="destructive")
def delete_dimension(
    ctx, sketch: Sketch, indices: Annotated[list[int], Field(min_length=1)]
) -> dict:
    """Delete sketch dimensions by their info index."""
    ctx.require_design()
    items = list(sketch.sketchDimensions)
    picked = _delete_items(ctx, items, indices, "dimension")
    deleted = []
    for i in sorted(picked, reverse=True):
        param = getattr(picked[i], "parameter", None)
        deleted.append({"index": i, "type": dimension_type(picked[i]),
                        "parameter": None if param is None else param.name})
    _run_deletes(ctx, picked, "dimension")
    return {"sketch": ctx.ref(sketch), "deleted": deleted,
            "fully_constrained": fully_constrained(sketch)}


def reflect(p, a, u):
    """Reflect sketch-space point ``p`` across the line through ``a`` with unit direction ``u``."""
    k = (p.x - a.x) * u[0] + (p.y - a.y) * u[1]
    fx, fy = a.x + k * u[0], a.y + k * u[1]
    return adsk.core.Point3D.create(2 * fx - p.x, 2 * fy - p.y, 0)


_MIRRORABLE = ("line", "circle", "arc", "fitted_spline", "control_point_spline", "ellipse")


def _flat_points(curve, t):
    out = []
    for _, made in _key_points(curve, t):
        out.extend(made if isinstance(made, list) else [made])
    return out


@action("sketch", access="write", timeout=60)
def mirror(
    ctx,
    sketch: Sketch,
    curves: Annotated[list[SketchCurve], Field(min_length=1)],
    line: SketchCurve,
    constrain: bool = True,
) -> dict:
    """Create mirrored copies of curves across a sketch line, with symmetry constraints."""
    ctx.require_design()
    check_owned(ctx, sketch, curves, "curves")
    check_owned(ctx, sketch, line, "line")
    if type_name(line) != "line":
        ctx.fail(INVALID_PARAMS, "line must be a sketch line, not %s" % type_name(line))
    if any(c.entityToken == line.entityToken for c in curves):
        ctx.fail(INVALID_PARAMS, "line must not be one of the curves")
    bad = sorted({type_name(c) for c in curves if type_name(c) not in _MIRRORABLE})
    if bad:
        ctx.fail(INVALID_PARAMS, "cannot mirror %s" % ", ".join(bad))
    a = line.startSketchPoint.geometry
    e = line.endSketchPoint.geometry
    n = math.hypot(e.x - a.x, e.y - a.y)
    u = ((e.x - a.x) / n, (e.y - a.y) / n)

    def R(p):
        return reflect(_coords(p), a, u)

    nmap = {}  # original point token -> (original, replacement SketchPoint)
    for c in curves:
        for p in _flat_points(c, type_name(c)):
            g = p.geometry
            r = R(g)
            if math.hypot(r.x - g.x, r.y - g.y) < _EPS:
                nmap[p.entityToken] = (p, p)

    def Q(p):
        got = nmap.get(p.entityToken)
        return got[1] if got else R(p)

    made = []
    with _deferred(sketch):
        for c in curves:
            t = type_name(c)
            if t == "line":
                new = build_curve(sketch, t, {"start": Q(c.startSketchPoint), "end": Q(c.endSketchPoint)}, {})
            elif t == "circle":
                new = build_curve(sketch, t, {"center": Q(c.centerSketchPoint)}, {"radius": c.radius})
            elif t == "arc":
                g = c.geometry
                new = build_curve(
                    sketch, t,
                    {"center": Q(c.centerSketchPoint), "start": Q(c.endSketchPoint),
                     "end": Q(c.startSketchPoint)},
                    {"radius": c.radius, "sweep": g.endAngle - g.startAngle})
            elif t == "fitted_spline":
                new = build_curve(sketch, t, {"fit": [Q(p) for p in c.fitPoints]},
                                  {"closed": c.isClosed})
            elif t == "control_point_spline":
                new = build_curve(sketch, t, {"control": [R(p) for p in c.controlPoints]},
                                  {"degree": int(c.degree), "closed": getattr(c, "isClosed", False)})
            else:
                g = c.geometry
                ctr = c.centerSketchPoint.geometry
                ux, uy = g.majorAxis.x, g.majorAxis.y
                P = adsk.core.Point3D.create
                new = add_ellipse(
                    sketch, Q(c.centerSketchPoint),
                    R(P(ctr.x + ux * c.majorAxisRadius, ctr.y + uy * c.majorAxisRadius, 0)),
                    R(P(ctr.x - uy * c.minorAxisRadius, ctr.y + ux * c.minorAxisRadius, 0)))
            new.isConstruction = c.isConstruction
            made.append(new)
            fresh = _flat_points(new, t)
            for p in _flat_points(c, t):
                if p.entityToken in nmap:
                    continue
                want = R(p.geometry)
                nmap[p.entityToken] = (p, min(
                    fresh, key=lambda q: math.hypot(q.geometry.x - want.x, q.geometry.y - want.y)))
        added = 0
        if constrain:
            gc = sketch.geometricConstraints
            for orig, new_p in nmap.values():
                if new_p is not orig:
                    gc.addSymmetry(orig, new_p, line)
                    added += 1
            for c, new in zip(curves, made):
                if type_name(c) == "circle":
                    gc.addEqual(c, new)
                    added += 1
    result = draw_result(ctx, sketch, made)
    result["constraints"] = added
    return result


_MAX_INSTANCES = 500


@action("sketch", access="write", timeout=120)
def pattern(
    ctx,
    sketch: Sketch,
    curves: Annotated[list[SketchCurve], Field(min_length=1)],
    type: Literal["rectangular", "circular"],
    count_x: Annotated[int, Field(ge=1)] = 1,
    spacing_x: Length | None = None,
    count_y: Annotated[int, Field(ge=1)] = 1,
    spacing_y: Length | None = None,
    direction: Angle = 0,
    center: Point2 | None = None,
    count: Annotated[int, Field(ge=2)] | None = None,
    total_angle: Angle = 360,
) -> dict:
    """Copy curves into a rectangular or circular pattern (independent copies)."""
    ctx.require_design()
    check_owned(ctx, sketch, curves, "curves")
    mats = []
    if type == "rectangular":
        if center is not None or count is not None:
            ctx.fail(INVALID_PARAMS, "rectangular does not use center or count")
        if count_x * count_y < 2:
            ctx.fail(INVALID_PARAMS, "count_x * count_y must be at least 2")
        if count_x > 1 and spacing_x is None:
            ctx.fail(INVALID_PARAMS, "spacing_x is required when count_x > 1")
        if count_y > 1 and spacing_y is None:
            ctx.fail(INVALID_PARAMS, "spacing_y is required when count_y > 1")
        total = count_x * count_y - 1
    else:
        if center is None or count is None:
            ctx.fail(INVALID_PARAMS, "circular needs center and count")
        if spacing_x is not None or spacing_y is not None or count_x != 1 or count_y != 1:
            ctx.fail(INVALID_PARAMS, "circular does not use spacing_x, spacing_y, count_x or count_y")
        span = _rad(total_angle)
        if not 0 < abs(span) <= 2 * math.pi + 1e-9:
            ctx.fail(INVALID_PARAMS, "total_angle must be non-zero and at most 360 deg")
        total = count - 1
    if total > _MAX_INSTANCES:
        ctx.fail(INVALID_PARAMS, "at most %d instances (got %d)" % (_MAX_INSTANCES, total))
    if not hasattr(sketch, "copy"):
        ctx.fail(UNSUPPORTED, "sketch pattern needs Sketch.copy")
    if type == "rectangular":
        d = _rad(direction)
        d1, d2 = (math.cos(d), math.sin(d)), (-math.sin(d), math.cos(d))
        sx = spacing_x.cm if spacing_x is not None else 0.0
        sy = spacing_y.cm if spacing_y is not None else 0.0
        for i in range(count_x):
            for j in range(count_y):
                if (i, j) != (0, 0):
                    m = adsk.core.Matrix3D.create()
                    m.translation = adsk.core.Vector3D.create(
                        i * sx * d1[0] + j * sy * d2[0], i * sx * d1[1] + j * sy * d2[1], 0)
                    mats.append(m)
    else:
        step = span / count if abs(abs(span) - 2 * math.pi) < 1e-9 else span / (count - 1)
        for k in range(1, count):
            m = adsk.core.Matrix3D.create()
            m.setToRotation(k * step, adsk.core.Vector3D.create(0, 0, 1), center)
            mats.append(m)
    coll = _collection(curves)
    before = sketch.sketchCurves.count
    for m in mats:
        sketch.copy(coll, m)
    return {
        "sketch": ctx.ref(sketch),
        "instances": len(mats),
        "curves": sketch.sketchCurves.count - before,
        "profiles": sketch.profiles.count,
    }


def add_ellipse(sketch, center, major_point, point):
    """``sketchEllipses.add`` (§ 5.7); shared with ``replay`` and ``reflect``."""
    return sketch.sketchCurves.sketchEllipses.add(center, major_point, point)


@action("sketch", access="write")
def ellipse(
    ctx,
    sketch: Sketch,
    center: Point2,
    major_point: Point2,
    point: Point2,
    construction: bool = False,
) -> dict:
    """Draw an ellipse from its center, the end of the major axis and a point on the ellipse."""
    ctx.require_design()
    if _same(center, major_point):
        ctx.fail(INVALID_PARAMS, "major_point must differ from center")
    dx, dy = major_point.x - center.x, major_point.y - center.y
    cross = abs(dx * (point.y - center.y) - dy * (point.x - center.x)) / math.hypot(dx, dy)
    if cross < _EPS:
        ctx.fail(INVALID_PARAMS, "point must not lie on the major axis line")
    e = add_ellipse(sketch, center, major_point, point)
    _mark([e], construction)
    return draw_result(ctx, sketch, [e])


@action("sketch", access="write")
def slot(
    ctx,
    sketch: Sketch,
    mode: Literal["center_to_center", "overall"],
    p1: Point2,
    p2: Point2,
    width: Length,
    construction: bool = False,
) -> dict:
    """Draw a slot from two lines and two tangent arcs."""
    ctx.require_design()
    r = width.cm / 2
    if r <= 0:
        ctx.fail(INVALID_PARAMS, "width must be positive")
    if _same(p1, p2):
        ctx.fail(INVALID_PARAMS, "p1 and p2 must differ")
    length = math.hypot(p2.x - p1.x, p2.y - p1.y)
    dx, dy = (p2.x - p1.x) / length, (p2.y - p1.y) / length
    nx, ny = -dy, dx
    if mode == "overall":
        if length <= width.cm:
            ctx.fail(INVALID_PARAMS, "the slot's overall length must exceed its width")
        ax, ay, bx, by = p1.x + dx * r, p1.y + dy * r, p2.x - dx * r, p2.y - dy * r
    else:
        ax, ay, bx, by = p1.x, p1.y, p2.x, p2.y

    def Q(x, y):
        return adsk.core.Point3D.create(x, y, 0)

    lines = sketch.sketchCurves.sketchLines
    arcs = sketch.sketchCurves.sketchArcs
    gc = sketch.geometricConstraints
    with _deferred(sketch):
        l1 = lines.addByTwoPoints(Q(ax + nx * r, ay + ny * r), Q(bx + nx * r, by + ny * r))
        l2 = lines.addByTwoPoints(Q(bx - nx * r, by - ny * r), Q(ax - nx * r, ay - ny * r))
        arc_b = arcs.addByThreePoints(l1.endSketchPoint, Q(bx + dx * r, by + dy * r), l2.startSketchPoint)
        arc_a = arcs.addByThreePoints(l2.endSketchPoint, Q(ax - dx * r, ay - dy * r), l1.startSketchPoint)
        for a, b in ((l1, arc_b), (l2, arc_b), (l2, arc_a), (l1, arc_a)):
            gc.addTangent(a, b)
        gc.addEqual(arc_a, arc_b)
        curves = [l1, arc_b, l2, arc_a]
        _mark(curves, construction)
    return draw_result(ctx, sketch, curves)


@action("sketch", access="write")
def point(
    ctx, sketch: Sketch, points: Annotated[list[Point2], Field(min_length=1)]
) -> dict:
    """Add standalone sketch points."""
    ctx.require_design()
    made = [sketch.sketchPoints.add(p) for p in points]
    return {
        "sketch": ctx.ref(sketch),
        "points": [{"ref": ctx.ref(m), "xy": xy(ctx, m.geometry)} for m in made],
        "profiles": sketch.profiles.count,
    }


_H_ALIGN = {"left": "LeftHorizontalAlignment", "center": "CenterHorizontalAlignment",
            "right": "RightHorizontalAlignment"}
_V_ALIGN = {"top": "TopVerticalAlignment", "middle": "MiddleVerticalAlignment",
            "bottom": "BottomVerticalAlignment"}


def add_text(ctx, sketch, spec):
    """Create sketch text (§ 5.10); ``spec`` holds the ``text`` parameters in cm / radians.

    Keys: content, height, mode, position, box_corner, path, above, horizontal_align,
    vertical_align, font, angle. Shared with ``replay``."""
    content, height, mode = spec["content"], spec["height"], spec.get("mode", "box")
    position, corner, path = spec.get("position"), spec.get("box_corner"), spec.get("path")
    angle = spec.get("angle", 0.0)
    above = spec.get("above", True)
    if height <= 0:
        ctx.fail(INVALID_PARAMS, "height must be positive")
    texts = sketch.sketchTexts
    if not hasattr(texts, "createInput2"):
        ctx.fail(UNSUPPORTED, "sketch text needs SketchTexts.createInput2")
    if mode == "box":
        _check_mode(ctx, mode, {"position": position, "box_corner": corner, "path": path},
                    ["position"], ["box_corner"])
    else:
        _check_mode(ctx, mode, {"position": position, "box_corner": corner, "path": path}, ["path"])
        if abs(angle) > 1e-9:
            ctx.fail(INVALID_PARAMS, "mode %s does not use angle" % mode)
        check_owned(ctx, sketch, path, "path")
    inp = texts.createInput2(content, height)
    h_align = getattr(adsk.core.HorizontalAlignments, _H_ALIGN[spec.get("horizontal_align", "left")])
    if mode == "box":
        v_align = getattr(adsk.core.VerticalAlignments, _V_ALIGN[spec.get("vertical_align", "bottom")])
        rows = content.split("\n")
        if corner is None:
            corner = adsk.core.Point3D.create(
                position.x + max(len(r) for r in rows) * height,
                position.y + len(rows) * 1.5 * height, 0)
        if abs(corner.x - position.x) < _EPS or abs(corner.y - position.y) < _EPS:
            ctx.fail(INVALID_PARAMS, "the text box has zero width or height")
        inp.setAsMultiLine(position, corner, h_align, v_align, 0)
        inp.angle = angle % (2 * math.pi)  # Fusion rejects negative angles
    elif mode == "along_path":
        inp.setAsAlongPath(path, above, h_align, 0)
    else:
        if not hasattr(inp, "setAsFitOnPath"):
            ctx.fail(UNSUPPORTED, "fit-on-path text is not supported by this Fusion build")
        inp.setAsFitOnPath(path, above)
    if spec.get("font"):
        inp.fontName = spec["font"]
    return texts.add(inp)


@action("sketch", access="write")
def text(
    ctx,
    sketch: Sketch,
    content: Annotated[str, Field(min_length=1)],
    height: Length,
    mode: Literal["box", "along_path", "fit_on_path"] = "box",
    position: Point2 | None = None,
    box_corner: Point2 | None = None,
    path: SketchCurve | None = None,
    above: bool = True,
    horizontal_align: Literal["left", "center", "right"] = "left",
    vertical_align: Literal["top", "middle", "bottom"] = "bottom",
    font: str | None = None,
    angle: Angle = 0,
) -> dict:
    """Add sketch text in a box or along / fitted to a curve."""
    ctx.require_design()
    rad = _rad(angle)
    t = add_text(ctx, sketch, dict(
        content=content, height=height.cm, mode=mode, position=position, box_corner=box_corner,
        path=path, above=above, horizontal_align=horizontal_align, vertical_align=vertical_align,
        font=font, angle=rad))
    return {
        "sketch": ctx.ref(sketch),
        "text": {
            "ref": ctx.ref(t),
            "content": t.text,
            "height": ctx.length_out(t.height),
            "font": t.fontName,
            "angle": ctx.angle_out(text_angle(t)),
            "mode": mode,
        },
        "profiles": sketch.profiles.count,
    }


# --- constraints and dimensions (design § 6) -----------------------------------------------------

_SPLINES = ("fitted_spline", "control_point_spline", "fixed_spline")
_ROUND = ("circle", "arc", "ellipse", "elliptical_arc")


def _is_line(c):
    return kind(c) == "SketchLine"


def _all(pred, items):
    return all(pred(i) for i in items)


def add_constraint(ctx, sketch, type_, curves, points, symmetry_line=None):
    """The § 6.1 call table: add one constraint (``fix`` returns the count of fixed entities)."""
    gc = sketch.geometricConstraints
    nc, npt = len(curves), len(points)

    def bad(forms):
        ctx.fail(INVALID_PARAMS, "%s needs %s" % (type_, forms))

    if symmetry_line is not None and type_ != "symmetry":
        ctx.fail(INVALID_PARAMS, "symmetry_line is only used by symmetry")
    if type_ == "fix":
        ents = list(curves) + list(points)
        if not ents:
            bad("at least one curve or point")
        for e in ents:
            e.isFixed = True
        return len(ents)
    if type_ == "coincident":
        if (npt, nc) == (2, 0):
            return gc.addCoincident(points[0], points[1])
        if (npt, nc) == (1, 1):
            return gc.addCoincident(points[0], curves[0])
        bad("2 points, or 1 point and 1 curve")
    if type_ in ("horizontal", "vertical"):
        name = type_.capitalize()
        if (nc, npt) == (1, 0) and _is_line(curves[0]):
            return getattr(gc, "add" + name)(curves[0])
        if (nc, npt) == (0, 2):
            return getattr(gc, "add%sPoints" % name)(points[0], points[1])
        bad("1 line, or 2 points")
    if type_ in ("parallel", "perpendicular", "collinear"):
        if npt or nc != 2 or not _all(_is_line, curves):
            bad("2 lines")
        return getattr(gc, "add" + type_.capitalize())(curves[0], curves[1])
    if type_ == "tangent":
        if npt or nc != 2:
            bad("2 curves")
        return gc.addTangent(curves[0], curves[1])
    if type_ == "equal":
        if npt or nc != 2:
            bad("2 curves")
        return gc.addEqual(curves[0], curves[1])
    if type_ == "concentric":
        if npt or nc != 2 or not _all(lambda c: type_name(c) in _ROUND, curves):
            bad("2 circles, arcs or ellipses")
        return gc.addConcentric(curves[0], curves[1])
    if type_ == "midpoint":
        if (npt, nc) != (1, 1):
            bad("1 point and 1 curve")
        return gc.addMidPoint(points[0], curves[0])
    if type_ == "symmetry":
        if symmetry_line is None or not _is_line(symmetry_line):
            bad("symmetry_line (a line)")
        if (npt, nc) == (2, 0):
            return gc.addSymmetry(points[0], points[1], symmetry_line)
        if (npt, nc) == (0, 2):
            return gc.addSymmetry(curves[0], curves[1], symmetry_line)
        bad("2 points or 2 curves, plus symmetry_line")
    if type_ == "smooth":
        if npt or nc != 2 or not any(type_name(c) in _SPLINES for c in curves):
            bad("2 curves, at least one a spline")
        return gc.addSmooth(curves[0], curves[1])
    # curvature
    if npt or nc != 2:
        bad("2 curves")
    if not hasattr(gc, "addCurvature"):
        ctx.fail(UNSUPPORTED, "curvature constraints are not supported by this Fusion build")
    return gc.addCurvature(curves[0], curves[1])


@action("sketch", access="write")
def constraint(
    ctx,
    sketch: Sketch,
    type: Literal[
        "coincident", "horizontal", "vertical", "parallel", "perpendicular", "tangent", "equal",
        "concentric", "midpoint", "collinear", "symmetry", "fix", "smooth", "curvature",
    ],
    curves: list[SketchCurve] = [],
    points: list[SketchPoint] = [],
    symmetry_line: SketchCurve | None = None,
) -> dict:
    """Add a geometric constraint (or fix geometry) between curves and points of a sketch."""
    ctx.require_design()
    check_owned(ctx, sketch, curves, "curves")
    check_owned(ctx, sketch, points, "points")
    if symmetry_line is not None:
        check_owned(ctx, sketch, symmetry_line, "symmetry_line")
    made = add_constraint(ctx, sketch, type, curves, points, symmetry_line)
    if type == "fix":
        detail = {"type": "fix", "fixed": made}
    else:
        detail = {"type": type, "ref": None if made is None else ctx.ref(made)}
    return {
        "sketch": ctx.ref(sketch),
        "constraint": detail,
        "fully_constrained": fully_constrained(sketch),
    }


@action("sketch", access="write", timeout=120)
def auto_constrain(
    ctx, sketch: Sketch, result: Literal["thorough", "fast", "adjust"] = "thorough"
) -> dict:
    """Let Fusion add constraints and dimensions automatically (thorough, fast or adjust)."""
    ctx.require_design()
    if not (hasattr(sketch, "createAutoConstrainInput") and hasattr(sketch, "autoConstrain")):
        ctx.fail(UNSUPPORTED, "AutoConstrain needs a Fusion build from 2026 or later")
    types_ = adsk.fusion.AutoConstrainResultTypes
    inp = sketch.createAutoConstrainInput()
    inp.resultOption = {
        "thorough": types_.Option1AutoConstrainResultType,
        "fast": types_.Option2AutoConstrainResultType,
        "adjust": types_.Option3AutoConstrainResultType,
    }[result]
    r = sketch.autoConstrain(inp)
    if r is None:
        ctx.fail(FUSION_ERROR, "sketch not eligible for adjust; use thorough or fast")
    return {
        "sketch": ctx.ref(sketch),
        "constraints_added": len(r.addedConstraints),
        "dimensions_added": len(r.addedDimensions),
        "moved": len(r.movedGeometry),
        "fully_constrained": r.isFullyConstrained,
    }


def _unit2(dx, dy):
    n = math.hypot(dx, dy)
    return dx / n, dy / n


def _line_ends(line):
    a, b = line.startSketchPoint.geometry, line.endSketchPoint.geometry
    return a, b


def _line_intersection(l1, l2):
    """Intersection of the infinite lines, or None when parallel."""
    a, b = _line_ends(l1)
    c, d = _line_ends(l2)
    r, s = (b.x - a.x, b.y - a.y), (d.x - c.x, d.y - c.y)
    det = r[0] * s[1] - r[1] * s[0]
    if abs(det) < 1e-12 * max(1.0, math.hypot(*r) * math.hypot(*s)):
        return None
    t = ((c.x - a.x) * s[1] - (c.y - a.y) * s[0]) / det
    return a.x + t * r[0], a.y + t * r[1]


def _angular_text_point(l1, l2):
    """§ 6.3: 5 mm from the intersection along the bisector of the two rays to the far ends."""
    x = _line_intersection(l1, l2)
    if x is None:
        return None
    units_ = []
    for ln in (l1, l2):
        a, b = _line_ends(ln)
        far = a if math.hypot(a.x - x[0], a.y - x[1]) > math.hypot(b.x - x[0], b.y - x[1]) else b
        units_.append(_unit2(far.x - x[0], far.y - x[1]))
    ux, uy = _unit2(units_[0][0] + units_[1][0], units_[0][1] + units_[1][1])
    return adsk.core.Point3D.create(x[0] + 0.5 * ux, x[1] + 0.5 * uy, 0)


_DIM_ORIENTATION = {
    "distance": "AlignedDimensionOrientation",
    "horizontal": "HorizontalDimensionOrientation",
    "vertical": "VerticalDimensionOrientation",
}


def make_dimension(ctx, sketch, type_, curves, points, driving, text_point=None):
    """The § 6.3 call table with default text points; returns the new dimension."""
    nc, npt = len(curves), len(points)
    sd = sketch.sketchDimensions

    def bad(forms):
        ctx.fail(INVALID_PARAMS, "%s needs %s" % (type_, forms))

    def pt(x, y):
        return adsk.core.Point3D.create(x, y, 0)

    t = text_point
    if type_ in _DIM_ORIENTATION:
        if (npt, nc) == (2, 0):
            a, b = points
        elif (npt, nc) == (0, 1) and _is_line(curves[0]):
            a, b = curves[0].startSketchPoint, curves[0].endSketchPoint
        else:
            bad("2 points, or 1 line")
        if t is None:
            ga, gb = a.geometry, b.geometry
            mx, my = (ga.x + gb.x) / 2, (ga.y + gb.y) / 2
            if type_ == "horizontal":
                t = pt(mx, my + 0.5)
            elif type_ == "vertical":
                t = pt(mx + 0.5, my)
            else:
                dx, dy = gb.x - ga.x, gb.y - ga.y
                if math.hypot(dx, dy) < _EPS:
                    t = pt(mx, my + 0.5)
                else:
                    nx, ny = _unit2(-dy, dx)
                    t = pt(mx + 0.5 * nx, my + 0.5 * ny)
        orientation = getattr(adsk.fusion.DimensionOrientations, _DIM_ORIENTATION[type_])
        return sd.addDistanceDimension(a, b, orientation, t, driving)
    if type_ in ("radial", "diameter"):
        if npt or nc != 1 or type_name(curves[0]) not in ("circle", "arc"):
            bad("1 circle or arc")
        c = curves[0]
        if t is None:
            g, r = c.centerSketchPoint.geometry, 1.5 * c.radius * math.sqrt(0.5)
            t = pt(g.x + r, g.y + r)
        add = sd.addRadialDimension if type_ == "radial" else sd.addDiameterDimension
        return add(c, t, driving)
    if type_ == "angular":
        if npt or nc != 2 or not _all(_is_line, curves):
            bad("2 lines")
        if t is None:
            t = _angular_text_point(curves[0], curves[1])
            if t is None:
                ctx.fail(INVALID_PARAMS, "the lines are parallel; an angular dimension needs "
                         "intersecting lines")
        return sd.addAngularDimension(curves[0], curves[1], t, driving)
    # offset
    if npt or nc != 2 or not _all(_is_line, curves):
        bad("2 parallel lines")
    l1, l2 = curves
    (a1, b1), (a2, b2) = _line_ends(l1), _line_ends(l2)
    d1 = _unit2(b1.x - a1.x, b1.y - a1.y)
    d2 = _unit2(b2.x - a2.x, b2.y - a2.y)
    if abs(d1[0] * d2[1] - d1[1] * d2[0]) > 1e-9:
        bad("2 parallel lines")
    if t is None:
        m1, m2 = ((a1.x + b1.x) / 2, (a1.y + b1.y) / 2), ((a2.x + b2.x) / 2, (a2.y + b2.y) / 2)
        t = pt((m1[0] + m2[0]) / 2 + 0.5 * d1[0], (m1[1] + m2[1]) / 2 + 0.5 * d1[1])
    return sd.addOffsetDimension(l1, l2, t, driving)


@action("sketch", access="write")
def dimension(
    ctx,
    sketch: Sketch,
    type: Literal[
        "distance", "horizontal", "vertical", "radial", "diameter", "angular", "offset"
    ],
    curves: list[SketchCurve] = [],
    points: list[SketchPoint] = [],
    value: Length | None = None,
    angle: Angle | None = None,
    driven: bool = False,
    text_point: Point2 | None = None,
) -> dict:
    """Add a sketch dimension; give value (or angle for angular) to drive it, or driven=true."""
    ctx.require_design()
    check_owned(ctx, sketch, curves, "curves")
    check_owned(ctx, sketch, points, "points")
    if type == "angular" and value is not None:
        ctx.fail(INVALID_PARAMS, "angular dimensions take angle, not value")
    if type != "angular" and angle is not None:
        ctx.fail(INVALID_PARAMS, "%s dimensions take value, not angle" % type)
    given = value if value is not None else angle
    if driven and given is not None:
        ctx.fail(INVALID_PARAMS, "driven dimensions take no value")
    dim = make_dimension(ctx, sketch, type, curves, points, not driven, text_point)
    if given is not None:
        dim.parameter.expression = given.expr
    return {
        "sketch": ctx.ref(sketch),
        "dimension": {"type": type, **_dimension_values(ctx, type, dim)},
        "fully_constrained": fully_constrained(sketch),
    }


# --- editing and projection (design § 7) ---------------------------------------------------------


def _collection(items):
    coll = adsk.core.ObjectCollection.create()
    for i in items:
        coll.add(i)
    return coll


@action("sketch", access="write")
def offset(
    ctx,
    sketch: Sketch,
    curves: Annotated[list[SketchCurve], Field(min_length=1)],
    distance: Length,
    side_point: Point2,
    construction: bool = False,
) -> dict:
    """Offset a connected chain of curves by a distance towards the side of side_point."""
    ctx.require_design()
    check_owned(ctx, sketch, curves, "curves")
    if distance.cm <= 0:
        ctx.fail(INVALID_PARAMS, "distance must be greater than 0")
    made = list(sketch.offset(_collection(curves), side_point, distance.cm))
    _mark(made, construction)
    return draw_result(ctx, sketch, made)


@action("sketch", access="write")
def trim(ctx, sketch: Sketch, curve: SketchCurve, point: Point2) -> dict:
    """Trim the piece of a curve nearest to point, between its intersections."""
    ctx.require_design()
    check_owned(ctx, sketch, curve, "curve")
    res = curve.trim(point, True)
    return draw_result(ctx, sketch, list(res) if res else [])


@action("sketch", access="write")
def extend(ctx, sketch: Sketch, curve: SketchCurve, point: Point2) -> dict:
    """Extend the end of a curve nearest to point until it meets other geometry."""
    ctx.require_design()
    check_owned(ctx, sketch, curve, "curve")
    res = curve.extend(point, True)
    return draw_result(ctx, sketch, list(res) if res else [])


@action("sketch", access="write", timeout=60)
def project(
    ctx,
    sketch: Sketch,
    mode: Literal["project", "cut_edges", "intersect"] = "project",
    edges: list[Edge] = [],
    faces: list[Face] = [],
    bodies: list[Body] = [],
    curves: list[SketchCurve] = [],
    points: list[SketchPoint] = [],
    linked: bool = True,
) -> dict:
    """Project edges, faces, bodies or other sketches' geometry onto the sketch, or intersect them."""
    ctx.require_design()
    entities = list(edges) + list(faces) + list(bodies) + list(curves) + list(points)
    if not entities:
        ctx.fail(INVALID_PARAMS, "give at least one of edges, faces, bodies, curves, points")
    for e in list(curves) + list(points):
        if e.parentSketch.entityToken == sketch.entityToken:
            ctx.fail(INVALID_PARAMS, "%s is in sketch %s itself" % (e.entityToken, sketch.name))
    got = []
    if mode == "project":
        if linked:
            for e in entities:
                try:
                    got.extend(sketch.project(e))
                except Exception as exc:
                    if e not in bodies:
                        raise
                    ctx.fail(FUSION_ERROR, "projecting body %s failed: %s" % (e.name, exc))
        else:
            if not hasattr(sketch, "project2"):
                ctx.fail(UNSUPPORTED, "unlinked projection needs Sketch.project2")
            got.extend(sketch.project2(entities, False))
    elif mode == "cut_edges":
        if edges or faces or curves or points:
            ctx.fail(INVALID_PARAMS, "cut_edges projects bodies only")
        for b in bodies:
            got.extend(sketch.projectCutEdges(b))
    else:
        got.extend(sketch.intersectWithSketchPlane(entities))
    return {
        "sketch": ctx.ref(sketch),
        "curves": [curve_summary(ctx, c) for c in got if isinstance(c, adsk.fusion.SketchCurve)],
        "points": [ctx.ref(p) for p in got if isinstance(p, adsk.fusion.SketchPoint)],
        "profiles": sketch.profiles.count,
    }


@action("sketch", access="write")
def set_construction(
    ctx,
    sketch: Sketch,
    curves: Annotated[list[SketchCurve], Field(min_length=1)],
    value: bool = True,
) -> dict:
    """Turn curves into construction geometry, or back into normal geometry."""
    ctx.require_design()
    check_owned(ctx, sketch, curves, "curves")
    for c in curves:
        try:
            c.isConstruction = value
        except Exception as exc:
            ctx.fail(FUSION_ERROR, "cannot set construction on %s: %s" % (c.entityToken, exc))
    return draw_result(ctx, sketch, curves)


# --- replay (design § 8) -------------------------------------------------------------------------


class _Item(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _DUnits(_Item):
    length: str | None = None
    angle: str | None = None


class _DPoint(_Item):
    index: int
    xy: list[float]
    fixed: bool = False
    origin: bool = False
    reference: bool = False


class _DCurve(_Item):
    index: int
    type: str
    construction: bool = False
    fixed: bool = False
    reference: bool = False
    replayable: bool = True
    owner: dict[str, Any] | None = None
    start: int | None = None
    end: int | None = None
    center: int | None = None
    radius: float | None = None
    sweep: float | None = None
    fit_points: list[int] = []
    control_points: list[int] = []
    degree: int | None = None
    closed: bool = False
    major_radius: float | None = None
    minor_radius: float | None = None
    major_axis: list[float] | None = None


class _DText(_Item):
    index: int
    content: str
    height: float
    font: str | None = None
    angle: float = 0.0
    mode: str | None = None
    box: list[list[float]] | None = None
    horizontal_align: str | None = None
    vertical_align: str | None = None
    path: int | None = None
    above: bool | None = None
    replayable: bool = True


class _DConstraint(_Item):
    index: int
    type: str
    points: list[int] = []
    curves: list[int] = []
    symmetry_line: int | None = None
    replayable: bool = True
    owned: bool = False


class _DDimension(_Item):
    index: int
    type: str
    points: list[int] = []
    curves: list[int] = []
    value: float | None = None
    driving: bool = True
    text_point: list[float] | None = None
    replayable: bool = True


class SketchDescription(_Item):
    """The ``info`` description of a sketch (§ 8.1); unknown keys are ignored."""

    units: _DUnits | None = None
    points: list[_DPoint] = []
    curves: list[_DCurve] = []
    constraints: list[_DConstraint] = []
    dimensions: list[_DDimension] = []
    texts: list[_DText] = []


def _num(v):
    text = ("%.12f" % v).rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


class _Convert:
    """Description numbers (in the description's units) to internal cm / rad (§ 8.1)."""

    def __init__(self, ctx, du):
        defaults = _units.input_defaults()
        self.length = (du.length if du and du.length else None) or defaults["length"]
        self.angle = (du.angle if du and du.angle else None) or defaults["angle"]
        self._um = ctx.design.unitsManager

    def L(self, v):
        return self._um.evaluateExpression("%s %s" % (_num(v), self.length), "cm")

    def A(self, v):
        return self._um.evaluateExpression("%s %s" % (_num(v), self.angle), "rad")

    def expr(self, t, v):
        return "%s %s" % (_num(v), self.angle if t == "angular" else self.length)


def _coords(p):
    return p.geometry if isinstance(p, adsk.fusion.SketchPoint) else p


def build_curve(sketch, type_, pts, data):
    """Create one curve of ``type_`` (§ 8.2 step 3, also used by ``mirror``).

    ``pts``: line start/end, circle center, arc center/start/end (counter-clockwise from start),
    splines ``fit`` / ``control`` lists; each a Point3D or an existing SketchPoint. ``data``:
    ``radius``, ``sweep`` (rad), ``degree``, ``closed``."""
    curves = sketch.sketchCurves
    if type_ == "line":
        return curves.sketchLines.addByTwoPoints(pts["start"], pts["end"])
    if type_ == "circle":
        return curves.sketchCircles.addByCenterRadius(pts["center"], data["radius"])
    if type_ == "arc":
        c, s = _coords(pts["center"]), _coords(pts["start"])
        a = math.atan2(s.y - c.y, s.x - c.x) + data["sweep"] / 2
        mid = adsk.core.Point3D.create(
            c.x + data["radius"] * math.cos(a), c.y + data["radius"] * math.sin(a), 0
        )
        return curves.sketchArcs.addByThreePoints(pts["start"], mid, pts["end"])
    if type_ == "fitted_spline":
        sp = curves.sketchFittedSplines.add(_collection(pts["fit"]))
    else:
        sp = curves.sketchControlPointSplines.add(list(pts["control"]), data["degree"])
    if data.get("closed"):
        sp.isClosed = True
    return sp


def _key_points(curve, t):
    """[(key, SketchPoint)] of a curve's key points in § 3.2 description order."""
    if t == "line":
        return [("start", curve.startSketchPoint), ("end", curve.endSketchPoint)]
    if t in ("circle", "ellipse"):
        return [("center", curve.centerSketchPoint)]
    if t == "arc":
        return [("center", curve.centerSketchPoint), ("start", curve.startSketchPoint),
                ("end", curve.endSketchPoint)]
    if t == "fitted_spline":
        return [("fit_points", list(curve.fitPoints))]
    return [("control_points", list(curve.controlPoints))]


def _map_points(dc, curve, pmap):
    """Map the description point indices of ``dc`` not yet known to the new curve's points."""
    for key, made in _key_points(curve, dc.type):
        wanted = getattr(dc, key)
        if isinstance(made, list):
            pairs = zip(wanted, made)
        else:
            pairs = [(wanted, made)] if wanted is not None else []
        for idx, p in pairs:
            pmap.setdefault(idx, p)


_BUILDABLE = ("line", "circle", "arc", "fitted_spline", "control_point_spline", "ellipse")


def _description(ctx, raw):
    try:
        return SketchDescription.model_validate(raw)
    except ValidationError as exc:
        hints = ["%s: %s" % (".".join(str(x) for x in e["loc"]), e["msg"]) for e in exc.errors()]
        ctx.fail(INVALID_PARAMS, "invalid description", hints)


def replay_into(ctx, sketch, description):
    """Rebuild a described sketch inside ``sketch`` (§ 8.2); returns the replay result."""
    desc = _description(ctx, description)
    conv = _Convert(ctx, desc.units)
    skipped = []
    pmap, cmap = {}, {}
    xy_of = {p.index: (conv.L(p.xy[0]), conv.L(p.xy[1])) for p in desc.points}
    for p in desc.points:
        if p.origin:
            pmap[p.index] = sketch.originPoint

    def P(i):
        if i in pmap:
            return pmap[i]
        x, y = xy_of[i]
        return adsk.core.Point3D.create(x, y, 0)

    with _deferred(sketch):
        for dc in desc.curves:
            if dc.owner is not None:
                continue  # recreated with its owner
            if not dc.replayable or dc.type not in _BUILDABLE:
                skipped.append({"kind": "curve", "index": dc.index,
                                "reason": "type %s not replayable" % dc.type})
                continue
            pts = {
                "start": None if dc.start is None else P(dc.start),
                "end": None if dc.end is None else P(dc.end),
                "center": None if dc.center is None else P(dc.center),
                "fit": [P(i) for i in dc.fit_points],
                "control": [P(i) for i in dc.control_points],
            }
            data = {
                "radius": None if dc.radius is None else conv.L(dc.radius),
                "sweep": None if dc.sweep is None else conv.A(dc.sweep),
                "degree": dc.degree,
                "closed": dc.closed,
            }
            if dc.type == "ellipse":
                cx, cy = xy_of[dc.center]
                ax, ay = dc.major_axis
                major, minor = conv.L(dc.major_radius), conv.L(dc.minor_radius)
                mk = adsk.core.Point3D.create
                curve = add_ellipse(
                    sketch, pts["center"], mk(cx + ax * major, cy + ay * major, 0),
                    mk(cx - ay * minor, cy + ax * minor, 0))
            else:
                curve = build_curve(sketch, dc.type, pts, data)
            _map_points(dc, curve, pmap)
            curve.isConstruction = dc.construction
            cmap[dc.index] = curve
            if dc.type == "ellipse":
                for role, attr in (("major", "majorAxisLine"), ("minor", "minorAxisLine")):
                    _adopt_owned(desc, pmap, cmap, "ellipse", dc.index, role, getattr(curve, attr, None))
        texts = _replay_texts(ctx, sketch, desc, conv, xy_of, pmap, cmap, skipped)
        for dc in desc.curves:
            if dc.owner is not None and dc.index not in cmap:
                skipped.append({"kind": "curve", "index": dc.index, "reason": "owner not recreated"})
        for p in desc.points:
            if p.index not in pmap:
                x, y = xy_of[p.index]
                pmap[p.index] = sketch.sketchPoints.add(adsk.core.Point3D.create(x, y, 0))
    n_constraints = _replay_constraints(ctx, sketch, desc, pmap, cmap, skipped)
    n_dimensions = _replay_dimensions(ctx, sketch, desc, conv, pmap, cmap, skipped)
    for p in desc.points:
        if (p.fixed or p.reference) and not p.origin:
            pmap[p.index].isFixed = True
    for dc in desc.curves:
        if (dc.fixed or dc.reference) and dc.index in cmap:
            cmap[dc.index].isFixed = True
    by_index = sorted(desc.curves, key=lambda c: c.index)
    return {
        "sketch": ctx.ref(sketch),
        "curves": [None if c.index not in cmap else ctx.ref(cmap[c.index]) for c in by_index],
        "points": len(pmap),
        "constraints": n_constraints,
        "dimensions": n_dimensions,
        "texts": texts,
        "skipped": skipped,
        "profiles": sketch.profiles.count,
    }


def _adopt_owned(desc, pmap, cmap, kind_, index, role, line):
    """Map the description curve owned by (``kind_``, ``index``, ``role``) onto ``line``."""
    if line is None:
        return
    for oc in desc.curves:
        if oc.owner and oc.owner.get("kind") == kind_ and oc.owner.get("index") == index \
                and oc.owner.get("role") == role:
            cmap[oc.index] = line
            for idx, p in ((oc.start, line.startSketchPoint), (oc.end, line.endSketchPoint)):
                if idx is not None:
                    pmap.setdefault(idx, p)


def _replay_texts(ctx, sketch, desc, conv, xy_of, pmap, cmap, skipped):
    created = 0
    mk = adsk.core.Point3D.create
    for dt in desc.texts:
        if not dt.replayable or dt.mode is None:
            skipped.append({"kind": "text", "index": dt.index, "reason": "text not replayable"})
            continue
        if dt.mode != "box" and dt.path not in cmap:
            skipped.append({"kind": "text", "index": dt.index, "reason": "entity skipped"})
            continue
        spec = dict(
            content=dt.content, height=conv.L(dt.height), mode=dt.mode, font=dt.font,
            angle=conv.A(dt.angle), above=True if dt.above is None else dt.above,
            horizontal_align=dt.horizontal_align or "left",
            vertical_align=dt.vertical_align or "bottom")
        if dt.mode == "box":
            if dt.box is not None:
                (x1, y1), (x2, y2) = dt.box
                spec["position"] = mk(conv.L(x1), conv.L(y1), 0)
                spec["box_corner"] = mk(conv.L(x2), conv.L(y2), 0)
        else:
            spec["path"] = cmap[dt.path]
        try:
            made = add_text(ctx, sketch, spec)
        except Exception as exc:
            skipped.append({"kind": "text", "index": dt.index, "reason": "fusion: %s" % exc})
            continue
        created += 1
        definition = getattr(made, "definition", None)
        if dt.mode == "box" and definition is not None:
            for k, line in enumerate(getattr(definition, "rectangleLines", [])):
                _adopt_owned(desc, pmap, cmap, "text", dt.index, k, line)
    return created


def _entities(item, pmap, cmap):
    """(points, curves) of a described constraint/dimension, or None when one is unmapped."""
    if any(i not in pmap for i in item.points) or any(i not in cmap for i in item.curves):
        return None
    return [pmap[i] for i in item.points], [cmap[i] for i in item.curves]


def _replay_constraints(ctx, sketch, desc, pmap, cmap, skipped):
    added = 0
    for dk in desc.constraints:
        if dk.owned:
            continue
        found = None if not dk.replayable else _entities(dk, pmap, cmap)
        line = None
        if found is not None and dk.symmetry_line is not None:
            line = cmap.get(dk.symmetry_line)
            if line is None:
                found = None
        if found is None:
            skipped.append({"kind": "constraint", "index": dk.index, "reason": "entity skipped"})
            continue
        try:
            add_constraint(ctx, sketch, dk.type, found[1], found[0], line)
            added += 1
        except Exception as exc:
            skipped.append({"kind": "constraint", "index": dk.index, "reason": "fusion: %s" % exc})
    return added


def _replay_dimensions(ctx, sketch, desc, conv, pmap, cmap, skipped):
    added = 0
    for dd in desc.dimensions:
        found = None if not dd.replayable else _entities(dd, pmap, cmap)
        if found is None:
            skipped.append({"kind": "dimension", "index": dd.index, "reason": "entity skipped"})
            continue
        try:
            tp = None
            if dd.text_point is not None:
                tp = adsk.core.Point3D.create(conv.L(dd.text_point[0]), conv.L(dd.text_point[1]), 0)
            dim = make_dimension(ctx, sketch, dd.type, found[1], found[0], dd.driving, tp)
            if dd.driving and dd.value is not None:
                dim.parameter.expression = conv.expr(dd.type, dd.value)
            added += 1
        except Exception as exc:
            skipped.append({"kind": "dimension", "index": dd.index, "reason": "fusion: %s" % exc})
    return added


@action("sketch", access="write", timeout=180)
def replay(
    ctx,
    description: dict[str, Any],
    sketch: Sketch | None = None,
    plane: Plane | None = None,
    component: Component | None = None,
    name: str | None = None,
) -> dict:
    """Rebuild a sketch from an info description, in an existing sketch or on a new plane."""
    ctx.require_design()
    if (sketch is None) == (plane is None):
        ctx.fail(INVALID_PARAMS, "give exactly one of sketch and plane")
    if sketch is not None:
        if component is not None or name is not None:
            ctx.fail(INVALID_PARAMS, "component and name only apply with plane")
        target = sketch
    else:
        target = create_sketch(ctx, plane, component, name)
    return replay_into(ctx, target, description)


# --- live scenarios (design § 10.2) --------------------------------------------------------------

from .live_common import _MM, _lc, _near  # noqa: E402


async def _call(session, act, params, tool="sketch"):
    return await _lc().call(session, tool, act, params, units=_MM)


async def _do(session, act, params, tool="sketch"):
    env = await _call(session, act, params, tool)
    assert env["ok"], "%s.%s: %s" % (tool, act, env.get("error"))
    return env["result"]


async def _fails(session, act, params, code, tool="sketch"):
    env = await _call(session, act, params, tool)
    assert not env["ok"] and env["error"]["code"] == code, "%s.%s expected %s, got %r" % (
        tool, act, code, env)
    return env["error"]


async def _new(session, plane="XY", **extra):
    return (await _do(session, "create", dict(plane=plane, **extra)))["sketch"]


async def _info(session, ref, **kw):
    return await _do(session, "info", dict(sketch=ref, **kw))


def _refs(result):
    return [c["ref"] for c in result["curves"]]


def _xy_of(info, curve, key):
    return info["points"][curve[key]]["xy"]


def _ends(info, curve):
    return sorted(tuple(round(v, 6) for v in _xy_of(info, curve, k)) for k in ("start", "end"))


def _of(info, type_):
    return [c for c in info["curves"] if c["type"] == type_]


def _areas(info):
    return sorted(p["area"] for p in info["profiles"])


async def _rect(session, ref, p1=(0, 0), p2=(20, 10)):
    return await _do(session, "rectangle",
                     {"sketch": ref, "mode": "two_point", "p1": list(p1), "p2": list(p2)})


async def _constrain_rect(session, ref):
    """Make sure the rectangle has a horizontal and a vertical constraint, adding them via the constraint
    action when the account does not create them with the geometry; returns the constraints."""
    d = await _info(session, ref)
    have = {c["type"] for c in d["constraints"]}
    for kind, axis in (("horizontal", 1), ("vertical", 0)):
        if kind not in have:
            line = [c for c in _of(d, "line")
                    if abs(_xy_of(d, c, "start")[axis] - _xy_of(d, c, "end")[axis]) < 1e-6][0]
            await _do(session, "constraint", {"sketch": ref, "type": kind, "curves": [line["ref"]]})
    return (await _info(session, ref))["constraints"]


async def _live_list(session):
    a, b = await _new(session), await _new(session, "XZ")
    r = await _do(session, "list", {})
    toks = {i["ref"]["token"]: i for i in r["items"]}
    assert r["total"] >= 2 and a["token"] in toks and b["token"] in toks, r
    assert {"ref", "plane", "component", "profiles", "curves", "fully_constrained", "visible"} <= set(
        toks[a["token"]])
    only = await _do(session, "list", {"component": toks[a["token"]]["component"]})
    assert {a["token"], b["token"]} <= {i["ref"]["token"] for i in only["items"]}, only


async def _live_info(session):
    ref = await _new(session)
    await _rect(session, ref)
    await _constrain_rect(session, ref)
    d = await _info(session, ref)
    assert len(_of(d, "line")) == 4 and len(d["curves"]) == 4, d["curves"]
    assert len(d["profiles"]) == 1 and abs(d["profiles"][0]["area"] - 200) < 1e-6
    assert d["profiles"][0]["loops"] == 1
    kinds = {c["type"] for c in d["constraints"]}
    assert {"horizontal", "vertical"} <= kinds, kinds
    page = await _do(session, "info", {"sketch": ref, "section": "curves", "limit": 2})
    c = page["curves"]
    assert len(c["items"]) == 2 and (c["total"], c["offset"], c["next_offset"]) == (4, 0, 2), c


async def _live_create(session):
    ref = await _do(session, "create", {"plane": "XZ", "name": "S1"})
    assert ref["sketch"]["name"] == "S1", ref
    listed = (await _do(session, "list", {}))["items"]
    mine = [i for i in listed if i["ref"]["token"] == ref["sketch"]["token"]][0]
    assert "XZ" in (mine["plane"]["name"] or ""), mine
    box = await _lc().box(session, 10, 10, 10)
    assert (await _do(session, "create", {"plane": {"body": box, "index": 0}}))["sketch"]
    base = await _new(session)
    await _do(session, "circle", {"sketch": base, "mode": "center_radius", "center": [30, 0], "radius": 5})
    cyl = (await _do(session, "extrude", {"profile": {"sketch": base, "index": 0}, "distance": 10},
                     tool="model"))["bodies"][0]
    codes = []
    for i in range(3):
        env = await _call(session, "create", {"plane": {"body": cyl, "index": i}})
        codes.append(None if env["ok"] else env["error"]["code"])
    assert set(codes) <= {None, "INVALID_PARAMS"} and "INVALID_PARAMS" in codes, codes


async def _live_delete(session):
    ref = await _new(session)
    await _fails(session, "delete", {"sketch": ref}, "CONFIRM_REQUIRED")
    tokens = [i["ref"]["token"] for i in (await _do(session, "list", {}))["items"]]
    assert ref["token"] in tokens
    r = await _do(session, "delete", {"sketch": ref, "confirm": True})
    assert r["deleted"]["token"] == ref["token"], r
    tokens = [i["ref"]["token"] for i in (await _do(session, "list", {}))["items"]]
    assert ref["token"] not in tokens


async def _live_rectangle(session):
    for mode, args, area in (
        ("two_point", {"p1": [0, 0], "p2": [20, 10]}, 200),
        ("center", {"p1": [0, 0], "p2": [10, 5]}, 200),
        ("three_point", {"p1": [0, 0], "p2": [10, 10], "p3": [-5, 5]}, 100),
    ):
        ref = await _new(session)
        await _do(session, "rectangle", dict(sketch=ref, mode=mode, **args))
        d = await _info(session, ref)
        assert len(_of(d, "line")) == 4 and len(d["profiles"]) == 1, (mode, d["curves"])
        assert abs(d["profiles"][0]["area"] - area) < 0.01, (mode, d["profiles"])


async def _live_circle(session):
    ref = await _new(session)
    await _do(session, "circle", {"sketch": ref, "mode": "center_radius", "center": [0, 0], "diameter": 20})
    await _do(session, "circle", {"sketch": ref, "mode": "center_radius", "center": [50, 0], "radius": "10 mm"})
    await _fails(session, "circle", {"sketch": ref, "mode": "center_radius", "center": [0, 0],
                                     "radius": 5, "diameter": 10}, "INVALID_PARAMS")
    await _do(session, "circle", {"sketch": ref, "mode": "two_point", "p1": [0, 40], "p2": [20, 40]})
    await _do(session, "circle", {"sketch": ref, "mode": "three_point", "p1": [10, 100], "p2": [0, 110],
                                  "p3": [-10, 100]})
    d = await _info(session, ref)
    c = _of(d, "circle")
    assert [round(x["radius"], 6) for x in c] == [10, 10, 10, 10], c
    _near(_xy_of(d, c[2], "center"), [10, 40], 1e-6)
    _near(_xy_of(d, c[3], "center"), [0, 100], 1e-6)


async def _live_line(session):
    ref = await _new(session)
    await _do(session, "line", {"sketch": ref, "points": [[0, 0], [10, 0], [10, 10], [0, 10]], "closed": True})
    d = await _info(session, ref)
    lines = _of(d, "line")
    counts = {}
    for ln in lines:
        for k in ("start", "end"):
            counts[ln[k]] = counts.get(ln[k], 0) + 1
    assert len(lines) == 4 and set(counts.values()) == {2} and len(d["profiles"]) == 1, (counts, d)
    one = await _new(session)
    await _do(session, "line", {"sketch": one, "points": [[0, 0], [5, 5]]})
    assert len(_of(await _info(session, one), "line")) == 1
    await _fails(session, "line", {"sketch": one, "points": [[0, 0]]}, "INVALID_PARAMS")


async def _live_arc(session):
    ref = await _new(session)
    await _do(session, "arc", {"sketch": ref, "mode": "center_start_sweep", "center": [0, 0],
                               "start": [10, 0], "sweep": 90})
    await _do(session, "arc", {"sketch": ref, "mode": "three_point", "start": [10, 50], "point": [0, 60],
                               "end": [-10, 50]})
    d = await _info(session, ref)
    a, b = _of(d, "arc")
    _near(_xy_of(d, a, "end"), [0, 10], 1e-6)
    assert abs(b["radius"] - 10) < 1e-6 and abs(b["sweep"] - 180) < 1e-6, b


async def _live_spline(session):
    pts = [[0, 0], [10, 10], [20, 0], [30, 10]]
    ref = await _new(session)
    await _do(session, "spline", {"sketch": ref, "mode": "fit", "points": pts})
    d = await _info(session, ref)
    s = _of(d, "fitted_spline")[0]
    assert len(s["fit_points"]) == 4
    closed = await _new(session)
    await _do(session, "spline", {"sketch": closed, "mode": "fit", "closed": True,
                                  "points": [[0, 0], [20, 0], [20, 20], [0, 20]]})
    d = await _info(session, closed)
    assert _of(d, "fitted_spline")[0]["closed"] is True and len(d["profiles"]) == 1
    ctl = await _new(session)
    await _do(session, "spline", {"sketch": ctl, "mode": "control", "points": pts, "degree": 3})
    c = _of(await _info(session, ctl), "control_point_spline")[0]
    assert len(c["control_points"]) == 4


async def _live_polygon(session):
    import math

    ref = await _new(session)
    await _do(session, "polygon", {"sketch": ref, "center": [0, 0], "sides": 6, "radius": 10})
    d = await _info(session, ref)
    assert len(_of(d, "line")) == 6
    for ln in _of(d, "line"):
        for k in ("start", "end"):
            assert abs(math.hypot(*_xy_of(d, ln, k)) - 10) < 1e-6
    circ = await _new(session)
    await _do(session, "polygon", {"sketch": circ, "center": [0, 0], "sides": 6, "radius": 10,
                                   "mode": "circumscribed"})
    d = await _info(session, circ)
    for ln in _of(d, "line"):
        a, b = _xy_of(d, ln, "start"), _xy_of(d, ln, "end")
        assert abs(math.hypot((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) - 10) < 1e-6
    await _fails(session, "polygon", {"sketch": ref, "center": [0, 0], "sides": 2, "radius": 10},
                 "INVALID_PARAMS")


async def _live_ellipse(session):
    ref = await _new(session)
    await _do(session, "ellipse", {"sketch": ref, "center": [0, 0], "major_point": [20, 0], "point": [0, 10]})
    d = await _info(session, ref)
    e = _of(d, "ellipse")[0]
    assert abs(e["major_radius"] - 20) < 1e-6 and abs(e["minor_radius"] - 10) < 1e-6, e
    assert len(d["profiles"]) == 1 and abs(d["profiles"][0]["area"] - 628.32) < 0.01


async def _live_slot(session):
    for mode, area in (("center_to_center", 378.54), ("overall", 278.54)):
        ref = await _new(session)
        await _do(session, "slot", {"sketch": ref, "mode": mode, "p1": [0, 0], "p2": [30, 0], "width": 10})
        d = await _info(session, ref)
        assert len(_of(d, "line")) == 2 and len(_of(d, "arc")) == 2, d["curves"]
        assert len(d["profiles"]) == 1 and abs(d["profiles"][0]["area"] - area) < 0.01, d["profiles"]
    await _fails(session, "slot", {"sketch": ref, "mode": "overall", "p1": [0, 0], "p2": [30, 0],
                                   "width": 0}, "INVALID_PARAMS")


async def _live_point(session):
    ref = await _new(session)
    r = await _do(session, "point", {"sketch": ref, "points": [[5, 5], [10, -3]]})
    assert len(r["points"]) == 2 and all(p["ref"]["token"] for p in r["points"]), r
    d = await _info(session, ref)
    xys = [tuple(round(v, 6) for v in p["xy"]) for p in d["points"]]
    assert (5.0, 5.0) in xys and (10.0, -3.0) in xys, xys


async def _live_text(session):
    ref = await _new(session)
    await _do(session, "text", {"sketch": ref, "content": "AB", "height": 5, "position": [0, 0]})
    line = await _do(session, "line", {"sketch": ref, "points": [[0, 20], [40, 20]]})
    await _do(session, "text", {"sketch": ref, "content": "On", "height": 3, "mode": "along_path",
                                "path": line["curves"][0]["ref"]})
    d = await _info(session, ref)
    box = [t for t in d["texts"] if t["mode"] == "box"][0]
    assert box["content"] == "AB" and abs(box["height"] - 5) < 1e-6, box
    along = [t for t in d["texts"] if t["mode"] == "along_path"][0]
    line_index = [c["index"] for c in d["curves"] if c["type"] == "line" and "owner" not in c][0]
    assert along["path"] == line_index, (along, line_index)
    await _fails(session, "text", {"sketch": ref, "content": "Z", "height": 0, "position": [0, 0]},
                 "INVALID_PARAMS")
    env = await _call(session, "text", {"sketch": ref, "content": "Fit", "height": 3, "mode": "fit_on_path",
                                        "path": line["curves"][0]["ref"]})
    assert env["ok"] or env["error"]["code"] == "UNSUPPORTED", env


async def _live_fillet(session):
    ref = await _new(session)
    r = await _do(session, "line", {"sketch": ref, "points": [[0, 20], [0, 0], [20, 0]]})
    one, two = _refs(r)
    await _fails(session, "fillet", {"sketch": ref, "curve_one": one, "curve_two": two, "radius": 0},
                 "INVALID_PARAMS")
    await _do(session, "fillet", {"sketch": ref, "curve_one": one, "curve_two": two, "radius": 5})
    d = await _info(session, ref)
    arc = _of(d, "arc")[0]
    assert abs(arc["radius"] - 5) < 1e-6, arc
    assert _ends(d, arc) == [(0.0, 5.0), (5.0, 0.0)], _ends(d, arc)


async def _live_delete_constraint(session):
    ref = await _new(session)
    await _rect(session, ref)
    before = await _constrain_rect(session, ref)
    idx = [c["index"] for c in before if c["type"] == "horizontal"][0]
    await _fails(session, "delete_constraint", {"sketch": ref, "indices": [idx]}, "CONFIRM_REQUIRED")
    assert len((await _info(session, ref))["constraints"]) == len(before)
    r = await _do(session, "delete_constraint", {"sketch": ref, "indices": [idx], "confirm": True})
    assert r["deleted"][0]["type"] == "horizontal", r
    assert len((await _info(session, ref))["constraints"]) == len(before) - 1
    await _fails(session, "delete_constraint", {"sketch": ref, "indices": [999], "confirm": True},
                 "INVALID_PARAMS")


async def _live_delete_dimension(session):
    ref = await _new(session)
    c = await _do(session, "circle", {"sketch": ref, "mode": "center_radius", "center": [0, 0], "radius": 10})
    dim = await _do(session, "dimension", {"sketch": ref, "type": "radial", "curves": _refs(c)})
    assert len((await _info(session, ref))["dimensions"]) == 1
    await _fails(session, "delete_dimension", {"sketch": ref, "indices": [0]}, "CONFIRM_REQUIRED")
    r = await _do(session, "delete_dimension", {"sketch": ref, "indices": [0], "confirm": True})
    assert r["deleted"][0]["type"] == "radial" and r["deleted"][0]["parameter"] == dim["dimension"]["parameter"]
    d = await _info(session, ref)
    assert d["dimensions"] == [] and abs(_of(d, "circle")[0]["radius"] - 10) < 1e-6


async def _live_mirror(session):
    ref = await _new(session)
    src = await _do(session, "line", {"sketch": ref, "points": [[10, 0], [20, 10]]})
    axis = await _do(session, "line", {"sketch": ref, "points": [[0, -10], [0, 10]]})
    r = await _do(session, "mirror", {"sketch": ref, "curves": _refs(src), "line": _refs(axis)[0]})
    d = await _info(session, ref)
    assert _ends(d, [c for c in d["curves"] if c["ref"]["token"] == r["curves"][0]["ref"]["token"]][0]) == [
        (-20.0, 10.0), (-10.0, 0.0)]
    assert len([c for c in d["constraints"] if c["type"] == "symmetry"]) == 2, d["constraints"]
    arc = await _do(session, "arc", {"sketch": ref, "mode": "center_start_sweep", "center": [50, 0],
                                     "start": [60, 0], "sweep": 90})
    await _fails(session, "mirror", {"sketch": ref, "curves": _refs(src), "line": _refs(arc)[0]},
                 "INVALID_PARAMS")


async def _live_pattern(session):
    ref = await _new(session)
    c = await _do(session, "circle", {"sketch": ref, "mode": "center_radius", "center": [0, 0], "radius": 2})
    await _do(session, "pattern", {"sketch": ref, "curves": _refs(c), "type": "rectangular", "count_x": 3,
                                   "spacing_x": 10, "count_y": 2, "spacing_y": 10})
    d = await _info(session, ref)
    got = sorted(tuple(round(v, 6) for v in _xy_of(d, x, "center")) for x in _of(d, "circle"))
    assert got == sorted((x * 10.0, y * 10.0) for x in range(3) for y in range(2)), got
    await _fails(session, "pattern", {"sketch": ref, "curves": _refs(c), "type": "rectangular",
                                      "count_x": 502, "spacing_x": 1}, "INVALID_PARAMS")
    xz = await _new(session, "XZ")
    c = await _do(session, "circle", {"sketch": xz, "mode": "center_radius", "center": [10, 0], "radius": 2})
    await _do(session, "pattern", {"sketch": xz, "curves": _refs(c), "type": "circular", "center": [0, 0],
                                   "count": 4})
    d = await _info(session, xz)
    got = sorted(tuple(round(v, 6) for v in _xy_of(d, x, "center")) for x in _of(d, "circle"))
    assert got == sorted([(10.0, 0.0), (0.0, 10.0), (-10.0, 0.0), (0.0, -10.0)]), got


async def _live_constraint(session):
    ref = await _new(session)
    ln = await _do(session, "line", {"sketch": ref, "points": [[0, 0], [10, 5]]})
    await _do(session, "constraint", {"sketch": ref, "type": "horizontal", "curves": _refs(ln)})
    d = await _info(session, ref)
    line = _of(d, "line")[0]
    assert abs(_xy_of(d, line, "start")[1] - _xy_of(d, line, "end")[1]) < 1e-6
    fixed = await _do(session, "line", {"sketch": ref, "points": [[0, 30], [10, 30]]})
    await _do(session, "constraint", {"sketch": ref, "type": "fix", "curves": _refs(fixed)})
    d = await _info(session, ref)
    assert [c for c in d["curves"] if c["ref"]["token"] == _refs(fixed)[0]["token"]][0]["fixed"] is True
    a = await _do(session, "line", {"sketch": ref, "points": [[0, 70], [10, 70]]})
    b = await _do(session, "line", {"sketch": ref, "points": [[10, 70], [20, 80]]})
    await _do(session, "constraint", {"sketch": ref, "type": "coincident", "points": [
        a["curves"][0]["points"]["end"], b["curves"][0]["points"]["start"]]})
    circ = await _do(session, "circle", {"sketch": ref, "mode": "center_radius", "center": [40, 55],
                                         "radius": 5})
    tan = await _do(session, "line", {"sketch": ref, "points": [[30, 50], [50, 50]]})
    await _do(session, "constraint", {"sketch": ref, "type": "tangent", "curves": _refs(tan) + _refs(circ)})
    pts = await _do(session, "point", {"sketch": ref, "points": [[-5, 100], [5, 100]]})
    axis = await _do(session, "line", {"sketch": ref, "points": [[0, 95], [0, 105]]})
    await _do(session, "constraint", {"sketch": ref, "type": "symmetry",
                                      "points": [p["ref"] for p in pts["points"]],
                                      "symmetry_line": _refs(axis)[0]})
    spline_a = await _do(session, "spline", {"sketch": ref, "mode": "fit",
                                             "points": [[60, 0], [70, 10], [80, 0]]})
    spline_b = await _do(session, "spline", {"sketch": ref, "mode": "fit",
                                             "points": [[80, 0], [90, 10], [100, 0]]})
    env = await _call(session, "constraint", {"sketch": ref, "type": "curvature",
                                              "curves": _refs(spline_a) + _refs(spline_b)})
    assert env["ok"] or env["error"]["code"] == "UNSUPPORTED", env


async def _live_auto_constrain(session):
    ref = await _new(session)
    await _do(session, "line", {"sketch": ref, "points": [[0, 0], [10, 0.4], [10.3, 10], [0.2, 10]],
                                "closed": True})
    env = await _call(session, "auto_constrain", {"sketch": ref})
    if env["ok"]:
        assert env["result"]["constraints_added"] > 0, env
    else:
        assert env["error"]["code"] == "UNSUPPORTED", env


async def _live_dimension(session):
    ref = await _new(session)
    c = await _do(session, "circle", {"sketch": ref, "mode": "center_radius", "center": [0, 0], "radius": 10})
    r = await _do(session, "dimension", {"sketch": ref, "type": "radial", "curves": _refs(c), "value": "7 mm"})
    assert r["dimension"]["parameter"], r
    assert abs(_of(await _info(session, ref), "circle")[0]["radius"] - 7) < 1e-6
    l1 = await _do(session, "line", {"sketch": ref, "points": [[0, 30], [10, 30]]})
    l2 = await _do(session, "line", {"sketch": ref, "points": [[0, 30], [10, 35]]})
    await _do(session, "dimension", {"sketch": ref, "type": "angular", "curves": _refs(l1) + _refs(l2),
                                     "angle": 30})
    ang = [d for d in (await _info(session, ref))["dimensions"] if d["type"] == "angular"][0]
    assert abs(ang["value"] - 30) < 1e-6, ang
    await _fails(session, "dimension", {"sketch": ref, "type": "radial", "curves": _refs(c), "value": 3,
                                        "driven": True}, "INVALID_PARAMS")
    r = await _do(session, "dimension", {"sketch": ref, "type": "diameter", "curves": _refs(c), "driven": True})
    assert r["dimension"]["driving"] is False and abs(r["dimension"]["value"] - 14) < 1e-6, r


async def _live_offset(session):
    ref = await _new(session)
    r = await _rect(session, ref)
    new = await _do(session, "offset", {"sketch": ref, "curves": _refs(r), "distance": 2,
                                        "side_point": [-5, -5]})
    d = await _info(session, ref)
    assert len(new["curves"]) == 4 and len(_of(d, "line")) == 8, d["curves"]
    areas = _areas(d)
    assert any(abs(a - 200) < 0.01 for a in areas) and any(abs(a - 136) < 0.01 for a in areas), areas


async def _live_trim(session):
    ref = await _new(session)
    a = await _do(session, "line", {"sketch": ref, "points": [[0, 0], [20, 0]]})
    await _do(session, "line", {"sketch": ref, "points": [[10, -10], [10, 10]]})
    r = await _do(session, "trim", {"sketch": ref, "curve": _refs(a)[0], "point": [15, 0]})
    assert r["curves"], r
    d = await _info(session, ref)
    assert [(0.0, 0.0), (10.0, 0.0)] in [_ends(d, c) for c in _of(d, "line")], [_ends(d, c) for c in _of(d, "line")]


async def _live_extend(session):
    ref = await _new(session)
    a = await _do(session, "line", {"sketch": ref, "points": [[0, 0], [15, 0]]})
    await _do(session, "line", {"sketch": ref, "points": [[20, -10], [20, 10]]})
    await _do(session, "extend", {"sketch": ref, "curve": _refs(a)[0], "point": [15, 0]})
    d = await _info(session, ref)
    assert [(0.0, 0.0), (20.0, 0.0)] in [_ends(d, c) for c in _of(d, "line")], [_ends(d, c) for c in _of(d, "line")]


async def _live_project(session):
    body = await _lc().box(session, 10, 10, 10)
    got = None
    for i in range(6):
        ref = await _new(session)
        env = await _call(session, "project", {"sketch": ref, "faces": [{"body": body, "index": i}]})
        if env["ok"] and len(env["result"]["curves"]) == 4:
            got = await _info(session, ref)
            break
    assert got and all(c["reference"] for c in got["curves"]), got
    plane = (await _do(session, "construction_plane", {"method": "offset", "plane": "XY", "offset": 5},
                       tool="model"))["plane"]
    mid = await _new(session, plane)
    cut = await _do(session, "project", {"sketch": mid, "mode": "cut_edges", "bodies": [body]})
    assert len(cut["curves"]) == 4, cut
    inter = await _new(session, plane)
    both = await _do(session, "project", {"sketch": inter, "mode": "intersect", "bodies": [body]})
    assert len(both["curves"]) == 4, both
    env = await _call(session, "project", {"sketch": await _new(session), "mode": "project",
                                           "faces": [{"body": body, "index": 0}], "linked": False})
    assert env["ok"] or env["error"]["code"] == "UNSUPPORTED", env


async def _live_set_construction(session):
    ref = await _new(session)
    r = await _rect(session, ref)
    await _do(session, "set_construction", {"sketch": ref, "curves": _refs(r), "value": True})
    d = await _info(session, ref)
    assert all(c["construction"] for c in _of(d, "line")) and d["profiles"] == [], d
    await _do(session, "set_construction", {"sketch": ref, "curves": _refs(r), "value": False})
    assert len((await _info(session, ref))["profiles"]) == 1


def _shape(d):
    """What a replay round trip must preserve (§ 10.2 replay)."""
    def r(v):
        return tuple(round(x, 6) for x in v)

    ell = [(r(d["points"][c["center"]]["xy"]), round(c["major_radius"], 6), round(c["minor_radius"], 6))
           for c in d["curves"] if c["type"] == "ellipse"]
    texts = [(t["content"], round(t["height"], 6), round(t["angle"], 6), t["mode"],
              None if t["box"] is None else (r(t["box"][0]), r(t["box"][1])),
              None if t["path"] is None else d["curves"][t["path"]]["type"]) for t in d["texts"]]
    return {
        "types": sorted(c["type"] for c in d["curves"]),
        "points": sorted(r(p["xy"]) for p in d["points"]),
        "constraints": sorted(c["type"] for c in d["constraints"]),
        "dimensions": sorted((x["type"], round(x["value"], 6)) for x in d["dimensions"]),
        "areas": [round(a, 4) for a in _areas(d)],
        "ellipses": ell,
        "texts": sorted(texts, key=lambda t: t[0]),
    }


async def _live_replay(session):
    ref = await _new(session)
    poly = await _do(session, "line", {"sketch": ref, "points": [[0, 0], [20, 0], [20, 10]]})
    c0 = await _do(session, "circle", {"sketch": ref, "mode": "center_radius", "center": [40, 15], "radius": 5})
    c1 = await _do(session, "circle", {"sketch": ref, "mode": "center_radius", "center": [60, 15], "radius": 5})
    tan = await _do(session, "line", {"sketch": ref, "points": [[30, 20], [50, 20]]})
    await _do(session, "arc", {"sketch": ref, "mode": "center_start_sweep", "center": [0, 30],
                               "start": [10, 30], "sweep": 90})
    await _do(session, "spline", {"sketch": ref, "mode": "fit",
                                  "points": [[0, 50], [10, 60], [20, 50], [30, 60]]})
    la = await _do(session, "line", {"sketch": ref, "points": [[0, 70], [10, 70]]})
    lb = await _do(session, "line", {"sketch": ref, "points": [[10, 70], [20, 80]]})
    await _do(session, "constraint", {"sketch": ref, "type": "horizontal", "curves": [_refs(poly)[0]]})
    await _do(session, "constraint", {"sketch": ref, "type": "tangent", "curves": _refs(tan) + _refs(c0)})
    await _do(session, "constraint", {"sketch": ref, "type": "equal", "curves": _refs(c0) + _refs(c1)})
    await _do(session, "constraint", {"sketch": ref, "type": "coincident", "points": [
        la["curves"][0]["points"]["end"], lb["curves"][0]["points"]["start"]]})
    await _do(session, "dimension", {"sketch": ref, "type": "radial", "curves": _refs(c1), "value": 5})
    await _do(session, "dimension", {"sketch": ref, "type": "distance", "points": [
        poly["curves"][0]["points"]["start"], poly["curves"][0]["points"]["end"]], "value": 20})
    await _do(session, "ellipse", {"sketch": ref, "center": [80, 0], "major_point": [90, 0], "point": [80, 5]})
    await _do(session, "text", {"sketch": ref, "content": "AB", "height": 5, "position": [0, -30],
                                "box_corner": [30, -20], "angle": 30})
    await _do(session, "text", {"sketch": ref, "content": "On", "height": 3, "mode": "along_path",
                                "path": _refs(la)[0]})
    await _do(session, "point", {"sketch": ref, "points": [[90, 90]]})
    src = await _info(session, ref)
    new = await _do(session, "replay", {"description": src, "plane": "XY"})
    assert new["skipped"] == [], new["skipped"]
    out = await _info(session, new["sketch"])
    assert len(out["curves"]) == len(src["curves"]), (len(out["curves"]), len(src["curves"]))
    a, b = _shape(src), _shape(out)
    for key in a:
        assert a[key] == b[key], (key, a[key], b[key])
    into = await _new(session)
    again = await _do(session, "replay", {"description": src, "sketch": into})
    assert again["skipped"] == [] and len(again["curves"]) == len(src["curves"])


LIVE_SCENARIOS = [
    (name, "sketch." + name, fn)
    for name, fn in (
        ("list", _live_list), ("info", _live_info), ("create", _live_create), ("delete", _live_delete),
        ("rectangle", _live_rectangle), ("circle", _live_circle), ("line", _live_line),
        ("arc", _live_arc), ("spline", _live_spline), ("polygon", _live_polygon),
        ("ellipse", _live_ellipse), ("slot", _live_slot), ("point", _live_point), ("text", _live_text),
        ("fillet", _live_fillet), ("delete_constraint", _live_delete_constraint),
        ("delete_dimension", _live_delete_dimension), ("mirror", _live_mirror),
        ("pattern", _live_pattern), ("constraint", _live_constraint),
        ("auto_constrain", _live_auto_constrain), ("dimension", _live_dimension),
        ("offset", _live_offset), ("trim", _live_trim), ("extend", _live_extend),
        ("project", _live_project), ("set_construction", _live_set_construction),
        ("replay", _live_replay),
    )
]
