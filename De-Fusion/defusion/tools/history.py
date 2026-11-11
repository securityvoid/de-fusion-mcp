"""History tool: the parametric timeline, position snapshots, guarded undo and recipes."""
import math
import typing
from typing import Any, Literal

import adsk.core
import adsk.fusion

from .. import actions
from ..actions import action
from ..context import Ctx
from ..errors import (
    AMBIGUOUS,
    FUSION_ERROR,
    INVALID_PARAMS,
    NOT_FOUND,
    UNSUPPORTED,
    WRONG_DESIGN_TYPE,
    ActionError,
)
from .. import types
from ..types import TimelineItem

ItemArg = int | TimelineItem

# --- shared helpers (design § 2) -----------------------------------------------------------------


def _guarded(fn):
    """``fn()``, or ``None`` when the read raises (design § 2.2)."""
    try:
        return fn()
    except Exception:
        return None


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _item(tl, ref):
    """A timeline item from an index or an already resolved item (design § 2.1)."""
    if not _is_int(ref):
        return ref
    if not 0 <= ref < tl.count:
        raise ActionError(INVALID_PARAMS, "timeline index %d out of range 0..%d" % (ref, tl.count - 1))
    return tl.item(ref)


def _health(obj):
    states = adsk.fusion.FeatureHealthStates
    names = (
        (states.HealthyFeatureHealthState, "healthy"),
        (states.WarningFeatureHealthState, "warning"),
        (states.ErrorFeatureHealthState, "error"),
        (states.UnknownFeatureHealthState, "unknown"),
    )
    value = obj.healthState
    return next((n for s, n in names if value == s), None)


def _type_name(obj):
    if obj.isGroup:
        return "TimelineGroup"
    return obj.entity.objectType.split("::")[-1]


def _item_json(ctx, obj):
    """JSON for a timeline object (design § 2.2); each read is guarded on its own."""
    is_group = _guarded(lambda: bool(obj.isGroup))
    return {
        "index": _guarded(lambda: obj.index),
        "name": _guarded(lambda: obj.name),
        "type": _guarded(lambda: _type_name(obj)),
        "entity": None if is_group else _guarded(lambda: ctx.ref(obj.entity)),
        "is_group": is_group,
        "suppressed": _guarded(lambda: obj.isSuppressed),
        "rolled_back": _guarded(lambda: obj.isRolledBack),
        "health": _guarded(lambda: _health(obj)),
        "message": _guarded(lambda: obj.errorOrWarningMessage or None),
        "group": _guarded(lambda: obj.parentGroup.index if obj.parentGroup is not None else None),
    }


def _with_collapsed(obj, fn):
    """Run ``fn``; when it raises inside an expanded timeline group, collapse the group and retry
    once (design § 2.3)."""
    try:
        return fn()
    except Exception as exc:
        group = _guarded(lambda: obj.parentGroup)
        if group is None or group.isCollapsed:
            raise
        group.isCollapsed = True
        try:
            return fn()
        except Exception as exc2:
            raise ActionError(
                FUSION_ERROR, str(exc2), hints=["item is inside timeline group %s" % group.name]
            ) from exc


def _timeline(ctx):
    return ctx.require_parametric().timeline


def _snapshots(ctx):
    design = ctx.require_parametric()
    if not hasattr(design, "snapshots"):
        raise ActionError(UNSUPPORTED, "this Fusion version has no Design.snapshots")
    return design.snapshots


def _need(obj, member, owner):
    """UNSUPPORTED naming ``owner.member`` when the Fusion object lacks it (design § 2.4)."""
    if not hasattr(obj, member):
        raise ActionError(UNSUPPORTED, "this Fusion version has no %s.%s" % (owner, member))


def _ok(result, what):
    """Fusion calls that return False instead of raising (design § 2.4)."""
    if result is False:
        raise ActionError(FUSION_ERROR, "%s returned false" % what)
    return result


def _snapshot(snaps, ref):
    """A snapshot by index or exact name (design § 2.5)."""
    if _is_int(ref):
        s = snaps.item(ref) if 0 <= ref < snaps.count else None
        if s is None:
            raise ActionError(INVALID_PARAMS, "snapshot index %d out of range 0..%d" % (ref, snaps.count - 1))
        return s
    found = [(i, snaps.item(i)) for i in range(snaps.count)]
    found = [(i, s) for i, s in found if s.name == ref]
    if not found:
        raise ActionError(NOT_FOUND, "no snapshot named %r" % ref)
    if len(found) > 1:
        raise ActionError(
            AMBIGUOUS,
            "%d snapshots are named %r; use an index" % (len(found), ref),
            candidates=[{"index": i, "name": s.name} for i, s in found],
        )
    return found[0][1]


def _snapshot_json(i, s):
    return {
        "index": i,
        "name": s.name,
        "timeline_index": _guarded(lambda: s.timelineObject.index),
    }


# --- read actions (design § 3) -------------------------------------------------------------------


def _ordered_objects(tl):
    """Every timeline item, with each group whose index is not among them inserted in index order."""
    objs = [tl.item(i) for i in range(tl.count)]
    if hasattr(tl, "timelineGroups"):
        seen = {o.index for o in objs}
        for g in tl.timelineGroups:
            if g.index not in seen:
                pos = next((k for k, o in enumerate(objs) if o.index > g.index), len(objs))
                objs.insert(pos, g)
    return objs


@action("history", access="read", name="list")
def list_items(ctx: Ctx, offset: int = 0, limit: int = 100) -> dict:
    """List timeline items with type, entity Ref, suppression, health and group, plus the marker."""
    tl = _timeline(ctx)
    page = ctx.page(_ordered_objects(tl), offset, limit, build=lambda o: _item_json(ctx, o))
    return {**page, "marker": tl.markerPosition, "count": tl.count}


@action("history", access="read")
def snapshot_list(ctx: Ctx, offset: int = 0, limit: int = 100) -> dict:
    """List position snapshots and whether uncaptured position changes are pending."""
    snaps = _snapshots(ctx)
    page = ctx.page(
        range(snaps.count), offset, limit, build=lambda i: _snapshot_json(i, snaps.item(i))
    )
    return {**page, "pending": snaps.hasPendingSnapshot}


# --- write actions (design § 4) ------------------------------------------------------------------

_MARKER_MOVES = {
    "begin": "moveToBeginning",
    "end": "moveToEnd",
    "next": "movetoNextStep",  # lower-case "t" is Fusion's spelling
    "prev": "moveToPreviousStep",
}


@action("history", access="write", timeout=60)
def set_marker(ctx: Ctx, position: Literal["begin", "end", "next", "prev"] | int | TimelineItem) -> dict:
    """Move the timeline marker to begin, end, next, prev, a marker position or before an item."""
    tl = _timeline(ctx)
    if isinstance(position, str):
        method = _MARKER_MOVES[position]
        _need(tl, method, "Timeline")
        getattr(tl, method)()  # false at the begin/end boundary leaves the marker, not an error
    elif _is_int(position):
        if not 0 <= position <= tl.count:
            raise ActionError(
                INVALID_PARAMS, "marker position %d out of range 0..%d" % (position, tl.count)
            )
        tl.markerPosition = position
    else:
        _ok(_with_collapsed(position, lambda: position.rollTo(True)), "TimelineObject.rollTo")
    return {"marker": tl.markerPosition, "count": tl.count}


