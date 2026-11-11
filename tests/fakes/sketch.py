"""Sketch fakes for tests/test_sketch.py (design § 10.1), built on the core fake ``adsk`` package.

Importing this module adds the enums and small geometry classes the core fake lacks to
``adsk.core`` / ``adsk.fusion``. Every Fusion call a test needs to assert is appended to
``sketch.calls`` as ``(name, *args)``."""
import math

from adsk import core, fusion
from adsk.core import Collection, Entity, ObjectCollection, Point3D, Vector3D, _Fake

TAU = 2 * math.pi


# --- enums and geometry missing from the core fake ------------------------------------------------


class HorizontalAlignments:
    LeftHorizontalAlignment = 0
    CenterHorizontalAlignment = 1
    RightHorizontalAlignment = 2


class VerticalAlignments:
    TopVerticalAlignment = 0
    MiddleVerticalAlignment = 1
    BottomVerticalAlignment = 2


class DimensionOrientations:
    AlignedDimensionOrientation = 0
    HorizontalDimensionOrientation = 1
    VerticalDimensionOrientation = 2


class AutoConstrainResultTypes:
    Option1AutoConstrainResultType = 1
    Option2AutoConstrainResultType = 2
    Option3AutoConstrainResultType = 3


def _extend_matrix3d(cls=core.Matrix3D):
    """Add rotation/translation recording to the core fake Matrix3D without replacing it."""
    orig_init = cls.__init__

    def __init__(self):
        orig_init(self)
        self.translation = Vector3D(0, 0, 0)
        self.rotation = None  # (angle, axis xyz, origin xyz) once setToRotation is called

    def setToRotation(self, angle, axis, origin):
        self.rotation = (angle, (axis.x, axis.y, axis.z), (origin.x, origin.y, origin.z))
        return True

    if not hasattr(cls, "setToRotation"):
        cls.__init__, cls.setToRotation = __init__, setToRotation


_extend_matrix3d()


class Arc3D(_Fake):
    def __init__(self, center, radius, start_angle, end_angle):
        self.center, self.radius = center, radius
        self.startAngle, self.endAngle = start_angle, end_angle


class Ellipse3D(_Fake):
    def __init__(self, center, major_axis, major_radius, minor_radius):
        self.center, self.majorAxis = center, major_axis
        self.majorRadius, self.minorRadius = major_radius, minor_radius


for _ns, _objs in (
    (core, (HorizontalAlignments, VerticalAlignments, Arc3D, Ellipse3D)),
    (fusion, (DimensionOrientations, AutoConstrainResultTypes)),
):
    for _o in _objs:
        setattr(_ns, _o.__name__, _o)


def plane_face(body):
    g = core.Plane()
    g.surfaceType = core.SurfaceTypes.PlaneSurfaceType
    return body.add_face(g)


def cylinder_face(body):
    g = core.Cylinder()
    g.surfaceType = core.SurfaceTypes.CylinderSurfaceType
    return body.add_face(g)


# --- mixin: objectType from the class name, and attributes a test can make "missing" ---------------


class _T:
    _missing = frozenset()

    @property
    def objectType(self):
        return "adsk::fusion::" + type(self).__name__

    def __getattribute__(self, name):
        if name in object.__getattribute__(self, "_missing"):
            raise AttributeError(name)
        return super().__getattribute__(name)


class Live(_T, Collection):
    """A collection that views a list owned by someone else."""

    def __init__(self, backing):
        super().__init__()
        self._items = backing


def pt(p):
    return (p.x, p.y)


def _coords(p):
    return p.geometry if isinstance(p, fusion.SketchPoint) else p


def _dist(a, b):
    return math.hypot(a.x - b.x, a.y - b.y)


# --- sketch entities -----------------------------------------------------------------------------


class SketchPoint(_T, fusion.SketchPoint):
    def __init__(self, sketch, x=0.0, y=0.0):
        super().__init__(sketch)
        self.geometry = Point3D(x, y, 0.0)
        self.isFixed = False
        self.isReference = False
        sketch.parentComponent._design._register(self)

    @property
    def worldGeometry(self):
        return self.parentSketch.sketchToModelSpace(self.geometry)


