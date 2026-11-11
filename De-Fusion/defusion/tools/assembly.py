"""Components, occurrences, joints, rigid groups and contact sets of the active design."""
from typing import Literal

import adsk.core
import adsk.fusion
from pydantic import BaseModel

from ..actions import action
from ..errors import AMBIGUOUS, FUSION_ERROR, INVALID_PARAMS, NOT_FOUND, UNSUPPORTED, ActionError
from ..types import (
    Angle, Body, Component, ContactSet, Edge, Face, JointOrigin, Length, Occurrence, SketchCurve,
    SketchPoint, Vertex,
)
from .common import _items

JointType = Literal["rigid", "revolute", "slider", "cylindrical", "pin_slot", "planar", "ball"]
AxisName = Literal["x", "y", "z"]
KeyPoint = Literal["center", "start", "middle", "end"]

_JT = adsk.fusion.JointTypes
JOINT_TYPES = {
    "rigid": _JT.RigidJointType,
    "revolute": _JT.RevoluteJointType,
    "slider": _JT.SliderJointType,
    "cylindrical": _JT.CylindricalJointType,
    "pin_slot": _JT.PinSlotJointType,
    "planar": _JT.PlanarJointType,
    "ball": _JT.BallJointType,
}
JOINT_TYPE_NAMES = {v: k for k, v in JOINT_TYPES.items()}
_JD = adsk.fusion.JointDirections
AXES = {"x": _JD.XAxisJointDirection, "y": _JD.YAxisJointDirection, "z": _JD.ZAxisJointDirection}
_KP = adsk.fusion.JointKeyPointTypes
KEYPOINTS = {
    "center": _KP.CenterKeyPoint,
    "start": _KP.StartKeyPoint,
    "middle": _KP.MiddleKeyPoint,
    "end": _KP.EndKeyPoint,
}


class JointGeo(BaseModel, extra="forbid"):
    face: Face | None = None
    edge: Edge | None = None
    sketch_curve: SketchCurve | None = None
    vertex: Vertex | None = None
    sketch_point: SketchPoint | None = None
    origin_of: Occurrence | None = None  # origin point of this occurrence's component
    root_origin: bool = False  # origin point of the root component
    joint_origin: JointOrigin | None = None  # an existing joint origin, used as is
    keypoint: KeyPoint = "center"  # used for face, edge, sketch_curve only
    occurrence: Occurrence | None = None  # assembly context for the entity


class JointLimitsIn(BaseModel, extra="forbid"):
    rotation_min: Angle | None = None
    rotation_max: Angle | None = None
    rotation_rest: Angle | None = None
    slide_min: Length | None = None
    slide_max: Length | None = None
    slide_rest: Length | None = None


class ContactSetCreate(BaseModel, extra="forbid"):
    name: str
    occurrences: list[Occurrence] = []
    bodies: list[Body] = []


def _occ_out(ctx, occ):
    path = occ.fullPathName
    parent = path.rpartition("+")[0] or None
    origin, x, y, z = occ.transform2.getAsCoordinateSystem()
    vec = lambda v: [v.x, v.y, v.z]  # noqa: E731
    return {
        "occurrence": ctx.ref(occ),
        "path": path,
        "parent": parent,
        "depth": path.count("+") + 1,
        "component": ctx.ref(occ.component),
        "grounded": occ.isGrounded,
        "light_bulb": occ.isLightBulbOn,
        "visible": occ.isVisible,
        "transform": {
            "origin": [ctx.length_out(c) for c in (origin.x, origin.y, origin.z)],
            "x_axis": vec(x),
            "y_axis": vec(y),
            "z_axis": vec(z),
        },
    }


def _analysis(design):
    if not design.isContactAnalysisEnabled:
        return "off"
    return "contact_sets" if design.isContactSetAnalysis else "all"