@action("history", access="write", timeout=60)
def reorder(
    ctx: Ctx, item: ItemArg, before: Literal["end"] | ItemArg, dry_run: bool = False
) -> dict:
    """Move a timeline item before another item or to the end; dry_run only checks feasibility."""
    tl = _timeline(ctx)
    obj = _item(tl, item)
    if isinstance(before, str):
        target = -1
    else:
        target = _item(tl, before).index
        if target == obj.index:
            raise ActionError(INVALID_PARAMS, "item and before are the same timeline item")
    ok = bool(obj.canReorder(target))
    if dry_run:
        return {"can_reorder": ok, "moved": False, "item": _item_json(ctx, obj)}
    if not ok:
        raise ActionError(
            INVALID_PARAMS,
            "cannot move %s before %s" % (obj.name, "the end" if target == -1 else target),
            hints=["a feature cannot move before the items it depends on"],
        )
    old = obj.index
    _ok(_with_collapsed(obj, lambda: obj.reorder(target)), "TimelineObject.reorder")
    # live: canReorder can be true and reorder a silent no-op (an extrude before its sketch), so report the real position
    return {"can_reorder": True, "moved": obj.index != old, "item": _item_json(ctx, obj)}


@action("history", access="write", timeout=60)
def set_suppressed(ctx: Ctx, item: ItemArg, suppressed: bool) -> dict:
    """Suppress or unsuppress a timeline item."""
    obj = _item(_timeline(ctx), item)
    if obj.isGroup:
        raise ActionError(INVALID_PARAMS, "timeline groups cannot be suppressed; suppress their items")
    _with_collapsed(obj, lambda: setattr(obj, "isSuppressed", suppressed))
    return {"item": _item_json(ctx, obj)}


@action("history", access="write")
def rename(ctx: Ctx, item: ItemArg, name: str) -> dict:
    """Rename a timeline item (and its entity)."""
    obj = _item(_timeline(ctx), item)
    if not name.strip():
        raise ActionError(INVALID_PARAMS, "name must not be empty")
    _with_collapsed(obj, lambda: setattr(obj, "name", name))
    return {"item": _item_json(ctx, obj)}


def _members(tl, group):
    """The items inside ``group``; a collapsed group hides them from ``tl.item`` so ask the group."""
    return [group.item(i) for i in range(group.count)]


def _member_indices(group):
    """Timeline indices of a group's items; Fusion reads them only while the group is expanded."""
    collapsed = group.isCollapsed
    group.isCollapsed = False
    try:
        return [group.item(i).index for i in range(group.count)]
    finally:
        group.isCollapsed = collapsed


@action("history", access="write")
def group_add(ctx: Ctx, start: ItemArg, end: ItemArg, name: str | None = None) -> dict:
    """Group a contiguous range of timeline items, with an optional group name."""
    tl = _timeline(ctx)
    _need(tl, "timelineGroups", "Timeline")
    first, last = _item(tl, start), _item(tl, end)
    if first.index > last.index:
        raise ActionError(
            INVALID_PARAMS, "start (%d) must not come after end (%d)" % (first.index, last.index)
        )
    g = tl.timelineGroups.add(first.index, last.index)
    if g is None:
        raise ActionError(FUSION_ERROR, "range cannot be grouped")
    if name:
        g.name = name
    return {"group": _item_json(ctx, g), "members": _member_indices(g)}


# --- destructive actions (design § 5) --------------------------------------------------------------


@action("history", access="destructive", timeout=60)
def group_delete(ctx: Ctx, group: ItemArg, delete_contents: bool = False) -> dict:
    """Delete a timeline group, keeping its items unless delete_contents is true."""
    tl = _timeline(ctx)
    _need(tl, "timelineGroups", "Timeline")
    obj = _item(tl, group)
    if not obj.isGroup:
        raise ActionError(INVALID_PARAMS, "timeline item %s is not a group" % obj.name)
    g = adsk.fusion.TimelineGroup.cast(obj)
    name, members = g.name, [o.name for o in _members(tl, g)]
    _ok(g.deleteMe(delete_contents), "TimelineGroup.deleteMe")
    return {"deleted": name, "contents_deleted": delete_contents, "members": members}


@action("history", access="destructive", timeout=60)
def delete_after_marker(ctx: Ctx) -> dict:
    """Delete every timeline item after the marker and return their names."""
    tl = _timeline(ctx)
    names = [tl.item(i).name for i in range(tl.markerPosition, tl.count)]
    if names:
        _ok(tl.deleteAllAfterMarker(), "Timeline.deleteAllAfterMarker")
    return {"deleted": names, "count": tl.count}


# --- position snapshots (design § 4.6, § 4.7, § 5.3, § 5.4) ---------------------------------------------


@action("history", access="write")
def snapshot_add(ctx: Ctx, name: str | None = None) -> dict:
    """Capture pending occurrence position changes as a snapshot, with an optional name."""
    snaps = _snapshots(ctx)
    _need(snaps, "hasPendingSnapshot", "Snapshots")
    _need(snaps, "add", "Snapshots")
    if not snaps.hasPendingSnapshot:
        raise ActionError(INVALID_PARAMS, "no pending position changes to capture")
    s = snaps.add()
    if name:
        s.name = name
    return {"snapshot": _snapshot_json(snaps.count - 1, s)}


@action("history", access="write")
def snapshot_rename(ctx: Ctx, snapshot: int | str, name: str) -> dict:
    """Rename a position snapshot addressed by index or exact name."""
    snaps = _snapshots(ctx)
    s = _snapshot(snaps, snapshot)
    index = snapshot if _is_int(snapshot) else next(i for i in range(snaps.count) if snaps.item(i).name == snapshot)
    s.name = name
    return {"snapshot": _snapshot_json(index, s)}


@action("history", access="destructive")
def snapshot_revert_pending(ctx: Ctx) -> dict:
    """Discard uncaptured occurrence position changes."""
    snaps = _snapshots(ctx)
    _need(snaps, "hasPendingSnapshot", "Snapshots")
    _need(snaps, "revertPendingSnapshot", "Snapshots")
    if not snaps.hasPendingSnapshot:
        return {"reverted": False}
    _ok(snaps.revertPendingSnapshot(), "Snapshots.revertPendingSnapshot")
    return {"reverted": True}


@action("history", access="destructive")
def snapshot_delete(ctx: Ctx, snapshot: int | str) -> dict:
    """Delete a position snapshot addressed by index or exact name."""
    snaps = _snapshots(ctx)
    s = _snapshot(snaps, snapshot)
    _need(s, "deleteMe", "Snapshot")
    name = s.name
    _ok(s.deleteMe(), "Snapshot.deleteMe")
    return {"deleted": name, "count": snaps.count}


# --- undo (design § 4.8) ---------------------------------------------------------------------------------