class Curve(_T, fusion.SketchCurve):
    def __init__(self, sketch):
        super().__init__(sketch)
        self.isConstruction = False
        self.isFixed = False
        self.isReference = False
        self.isFullyConstrained = False
        sketch.parentComponent._design._register(self)

    def trim(self, point, create_constraints):
        return self.parentSketch._edit("trim", self, point, create_constraints)

    def extend(self, point, create_constraints):
        return self.parentSketch._edit("extend", self, point, create_constraints)


class SketchLine(Curve, fusion.SketchLine):
    def __init__(self, sketch, a, b):
        super().__init__(sketch)
        self.startSketchPoint, self.endSketchPoint = a, b

    @property
    def geometry(self):
        return core.Line3D()


class SketchCircle(Curve, fusion.SketchCircle):
    def __init__(self, sketch, center, radius):
        super().__init__(sketch)
        self.centerSketchPoint, self.radius = center, radius


class SketchArc(Curve):
    def __init__(self, sketch, center, start, end, radius, sweep):
        super().__init__(sketch)
        self.centerSketchPoint, self.startSketchPoint, self.endSketchPoint = center, start, end
        self.radius = radius
        a0 = math.atan2(start.geometry.y - center.geometry.y, start.geometry.x - center.geometry.x)
        self.geometry = Arc3D(center.geometry, radius, a0, a0 + sweep)


class SketchFittedSpline(Curve):
    def __init__(self, sketch, fit_points):
        super().__init__(sketch)
        self.fitPoints = Live(fit_points)
        self.isClosed = False


class SketchControlPointSpline(Curve):
    closed_raises = False  # class-level switch: setting isClosed raises (older Fusion builds)

    def __init__(self, sketch, control_points, degree):
        super().__init__(sketch)
        self.controlPoints = Live(control_points)
        self.degree = degree
        self._closed = False

    @property
    def isClosed(self):
        return self._closed

    @isClosed.setter
    def isClosed(self, v):
        if self.closed_raises:
            raise RuntimeError("closed control-point splines are not supported")
        self._closed = v


class SketchConicCurve(Curve):
    pass


class SketchFixedSpline(Curve):
    pass


class SketchEllipse(Curve):
    def __init__(self, sketch, center, major_axis, major_radius, minor_radius, axis_lines=None):
        super().__init__(sketch)
        self.centerSketchPoint = center
        self.majorAxisRadius, self.minorAxisRadius = major_radius, minor_radius
        self.geometry = Ellipse3D(center.geometry, major_axis, major_radius, minor_radius)
        if axis_lines is not None:
            self.majorAxisLine, self.minorAxisLine = axis_lines


class MultiLineTextDefinition(_T):
    def __init__(self, lines, h_align, v_align):
        self.rectangleLines = lines
        self.horizontalAlignment, self.verticalAlignment = h_align, v_align


class AlongPathTextDefinition(_T):
    def __init__(self, path, above, h_align):
        self.path, self.isAbovePath, self.horizontalAlignment = path, above, h_align


class FitOnPathTextDefinition(_T):
    def __init__(self, path, above):
        self.path, self.isAbovePath = path, above


class SketchTextInput(_T):
    """What ``createInput2`` returns; ``_missing`` may hide ``setAsFitOnPath``."""

    def __init__(self, sketch, text, height):
        self._sk = sketch
        self.text, self.height = text, height
        self.angle, self.fontName = 0.0, "Arial"
        self.definition = None

    def setAsMultiLine(self, p, q, h_align, v_align, spacing):
        self._sk._rec("text.setAsMultiLine", p, q, h_align, v_align, spacing)
        self.definition = ("multi", p, q, h_align, v_align)

    def setAsAlongPath(self, path, above, h_align, spacing):
        self._sk._rec("text.setAsAlongPath", path, above, h_align, spacing)
        self.definition = ("along", path, above, h_align)

    def setAsFitOnPath(self, path, above):
        self._sk._rec("text.setAsFitOnPath", path, above)
        self.definition = ("fit", path, above)