@action("assembly", access="read")
def list_components(ctx, offset: int = 0, limit: int = 100) -> dict:
    """List the design's components (root first) with body, sketch, occurrence and joint counts."""
    design = ctx.require_design()
    root = design.rootComponent
    comps = [root] + [c for c in _items(design.allComponents) if c.entityToken != root.entityToken]

    def item(c):
        is_root = c.entityToken == root.entityToken
        return {
            "component": ctx.ref(c),
            "part_number": c.partNumber,
            "is_root": is_root,
            "bodies": c.bRepBodies.count,
            "sketches": c.sketches.count,
            "occurrences": root.allOccurrencesByComponent(c).count,
            "joints": c.joints.count,
            "as_built_joints": c.asBuiltJoints.count,
        }

    return ctx.page(comps, offset, limit, build=item)


@action("assembly", access="read")
def list_occurrences(
    ctx, component: Component | None = None, recursive: bool = True, offset: int = 0, limit: int = 100
) -> dict:
    """List occurrences with path, depth, parent, state and world transform."""
    design = ctx.require_design()
    comp = component or design.rootComponent
    coll = comp.allOccurrences if recursive else comp.occurrences
    return ctx.page(_items(coll), offset, limit, build=lambda o: _occ_out(ctx, o))


@action("assembly", access="read")
def contact_get(ctx) -> dict:
    """Report the contact analysis mode and the design's contact sets."""
    design = ctx.require_design()
    sets = None
    if hasattr(design, "contactSets"):
        sets = []
        for cs in _items(design.contactSets):
            attr = next((a for a in ("occurrencesAndBodies", "entities") if hasattr(cs, a)), None)
            members = None if attr is None else ctx.refs(list(getattr(cs, attr)))
            sets.append(
                {"contact_set": ctx.ref(cs), "name": cs.name, "suppressed": cs.isSuppressed,
                 "members": members}
            )
    return {"analysis": _analysis(design), "contact_sets": sets}



@action("assembly", access="write")
def create_component(
    ctx, name: str | None = None, parent: Occurrence | None = None, bodies: list[Body] | None = None
) -> dict:
    """Create a component (optionally under a parent occurrence) and move bodies into it."""
    design = ctx.require_design()
    if bodies is not None and not bodies:
        ctx.fail(INVALID_PARAMS, "bodies must not be empty", hints=["omit bodies for an empty component"])
    if bodies and not hasattr(bodies[0], "moveToComponent"):
        ctx.fail(UNSUPPORTED, "this Fusion build cannot move bodies into a component")
    parent_comp = parent.component if parent else design.rootComponent
    occ = parent_comp.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    if parent:
        occ = occ.createForAssemblyContext(parent)
    if name:
        occ.component.name = name
    moved = [b.moveToComponent(occ) for b in bodies or []]
    return {"component": ctx.ref(occ.component), "occurrence": ctx.ref(occ), "bodies": ctx.refs(moved)}


_KEYPOINT_HINT = (
    "valid keypoints depend on the entity: faces use center; open curves use start/middle/end; "
    "circles use center"
)
_GEO_FIELDS = ("face", "edge", "sketch_curve", "vertex", "sketch_point", "joint_origin")


def _owner(kind, entity):
    if kind in ("face", "edge", "vertex"):
        return entity.body.parentComponent
    if kind in ("sketch_curve", "sketch_point"):
        return entity.parentSketch.parentComponent
    return entity.parentComponent


def _in_context(ctx, g, entity, owner):
    """The entity as seen from the root component, and its context occurrence (design 3.5 step 3)."""
    if entity.assemblyContext is not None:
        return entity, entity.assemblyContext
    if g.occurrence is not None:
        if g.occurrence.component != owner:
            ctx.fail(INVALID_PARAMS, "occurrence %r is not an occurrence of the entity's component %r"
                     % (g.occurrence.name, owner.name))
        return entity.createForAssemblyContext(g.occurrence), g.occurrence
    root = ctx.design.rootComponent
    if owner == root:
        return entity, None
    occs = _items(root.allOccurrencesByComponent(owner))
    if not occs:
        raise ActionError(NOT_FOUND, "component %r has no occurrence" % owner.name)
    if len(occs) > 1:
        raise ActionError(
            AMBIGUOUS, "component %r has %d occurrences; set geometry.occurrence" % (owner.name, len(occs)),
            candidates=ctx.refs(occs),
        )
    return entity.createForAssemblyContext(occs[0]), occs[0]