def _transaction(ctx, op):
    """Run Fusion's ``Transaction.Undo`` / ``Transaction.Redo`` text command (``UndoCommand.execute()`` is a no-op
    from the API); it acts synchronously on the active document's transaction chain."""
    ctx.app.executeTextCommand("Transaction." + op)


def _current_design(ctx):
    """The design re-read from the document; the pre-undo object may be stale."""
    product = ctx.app.activeDocument.products.itemByProductType("DesignProductType")
    return adsk.fusion.Design.cast(product)


@action("history", access="write", timeout=60, undo_group=False)
def undo(ctx: Ctx) -> dict:
    """Undo the last operation; refuses (and redoes) an undo that would drop the timeline."""
    design = ctx.require_design()
    kinds = adsk.fusion.DesignTypes
    before = design.designType
    _transaction(ctx, "Undo")
    after = _current_design(ctx).designType
    if before == kinds.ParametricDesignType and after == kinds.DirectDesignType:
        _transaction(ctx, "Redo")
        restored = _current_design(ctx).designType == kinds.ParametricDesignType
        raise ActionError(
            WRONG_DESIGN_TYPE,
            "undo would convert the design to direct modelling and drop the timeline; "
            + ("it was redone" if restored else "redo did not restore parametric mode"),
            hints=["remove the feature with history.set_suppressed or a delete action instead"],
        )
    kind = "parametric" if after == kinds.ParametricDesignType else "direct"
    return {"undone": True, "design_type": kind}


# --- recipes: dump (design § 6, § 7) ------------------------------------------------------------------


class _Unmapped(Exception):
    """A timeline item the recipe cannot express; the message becomes the unknown step's reason."""


def _all_bodies(design):
    """Every B-Rep body of every component, in component order then body order."""
    out, seen = [], set()
    for comp in [design.rootComponent] + list(design.allComponents):
        if comp.entityToken not in seen:
            seen.add(comp.entityToken)
            out.extend(comp.bRepBodies)
    return out


def _model_parameters(design, entity):
    """The model parameters ``entity`` created (matched by ``createdBy``)."""
    token = entity.entityToken
    return [
        p for p in design.allParameters
        if isinstance(p, adsk.fusion.ModelParameter) and _guarded(lambda: p.createdBy.entityToken) == token
    ]


def _source(obj):
    return {"index": _guarded(lambda: obj.index), "name": _guarded(lambda: obj.name),
            "type": _guarded(lambda: _type_name(obj))}


def _unknown(ctx, obj, reason):
    """The step for an item that cannot be replayed (design § 7.5); never raises."""
    params = _guarded(lambda: {p.name: p.expression for p in _model_parameters(ctx.design, obj.entity)})
    src = _source(obj)
    return {
        "kind": "unknown", "type": src["type"], "name": src["name"],
        "parameters": params or {}, "reason": reason, "source": src,
    }


def _unnamed(entity):
    return _Unmapped("unnamed reference: %s" % type(entity).__name__)


def _body_ref(body):
    if getattr(body, "assemblyContext", None) is not None:
        raise _unnamed(body)
    return {"name": body.name}


def _index_ref(entity, seq_name):
    """``{"body": {"name"}, "index"}`` for a face or edge: its position in the body's sequence."""
    body = entity.body
    if getattr(entity, "assemblyContext", None) is not None or getattr(body, "assemblyContext", None) is not None:
        raise _unnamed(entity)
    for i, e in enumerate(getattr(body, seq_name)):
        if e.entityToken == entity.entityToken:
            return {"body": {"name": body.name}, "index": i}
    raise _unnamed(entity)


def _face_ref(face):
    return _index_ref(face, "faces")


def _edge_ref(edge):
    return _index_ref(edge, "edges")


def _origin_plane_name(plane):
    comp = _guarded(lambda: plane.component)
    if comp is None:
        return None
    for name, p in (("XY", comp.xYConstructionPlane), ("XZ", comp.xZConstructionPlane),
                    ("YZ", comp.yZConstructionPlane)):
        if p.entityToken == plane.entityToken:
            return name
    return None


def _plane_form(plane):
    """Plane form of core ``Plane``: origin plane name, construction plane name ref or face ref."""
    if isinstance(plane, adsk.fusion.BRepFace):
        return _face_ref(plane)
    if isinstance(plane, adsk.fusion.ConstructionPlane):
        return _origin_plane_name(plane) or {"name": plane.name}
    raise _unnamed(plane)


def _sketch_inputs(ctx, sketch):
    """``sketch.create`` now (marker before the sketch) and ``sketch.replay`` after it (§ 7.2)."""
    from . import sketch as sketch_tool

    create = {"tool": "sketch", "action": "create", "params": {"plane": _plane_form(sketch.referencePlane)}}

    def replay():
        return {"tool": "sketch", "action": "replay",
                "params": {"sketch": {"name": sketch.name},
                           "description": sketch_tool.describe(ctx, sketch)}}

    return [create, replay]


# --- value formatting and reference forms (design § 6, § 7.3) -----------------------------------------


def _fixed_length(ctx, cm):
    return "%s %s" % (format(ctx.length_out(cm), ".10g"), ctx.output.length_unit)


def _fixed_angle(ctx, rad):
    return "%s %s" % (format(ctx.angle_out(rad), ".10g"), ctx.output.angle_unit)


def _keeps_expression(p):
    """True when ``p.expression`` is replayable: its dependencies exist and are all user parameters."""
    try:
        deps = list(p.dependencyParameters)
    except AttributeError:
        return False
    return not any(isinstance(d, adsk.fusion.ModelParameter) for d in deps)


def _L(ctx, p):
    return p.expression if _keeps_expression(p) else _fixed_length(ctx, p.value)


def _A(ctx, p):
    return p.expression if _keeps_expression(p) else _fixed_angle(ctx, p.value)


def _N(p):
    return p.expression if _keeps_expression(p) else int(round(p.value))


def _operation(feature):
    """The core ``Operation`` literal for ``feature.operation``."""
    for literal in typing.get_args(types.Operation):
        if types.adsk_enum(literal) == feature.operation:
            return literal
    raise _Unmapped("operation %r has no Operation literal" % (feature.operation,))


def _require(cond, reason):
    if not cond:
        raise _Unmapped(reason)


def _name_ref(entity):
    if getattr(entity, "assemblyContext", None) is not None:
        raise _unnamed(entity)
    return {"name": entity.name}


def _entity_refs(entities):
    """Name refs for body or feature inputs (patterns, mirror, move)."""
    out = []
    for e in entities:
        _require(
            isinstance(e, (adsk.fusion.BRepBody, adsk.fusion.Feature)),
            "inputEntities are not bodies or features: %s" % type(e).__name__,
        )
        out.append(_name_ref(e))
    return out


def _origin_axis_name(axis):
    comp = _guarded(lambda: axis.component)
    if comp is None:
        return None
    for name, a in (("X", comp.xConstructionAxis), ("Y", comp.yConstructionAxis),
                    ("Z", comp.zConstructionAxis)):
        if a.entityToken == axis.entityToken:
            return name
    return None