class SketchText(_T, Entity):
    def __init__(self, sketch, inp):
        super().__init__(None)
        self.parentSketch = sketch
        self.text, self.height, self.angle = inp.text, inp.height, inp.angle
        self.fontName = inp.fontName
        kind, *a = inp.definition
        if kind == "multi":
            p, q, h, v = a
            cx, cy = (p.x + q.x) / 2, (p.y + q.y) / 2
            ca, sa = math.cos(inp.angle), math.sin(inp.angle)
            corners = [(p.x, p.y), (q.x, p.y), (q.x, q.y), (p.x, q.y)]
            rot = [(cx + (x - cx) * ca - (y - cy) * sa, cy + (x - cx) * sa + (y - cy) * ca)
                   for x, y in corners]
            pts = [sketch._new_point(x, y) for x, y in rot]
            lines = [sketch.sketchCurves.sketchLines._add(SketchLine(sketch, pts[i], pts[(i + 1) % 4]))
                     for i in range(4)]
            self.definition = MultiLineTextDefinition(lines, h, v)
        elif kind == "along":
            self.definition = AlongPathTextDefinition(*a)
        else:
            self.definition = FitOnPathTextDefinition(*a)
        sketch.parentComponent._design._register(self)


# --- constraints and dimensions --------------------------------------------------------------------


class _Item(_T, Entity):
    def __init__(self, sketch, **attrs):
        super().__init__(None)
        self.parentSketch = sketch
        self.isDeletable = True
        self.delete_ok = True
        self.__dict__.update(attrs)

    def deleteMe(self):
        self.parentSketch.calls.append(("deleteMe", self))
        if not self.delete_ok:
            return False
        for lst in (self.parentSketch._gcs, self.parentSketch._dims):
            if self in lst:
                lst.remove(self)
        return True


def _constraint(name):
    return type(name, (_Item,), {})


CONSTRAINT_CLASSES = {n: _constraint(n) for n in (
    "CoincidentConstraint", "HorizontalConstraint", "HorizontalPointsConstraint",
    "VerticalConstraint", "VerticalPointsConstraint", "ParallelConstraint",
    "PerpendicularConstraint", "CollinearConstraint", "TangentConstraint", "EqualConstraint",
    "ConcentricConstraint", "MidPointConstraint", "SymmetryConstraint", "SmoothConstraint",
    "CurvatureConstraint", "OffsetConstraint")}


class Param(_T, Entity):
    """A dimension parameter; assigning ``expression`` evaluates it with the design units."""

    def __init__(self, sketch, name, value, unit):
        super().__init__(name)
        self._sketch, self.value, self.unit = sketch, value, unit
        self._expression = "%s %s" % (value, unit)

    @property
    def expression(self):
        return self._expression

    @expression.setter
    def expression(self, text):
        self._sketch.calls.append(("expression", self.name, text))
        um = self._sketch.parentComponent._design.unitsManager
        self.value = um.evaluateExpression(text, "cm" if self.unit == "cm" else "rad")
        self._expression = text


class _Dim(_Item):
    def __init__(self, sketch, driving, text_point, value, unit, **attrs):
        super().__init__(sketch, **attrs)
        self.isDriving = driving
        self.textPosition = text_point
        if driving:
            self.parameter = Param(sketch, "d%d" % (len(sketch._dims) + 1), value, unit)
        else:
            self.parameter = None
            self.value = value


class SketchLinearDimension(_Dim):
    pass


class SketchRadialDimension(_Dim):
    pass


class SketchDiameterDimension(_Dim):
    pass


class SketchAngularDimension(_Dim):
    pass


class SketchOffsetDimension(_Dim):
    pass


class UnknownConstraint(_Item):
    pass


class UnknownDimension(_Dim):
    pass


class AutoConstrainResult(_Fake):
    def __init__(self, added=(), dims=(), moved=(), full=False):
        self.addedConstraints, self.addedDimensions = list(added), list(dims)
        self.movedGeometry, self.isFullyConstrained = list(moved), full