def _geometry(ctx, g):
    """JointGeo -> (JointGeometry or JointOrigin, context occurrence or None) (design 3.5)."""
    given = [k for k in _GEO_FIELDS if getattr(g, k) is not None]
    given += [k for k, on in (("origin_of", g.origin_of is not None), ("root_origin", g.root_origin)) if on]
    if len(given) != 1:
        ctx.fail(INVALID_PARAMS, "set exactly one geometry field", hints=["one of %s, origin_of, root_origin"
                                                                         % ", ".join(_GEO_FIELDS)])
    kind = given[0]
    jg = adsk.fusion.JointGeometry
    if kind == "origin_of":
        occ = g.origin_of
        point = occ.component.originConstructionPoint.createForAssemblyContext(occ)
        return jg.createByPoint(point), occ
    if kind == "root_origin":
        return jg.createByPoint(ctx.design.rootComponent.originConstructionPoint), None
    raw = getattr(g, kind)
    entity, occ = _in_context(ctx, g, raw, _owner(kind, raw))
    if kind == "joint_origin":
        return entity, occ
    kp = KEYPOINTS[g.keypoint]
    try:
        if kind == "face":
            if entity.geometry.surfaceType == adsk.core.SurfaceTypes.PlaneSurfaceType:
                return jg.createByPlanarFace(entity, None, kp), occ
            return jg.createByNonPlanarFace(entity, kp), occ
        if kind in ("edge", "sketch_curve"):
            return jg.createByCurve(entity, kp), occ
        return jg.createByPoint(entity), occ
    except Exception as exc:
        raise ActionError(FUSION_ERROR, "%s (%s)" % (exc, type(exc).__name__), hints=[_KEYPOINT_HINT]) from exc


_ROTATION_TYPES = ("revolute", "cylindrical", "pin_slot")
_SLIDE_TYPES = ("slider", "cylindrical", "pin_slot")
_ROTATION_FIELDS = ("rotation_min", "rotation_max", "rotation_rest")
_SLIDE_FIELDS = ("slide_min", "slide_max", "slide_rest")


def _check_second_axis(type, second_axis):
    if second_axis is not None and type not in ("pin_slot", "ball"):
        raise ActionError(INVALID_PARAMS, "second_axis only applies to pin_slot and ball, not %s" % type)


def _set_motion(inp, type, axis, second_axis):
    a = AXES[axis]
    if type == "rigid":
        inp.setAsRigidJointMotion()
        return
    if type in ("pin_slot", "ball"):
        b = AXES[second_axis or next(n for n in AXES if n != axis)]
        (inp.setAsPinSlotJointMotion if type == "pin_slot" else inp.setAsBallJointMotion)(a, b)
        return
    getattr(inp, {
        "revolute": "setAsRevoluteJointMotion", "slider": "setAsSliderJointMotion",
        "cylindrical": "setAsCylindricalJointMotion", "planar": "setAsPlanarJointMotion",
    }[type])(a)


def _check_limits(type, limits):
    """Reject limits the joint type has no motion for, and min >= max (before anything is created)."""
    if limits is None:
        return
    for fields, types in ((_ROTATION_FIELDS, _ROTATION_TYPES), (_SLIDE_FIELDS, _SLIDE_TYPES)):
        for f in fields:
            if getattr(limits, f) is not None and type not in types:
                raise ActionError(INVALID_PARAMS, "limits.%s does not apply to %s joints" % (f, type))
    for lo, hi, attr in (("rotation_min", "rotation_max", "rad"), ("slide_min", "slide_max", "cm")):
        a, b = getattr(limits, lo), getattr(limits, hi)
        if a is not None and b is not None and getattr(a, attr) >= getattr(b, attr):
            raise ActionError(INVALID_PARAMS, "limits.%s must be less than limits.%s" % (lo, hi))