def _axis_form(axis):
    """Axis form of core ``Axis``: origin axis name, construction axis name ref or edge ref."""
    if isinstance(axis, adsk.fusion.BRepEdge):
        return _edge_ref(axis)
    if isinstance(axis, adsk.fusion.ConstructionAxis):
        return _origin_axis_name(axis) or {"name": axis.name}
    raise _unnamed(axis)


def _profile_index(profile):
    sketch = profile.parentSketch
    props = profile.areaProperties()
    area, c = props.area, props.centroid
    for i, q in enumerate(sketch.profiles):
        qp = q.areaProperties()
        qc = qp.centroid
        if (abs(qp.area - area) <= 1e-9 and abs(qc.x - c.x) <= 1e-7 and abs(qc.y - c.y) <= 1e-7
                and abs(qc.z - c.z) <= 1e-7):
            return sketch, i
    raise _unnamed(profile)


def _profile_ref(profile):
    """``{"sketch": {"name"}, "index": i}``; a collection of profiles gives an index list."""
    if isinstance(profile, adsk.fusion.Profile):
        sketch, i = _profile_index(profile)
        return {"sketch": _name_ref(sketch), "index": i}
    found = [_profile_index(p) for p in profile]
    _require(found, "feature has no profile")
    _require(len({s.entityToken for s, _ in found}) == 1, "profiles come from several sketches")
    return {"sketch": _name_ref(found[0][0]), "index": [i for _, i in found]}


def _point_fixed(ctx, point):
    return [_fixed_length(ctx, v) for v in (point.x, point.y, point.z)]


# --- feature readers (design § 7.3) --------------------------------------------------------------------


def _read_extrude(ctx, f):
    one = f.extentOne
    _require(isinstance(one, adsk.fusion.DistanceExtentDefinition),
             "extrude extent is not a plain distance")
    _require(_guarded(lambda: f.extentTwo) is None, "extrude has a second extent")
    _require(f.taperAngleOne.value == 0, "extrude has a taper angle")
    return [{"tool": "model", "action": "extrude",
             "params": {"profile": _profile_ref(f.profile), "distance": _L(ctx, one.distance),
                        "operation": _operation(f)}}]


def _read_revolve(ctx, f):
    extent = f.extentDefinition
    _require(isinstance(extent, adsk.fusion.AngleExtentDefinition), "revolve extent is not an angle")
    params = {"profile": _profile_ref(f.profile), "axis": _axis_form(f.axis),
              "angle": _A(ctx, extent.angle)}
    if extent.isSymmetric:
        params["extent"] = "symmetric"
    params["operation"] = _operation(f)
    return [{"tool": "model", "action": "revolve", "params": params}]


def _single_edge_set(f, kind):
    sets = list(f.edgeSets)
    _require(len(sets) == 1 and isinstance(sets[0], kind),
             "%s needs exactly one %s edge set" % (type(f).__name__, kind.__name__))
    return sets[0]


def _read_fillet(ctx, f):
    es = _single_edge_set(f, adsk.fusion.ConstantRadiusFilletEdgeSet)
    return [{"tool": "model", "action": "fillet",
             "params": {"edges": [_edge_ref(e) for e in es.edges], "radius": _L(ctx, es.radius)}}]


def _read_chamfer(ctx, f):
    es = _single_edge_set(f, adsk.fusion.EqualDistanceChamferEdgeSet)
    return [{"tool": "model", "action": "chamfer",
             "params": {"edges": [_edge_ref(e) for e in es.edges], "distance": _L(ctx, es.distance)}}]


def _read_shell(ctx, f):
    faces = list(f.inputEntities)
    _require(all(isinstance(x, adsk.fusion.BRepFace) for x in faces), "shell inputs are not faces")
    return [{"tool": "model", "action": "shell",
             "params": {"faces": [_face_ref(x) for x in faces], "thickness": _L(ctx, f.insideThickness)}}]


def _read_rect_pattern(ctx, f):
    params = {"entities": _entity_refs(f.inputEntities),
              "direction_one": _axis_form(f.directionOneEntity),
              "quantity_one": _N(f.quantityOne), "distance_one": _L(ctx, f.distanceOne),
              "distance_type": "extent"
              if f.patternDistanceType == adsk.fusion.PatternDistanceType.ExtentPatternDistanceType
              else "spacing"}
    two = _guarded(lambda: f.directionTwoEntity)
    if two is not None:
        params.update(direction_two=_axis_form(two), quantity_two=_N(f.quantityTwo),
                      distance_two=_L(ctx, f.distanceTwo))
    return [{"tool": "model", "action": "rect_pattern", "params": params}]


def _read_circ_pattern(ctx, f):
    return [{"tool": "model", "action": "circ_pattern",
             "params": {"entities": _entity_refs(f.inputEntities), "axis": _axis_form(f.axis),
                        "quantity": _N(f.quantity), "total_angle": _A(ctx, f.totalAngle),
                        "symmetric": f.isSymmetric}}]


def _read_mirror(ctx, f):
    return [{"tool": "model", "action": "mirror",
             "params": {"entities": _entity_refs(f.inputEntities), "plane": _plane_form(f.mirrorPlane)}}]


def _read_combine(ctx, f):
    return [{"tool": "model", "action": "combine",
             "params": {"target": _name_ref(f.targetBody),
                        "tools": [_name_ref(b) for b in f.toolBodies], "operation": _operation(f),
                        "keep_tools": f.isKeepToolBodies}}]


def _read_hole(ctx, f):
    _require(f.holeType == adsk.fusion.HoleTypes.SimpleHoleType, "hole is not a simple hole")
    _require(isinstance(f.extentDefinition, adsk.fusion.DistanceExtentDefinition),
             "hole extent is not a distance")
    pos = _guarded(lambda: f.holePositionDefinition)
    _require(isinstance(pos, adsk.fusion.PointHolePositionDefinition),
             "hole position is not a single point")
    _require(isinstance(pos.planarEntity, adsk.fusion.BRepFace), "hole is not placed on a face")
    return [{"tool": "model", "action": "hole",
             "params": {"face": _face_ref(pos.planarEntity), "point": _point_fixed(ctx, pos.point),
                        "diameter": _L(ctx, f.holeDiameter),
                        "depth": _L(ctx, f.extentDefinition.distance),
                        "tip_angle": _A(ctx, f.tipAngle)}}]


def _read_thread(ctx, f):
    info = f.threadInfo
    inputs = _guarded(lambda: list(f.inputEntities))
    faces = inputs if inputs is not None else [f.inputCylindricalFace]
    params = {"faces": [_face_ref(x) for x in faces], "thread_type": info.threadType,
              "designation": info.threadDesignation, "thread_class": info.threadClass,
              "internal": info.isInternal, "modeled": f.isModeled, "full_length": f.isFullLength}
    if hasattr(info, "isRightHanded"):
        params["right_handed"] = info.isRightHanded
    if not f.isFullLength:
        params["length"] = _L(ctx, f.threadLength)
        params["offset"] = _L(ctx, f.threadOffset)
    return [{"tool": "model", "action": "thread", "params": params}]