class AutoConstrainInput(_Fake):
    resultOption = None


class _Profile(_T, fusion.Profile):
    def __init__(self, area, loops=1):
        super().__init__()
        self._area, self._loops = area, loops

    def areaProperties(self):
        a = self._area
        return type("AreaProps", (), {"area": a})()

    @property
    def profileLoops(self):
        return Collection([None] * self._loops)


# --- the sketch ------------------------------------------------------------------------------------


class _Coll(_T):
    """A group of "add" methods on a sketch; ``_sk`` is the owning sketch."""

    def __init__(self, sk):
        self._sk = sk

    def _rec(self, name, *args):
        self._sk._rec(name, *args)

    def _point(self, p):
        """The SketchPoint for ``p`` (an existing one is reused, a Point3D makes a new one)."""
        if isinstance(p, fusion.SketchPoint):
            return p
        return self._sk._new_point(p.x, p.y)

    def _add(self, curve):
        self._sk._curves.append(curve)
        return curve


class SketchLines(_Coll):
    def addByTwoPoints(self, a, b):
        self._rec("lines.addByTwoPoints", a, b)
        return self._add(SketchLine(self._sk, self._point(a), self._point(b)))

    def _rect(self, corners):
        pts = [self._sk._new_point(x, y) for x, y in corners]
        return ObjectCollection(
            [self._add(SketchLine(self._sk, pts[i], pts[(i + 1) % 4])) for i in range(4)]
        )

    def addTwoPointRectangle(self, a, b):
        self._rec("lines.addTwoPointRectangle", a, b)
        a, b = _coords(a), _coords(b)
        return self._rect([(a.x, a.y), (b.x, a.y), (b.x, b.y), (a.x, b.y)])

    def addCenterPointRectangle(self, c, corner):
        self._rec("lines.addCenterPointRectangle", c, corner)
        c, k = _coords(c), _coords(corner)
        dx, dy = k.x - c.x, k.y - c.y
        return self._rect([(c.x - dx, c.y - dy), (c.x + dx, c.y - dy), (c.x + dx, c.y + dy),
                           (c.x - dx, c.y + dy)])

    def addThreePointRectangle(self, a, b, c):
        self._rec("lines.addThreePointRectangle", a, b, c)
        a, b, c = _coords(a), _coords(b), _coords(c)
        ex, ey = b.x - a.x, b.y - a.y
        n = math.hypot(ex, ey)
        nx, ny = -ey / n, ex / n
        h = (c.x - a.x) * nx + (c.y - a.y) * ny
        return self._rect([(a.x, a.y), (b.x, b.y), (b.x + nx * h, b.y + ny * h),
                           (a.x + nx * h, a.y + ny * h)])


def _circumcircle(a, b, c):
    d = 2 * (a.x * (b.y - c.y) + b.x * (c.y - a.y) + c.x * (a.y - b.y))
    if abs(d) < 1e-12:
        raise RuntimeError("the three points are collinear")
    ux = ((a.x**2 + a.y**2) * (b.y - c.y) + (b.x**2 + b.y**2) * (c.y - a.y)
          + (c.x**2 + c.y**2) * (a.y - b.y)) / d
    uy = ((a.x**2 + a.y**2) * (c.x - b.x) + (b.x**2 + b.y**2) * (a.x - c.x)
          + (c.x**2 + c.y**2) * (b.x - a.x)) / d
    return ux, uy, math.hypot(a.x - ux, a.y - uy)


class SketchCircles(_Coll):
    def addByCenterRadius(self, c, r):
        self._rec("circles.addByCenterRadius", c, r)
        return self._add(SketchCircle(self._sk, self._point(c), r))

    def addByTwoPoints(self, a, b):
        self._rec("circles.addByTwoPoints", a, b)
        a, b = _coords(a), _coords(b)
        mid = self._sk._new_point((a.x + b.x) / 2, (a.y + b.y) / 2)
        return self._add(SketchCircle(self._sk, mid, _dist(a, b) / 2))

    def addByThreePoints(self, a, b, c):
        self._rec("circles.addByThreePoints", a, b, c)
        x, y, r = _circumcircle(_coords(a), _coords(b), _coords(c))
        return self._add(SketchCircle(self._sk, self._sk._new_point(x, y), r))