def _apply_limits(ctx, joint, type, limits):
    """Write the given limits to the joint and return the reported limits (None if the type has none)."""
    out = {}
    for key, types, attr, fields, to_out in (
        ("rotation", _ROTATION_TYPES, "rotationLimits", _ROTATION_FIELDS, ctx.angle_out),
        ("slide", _SLIDE_TYPES, "slideLimits", _SLIDE_FIELDS, ctx.length_out),
    ):
        if type not in types:
            continue
        lim = getattr(joint.jointMotion, attr)
        reported = {}
        for field, (short, prop) in zip(fields, (("min", "Minimum"), ("max", "Maximum"), ("rest", "Rest"))):
            v = getattr(limits, field) if limits is not None else None
            if v is not None:
                setattr(lim, "is%sValueEnabled" % prop, True)
                setattr(lim, prop.lower() + "Value", v.rad if key == "rotation" else v.cm)
            enabled = getattr(lim, "is%sValueEnabled" % prop)
            reported[short] = to_out(getattr(lim, prop.lower() + "Value")) if enabled else None
        out[key] = reported
    return out or None


def _joint_out(ctx, joint, full=True):
    """The joint result (design 3.6); ``full=False`` omits offset, angle and flipped (as-built joints)."""
    one, two = joint.occurrenceOne, joint.occurrenceTwo
    out = {
        "joint": ctx.ref(joint),
        "type": JOINT_TYPE_NAMES[joint.jointMotion.jointType],
        "occurrence_one": ctx.ref(one) if one is not None else None,
        "occurrence_two": ctx.ref(two) if two is not None else None,
    }
    if full:
        out["offset"] = ctx.length_out(joint.offset.value)
        out["angle"] = ctx.angle_out(joint.angle.value)
        out["flipped"] = joint.isFlipped
    return out


@action("assembly", access="write")
def joint(
    ctx, type: JointType, geometry_one: JointGeo, geometry_two: JointGeo, offset: Length | None = None,
    angle: Angle | None = None, flip: bool = False, axis: AxisName = "z",
    second_axis: AxisName | None = None, limits: JointLimitsIn | None = None, name: str | None = None,
) -> dict:
    """Create a joint between two occurrences; geometry_one is the moving side."""
    ctx.require_design()
    _check_second_axis(type, second_axis)
    g1, occ1 = _geometry(ctx, geometry_one)
    g2, occ2 = _geometry(ctx, geometry_two)
    if occ1 == occ2:
        ctx.fail(INVALID_PARAMS, "geometry_one and geometry_two must be in different occurrences")
    _check_limits(type, limits)
    joints = ctx.design.rootComponent.joints
    inp = joints.createInput(g1, g2)
    _set_motion(inp, type, axis, second_axis)
    if offset is not None:
        inp.offset = offset.value_input()
    if angle is not None:
        inp.angle = angle.value_input()
    inp.isFlipped = flip
    j = joints.add(inp)
    if name:
        j.name = name
    out = _joint_out(ctx, j)
    out["limits"] = _apply_limits(ctx, j, type, limits)
    return out


@action("assembly", access="write")
def as_built_joint(
    ctx, type: JointType, occurrence_one: Occurrence, occurrence_two: Occurrence,
    geometry: JointGeo | None = None, axis: AxisName = "z", second_axis: AxisName | None = None,
    limits: JointLimitsIn | None = None, name: str | None = None,
) -> dict:
    """Create an as-built joint that keeps two occurrences where they are."""
    ctx.require_design()
    if occurrence_one == occurrence_two:
        ctx.fail(INVALID_PARAMS, "occurrence_one and occurrence_two must be different occurrences")
    if geometry is None and type != "rigid":
        ctx.fail(INVALID_PARAMS, "geometry required for %s" % type)
    _check_second_axis(type, second_axis)
    _check_limits(type, limits)
    g = _geometry(ctx, geometry)[0] if geometry is not None else None
    abj = ctx.design.rootComponent.asBuiltJoints
    try:
        inp = abj.createInput(occurrence_one, occurrence_two, g)
    except Exception:
        if geometry is None or geometry.joint_origin is None:
            raise
        raise ActionError(
            UNSUPPORTED, "as-built joints do not accept a joint origin on this Fusion build"
        ) from None
    _set_motion(inp, type, axis, second_axis)
    j = abj.add(inp)
    if name:
        j.name = name
    out = _joint_out(ctx, j, full=False)
    out["limits"] = _apply_limits(ctx, j, type, limits)
    return out