def _read_construction_plane(ctx, plane):
    d = plane.definition
    if isinstance(d, adsk.fusion.ConstructionPlaneOffsetDefinition):
        params = {"method": "offset", "plane": _plane_form(d.planarEntity), "offset": _L(ctx, d.offset)}
    elif isinstance(d, adsk.fusion.ConstructionPlaneAtAngleDefinition):
        params = {"method": "angle", "axis": _axis_form(d.linearEntity),
                  "plane": _plane_form(d.planarEntity), "angle": _A(ctx, d.angle)}
    elif isinstance(d, adsk.fusion.ConstructionPlaneMidplaneDefinition):
        params = {"method": "midplane", "plane": _plane_form(d.planarEntityOne),
                  "plane_two": _plane_form(d.planarEntityTwo)}
    else:
        raise _Unmapped("unsupported construction plane definition: %s" % type(d).__name__)
    return [{"tool": "model", "action": "construction_plane", "params": params}]


def _read_construction_axis(ctx, axis):
    d = axis.definition
    if isinstance(d, adsk.fusion.ConstructionAxisByLineDefinition):
        _require(isinstance(d.linearEntity, adsk.fusion.BRepEdge), "axis is not defined by an edge")
        params = {"method": "edge", "edge": _edge_ref(d.linearEntity)}
    elif isinstance(d, adsk.fusion.ConstructionAxisTwoPlaneDefinition):
        params = {"method": "two_planes", "plane": _plane_form(d.planarEntityOne),
                  "plane_two": _plane_form(d.planarEntityTwo)}
    else:
        raise _Unmapped("unsupported construction axis definition: %s" % type(d).__name__)
    return [{"tool": "model", "action": "construction_axis", "params": params}]


_EPS = 1e-9
_AXES = {(1, 0, 0): "X", (0, 1, 0): "Y", (0, 0, 1): "Z"}


def _rotation_axis(R, theta):
    """Unit rotation axis of the 3x3 matrix ``R`` rotating by ``theta`` (design § 7.3, move)."""
    if abs(theta - math.pi) < 1e-6:
        cols = [[R[r][c] + (1.0 if r == c else 0.0) for r in range(3)] for c in range(3)]
        col = max(cols, key=lambda v: sum(x * x for x in v))
        n = math.sqrt(sum(x * x for x in col))
        return [x / n for x in col]
    k = 2.0 * math.sin(theta)
    return [(R[2][1] - R[1][2]) / k, (R[0][2] - R[2][0]) / k, (R[1][0] - R[0][1]) / k]


def _read_move(ctx, f):
    _require(hasattr(f, "transform"), "move feature has no transform")
    a = list(f.transform.asArray())
    R = [[a[0], a[1], a[2]], [a[4], a[5], a[6]], [a[8], a[9], a[10]]]
    t = [a[3], a[7], a[11]]
    params = {"bodies": _entity_refs(f.inputEntities)}
    if any(abs(v) > _EPS for v in t):
        params["translate"] = [_fixed_length(ctx, v) for v in t]
    identity = all(abs(R[r][c] - (1.0 if r == c else 0.0)) <= _EPS for r in range(3) for c in range(3))
    if not identity:
        trace = R[0][0] + R[1][1] + R[2][2]
        theta = math.acos(max(-1.0, min(1.0, (trace - 1.0) / 2.0)))
        u = _rotation_axis(R, theta)
        for unit, name in _AXES.items():
            if all(abs(u[i] - unit[i]) <= _EPS for i in range(3)):
                params.update(rotate_axis=name, angle=_fixed_angle(ctx, theta))
                break
            if all(abs(u[i] + unit[i]) <= _EPS for i in range(3)):
                params.update(rotate_axis=name, angle=_fixed_angle(ctx, -theta))
                break
        else:
            raise _Unmapped("rotation axis is not an origin axis")
    return [{"tool": "model", "action": "move", "params": params}]


# --- coil (design § 7.4) -------------------------------------------------------------------------------------

_COIL_FORMAT = "defusion.request/1"
# role substring -> (request key, unit for evaluateExpression); first match wins
_COIL_ROLES = (
    ("diameter", "diameter", "cm"),
    ("revolution", "revolutions", ""),
    ("height", "height", "cm"),
    ("pitch", "pitch", "cm"),
    ("angle", "taper_angle", "rad"),
    ("section", "section_size", "cm"),
)


def _coil_request(ctx, coil):
    """The stored ``defusion.request/1`` of a coil, or ``None`` when missing or malformed."""
    req = _guarded(lambda: ctx.attr_get(coil, "request"))
    if (not isinstance(req, dict) or req.get("format") != _COIL_FORMAT or req.get("tool") != "model"
            or req.get("action") != "coil" or not isinstance(req.get("params"), dict)):
        return None
    return req


def _reduce_ref(r):
    """A Ref dict with a token becomes a name Ref: tokens do not exist in the replay document."""
    if not (isinstance(r, dict) and "token" in r):
        return r
    out = {"name": r.get("name")}
    if r.get("component") is not None:
        out["component"] = r["component"]
    return out


def _coil_params(req):
    params = dict(req["params"])
    for key in ("plane", "component"):
        if key in params:
            params[key] = _reduce_ref(params[key])
    if isinstance(params.get("participant_bodies"), list):
        params["participant_bodies"] = [_reduce_ref(r) for r in params["participant_bodies"]]
    return params


def _coil_current(ctx, coil, params):
    """``{key: expression}`` of the coil's matched model parameters when any differs from the request."""
    um = ctx.design.unitsManager
    matched, stale = {}, False
    for p in _model_parameters(ctx.design, coil):
        role = str(p.role).lower()
        hit = next(((k, u) for sub, k, u in _COIL_ROLES if sub in role), None)
        if hit is None:
            continue
        key, unit = hit
        matched[key] = p.expression
        if params.get(key) is None:
            continue
        try:
            if abs(um.evaluateExpression(params[key], unit) - p.value) > 1e-6 * max(1.0, abs(p.value)):
                stale = True
        except Exception:
            stale = True
    return matched if stale else None


def _read_coil(ctx, coil):
    req = _coil_request(ctx, coil)
    if req is None:
        raise _Unmapped("coil has no stored request")
    params = _coil_params(req)
    step = {"tool": "model", "action": "coil", "params": params}
    current = _coil_current(ctx, coil, params)
    if current is not None:
        step.update(stale=True, current=current)
    return [step]


def _is_coil_base_sketch(ctx, tl, i):
    """True for the sketch ``model.coil`` created for the coil right after it; never raises."""
    try:
        obj, nxt = tl.item(i), tl.item(i + 1)
        if obj.isGroup or nxt.isGroup or _type_name(obj) != "Sketch" or _type_name(nxt) != "CoilFeature":
            return False
        req = _coil_request(ctx, nxt.entity)
        return req is not None and obj.name == "%s base" % (req["params"].get("name") or "Coil")
    except Exception:
        return False