class SketchArcs(_Coll):
    def addByThreePoints(self, s, m, e):
        self._rec("arcs.addByThreePoints", s, m, e)
        sp, mp, ep = _coords(s), _coords(m), _coords(e)
        cx, cy, r = _circumcircle(sp, mp, ep)

        def ang(p):
            return math.atan2(p.y - cy, p.x - cx)

        sweep_m, sweep_e = (ang(mp) - ang(sp)) % TAU, (ang(ep) - ang(sp)) % TAU
        start, end = self._point(s), self._point(e)
        if sweep_m > sweep_e:  # Fusion arcs run counter-clockwise: swap the ends
            start, end, sweep_e = end, start, TAU - sweep_e
        return self._add(SketchArc(self._sk, self._sk._new_point(cx, cy), start, end, r, sweep_e))

    def addByCenterStartSweep(self, c, s, sweep):
        self._rec("arcs.addByCenterStartSweep", c, s, sweep)
        cp, sp = _coords(c), _coords(s)
        r = _dist(cp, sp)
        a0 = math.atan2(sp.y - cp.y, sp.x - cp.x)
        far = self._sk._new_point(cp.x + r * math.cos(a0 + sweep), cp.y + r * math.sin(a0 + sweep))
        start, end = self._point(s), far
        if sweep < 0:
            start, end, sweep = far, start, -sweep
        return self._add(SketchArc(self._sk, self._point(c), start, end, r, sweep))

    def addFillet(self, c1, p1, c2, p2, radius):
        self._rec("arcs.addFillet", c1, p1, c2, p2, radius)
        a, b = self._sk._new_point(p1.x, p1.y), self._sk._new_point(p2.x, p2.y)
        return self._add(SketchArc(self._sk, self._sk._new_point(0, 0), a, b, radius, math.pi / 2))


class SketchFittedSplines(_Coll):
    def add(self, coll):
        self._rec("fitted.add", coll)
        pts = [self._point(p) for p in coll]
        return self._add(SketchFittedSpline(self._sk, pts))


class SketchEllipses(_Coll):
    def add(self, c, m, p):
        self._rec("ellipses.add", c, m, p)
        c, m, p = _coords(c), _coords(m), _coords(p)
        a = _dist(c, m)
        ux, uy = (m.x - c.x) / a, (m.y - c.y) / a
        px, py = (p.x - c.x) * ux + (p.y - c.y) * uy, -(p.x - c.x) * uy + (p.y - c.y) * ux
        if (px / a) ** 2 >= 1:
            raise RuntimeError("point is outside the ellipse")
        b = abs(py) / math.sqrt(1 - (px / a) ** 2)
        center = self._sk._new_point(c.x, c.y)
        lines = None
        if self._sk.axis_lines:  # Fusion builds the axis lines with the ellipse on some builds
            ends = [(c.x + ux * a, c.y + uy * a), (c.x - uy * b, c.y + ux * b)]
            lines = tuple(
                self._sk.sketchCurves.sketchLines._add(
                    SketchLine(self._sk, center, self._sk._new_point(x, y))) for x, y in ends)
        return self._add(SketchEllipse(self._sk, center, Vector3D(ux, uy, 0), a, b, lines))


class SketchControlPointSplines(_Coll):
    def add(self, coll, degree):
        self._rec("control.add", coll, degree)
        pts = [self._point(p) for p in coll]
        return self._add(SketchControlPointSpline(self._sk, pts, degree))


class SketchCurves(_T, Collection):
    def __init__(self, sk):
        super().__init__()
        self._items = sk._curves
        self.sketchLines = SketchLines(sk)
        self.sketchCircles = SketchCircles(sk)
        self.sketchArcs = SketchArcs(sk)
        self.sketchFittedSplines = SketchFittedSplines(sk)
        self.sketchControlPointSplines = SketchControlPointSplines(sk)
        self.sketchEllipses = SketchEllipses(sk)