@action("assembly", access="write")
def rigid_group(
    ctx, occurrences: list[Occurrence], include_children: bool = True, name: str | None = None
) -> dict:
    """Group two or more occurrences so they move as one."""
    design = ctx.require_design()
    if len({o.entityToken for o in occurrences}) < 2:
        ctx.fail(INVALID_PARAMS, "a rigid group needs at least 2 distinct occurrences")
    coll = adsk.core.ObjectCollection.create()
    for o in occurrences:
        coll.add(o)
    g = design.rootComponent.rigidGroups.add(coll, include_children)
    if name:
        g.name = name
    return {"rigid_group": ctx.ref(g), "name": g.name, "occurrences": ctx.refs(_items(g.occurrences))}


@action("assembly", access="write")
def contact_set(
    ctx, analysis: Literal["off", "all", "contact_sets"] | None = None,
    create: ContactSetCreate | None = None, set: ContactSet | None = None, enabled: bool | None = None,
) -> dict:
    """Set the contact analysis mode, create a contact set, or enable/suppress one."""
    design = ctx.require_design()
    if analysis is None and create is None and set is None:
        ctx.fail(INVALID_PARAMS, "give at least one of analysis, create, set")
    if (set is None) != (enabled is None):
        ctx.fail(INVALID_PARAMS, "set and enabled must be given together")
    if create is not None:
        if len(create.occurrences) + len(create.bodies) < 2:
            ctx.fail(INVALID_PARAMS, "create needs at least 2 members in total")
        if not hasattr(design, "contactSets"):
            ctx.fail(UNSUPPORTED, "this Fusion build has no contact sets")
    if analysis is not None:
        design.isContactAnalysisEnabled = analysis != "off"
        if analysis != "off":
            design.isContactSetAnalysis = analysis == "contact_sets"
    created = None
    if create is not None:
        created = design.contactSets.add([*create.occurrences, *create.bodies])
        created.name = create.name
    if set is not None:
        set.isSuppressed = not enabled
    return {
        "analysis": _analysis(design),
        "created": ctx.ref(created) if created is not None else None,
        "updated": ctx.ref(set) if set is not None else None,
    }


# --- live scenarios (design § 4.2) ---------------------------------------------------------------------

import json  # noqa: E402

from .live_common import _do, _fresh, _lc  # noqa: E402


async def _a(session, act, params=None, units=None):
    env = await _lc().call(session, "assembly", act, params or {}, units=units)
    assert env["ok"], "assembly.%s: %s" % (act, json.dumps(env.get("error"))[:400])
    return env["result"]


async def _a_err(session, act, params, code):
    env = await _lc().call(session, "assembly", act, params)
    assert not env["ok"] and env["error"]["code"] == code, "assembly.%s expected %s, got %s" % (
        act, code, json.dumps(env)[:300])


async def _comp(session, name, **kw):
    return await _a(session, "create_component", dict(name=name, **kw))


async def _boxed(session, name, at=0):
    """A fixture box in its own component; returns the create_component result."""
    body = await _lc().box(session, 20, 20, 10)
    if at:
        await _do(session, "model", "move", {"bodies": [body], "translate": [at, 0, 0]})
    return await _comp(session, name, bodies=[body])


async def _comps(session):
    return {c["component"]["name"]: c for c in (await _a(session, "list_components"))["items"]}


async def _create_component(session):
    async with _fresh(session):
        await _comp(session, "A")
        await _comp(session, "B")
        b1, b2 = await _lc().box(session, 20, 20, 10), await _lc().box(session, 20, 20, 10)
        await _comp(session, "C", bodies=[b1, b2])
        comps = await _comps(session)
        assert {"A", "B", "C"} <= set(comps) and comps["C"]["bodies"] == 2, comps