# Feature readers by entity type name: ``reader(ctx, entity) -> [step, ...]`` (design § 7.3, § 7.4).
_FEATURE_READERS = {
    "ExtrudeFeature": _read_extrude,
    "RevolveFeature": _read_revolve,
    "FilletFeature": _read_fillet,
    "ChamferFeature": _read_chamfer,
    "ShellFeature": _read_shell,
    "RectangularPatternFeature": _read_rect_pattern,
    "CircularPatternFeature": _read_circ_pattern,
    "MirrorFeature": _read_mirror,
    "CombineFeature": _read_combine,
    "HoleFeature": _read_hole,
    "ThreadFeature": _read_thread,
    "ConstructionPlane": _read_construction_plane,
    "ConstructionAxis": _read_construction_axis,
    "MoveFeature": _read_move,
    "CoilFeature": _read_coil,
}


def _read_inputs(ctx, obj):
    """The steps (or callables producing a step after the item) for one timeline item."""
    entity = obj.entity
    kind = _type_name(obj)
    if kind == "Sketch":
        return _sketch_inputs(ctx, entity)
    reader = _FEATURE_READERS.get(kind)
    if reader is None:
        raise _Unmapped("unsupported timeline item type: %s" % kind)
    return reader(ctx, entity)


def _finish(ctx, obj, inputs, new_bodies):
    """Attach ``source``, ``bodies`` and ``suppressed`` to the item's steps (design § 6)."""
    src = _source(obj)
    steps = []
    for s in inputs:
        step = dict(s() if callable(s) else s)
        step["source"] = src
        steps.append(step)
    if new_bodies:
        steps[-1]["bodies"] = new_bodies
    if obj.isSuppressed:
        steps[0]["suppressed"] = True
    return steps


def _parameter_steps(design):
    """One ``model.parameter_create`` step per user parameter, dependencies first (design § 7.6)."""
    params = list(design.userParameters)

    def deps(p):
        names = {q.name for q in _guarded(lambda: list(p.dependencyParameters)) or []}
        return [q for q in params if q.name in names and q is not p]

    for _ in range(len(params) * len(params) + 1):
        moved = False
        for p in list(params):
            for d in deps(p):
                if params.index(d) > params.index(p):
                    params.remove(p)
                    params.insert(params.index(d) + 1, p)
                    moved = True
        if not moved:
            break
    return [
        {"tool": "model", "action": "parameter_create",
         "params": {"name": p.name, "expression": p.expression, "unit": p.unit, "comment": p.comment}}
        for p in params
    ]


def _dump_item(ctx, design, obj):
    _with_collapsed(obj, lambda: obj.rollTo(True))
    before = {b.entityToken for b in _all_bodies(design)}
    inputs = _read_inputs(ctx, obj)
    _with_collapsed(obj, lambda: obj.rollTo(False))
    created = [b.name for b in _all_bodies(design) if b.entityToken not in before]
    return _finish(ctx, obj, inputs, created)


@action("history", access="read", timeout=300)
def dump_recipe(ctx: Ctx, offset: int = 0, limit: int = 20) -> dict:
    """Dump the timeline as replayable recipe steps (a page of timeline items; marker restored)."""
    design = ctx.require_parametric()
    tl = design.timeline
    saved = tl.markerPosition
    page = ctx.page(range(tl.count), offset, limit)
    steps = _parameter_steps(design) if offset == 0 else []
    try:
        for i in page["items"]:
            obj = tl.item(i)
            if obj.isGroup or _is_coil_base_sketch(ctx, tl, i):
                continue
            try:
                steps += _dump_item(ctx, design, obj)
            except Exception as exc:
                reason = str(exc) if isinstance(exc, _Unmapped) else "%s: %s" % (type(exc).__name__, exc)
                steps.append(_unknown(ctx, obj, reason))
    finally:
        failure = None
        try:
            tl.markerPosition = saved
        except Exception as exc:
            failure = exc
        if failure is not None or tl.markerPosition != saved:
            raise ActionError(
                FUSION_ERROR, "marker restore failed: at %s, expected %s" % (tl.markerPosition, saved)
            )
    return {**page, "items": steps, "marker": tl.markerPosition}


# --- recipes: apply one step (design § 8) -----------------------------------------------------------------

_STEP_TOOLS = ("sketch", "model")
_STEP_EXTRAS = {"source": dict, "bodies": list, "suppressed": bool, "stale": bool, "current": dict}


def _check_step(step):
    """Design § 8.1: returns ``(tool, action, params)`` or raises INVALID_PARAMS."""
    if step.get("kind") == "unknown":
        raise ActionError(
            INVALID_PARAMS,
            "unknown step %s %s cannot be replayed" % (step.get("type"), step.get("name")),
            hints=[str(step.get("reason"))],
        )
    for key, kind in (("tool", str), ("action", str), ("params", dict)):
        if not isinstance(step.get(key), kind):
            raise ActionError(INVALID_PARAMS, "step.%s is required and must be a %s" % (key, kind.__name__))
    for key, value in step.items():
        if key in ("tool", "action", "params"):
            continue
        if key not in _STEP_EXTRAS:
            raise ActionError(INVALID_PARAMS, "step has an unknown key %r" % key)
        if not isinstance(value, _STEP_EXTRAS[key]) or (
            key == "bodies" and not all(isinstance(b, str) for b in value)
        ):
            raise ActionError(INVALID_PARAMS, "step.%s has the wrong type" % key)
    tool, name = step["tool"], step["action"]
    target = actions.registry().get(tool, name)
    if tool not in _STEP_TOOLS or (target is not None and target.access == "destructive"):
        raise ActionError(INVALID_PARAMS, "%s.%s cannot be a recipe step" % (tool, name))
    return tool, name, step["params"]


@action("history", access="write", timeout=180)
def apply_recipe_step(ctx: Ctx, step: dict[str, Any]) -> dict:
    """Run one recipe step (a sketch or model action call) and restore its recorded names."""
    tool, name, params = _check_step(step)
    design = ctx.require_design()
    tl = design.timeline if design.designType == adsk.fusion.DesignTypes.ParametricDesignType else None
    count_before = tl.count if tl is not None else 0
    tokens_before = {b.entityToken for b in _all_bodies(design)}
    result = actions.call_inline(ctx, tool, name, params)
    renamed, warnings = [], []
    added = tl.count - count_before if tl is not None else 0
    item = tl.item(count_before) if added == 1 else None
    source_name = (step.get("source") or {}).get("name")
    if item is not None and source_name:
        try:
            item.entity.name = source_name
        except Exception:
            item.name = source_name
        renamed.append(ctx.ref(item.entity))
    if "bodies" in step:
        created = [b for b in _all_bodies(design) if b.entityToken not in tokens_before]
        for body, new_name in zip(created, step["bodies"]):
            body.name = new_name
            renamed.append(ctx.ref(body))
        if len(created) != len(step["bodies"]):
            warnings.append("created %d bodies, recipe lists %d" % (len(created), len(step["bodies"])))
    if step.get("suppressed") and item is not None:
        _with_collapsed(item, lambda: setattr(item, "isSuppressed", True))
    out = {"step": {"tool": tool, "action": name}, "result": result, "renamed": renamed}
    if step.get("stale"):
        out["stale"] = True
    if warnings:
        out["warnings"] = warnings
    return out