class SketchPoints(_Coll, Collection):
    def __init__(self, sk):
        Collection.__init__(self)
        self._sk = sk
        self._items = sk._points

    def add(self, point):
        self._rec("points.add", point)
        return self._sk._new_point(point.x, point.y)


class SketchTexts(_Coll, Collection):
    def __init__(self, sk):
        Collection.__init__(self)
        self._sk = sk
        self._items = sk._texts

    def createInput2(self, text, height):
        self._rec("texts.createInput2", text, height)
        return SketchTextInput(self._sk, text, height)

    def add(self, inp):
        self._rec("texts.add", inp)
        t = SketchText(self._sk, inp)
        self._sk._texts.append(t)
        return t


class GeometricConstraints(_Coll, Collection):
    def __init__(self, sk):
        Collection.__init__(self)
        self._sk = sk
        self._items = sk._gcs

    def _make(self, name, cls, **attrs):
        c = CONSTRAINT_CLASSES.get(cls, UnknownConstraint)(self._sk, **attrs)
        self._sk._gcs.append(c)
        self._sk._rec(name, *attrs.values())
        return c

    def addCoincident(self, point, entity):
        return self._make("gc.addCoincident", "CoincidentConstraint", point=point, entity=entity)

    def addHorizontal(self, line):
        return self._make("gc.addHorizontal", "HorizontalConstraint", line=line)

    def addHorizontalPoints(self, a, b):
        return self._make("gc.addHorizontalPoints", "HorizontalPointsConstraint", pointOne=a, pointTwo=b)

    def addVertical(self, line):
        return self._make("gc.addVertical", "VerticalConstraint", line=line)

    def addVerticalPoints(self, a, b):
        return self._make("gc.addVerticalPoints", "VerticalPointsConstraint", pointOne=a, pointTwo=b)

    def addParallel(self, a, b):
        return self._make("gc.addParallel", "ParallelConstraint", lineOne=a, lineTwo=b)

    def addPerpendicular(self, a, b):
        return self._make("gc.addPerpendicular", "PerpendicularConstraint", lineOne=a, lineTwo=b)

    def addCollinear(self, a, b):
        return self._make("gc.addCollinear", "CollinearConstraint", lineOne=a, lineTwo=b)

    def addTangent(self, a, b):
        return self._make("gc.addTangent", "TangentConstraint", curveOne=a, curveTwo=b)

    def addEqual(self, a, b):
        return self._make("gc.addEqual", "EqualConstraint", curveOne=a, curveTwo=b)

    def addConcentric(self, a, b):
        return self._make("gc.addConcentric", "ConcentricConstraint", entityOne=a, entityTwo=b)

    def addMidPoint(self, point, curve):
        return self._make("gc.addMidPoint", "MidPointConstraint", point=point, midPointCurve=curve)

    def addSymmetry(self, a, b, line):
        return self._make("gc.addSymmetry", "SymmetryConstraint", entityOne=a, entityTwo=b,
                          symmetryLine=line)

    def addSmooth(self, a, b):
        return self._make("gc.addSmooth", "SmoothConstraint", curveOne=a, curveTwo=b)

    def addCurvature(self, a, b):
        return self._make("gc.addCurvature", "CurvatureConstraint", curveOne=a, curveTwo=b)


def _unit(dx, dy):
    n = math.hypot(dx, dy)
    return dx / n, dy / n