async def _list_components(session):
    async with _fresh(session):
        await _comp(session, "A")
        await _comp(session, "B")
        page = await _a(session, "list_components")
        assert page["total"] >= 3 and page["items"][0]["is_root"], page
        comps = await _comps(session)
        assert comps["A"]["occurrences"] == 1 and comps["B"]["occurrences"] == 1, comps
        one = await _a(session, "list_components", {"limit": 1})
        assert len(one["items"]) == 1 and one["next_offset"] == 1, one


async def _list_occurrences(session):
    async with _fresh(session):
        p = await _comp(session, "P")  # the first occurrence is not moved by a joint
        a = await _comp(session, "A")
        q = await _comp(session, "Q", parent=p["occurrence"])
        occs = {o["component"]["name"]: o for o in (await _a(session, "list_occurrences"))["items"]}
        assert occs["A"]["depth"] == 1 and occs["P"]["depth"] == 1 and occs["Q"]["depth"] == 2, occs
        assert occs["Q"]["parent"] == occs["P"]["path"], occs
        flat = (await _a(session, "list_occurrences", {"recursive": False}))["items"]
        assert "Q" not in {o["component"]["name"] for o in flat}, flat
        await _a(session, "joint", {"type": "rigid", "geometry_one": {"origin_of": a["occurrence"]},
                                    "geometry_two": {"root_origin": True}, "offset": "25.4 mm"})
        inches = await _a(session, "list_occurrences", {}, units={"length": "in"})
        origin = [o for o in inches["items"] if o["component"]["name"] == "A"][0]["transform"]["origin"]
        assert all(abs(x - y) < 1e-6 for x, y in zip(origin, [0, 0, 1])), origin
        assert q["occurrence"]


_ALL_TYPES = ("rigid", "revolute", "slider", "cylindrical", "pin_slot", "planar", "ball")


async def _joint(session):
    async with _fresh(session):
        n = 0
        for t in _ALL_TYPES:
            n += 1
            one, two = await _comp(session, "J%da" % n), await _comp(session, "J%db" % n)
            params = {"type": t, "geometry_one": {"origin_of": one["occurrence"]},
                      "geometry_two": {"origin_of": two["occurrence"]}}
            if t == "pin_slot":
                params["second_axis"] = "x"
            r = await _a(session, "joint", params)
            assert r["type"] == t, (t, r)
        one, two = await _comp(session, "Lim1"), await _comp(session, "Lim2")
        r = await _a(session, "joint", {
            "type": "revolute", "geometry_one": {"origin_of": one["occurrence"]},
            "geometry_two": {"origin_of": two["occurrence"]},
            "limits": {"rotation_min": -45, "rotation_max": 90}})
        rot = r["limits"]["rotation"]
        assert abs(rot["min"] + 45) < 1e-6 and abs(rot["max"] - 90) < 1e-6, r
        b1, b2 = await _boxed(session, "Fb1"), await _boxed(session, "Fb2", at=40)
        face = [f for f in (await _do(session, "inspect", "list_faces", {"body": b1["bodies"][0]}))["items"]
                if f["surface_type"] == "plane"][0]["ref"]
        edge = [e for e in (await _do(session, "inspect", "list_edges", {"body": b2["bodies"][0]}))["items"]
                if e["curve_type"] == "line"][0]["ref"]
        r = await _a(session, "joint", {
            "type": "revolute", "geometry_one": {"face": face, "keypoint": "center"},
            "geometry_two": {"edge": edge, "keypoint": "middle"}})
        assert r["type"] == "revolute", r
        await _comp(session, "A")
        await _do(session, "script", "execute_python", {"confirm": True, "code": (
            "c = design.allComponents.itemByName('A'); "
            "ji = c.jointOrigins.createInput(adsk.fusion.JointGeometry.createByPoint(c.originConstructionPoint)); "
            "jo = c.jointOrigins.add(ji); jo.name = 'JO1'")})
        occ = [o for o in (await _a(session, "list_occurrences"))["items"] if o["component"]["name"] == "A"][0]
        r = await _a(session, "joint", {"type": "rigid", "geometry_one": {"joint_origin": {"name": "JO1"}},
                                        "geometry_two": {"root_origin": True}})
        assert r["type"] == "rigid" and occ, r