# --- live scenarios (design § 9.2) -------------------------------------------------------------------

import json  # noqa: E402

from .live_common import _MM, _do, _fresh, _lc  # noqa: E402


async def _call(session, tool, act, params):
    env = await _lc().call(session, tool, act, params, units=_MM)
    assert env["ok"], "%s.%s: %s" % (tool, act, json.dumps(env.get("error"))[:400])
    return env


async def _err(session, act, params, code, tool="history"):
    env = await _lc().call(session, tool, act, params, units=_MM)
    assert not env["ok"] and env["error"]["code"] == code, "%s.%s expected %s, got %s" % (
        tool, act, code, json.dumps(env)[:300])
    return env["error"]


async def _h(session, act, params=None):
    return await _do(session, "history", act, params or {})


async def _box(session, w=10, d=10, h=10):
    return await _lc().box(session, w, d, h)


_OCC_X = "root.occurrences.item(1).transform2.translation.x"


async def _pose(session):
    """Two boxes in their own components, then occurrence 2 moved 10 mm in X so a position change is pending."""
    for at in (0, 30):
        body = await _box(session)
        if at:
            await _do(session, "model", "move", {"bodies": [body], "translate": [at, 0, 0]})
        await _do(session, "assembly", "create_component", {"bodies": [body]})
    code = ("t = adsk.core.Matrix3D.create()\n"
            "t.translation = adsk.core.Vector3D.create(1, 0, 0)\n"
            "root.occurrences.item(1).transform2 = t\n")
    await _do(session, "script", "execute_python", {"code": code + "None", "confirm": True})


async def _occ_x(session):
    r = await _do(session, "script", "execute_python", {"code": _OCC_X, "confirm": True})
    return r["value"]


async def _list(session):
    async with _fresh(session):
        await _box(session)
        await _do(session, "sketch", "create", {"plane": "XY"})
        r = await _h(session, "list")
        assert [i["type"] for i in r["items"]] == ["Sketch", "ExtrudeFeature", "Sketch"], r
        assert r["marker"] == 3 and r["count"] == 3, r
        p = await _h(session, "list", {"limit": 1})
        assert len(p["items"]) == 1 and p["next_offset"] == 1, p


async def _set_marker(session):
    async with _fresh(session):
        await _box(session)
        assert (await _h(session, "set_marker", {"position": "begin"}))["marker"] == 0
        assert (await _h(session, "set_marker", {"position": "next"}))["marker"] == 1
        assert (await _h(session, "set_marker", {"position": "prev"}))["marker"] == 0
        assert (await _h(session, "set_marker", {"position": 1}))["marker"] == 1
        ref = (await _h(session, "list"))["items"][1]["entity"]
        assert (await _h(session, "set_marker", {"position": ref}))["marker"] == 1
        assert (await _h(session, "set_marker", {"position": "end"}))["marker"] == 2
        await _err(session, "set_marker", {"position": 9}, "INVALID_PARAMS")


async def _reorder(session):
    async with _fresh(session):
        await _do(session, "sketch", "create", {"plane": "XY"})
        await _box(session)
        names = [i["name"] for i in (await _h(session, "list"))["items"]]
        r = await _h(session, "reorder", {"item": 0, "before": 2, "dry_run": True})
        assert r["can_reorder"] is True and r["moved"] is False, r
        r = await _h(session, "reorder", {"item": 0, "before": 2})
        assert r["moved"] is True, r
        after = [i["name"] for i in (await _h(session, "list"))["items"]]
        assert after[:2] == [names[1], names[0]], (names, after)
        r = await _h(session, "reorder", {"item": 0, "before": "end", "dry_run": True})
        assert r["can_reorder"] is False, r
        await _err(session, "reorder", {"item": 0, "before": "end"}, "INVALID_PARAMS")
    async with _fresh(session):
        # an extrude before its own sketch: refused, or (Fusion's canReorder quirk) accepted but not moved
        await _box(session)
        env = await _call(session, "history", "reorder", {"item": 1, "before": 0})
        if env["ok"]:
            assert env["result"]["moved"] is False and env["result"]["item"]["index"] == 1, env
        else:
            assert env["error"]["code"] == "INVALID_PARAMS", env


async def _set_suppressed(session):
    async with _fresh(session):
        await _box(session)
        env = await _call(session, "history", "set_suppressed", {"item": 1, "suppressed": True})
        assert env["result"]["item"]["suppressed"] is True and list(env["changes"]["bodies"]) == [1, 0], env
        env = await _call(session, "history", "set_suppressed", {"item": 1, "suppressed": False})
        assert list(env["changes"]["bodies"]) == [0, 1], env


async def _rename(session):
    async with _fresh(session):
        await _box(session)
        await _h(session, "rename", {"item": 1, "name": "Base"})
        assert (await _h(session, "list"))["items"][1]["name"] == "Base"


async def _group_add(session):
    async with _fresh(session):
        await _box(session)
        r = await _h(session, "group_add", {"start": 0, "end": 1, "name": "G"})
        assert r["group"]["name"] == "G" and r["group"]["is_group"] and r["members"] == [0, 1], r
        await _err(session, "group_add", {"start": 1, "end": 0}, "INVALID_PARAMS")


async def _group_delete(session):
    async with _fresh(session):
        await _box(session)
        await _h(session, "group_add", {"start": 0, "end": 1, "name": "G"})
        await _err(session, "group_delete", {"group": 0}, "CONFIRM_REQUIRED")
        r = await _h(session, "group_delete", {"group": 0, "confirm": True})
        assert r["deleted"] == "G" and r["contents_deleted"] is False, r
        assert (await _h(session, "list"))["count"] == 2
        await _h(session, "group_add", {"start": 0, "end": 1, "name": "G"})
        env = await _call(session, "history", "group_delete", {"group": 0, "delete_contents": True, "confirm": True})
        assert env["result"]["contents_deleted"] is True, env
        assert (await _h(session, "list"))["count"] == 0


async def _delete_after_marker(session):
    async with _fresh(session):
        await _box(session)
        await _do(session, "sketch", "create", {"plane": "XY"})
        await _h(session, "set_marker", {"position": 1})
        r = await _h(session, "delete_after_marker", {"confirm": True})
        assert len(r["deleted"]) == 2 and r["count"] == 1, r


async def _snapshot_list(session):
    async with _fresh(session):
        await _pose(session)
        assert (await _h(session, "snapshot_list"))["pending"] is True


async def _snapshot_add(session):
    async with _fresh(session):
        await _pose(session)
        r = await _h(session, "snapshot_add", {"name": "Pose A"})
        assert r["snapshot"]["name"] == "Pose A", r
        assert (await _h(session, "snapshot_list"))["pending"] is False
        await _err(session, "snapshot_add", {"name": "Pose B"}, "INVALID_PARAMS")


async def _snapshot_rename(session):
    async with _fresh(session):
        await _pose(session)
        await _h(session, "snapshot_add", {"name": "Pose A"})
        r = await _h(session, "snapshot_rename", {"snapshot": "Pose A", "name": "Pose B"})
        assert r["snapshot"]["name"] == "Pose B", r
        await _err(session, "snapshot_rename", {"snapshot": "nope", "name": "x"}, "NOT_FOUND")