def _angle_in_quadrant(l1, l2, t):
    """Angle (rad) between the lines in the quadrant that contains text point ``t``."""
    p1, p2 = l1.startSketchPoint.geometry, l1.endSketchPoint.geometry
    q1, q2 = l2.startSketchPoint.geometry, l2.endSketchPoint.geometry
    d1, d2 = (p2.x - p1.x, p2.y - p1.y), (q2.x - q1.x, q2.y - q1.y)
    det = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(det) < 1e-12:
        raise RuntimeError("lines are parallel")
    s = ((q1.x - p1.x) * d2[1] - (q1.y - p1.y) * d2[0]) / det
    x = (p1.x + s * d1[0], p1.y + s * d1[1])
    vx, vy = t.x - x[0], t.y - x[1]
    a = (vx * d2[1] - vy * d2[0]) / det
    b = (d1[0] * vy - d1[1] * vx) / det
    u1 = _unit(*d1)
    u2 = _unit(*d2)
    u1, u2 = (math.copysign(1, a) * u1[0], math.copysign(1, a) * u1[1]), (
        math.copysign(1, b) * u2[0], math.copysign(1, b) * u2[1])
    return math.acos(max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1])))


class SketchDimensions(_Coll, Collection):
    def __init__(self, sk):
        Collection.__init__(self)
        self._sk = sk
        self._items = sk._dims

    def _make(self, name, cls, driving, text_point, value, unit, *args, **attrs):
        self._sk._rec(name, *args, text_point, driving)
        d = cls(self._sk, driving, text_point, value, unit, **attrs)
        self._sk._dims.append(d)
        return d

    def addDistanceDimension(self, a, b, orientation, text_point, driving):
        pa, pb = a.geometry, b.geometry
        v = {DimensionOrientations.HorizontalDimensionOrientation: abs(pb.x - pa.x),
             DimensionOrientations.VerticalDimensionOrientation: abs(pb.y - pa.y)}.get(
            orientation, _dist(pa, pb))
        self._sk._rec("sd.orientation", orientation)
        return self._make("sd.addDistanceDimension", SketchLinearDimension, driving, text_point, v,
                          "cm", a, b, orientation, entityOne=a, entityTwo=b, orientation=orientation)

    def addRadialDimension(self, c, text_point, driving):
        return self._make("sd.addRadialDimension", SketchRadialDimension, driving, text_point,
                          c.radius, "cm", c, entity=c)

    def addDiameterDimension(self, c, text_point, driving):
        return self._make("sd.addDiameterDimension", SketchDiameterDimension, driving, text_point,
                          2 * c.radius, "cm", c, entity=c)

    def addAngularDimension(self, l1, l2, text_point, driving):
        v = _angle_in_quadrant(l1, l2, text_point)
        return self._make("sd.addAngularDimension", SketchAngularDimension, driving, text_point, v,
                          "rad", l1, l2, lineOne=l1, lineTwo=l2)

    def addOffsetDimension(self, l1, l2, text_point, driving):
        return self._make("sd.addOffsetDimension", SketchOffsetDimension, driving, text_point, 0.0,
                          "cm", l1, l2, line=l1, entityTwo=l2)