async def _transforms(session):
    return {o["component"]["name"]: o["transform"] for o in (await _a(session, "list_occurrences"))["items"]}


async def _as_built_joint(session):
    async with _fresh(session):
        a, b = await _boxed(session, "Ab1"), await _boxed(session, "Ab2", at=40)
        before = await _transforms(session)
        r = await _a(session, "as_built_joint", {"type": "rigid", "occurrence_one": a["occurrence"],
                                                 "occurrence_two": b["occurrence"]})
        assert r["type"] == "rigid", r
        assert await _transforms(session) == before
        c, d = await _boxed(session, "Ab3", at=80), await _boxed(session, "Ab4", at=120)
        r = await _a(session, "as_built_joint", {"type": "revolute", "occurrence_one": c["occurrence"],
                                                 "occurrence_two": d["occurrence"],
                                                 "geometry": {"origin_of": c["occurrence"]}})
        assert r["type"] == "revolute", r
        e, f = await _boxed(session, "Ab5", at=160), await _boxed(session, "Ab6", at=200)
        await _comp(session, "A")
        made = await _lc().call(session, "script", "execute_python", {"confirm": True, "code": (
            "c = design.allComponents.itemByName('A'); "
            "ji = c.jointOrigins.createInput(adsk.fusion.JointGeometry.createByPoint(c.originConstructionPoint)); "
            "jo = c.jointOrigins.add(ji); jo.name = 'JO1'")})
        if not made["ok"]:  # the default profile has no script tool; the joint_origin variant needs it
            assert made["error"]["code"] == "NOT_ALLOWED", made
            return
        env = await _lc().call(session, "assembly", "as_built_joint", {
            "type": "revolute", "occurrence_one": e["occurrence"], "occurrence_two": f["occurrence"],
            "geometry": {"joint_origin": {"name": "JO1"}}})
        assert env["ok"] or env["error"]["code"] == "UNSUPPORTED", env


async def _rigid_group(session):
    async with _fresh(session):
        a, b = await _comp(session, "G1"), await _comp(session, "G2")
        r = await _a(session, "rigid_group", {"occurrences": [a["occurrence"], b["occurrence"]], "name": "RG"})
        assert r["rigid_group"] and r["name"] == "RG" and len(r["occurrences"]) == 2, r


async def _contact_get(session):
    async with _fresh(session):
        r = await _a(session, "contact_get")
        assert r["analysis"] == "off" and r["contact_sets"] in ([], None), r


async def _contact_set(session):
    async with _fresh(session):
        a, b = await _boxed(session, "Cs1"), await _boxed(session, "Cs2", at=40)
        r = await _a(session, "contact_set", {"analysis": "contact_sets"})
        assert r["analysis"] == "contact_sets", r
        env = await _lc().call(session, "assembly", "contact_set", {
            "create": {"name": "C1", "occurrences": [a["occurrence"], b["occurrence"]]}})
        if not env["ok"]:
            assert env["error"]["code"] == "UNSUPPORTED", env
            assert (await _a(session, "contact_get"))["contact_sets"] is None
            return
        got = (await _a(session, "contact_get"))["contact_sets"]
        assert [s["name"] for s in got] == ["C1"] and got[0]["suppressed"] is False, got
        ref = env["result"]["created"]
        await _a(session, "contact_set", {"set": ref, "enabled": False})
        assert (await _a(session, "contact_get"))["contact_sets"][0]["suppressed"] is True


LIVE_SCENARIOS = [(f.__name__[1:], "assembly." + f.__name__[1:], f) for f in (
    _create_component, _list_components, _list_occurrences, _joint, _as_built_joint, _rigid_group,
    _contact_get, _contact_set)]
