"""Shared helpers for the model tool modules (design § 3)."""
import contextlib

import adsk.core
import adsk.fusion

from .. import types
from ..errors import INVALID_PARAMS, UNSUPPORTED, ActionError

SKETCH_CURVES = (
    "SketchLine", "SketchArc", "SketchCircle", "SketchEllipse", "SketchEllipticalArc",
    "SketchFittedSpline", "SketchFixedSpline", "SketchConicCurve", "SketchControlPointSpline",
)
GROUPS = {"bodies": ("BRepBody",), "features": ("Feature",), "faces": ("BRepFace",)}


def val(ctx, x, kind):
    """``x`` as a ``Value``: parameters arrive resolved, but declared defaults (``0``, ``"5 mm"``) do not."""
    if isinstance(x, types.Value):
        return x
    return types.ScalarSpec(kind, x).resolve(types.Resolver(ctx, path=kind))


def vi(ctx, x, kind="length"):
    """``ValueInput.createByString`` of the expression of ``x`` (design § 1, "vi(x)")."""
    return val(ctx, x, kind).value_input()


def coll(items):
    c = adsk.core.ObjectCollection.create()
    for i in items:
        c.add(i)
    return c


def same(a, b):
    """True when two API wrappers are the same entity (wrappers are recreated per access)."""
    return a is b or (
        getattr(a, "entityToken", None) is not None
        and getattr(a, "entityToken", None) == getattr(b, "entityToken", None)
    )


def kind(entity):
    name = entity.objectType.split("::")[-1]
    if hasattr(entity, "timelineObject") and hasattr(entity, "healthState") and name.endswith("Feature"):
        return "Feature"  # construction planes and axes also have a timeline object and a health state
    return name


def owner(entity):
    """The component that owns ``entity`` (design § 3.1)."""
    for path in (("parentComponent",), ("component",), ("body", "parentComponent"), ("parentSketch", "parentComponent"), ("parent",)):
        cur = entity
        for attr in path:
            cur = getattr(cur, attr, None)
            if cur is None:
                break
        if cur is not None:
            return cur
    return None


def same_owner(ctx, entities, param):
    comps = []
    for e in entities:
        c = owner(e)
        if not any(same(c, k) for k in comps):
            comps.append(c)
    if len(comps) > 1:
        ctx.fail(INVALID_PARAMS, "all %s must belong to one component" % param)
    return comps[0]


def _matches(k, allowed):
    return k in allowed or ("SketchCurve" in allowed and k in SKETCH_CURVES)


def expect(ctx, items, allowed, param):
    for item in items:
        k = kind(item)
        if not _matches(k, allowed):
            ctx.fail(INVALID_PARAMS, "%s accepts %s; got %s" % (param, ", ".join(allowed), k))


def uniform(ctx, items, groups, param):
    """The name of the one group of ``groups`` that every item falls in."""
    kinds = [kind(i) for i in items]
    for name in groups:
        if all(k in GROUPS[name] for k in kinds):
            return name
    ctx.fail(
        INVALID_PARAMS,
        "%s must all be of one kind (%s); got %s" % (param, ", ".join(groups), ", ".join(sorted(set(kinds)))),
    )


def require_api(ctx, obj, attr, what):
    if not hasattr(obj, attr):
        ctx.fail(
            UNSUPPORTED,
            "%s is not available in this Fusion build" % what,
            hints=["this API member is missing from the installed Fusion version"],
        )


def participants(ctx, inp, bodies):
    if bodies is not None:
        require_api(ctx, inp, "participantBodies", "participantBodies")
        inp.participantBodies = list(bodies)


def positive(ctx, value, param):
    if value.number <= 0:
        ctx.fail(INVALID_PARAMS, "%s must be > 0" % param)


_ORIGIN_ROLES = ("xYConstructionPlane", "xZConstructionPlane", "yZConstructionPlane")


def resolve_plane(ctx, plane, comp):
    """``plane`` as a Fusion plane: a declared default such as ``"XY"`` arrives unresolved."""
    if isinstance(plane, str):
        return types.PlaneSpec(plane).resolve(types.Resolver(ctx, {"component": comp}, "plane"))
    return plane


def origin_plane(comp, plane):
    """``plane``, or ``comp``'s plane of the same role when ``plane`` is another component's origin plane."""
    home = getattr(plane, "component", None)  # construction planes: `.component`, not `.parentComponent`
    if home is None:
        return plane
    for role in _ORIGIN_ROLES:
        if same(getattr(home, role), plane):
            return getattr(comp, role)
    return plane


@contextlib.contextmanager
def activated(ctx, comp):
    """Activate ``comp`` for the block and restore the previous activation afterwards."""
    design = ctx.design
    if same(comp, design.activeComponent):
        yield
        return
    previous = design.activeOccurrence
    root = design.rootComponent
    if same(comp, root):
        design.activateRootComponent()
    else:
        occs = root.allOccurrencesByComponent(comp)
        if occs.count == 0:
            ctx.fail(INVALID_PARAMS, "component has no occurrence")
        occs.item(0).activate()
    try:
        yield
    finally:
        if previous is None:
            design.activateRootComponent()
        else:
            previous.activate()


def finish(ctx, feat, name, **extra):
    if name:
        feat.name = name
    out = {"feature": ctx.ref(feat), "bodies": [ctx.ref(b) for b in feat.bodies]}
    out.update(extra)
    return out


def cells_hint(ctx, cells):
    hints = []
    for i in range(cells.count):
        box = cells.item(i).cellBody.boundingBox
        lo, hi = box.minPoint, box.maxPoint
        c = [ctx.length_out((a + b) / 2) for a, b in ((lo.x, hi.x), (lo.y, hi.y), (lo.z, hi.z))]
        hints.append("cell %d: center %s" % (i, [round(v, 6) for v in c]))
    return hints