class Sketch(_T, fusion.Sketch):
    """A fake sketch on the XY plane (sketch space == model space + ``origin``)."""

    def __init__(self, name, component, origin=(0, 0, 0), plane=None):
        super().__init__(name, component, origin)
        self._points, self._curves, self._gcs, self._dims, self._texts = [], [], [], [], []
        self.calls = []  # (name, *args) of every Fusion call a test may assert
        self.fail = {}  # call name -> exception to raise
        self.results = {}  # "trim" / "extend" / "offset" / "project"... -> list returned
        self.delete_ok = True
        self.isParametric = True
        self.isVisible = True
        self.isFullyConstrained = False
        self.referencePlane = plane
        self.origin = Point3D(*origin)
        self.xDirection, self.yDirection = Vector3D(1, 0, 0), Vector3D(0, 1, 0)
        self._defer = False
        self.sketchCurves = SketchCurves(self)
        self.sketchPoints = SketchPoints(self)
        self._texts = []
        self.axis_lines = False
        self.sketchTexts = SketchTexts(self)
        self.originPoint = self._new_point(0, 0)
        self.geometricConstraints = GeometricConstraints(self)
        self.sketchDimensions = SketchDimensions(self)
        component._design._register(self)

    # --- helpers for the fakes and for tests
    def _rec(self, name, *args):
        self.calls.append((name,) + args)
        if name in self.fail:
            raise self.fail[name]

    def _new_point(self, x, y):
        p = SketchPoint(self, x, y)
        self._points.append(p)
        return p

    def called(self, name):
        return [c[1:] for c in self.calls if c[0] == name]

    def _edit(self, name, curve, point, flag):
        self._rec(name, curve, point, flag)
        return ObjectCollection(self.results.get(name, []))

    # --- Fusion API
    @property
    def isComputeDeferred(self):
        return self._defer

    @isComputeDeferred.setter
    def isComputeDeferred(self, v):
        self._defer = v
        self.calls.append(("isComputeDeferred", v))

    def sketchToModelSpace(self, p):
        o = self._origin
        return Point3D(p.x + o[0], p.y + o[1], p.z + o[2])

    @property
    def profiles(self):
        return Collection(self._compute_profiles())

    def _compute_profiles(self):
        """One profile per circle, one per closed chain of lines (shoelace area)."""
        out = [_Profile(math.pi * c.radius**2) for c in self._curves
               if isinstance(c, SketchCircle) and not c.isConstruction]
        out += [_Profile(math.pi * c.majorAxisRadius * c.minorAxisRadius) for c in self._curves
                if isinstance(c, SketchEllipse) and not c.isConstruction]
        lines = [c for c in self._curves if isinstance(c, SketchLine) and not c.isConstruction]
        used = set()
        for start in lines:
            if id(start) in used:
                continue
            chain, cur, ok = [start], start, True
            used.add(id(start))
            while cur.endSketchPoint is not chain[0].startSketchPoint:
                nxt = next((l for l in lines if l.startSketchPoint is cur.endSketchPoint
                            and id(l) not in used), None)
                if nxt is None:
                    ok = False
                    break
                chain.append(nxt)
                used.add(id(nxt))
                cur = nxt
            if ok and len(chain) >= 3:
                pts = [l.startSketchPoint.geometry for l in chain]
                area = abs(sum(pts[i].x * pts[(i + 1) % len(pts)].y - pts[(i + 1) % len(pts)].x * pts[i].y
                               for i in range(len(pts)))) / 2
                out.append(_Profile(area))
        return out

    def offset(self, coll, point, distance):
        self._rec("offset", coll, point, distance)
        return ObjectCollection(self.results.get("offset", []))

    def project(self, e):
        self._rec("project", e)
        return ObjectCollection(self.results.get("project", []))

    def projectCutEdges(self, body):
        self._rec("projectCutEdges", body)
        return ObjectCollection(self.results.get("projectCutEdges", []))

    def intersectWithSketchPlane(self, coll):
        self._rec("intersectWithSketchPlane", coll)
        return ObjectCollection(self.results.get("intersectWithSketchPlane", []))

    def project2(self, coll, linked):
        self._rec("project2", coll, linked)
        return ObjectCollection(self.results.get("project2", []))

    def copy(self, coll, matrix):
        self._rec("copy", list(coll), matrix)
        new = [SketchLine(self, self._new_point(0, 0), self._new_point(1, 0)) for _ in coll]
        self._curves.extend(new)
        return ObjectCollection(new)

    def createAutoConstrainInput(self):
        return AutoConstrainInput()

    def autoConstrain(self, inp):
        self._rec("autoConstrain", inp)
        return self.results.get("autoConstrain")

    def deleteMe(self):
        self._rec("deleteMe", self)
        return self.delete_ok


def new_sketch(component, name="Sketch1", **kw):
    """A fake sketch added to ``component.sketches`` (and the design's token registry)."""
    sk = Sketch(name, component, **kw)
    component._sketches.append(sk)
    return sk


class _ComponentSketches(Collection):
    """``Component.sketches`` with ``add`` (the core fake's collection has none)."""

    def __init__(self, comp):
        super().__init__()
        self._comp = comp
        self._items = comp._sketches

    def add(self, plane, occurrence=None):
        self._comp.__dict__.setdefault("sketch_adds", []).append((plane, occurrence))
        return new_sketch(self._comp, "Sketch%d" % (len(self._items) + 1), plane=plane)


fusion.Component.sketches = property(lambda s: _ComponentSketches(s))