async def _snapshot_revert_pending(session):
    async with _fresh(session):
        await _pose(session)
        assert abs(await _occ_x(session) - 1.0) < 1e-6
        assert (await _h(session, "snapshot_revert_pending", {"confirm": True}))["reverted"] is True
        x = await _occ_x(session)
        assert abs(x) < 1e-6, x
        assert (await _h(session, "snapshot_revert_pending", {"confirm": True}))["reverted"] is False


async def _snapshot_delete(session):
    async with _fresh(session):
        await _pose(session)
        await _h(session, "snapshot_add", {"name": "Pose A"})
        n = (await _h(session, "snapshot_list"))["total"]
        r = await _h(session, "snapshot_delete", {"snapshot": "Pose A", "confirm": True})
        assert r["deleted"] == "Pose A" and r["count"] == n - 1, r


async def _undo(session):
    async with _fresh(session):
        await _box(session)
        env = await _call(session, "history", "undo", {})
        assert env["result"]["undone"] is True and env["result"]["design_type"] == "parametric", env
        assert (await _do(session, "inspect", "list_bodies", {}))["total"] == 0


_COIL = {"diameter": "30 mm", "coil_type": "revolution_height", "revolutions": 3, "height": "4 mm",
         "section_size": "1 mm"}


async def _stats(session):
    n = (await _do(session, "inspect", "list_bodies", {}))["total"]
    props = await _do(session, "inspect", "physical_properties", {})
    bb = await _do(session, "inspect", "bounds", {})
    return n, props["items"][0]["volume"], bb["min"], bb["max"]


async def _dump(session):
    steps, off = [], 0
    while off is not None:
        r = await _h(session, "dump_recipe", {"offset": off})
        steps += r["items"]
        off = r["next_offset"]
    return steps


async def _replay(session, steps):
    """Dump-replay target: a new document, every step applied; returns the document id."""
    doc = await _do(session, "document", "create", {})
    for s in steps:
        env = await _lc().call(session, "history", "apply_recipe_step", {"step": s}, units=_MM)
        assert env["ok"], "step %s: %s" % (json.dumps(s)[:200], json.dumps(env.get("error"))[:400])
    return doc["id"]


async def _round_trip(session, build):
    async with _fresh(session):
        await build(session)
        want = await _stats(session)
        steps = await _dump(session)
        assert not [s for s in steps if s.get("tool") == "history"], steps
        doc = await _replay(session, steps)
        try:
            got = await _stats(session)
        finally:
            await _lc().call(session, "document", "close", {"document": doc, "confirm": True})
        assert got[0] == want[0], (got, want)
        assert abs(got[1] - want[1]) <= 1e-6 * max(1.0, abs(want[1])), (got, want)
        for a, b in ((got[2], want[2]), (got[3], want[3])):
            assert all(abs(x - y) <= 1e-4 for x, y in zip(a, b)), (got, want)
        return steps


async def _build_part(session):
    await _do(session, "model", "parameter_create", {"name": "wall", "expression": "20 mm"})
    body = await _lc().box(session, 40, 20, "wall / 2")
    edge = (await _do(session, "inspect", "list_edges", {"body": body}))["items"][0]["ref"]
    await _do(session, "model", "fillet", {"edges": [edge], "radius": "1 mm"})
    plane = (await _do(session, "model", "construction_plane",
                       {"method": "offset", "plane": "XY", "offset": "wall / 4"}))["plane"]
    await _do(session, "model", "coil", dict(_COIL, plane=plane, center=[20, 10], operation="join"))


async def _recipe_part(session):
    async with _fresh(session):
        await _build_part(session)
        steps = await _dump(session)
        assert steps[0].get("tool") == "model" and steps[0]["action"] == "parameter_create", steps[0]
        assert steps[0]["params"]["name"] == "wall", steps[0]
        ext = [s for s in steps if s.get("action") == "extrude"]
        assert ext and ext[0]["params"]["distance"] == "wall / 2", ext
        assert not [s for s in steps if s.get("kind") == "unknown"], steps
        assert not [s for s in steps if "base" in json.dumps(s.get("params", {})).lower()
                    and s.get("action") != "coil"], steps
    await _round_trip(session, _build_part)


async def _apply_recipe_step(session):
    async with _fresh(session):
        await _box(session)
        await _h(session, "rename", {"item": 1, "name": "Base"})
        body = (await _do(session, "inspect", "list_bodies", {}))["items"][0]["ref"]
        await _do(session, "model", "rename", {"target": body, "name": "Plate"})
        steps = await _dump(session)
        assert len(steps) == 3, steps
        doc = await _replay(session, steps)
        try:
            names = [i["name"] for i in (await _h(session, "list"))["items"]]
            assert "Base" in names, names
            assert (await _do(session, "inspect", "list_bodies", {}))["items"][0]["ref"]["name"] == "Plate"
            bad = json.loads(json.dumps(steps[-1]))
            bad["params"]["bogus_param"] = 1
            err = await _err(session, "apply_recipe_step", {"step": bad}, "INVALID_PARAMS")
            assert err.get("schema"), err
            await _err(session, "apply_recipe_step", {"step": {"kind": "unknown", "type": "X", "name": "x", "parameters": {}, "reason": "r"}},
                       "INVALID_PARAMS")
        finally:
            await _lc().call(session, "document", "close", {"document": doc, "confirm": True})


def _coil_scenario(coil_type, **kw):
    async def build(session):
        await _do(session, "model", "coil", dict(_COIL, coil_type=coil_type, **kw))

    async def scenario(session):
        await _round_trip(session, build)

    scenario.__name__ = "_recipe_coil_" + coil_type
    return scenario


async def _coil_probe(session):
    async with _fresh(session):
        r = await _do(session, "model", "coil", dict(_COIL))
        params = (await _do(session, "model", "parameter_list", {"created_by": r["feature"]}))["items"]
        print("coil roles:", sorted((p.get("role"), p.get("name")) for p in params))
        assert params, r


LIVE_SCENARIOS = [(f.__name__[1:], "history." + f.__name__[1:], f) for f in (
    _list, _set_marker, _reorder, _set_suppressed, _rename, _group_add, _group_delete,
    _delete_after_marker, _snapshot_list, _snapshot_add, _snapshot_rename, _snapshot_revert_pending,
    _snapshot_delete, _undo)] + [
    ("recipe_part", "history.dump_recipe", _recipe_part),
    ("apply_recipe_step", "history.apply_recipe_step", _apply_recipe_step),
    ("recipe_coil_revolution_pitch", "history.dump_recipe",
     _coil_scenario("revolution_pitch", height=None, pitch="5 mm")),
    ("recipe_coil_height_pitch", "history.dump_recipe",
     _coil_scenario("height_pitch", revolutions=None, pitch="5 mm")),
    ("recipe_coil_spiral", "history.dump_recipe", _coil_scenario("spiral", height=None, pitch="5 mm")),
    ("coil_probe", "history.dump_recipe", _coil_probe),
]
