"""Inspect the active design: summary, entity info, topology, geometry, measurement, mass properties, interference, mesh comparison."""
import math
from typing import Literal

import adsk.core
import adsk.fusion

from ..actions import action
from ..context import Ctx
from ..errors import (
    FUSION_ERROR,
    INVALID_PARAMS,
    NO_DOCUMENT,
    UNSUPPORTED,
    ActionError,
)
from ..types import Body, Component, Entity, Length, Plane, Value
from ..units import CM_PER, RAD_PER
from .common import _enum_name, _try

Accuracy = Literal["low", "medium", "high", "very_high"]
_ACCURACY = {"low": "LowCalculationAccuracy", "medium": "MediumCalculationAccuracy",
             "high": "HighCalculationAccuracy", "very_high": "VeryHighCalculationAccuracy"}


# --- helpers (design § 1) -------------------------------------------------------------------------


def _pt(ctx, p):
    return [ctx.length_out(p.x), ctx.length_out(p.y), ctx.length_out(p.z)]


def _vec(v):
    c = v.copy()
    c.normalize()
    return [c.x, c.y, c.z]


def _bbox(ctx, bb):
    if bb is None:
        return None
    lo, hi = _pt(ctx, bb.minPoint), _pt(ctx, bb.maxPoint)
    return {
        "min": lo,
        "max": hi,
        "size": [h - l for l, h in zip(lo, hi)],
        "center": [(h + l) / 2 for l, h in zip(lo, hi)],
    }


def _bodies(design, component=None):
    """``(body, kind, occurrence_path)`` for one component, or the root plus every occurrence proxy."""
    if component is not None:
        return ([(b, "brep", "") for b in component.bRepBodies]
                + [(b, "mesh", "") for b in component.meshBodies])
    root = design.rootComponent
    out = [(b, "brep", "") for b in root.bRepBodies] + [(b, "mesh", "") for b in root.meshBodies]
    for occ in root.allOccurrences:
        path = occ.fullPathName
        out += [(b, "brep", path) for b in occ.bRepBodies]
        out += [(b, "mesh", path) for b in occ.meshBodies]
    return out


def _surface_names():
    s = adsk.core.SurfaceTypes
    names = {
        "PlaneSurfaceType": "plane", "CylinderSurfaceType": "cylinder", "ConeSurfaceType": "cone",
        "SphereSurfaceType": "sphere", "TorusSurfaceType": "torus",
        "EllipticalCylinderSurfaceType": "elliptical_cylinder",
        "EllipticalConeSurfaceType": "elliptical_cone", "NurbsSurfaceType": "nurbs",
    }
    return {getattr(s, k): v for k, v in names.items() if hasattr(s, k)}


def _curve_names():
    c = adsk.core.Curve3DTypes
    names = {
        "Line3DCurveType": "line", "Arc3DCurveType": "arc", "Circle3DCurveType": "circle",
        "Ellipse3DCurveType": "ellipse", "EllipticalArc3DCurveType": "elliptical_arc",
        "NurbsCurve3DCurveType": "nurbs",
    }
    return {getattr(c, k): v for k, v in names.items() if hasattr(c, k)}


def _surface(ctx, face, full=False):
    """Surface dict of a face: ``type`` plus per-type parameters (``full`` adds more)."""
    g = face.geometry
    kind = _surface_names().get(g.surfaceType, "other")
    d = {"type": kind}
    if kind == "plane":
        ok, n = face.evaluator.getNormalAtPoint(face.pointOnFace)
        d["normal"] = _vec(n)
        if full:
            d["origin"] = _pt(ctx, g.origin)
            d["u_direction"] = _vec(g.uDirection)
    elif kind == "cylinder":
        d.update(radius=ctx.length_out(g.radius), axis=_vec(g.axis), origin=_pt(ctx, g.origin))
    elif kind == "cone":
        d.update(radius=ctx.length_out(g.radius), axis=_vec(g.axis),
                 half_angle=ctx.angle_out(g.halfAngle))
        if full:
            d["origin"] = _pt(ctx, g.origin)
    elif kind == "sphere":
        d.update(radius=ctx.length_out(g.radius), center=_pt(ctx, g.origin))
    elif kind == "torus":
        d.update(major_radius=ctx.length_out(g.majorRadius), minor_radius=ctx.length_out(g.minorRadius),
                 axis=_vec(g.axis), center=_pt(ctx, g.origin))
    elif kind == "nurbs" and full:
        data = g.getData()
        d.update(degree_u=data[2], degree_v=data[3], control_points=[_pt(ctx, p) for p in data[1]],
                 control_point_counts=[g.controlPointCountU, g.controlPointCountV])
    return d


def _curve(ctx, edge, full=False):
    """Curve dict of an edge: ``type`` plus per-type parameters (``full`` adds more)."""
    g = edge.geometry
    kind = _curve_names().get(g.curveType, "other")
    d = {"type": kind}
    if kind in ("arc", "circle"):
        d.update(center=_pt(ctx, g.center), radius=ctx.length_out(g.radius))
        if full:
            d["normal"] = _vec(g.normal)
            if kind == "arc":
                d["start_angle"] = ctx.angle_out(g.startAngle)
                d["end_angle"] = ctx.angle_out(g.endAngle)
    elif kind == "line" and full:
        a, b = g.startPoint, g.endPoint
        v = adsk.core.Vector3D.create(b.x - a.x, b.y - a.y, b.z - a.z)
        d["direction"] = _vec(v)
    elif kind == "ellipse" and full:
        d.update(major_radius=ctx.length_out(g.majorRadius), minor_radius=ctx.length_out(g.minorRadius))
    elif kind == "nurbs" and full:
        data = g.getData()
        d.update(degree=data[2], control_points=[_pt(ctx, p) for p in data[1]])
    return d


def _index_of(collection, entity, cache=None):
    """Index of ``entity`` in ``collection`` by token, else ``None``. Pass one ``cache`` dict per
    collection to build the token map once."""
    if cache is None:
        cache = {}
    if not cache:
        for i, e in enumerate(collection):
            cache.setdefault(e.entityToken, i)
    return cache.get(entity.entityToken)


# --- summary and object_info (§ 2.1, § 2.2) ------------------------------------------------------


def _short(obj):
    return obj.objectType.split("::")[-1]


@action("inspect", access="read")
def summary(ctx: Ctx) -> dict:
    """Active document and design overview: names, state, counts, timeline."""
    doc = ctx.document
    if doc is None:
        raise ActionError(NO_DOCUMENT, "no active document", hints=["open or create a document"])
    df = _try(lambda: doc.dataFile)
    result = {
        "document": {
            "name": doc.name,
            "saved": _try(lambda: doc.isSaved),
            "modified": _try(lambda: doc.isModified),
            "version": _try(lambda: df.versionNumber),
            "file_id": _try(lambda: df.id),
        },
        "design": None,
    }
    design = ctx.design
    if design is None:
        return result
    parametric = design.designType == adsk.fusion.DesignTypes.ParametricDesignType
    intent = None
    if hasattr(design, "designIntent"):
        t = adsk.fusion.DesignIntentTypes
        intent = {t.PartDesignIntentType: "part", t.AssemblyDesignIntentType: "assembly",
                  t.HybridDesignIntentType: "hybrid"}.get(design.designIntent)
    root = design.rootComponent
    comps = [root] + [c for c in design.allComponents if c.entityToken != root.entityToken]

    def total(attr):
        return sum(getattr(c, attr).count for c in comps)

    result["design"] = {
        "type": "parametric" if parametric else "direct",
        "intent": intent,
        "units": design.unitsManager.defaultLengthUnits,
        "active_component": ctx.ref(design.activeComponent),
        "components": len(comps),
        "occurrences": root.allOccurrences.count,
        "bodies": total("bRepBodies"),
        "mesh_bodies": total("meshBodies"),
        "sketches": total("sketches"),
        "features": total("features"),
        "parameters": design.userParameters.count,
        "timeline": design.timeline.count if parametric else None,
        "timeline_marker": design.timeline.markerPosition if parametric else None,
    }
    return result


def _param_value(ctx, p):
    unit = p.unit
    if unit in CM_PER:
        return ctx.length_out(p.value)
    if unit in RAD_PER:
        return ctx.angle_out(p.value)
    return p.value


def _parent_of_component(ctx, c):
    root = ctx.design.rootComponent
    if c.entityToken == root.entityToken:
        return None
    occs = _try(lambda: root.allOccurrencesByComponent(c))
    if occs is not None and occs.count:
        up = occs.item(0).assemblyContext
        return up.component if up is not None else root
    return root


def _info(ctx, obj, kind):
    """``(properties, parent)`` for one entity; each read that fails gives ``None``."""
    design = ctx.design
    root = design.rootComponent
    parent_comp = _try(lambda: obj.parentComponent) or _try(lambda: obj.component)  # planes/axes: `.component`
    if kind == "BRepBody":
        props = {
            "is_solid": lambda: obj.isSolid, "is_visible": lambda: obj.isVisible,
            "volume": lambda: ctx.volume_out(obj.volume), "area": lambda: ctx.area_out(obj.area),
            "faces": lambda: obj.faces.count, "edges": lambda: obj.edges.count,
            "vertices": lambda: obj.vertices.count,
            "material": lambda: obj.material.name, "appearance": lambda: obj.appearance.name,
            "occurrence_path": lambda: obj.assemblyContext.fullPathName if obj.assemblyContext else "",
        }
        parent = parent_comp
    elif kind == "MeshBody":
        props = {
            "is_visible": lambda: obj.isVisible,
            "nodes": lambda: obj.mesh.nodeCount, "triangles": lambda: obj.mesh.triangleCount,
        }
        parent = parent_comp
    elif kind == "BRepFace":
        props = {
            "surface": lambda: _surface(ctx, obj), "area": lambda: ctx.area_out(obj.area),
            "index": lambda: _index_of(obj.body.faces, obj),
        }
        parent = _try(lambda: obj.body)
    elif kind == "BRepEdge":
        props = {
            "curve": lambda: _curve(ctx, obj), "length": lambda: ctx.length_out(obj.length),
            "index": lambda: _index_of(obj.body.edges, obj),
        }
        parent = _try(lambda: obj.body)
    elif kind == "BRepVertex":
        props = {
            "point": lambda: _pt(ctx, obj.geometry),
            "index": lambda: _index_of(obj.body.vertices, obj),
        }
        parent = _try(lambda: obj.body)
    elif kind == "Component":
        props = {
            "bodies": lambda: obj.bRepBodies.count, "mesh_bodies": lambda: obj.meshBodies.count,
            "sketches": lambda: obj.sketches.count, "features": lambda: obj.features.count,
            "occurrences": lambda: obj.occurrences.count,
            "part_number": lambda: obj.partNumber, "description": lambda: obj.description,
            "material": lambda: obj.material.name,
        }
        parent = _try(lambda: _parent_of_component(ctx, obj))
    elif kind == "Occurrence":
        def transform():
            a = list(obj.transform2.asArray())
            for i in (3, 7, 11):
                a[i] = ctx.length_out(a[i])
            return a

        props = {
            "full_path": lambda: obj.fullPathName, "is_visible": lambda: obj.isVisible,
            "is_grounded": lambda: obj.isGrounded, "is_light_bulb_on": lambda: obj.isLightBulbOn,
            "transform": transform,
        }
        parent = _try(lambda: obj.assemblyContext) or root
    elif kind == "Sketch":
        props = {
            "profiles": lambda: obj.profiles.count, "curves": lambda: obj.sketchCurves.count,
            "points": lambda: obj.sketchPoints.count,
            "is_fully_constrained": lambda: obj.isFullyConstrained,
            "is_visible": lambda: obj.isVisible,
            "reference_plane": lambda: ctx.ref(obj.referencePlane),
        }
        parent = parent_comp
    elif isinstance(obj, adsk.fusion.Feature):
        props = {
            "health": lambda: _enum_name(getattr(adsk.fusion, "FeatureHealthStates", None), obj.healthState, raw=True),
            "is_suppressed": lambda: obj.isSuppressed,
            "timeline_index": lambda: obj.timelineObject.index,
        }
        parent = parent_comp
    elif isinstance(obj, adsk.fusion.Parameter):
        props = {
            "expression": lambda: obj.expression, "value": lambda: _param_value(ctx, obj),
            "unit": lambda: obj.unit, "comment": lambda: obj.comment,
        }
        parent = None
    elif kind in ("ConstructionPlane", "ConstructionAxis", "ConstructionPoint"):
        props = {
            "is_visible": lambda: obj.isVisible,
            "definition": lambda: _short(obj.definition),
        }
        parent = _try(lambda: obj.parent) or parent_comp
    elif kind in ("Joint", "AsBuiltJoint"):
        props = {
            "motion": lambda: _enum_name(getattr(adsk.fusion, "JointTypes", None), obj.jointMotion.jointType, raw=True),
            "occurrence_one": lambda: ctx.ref(obj.occurrenceOne),
            "occurrence_two": lambda: ctx.ref(obj.occurrenceTwo),
            "is_suppressed": lambda: obj.isSuppressed,
        }
        parent = parent_comp
    else:
        props = {"name": lambda: obj.name} if hasattr(obj, "name") else {}
        parent = None
    return {k: _try(f) for k, f in props.items()}, parent


@action("inspect", access="read")
def object_info(ctx: Ctx, entity: Entity) -> dict:
    """Type, properties, bounding box and parent of any entity."""
    ctx.require_design()
    kind = _short(entity)
    props, parent = _info(ctx, entity, kind)
    bb = _try(lambda: entity.boundingBox)
    return {
        "ref": ctx.ref(entity),
        "type": kind,
        "properties": props,
        "bbox": _bbox(ctx, bb),
        "parent": ctx.ref(parent) if parent is not None else None,
    }


# --- list_bodies, list_faces, list_edges (§ 2.3-§ 2.5) -------------------------------------------


@action("inspect", access="read")
def list_bodies(ctx: Ctx, component: Component | None = None, offset: int = 0, limit: int = 100) -> dict:
    """List BRep and mesh bodies of one component, or of the whole design including occurrence proxies (paged)."""
    design = ctx.require_design()

    def row(item):
        body, kind, path = item
        brep = kind == "brep"
        return {
            "ref": ctx.ref(body),
            "kind": kind,
            "occurrence_path": path,
            "is_solid": body.isSolid if brep else None,
            "is_visible": body.isVisible,
            "volume": ctx.volume_out(body.volume) if brep else None,
            "area": ctx.area_out(body.area) if brep else None,
            "faces": body.faces.count if brep else None,
            "edges": body.edges.count if brep else None,
        }

    return ctx.page(_bodies(design, component), offset, limit, row)


@action("inspect", access="read")
def list_faces(ctx: Ctx, body: Body, offset: int = 0, limit: int = 100) -> dict:
    """List the faces of a body with surface type, area, centroid and normal or radius/axis (paged)."""
    ctx.require_design()
    faces = body.faces

    def row(i):
        face = faces.item(i)
        surface = _surface(ctx, face)
        return {
            "index": i,
            "ref": ctx.ref(face),
            "surface_type": surface.pop("type"),
            "area": ctx.area_out(face.area),
            "centroid": _pt(ctx, face.centroid),
            **surface,
        }

    return ctx.page(range(faces.count), offset, limit, row)


@action("inspect", access="read")
def list_edges(ctx: Ctx, body: Body, offset: int = 0, limit: int = 100) -> dict:
    """List the edges of a body with curve type, length, end points and arc/circle center and radius (paged)."""
    ctx.require_design()
    edges = body.edges

    def end(vertex):
        return _pt(ctx, vertex.geometry) if vertex is not None else None

    def row(i):
        edge = edges.item(i)
        curve = _curve(ctx, edge)
        return {
            "index": i,
            "ref": ctx.ref(edge),
            "curve_type": curve.pop("type"),
            "length": ctx.length_out(edge.length),
            "start": end(edge.startVertex),
            "end": end(edge.endVertex),
            **curve,
        }

    return ctx.page(range(edges.count), offset, limit, row)


# --- geometry and bounds (§ 2.6, § 2.7) ----------------------------------------------------------


def _face_detail(ctx, face):
    body = face.body
    edges, faces = body.edges, body.faces
    e_cache, f_cache = {}, {}
    own = []
    adjacent = []
    for e in face.edges:
        own.append(_index_of(edges, e, e_cache))
        for other in e.faces:
            if other.entityToken != face.entityToken:
                i = _index_of(faces, other, f_cache)
                if i not in adjacent:
                    adjacent.append(i)
    return {
        "ref": ctx.ref(face),
        "index": _index_of(faces, face),
        "surface": _surface(ctx, face, full=True),
        "area": ctx.area_out(face.area),
        "centroid": _pt(ctx, face.centroid),
        "perimeter": ctx.length_out(sum(e.length for e in face.edges)),
        "loops": face.loops.count,
        "edges": own,
        "adjacent_faces": adjacent,
    }


def _edge_detail(ctx, edge):
    body = edge.body
    v_cache = {}
    ends = [edge.startVertex, edge.endVertex]
    return {
        "ref": ctx.ref(edge),
        "index": _index_of(body.edges, edge),
        "curve": _curve(ctx, edge, full=True),
        "length": ctx.length_out(edge.length),
        "start": _pt(ctx, ends[0].geometry) if ends[0] is not None else None,
        "end": _pt(ctx, ends[1].geometry) if ends[1] is not None else None,
        "faces": [_index_of(body.faces, f) for f in edge.faces],
        "vertices": [_index_of(body.vertices, v, v_cache) for v in ends if v is not None],
    }


def _vertex_detail(ctx, vertex):
    body = vertex.body
    cache = {}
    return {
        "ref": ctx.ref(vertex),
        "index": _index_of(body.vertices, vertex),
        "point": _pt(ctx, vertex.geometry),
        "edges": [_index_of(body.edges, e, cache) for e in vertex.edges],
    }


def _body_detail(ctx, body):
    names = _surface_names()
    hist = {}
    for f in body.faces:
        n = names.get(f.geometry.surfaceType, "other")
        hist[n] = hist.get(n, 0) + 1
    return {
        "ref": ctx.ref(body),
        "kind": "brep",
        "is_solid": body.isSolid,
        "volume": ctx.volume_out(body.volume),
        "area": ctx.area_out(body.area),
        "lumps": body.lumps.count,
        "shells": body.shells.count,
        "faces": body.faces.count,
        "edges": body.edges.count,
        "vertices": body.vertices.count,
        "surface_types": hist,
        "bbox": _bbox(ctx, body.boundingBox),
    }


@action("inspect", access="read")
def geometry(ctx: Ctx, entity: Entity) -> dict:
    """Deep detail of one body, face, edge or vertex: full parameters, adjacency, topology counts."""
    ctx.require_design()
    f = adsk.fusion
    for cls, fn in ((f.BRepFace, _face_detail), (f.BRepEdge, _edge_detail),
                    (f.BRepVertex, _vertex_detail), (f.BRepBody, _body_detail)):
        if isinstance(entity, cls):
            return fn(ctx, entity)
    raise ActionError(INVALID_PARAMS, "geometry takes a body, face, edge or vertex",
                      hints=["got %s" % type(entity).__name__])


@action("inspect", access="read")
def bounds(ctx: Ctx, entity: Entity | None = None, precise: bool = False) -> dict:
    """Axis-aligned bounding box (min, max, size, center) of an entity or the whole design; precise gives the tight box."""
    design = ctx.require_design()
    target = entity if entity is not None else design.rootComponent
    if not precise:
        try:
            bb = target.boundingBox
        except AttributeError:
            raise ActionError(INVALID_PARAMS, "%s has no bounding box" % type(target).__name__) from None
        box = _bbox(ctx, bb)
    else:
        mm = getattr(ctx.app, "measureManager", None)
        if mm is None or not hasattr(mm, "getOrientedBoundingBox"):
            raise ActionError(UNSUPPORTED, "oriented bounding box API not available in this Fusion build")
        obb = mm.getOrientedBoundingBox(target, adsk.core.Vector3D.create(1, 0, 0),
                                        adsk.core.Vector3D.create(0, 1, 0))
        c, size = obb.centerPoint, [obb.length, obb.width, obb.height]
        lo = [v - s / 2 for v, s in zip((c.x, c.y, c.z), size)]
        hi = [v + s / 2 for v, s in zip((c.x, c.y, c.z), size)]
        out = ctx.length_out
        box = {"min": [out(v) for v in lo], "max": [out(v) for v in hi],
               "size": [out(v) for v in size], "center": [out(c.x), out(c.y), out(c.z)]}
    return {"ref": ctx.ref(entity) if entity is not None else None, "precise": precise, **box}


# --- measure_distance, measure_angle, physical_properties (§ 2.8-§ 2.10) -------------------------


@action("inspect", access="read")
def measure_distance(ctx: Ctx, a: Entity, b: Entity) -> dict:
    """Minimum distance between two entities with the closest point on each and the delta vector."""
    ctx.require_design()
    r = ctx.app.measureManager.measureMinimumDistance(a, b)
    pa, pb = _pt(ctx, r.positionOne), _pt(ctx, r.positionTwo)
    return {
        "distance": ctx.length_out(r.value),
        "point_a": pa,
        "point_b": pb,
        "delta": [q - p for p, q in zip(pa, pb)],
    }


@action("inspect", access="read")
def measure_angle(ctx: Ctx, a: Entity, b: Entity, c: Entity | None = None) -> dict:
    """Angle between two entities, or at the middle of three points or vertices."""
    ctx.require_design()
    args = (a, b) if c is None else (a, b, c)
    try:
        r = ctx.app.measureManager.measureAngle(*args)
    except ActionError:
        raise
    except Exception as exc:
        raise ActionError(FUSION_ERROR, str(exc)) from exc
    points = [r.positionOne, r.positionTwo] + ([r.positionThree] if c is not None else [])
    return {"angle": ctx.angle_out(r.value), "points": [_pt(ctx, p) for p in points]}


def _raw_properties(e, accuracy):
    """``(properties, accuracy label)``: the entity's physical properties at ``accuracy`` when the
    build supports levels, else the default ones (``"low"``)."""
    if hasattr(e, "getPhysicalProperties"):
        level = getattr(adsk.fusion.CalculationAccuracy, _ACCURACY[accuracy])
        return e.getPhysicalProperties(level), accuracy
    return e.physicalProperties, "low"


@action("inspect", access="read", timeout=120)
def physical_properties(ctx: Ctx, entities: list[Entity] | None = None, accuracy: Accuracy = "very_high") -> dict:
    """Mass, volume, area, density, center of mass and moments of inertia per entity and combined."""
    design = ctx.require_design()
    f = adsk.fusion
    targets = entities if entities is not None else [design.rootComponent]
    for e in targets:
        if not isinstance(e, (f.BRepBody, f.Component, f.Occurrence)):
            raise ActionError(INVALID_PARAMS, "physical_properties takes bodies, components or "
                              "occurrences, got %s" % type(e).__name__)
    k2 = ctx.length_out(1.0) ** 2
    k3 = ctx.length_out(1.0) ** 3
    items, raws = [], []
    for e in targets:
        p, acc = _raw_properties(e, accuracy)
        com = p.centerOfMass
        _, xx, yy, zz, xy, yz, xz = p.getXYZMomentsOfInertia()
        _, i1, i2, i3 = p.getPrincipalMomentsOfInertia()
        _, ax, ay, az = p.getPrincipalAxes()
        raws.append({"mass": p.mass, "volume": p.volume, "area": p.area, "com": (com.x, com.y, com.z),
                     "moments": (xx, yy, zz, xy, yz, xz)})
        items.append({
            "ref": ctx.ref(e),
            "accuracy": acc,
            "mass": ctx.mass_out(p.mass),
            "volume": ctx.volume_out(p.volume),
            "area": ctx.area_out(p.area),
            "density": p.density / k3,
            "center_of_mass": _pt(ctx, com),
            "moments": dict(zip(("xx", "yy", "zz", "xy", "yz", "xz"), (m * k2 for m in raws[-1]["moments"]))),
            "principal_moments": [i1 * k2, i2 * k2, i3 * k2],
            "principal_axes": [_vec(ax), _vec(ay), _vec(az)],
        })
    result = {"items": items}
    if len(raws) > 1:
        mass = sum(r["mass"] for r in raws)
        volume = sum(r["volume"] for r in raws)
        com = None
        if mass:
            com = [ctx.length_out(sum(r["mass"] * r["com"][i] for r in raws) / mass) for i in range(3)]
        result["total"] = {
            "mass": ctx.mass_out(mass),
            "volume": ctx.volume_out(volume),
            "area": ctx.area_out(sum(r["area"] for r in raws)),
            "density": mass / volume / k3 if volume else None,
            "center_of_mass": com,
            "moments": dict(zip(("xx", "yy", "zz", "xy", "yz", "xz"),
                                (sum(r["moments"][i] for r in raws) * k2 for i in range(6)))),
        }
    return result


# --- interference, get_selection, section_analysis (§ 2.11, § 2.13, § 2.14) ----------------------

_MAX_INTERFERENCE_BODIES = 200


@action("inspect", access="read", timeout=300)
def interference(ctx: Ctx, entities: list[Entity] | None = None, include_coincident_faces: bool = False,
                 offset: int = 0, limit: int = 100) -> dict:
    """Find interfering pairs of bodies or occurrences with the interference volume, without adding bodies (paged)."""
    design = ctx.require_design()
    if entities is None:
        entities = [b for b, kind, _ in _bodies(design) if kind == "brep" and b.isVisible]
    else:
        for e in entities:
            if not isinstance(e, (adsk.fusion.BRepBody, adsk.fusion.Occurrence)):
                raise ActionError(INVALID_PARAMS, "interference takes bodies or occurrences, got %s"
                                  % type(e).__name__)
    if len(entities) < 2:
        raise ActionError(INVALID_PARAMS, "interference needs at least 2 bodies or occurrences, got %d"
                          % len(entities))
    if len(entities) > _MAX_INTERFERENCE_BODIES:
        raise ActionError(
            INVALID_PARAMS,
            "%d bodies is more than the %d interference allows" % (len(entities), _MAX_INTERFERENCE_BODIES),
            hints=["pass entities with the bodies or occurrences to check"],
        )
    coll = adsk.core.ObjectCollection.create()
    for e in entities:
        coll.add(e)
    inp = design.createInterferenceInput(coll)
    inp.areCoincidentFacesIncluded = include_coincident_faces
    res = design.analyzeInterference(inp)

    def pair(i):
        r = res.item(i)
        body = r.interferenceBody
        return {
            "a": ctx.ref(r.entityOne),
            "b": ctx.ref(r.entityTwo),
            "volume": ctx.volume_out(body.volume),
            "bbox": _bbox(ctx, body.boundingBox),
        }

    page = ctx.page(range(res.count if res is not None else 0), offset, limit, pair)
    page["checked"] = len(entities)
    return page


@action("inspect", access="read")
def get_selection(ctx: Ctx, offset: int = 0, limit: int = 100) -> dict:
    """The user's current UI selection as refs with the pick point (paged)."""
    sels = ctx.ui.activeSelections

    def row(i):
        s = sels.item(i)
        return {"ref": ctx.ref(s.entity), "point": _pt(ctx, s.point) if s.point is not None else None}

    return ctx.page(range(sels.count), offset, limit, row)


@action("inspect", access="write")
def section_analysis(ctx: Ctx, plane: Plane, offset: Length = 0, flip: bool = False,
                     name: str | None = None) -> dict:
    """Add a section analysis on a plane at an offset, optionally flipped and named."""
    design = ctx.require_design()
    analyses = getattr(design, "analyses", None)
    sa = getattr(analyses, "sectionAnalyses", None)
    if sa is None:
        raise ActionError(UNSUPPORTED, "section analysis API not available in this Fusion build")
    if not isinstance(offset, Value):  # the framework leaves the default 0 unresolved
        offset = Value("0 cm", 0.0, "length")
    inp = sa.createInput(plane, offset.cm)  # Fusion's binding takes a float in cm, not a ValueInput
    if flip:
        if not hasattr(inp, "isFlipped"):
            raise ActionError(UNSUPPORTED, "flip is not available in this Fusion build")
        inp.isFlipped = True
    a = sa.add(inp)
    if name is not None:
        a.name = name
    label = ctx.ref(plane)
    root = design.rootComponent
    for key, attr in (("XY", "xYConstructionPlane"), ("XZ", "xZConstructionPlane"), ("YZ", "yZConstructionPlane")):
        for comp in (root, design.activeComponent):
            if getattr(comp, attr).entityToken == plane.entityToken:
                label = key
    return {"analysis": {
        "name": a.name,
        "index": sa.count - 1,
        "plane": label,
        "offset": ctx.length_out(offset.cm),
        "flipped": flip,
    }}


# --- compare_meshes (§ 2.12) ---------------------------------------------------------------------

_MAX_SAMPLES = 20000
_MAX_REFERENCE_TRIANGLES = 5000000
_WORLD_TOLERANCE_CM = 1e-4


def _mesh_arrays(m, index_attr):
    c = list(m.nodeCoordinatesAsDouble)
    idx = list(getattr(m, index_attr))
    nodes = [(c[i], c[i + 1], c[i + 2]) for i in range(0, len(c) - 2, 3)]
    tris = [(idx[i], idx[i + 1], idx[i + 2]) for i in range(0, len(idx) - 2, 3)]
    return nodes, tris


def _triangles(body):
    """``(nodes, triangles)`` of a mesh or BRep body in cm, world space (design § 2.12)."""
    if isinstance(body, adsk.fusion.MeshBody):
        if hasattr(body, "displayMesh"):
            nodes, tris = _mesh_arrays(body.displayMesh, "nodeIndices")
        elif hasattr(getattr(body, "mesh", None), "triangleNodeIndices"):
            nodes, tris = _mesh_arrays(body.mesh, "triangleNodeIndices")
        else:
            raise ActionError(UNSUPPORTED, "this Fusion build exposes no mesh data for MeshBody")
    else:
        manager = getattr(body, "meshManager", None)
        if manager is None:
            raise ActionError(UNSUPPORTED, "this Fusion build exposes no mesh data for BRepBody")
        calc = manager.createMeshCalculator()
        calc.setQuality(adsk.fusion.TriangleMeshQualityOptions.VeryHighQualityTriangleMesh)
        nodes, tris = _mesh_arrays(calc.calculate(), "nodeIndices")
    occ = getattr(body, "assemblyContext", None)
    if occ is not None and nodes:
        bb = body.boundingBox
        lo, hi = (bb.minPoint.x, bb.minPoint.y, bb.minPoint.z), (bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z)
        for i in range(3):
            if (abs(min(n[i] for n in nodes) - lo[i]) > _WORLD_TOLERANCE_CM
                    or abs(max(n[i] for n in nodes) - hi[i]) > _WORLD_TOLERANCE_CM):
                m = occ.transform2.asArray()
                nodes = [tuple(m[4 * r] * x + m[4 * r + 1] * y + m[4 * r + 2] * z + m[4 * r + 3]
                               for r in range(3)) for x, y, z in nodes]
                break
    return nodes, tris


def _sample_points(nodes, tris, samples):
    """All nodes then every triangle centroid, thinned to at most ``samples`` by a fixed stride."""
    pts = list(nodes)
    for a, b, c in tris:
        pa, pb, pc = nodes[a], nodes[b], nodes[c]
        pts.append(((pa[0] + pb[0] + pc[0]) / 3, (pa[1] + pb[1] + pc[1]) / 3, (pa[2] + pb[2] + pc[2]) / 3))
    if len(pts) > samples:
        pts = pts[:: math.ceil(len(pts) / samples)]
    return pts


def _seg_dist2(p, a, b):
    ab = [b[i] - a[i] for i in range(3)]
    n = sum(v * v for v in ab)
    t = 0.0 if n == 0 else min(max(sum((p[i] - a[i]) * ab[i] for i in range(3)) / n, 0.0), 1.0)
    return sum((p[i] - a[i] - t * ab[i]) ** 2 for i in range(3))


def _tri_dist2(p, a, b, c):
    """Squared distance from ``p`` to triangle ``abc``; a degenerate (zero-area) triangle is its edges."""
    try:
        return _tri_dist2_ericson(p, a, b, c)
    except ZeroDivisionError:
        return min(_seg_dist2(p, a, b), _seg_dist2(p, b, c), _seg_dist2(p, a, c))


def _tri_dist2_ericson(p, a, b, c):
    """Closest point on a triangle (Ericson, Real-Time Collision Detection 5.1.5), squared distance."""
    def sub(u, v):
        return (u[0] - v[0], u[1] - v[1], u[2] - v[2])

    def dot(u, v):
        return u[0] * v[0] + u[1] * v[1] + u[2] * v[2]

    def at(o, u, t):
        return (o[0] + u[0] * t, o[1] + u[1] * t, o[2] + u[2] * t)

    def d2(q):
        r = sub(p, q)
        return dot(r, r)

    ab, ac, ap = sub(b, a), sub(c, a), sub(p, a)
    d1, d2_ = dot(ab, ap), dot(ac, ap)
    if d1 <= 0 and d2_ <= 0:
        return d2(a)
    bp = sub(p, b)
    d3, d4 = dot(ab, bp), dot(ac, bp)
    if d3 >= 0 and d4 <= d3:
        return d2(b)
    vc = d1 * d4 - d3 * d2_
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        return d2(at(a, ab, d1 / (d1 - d3)))
    cp = sub(p, c)
    d5, d6 = dot(ab, cp), dot(ac, cp)
    if d6 >= 0 and d5 <= d6:
        return d2(c)
    vb = d5 * d2_ - d1 * d6
    if vb <= 0 and d2_ >= 0 and d6 <= 0:
        return d2(at(a, ac, d2_ / (d2_ - d6)))
    va = d3 * d6 - d5 * d4
    if va <= 0 and d4 - d3 >= 0 and d5 - d6 >= 0:
        return d2(at(b, sub(c, b), (d4 - d3) / ((d4 - d3) + (d5 - d6))))
    total = va + vb + vc
    return d2(at(at(a, ab, vb / total), ac, vc / total))


def _shell(c, k, dims):
    """Grid cells at Chebyshev distance ``k`` from cell ``c``, clamped to the grid."""
    if k == 0:
        yield tuple(c)
        return
    for x in range(max(c[0] - k, 0), min(c[0] + k, dims[0] - 1) + 1):
        for y in range(max(c[1] - k, 0), min(c[1] + k, dims[1] - 1) + 1):
            if abs(x - c[0]) == k or abs(y - c[1]) == k:
                zs = range(max(c[2] - k, 0), min(c[2] + k, dims[2] - 1) + 1)
            else:
                zs = [z for z in (c[2] - k, c[2] + k) if 0 <= z < dims[2]]
            for z in zs:
                yield (x, y, z)


def _deviation(points, nodes, tris):
    """Unsigned distance from each point to the triangle surface, via a uniform grid (design § 2.12)."""
    if not tris:
        raise ActionError(INVALID_PARAMS, "mesh has no triangles")
    pts = [(nodes[i], nodes[j], nodes[k]) for i, j, k in tris]
    lo = [min(v[i] for t in pts for v in t) - 1e-6 for i in range(3)]
    hi = [max(v[i] for t in pts for v in t) + 1e-6 for i in range(3)]
    diag = math.sqrt(sum((h - l) ** 2 for l, h in zip(lo, hi)))
    s = max(diag / (len(tris) ** (1 / 3)), 1e-6)
    dims = [max(1, math.ceil((h - l) / s)) for l, h in zip(lo, hi)]

    def cell(x, i):
        return min(max(int((x - lo[i]) // s), 0), dims[i] - 1)

    grid = {}
    for ti, t in enumerate(pts):
        c0 = [cell(min(v[i] for v in t), i) for i in range(3)]
        c1 = [cell(max(v[i] for v in t), i) for i in range(3)]
        for x in range(c0[0], c1[0] + 1):
            for y in range(c0[1], c1[1] + 1):
                for z in range(c0[2], c1[2] + 1):
                    grid.setdefault((x, y, z), []).append(ti)
    shells = max(dims)
    out = []
    for p in points:
        c = [cell(p[i], i) for i in range(3)]
        best, tested = math.inf, set()
        for k in range(shells + 1):
            for cx, cy, cz in _shell(c, k, dims):
                for ti in grid.get((cx, cy, cz), ()):
                    if ti not in tested:
                        tested.add(ti)
                        d = _tri_dist2(p, *pts[ti])
                        if d < best:
                            best = d
            if math.sqrt(best) <= k * s:
                break
        out.append(math.sqrt(best))
    return out


def _stats(d, tol=None):
    """Distance statistics (same unit as ``d``); ``within_tolerance`` is a fraction, or ``None``."""
    n = len(d)
    srt = sorted(d)
    mean = sum(d) / n
    mid = n // 2
    return {
        "min": srt[0],
        "max": srt[-1],
        "mean": mean,
        "rms": math.sqrt(sum(x * x for x in d) / n),
        "std": math.sqrt(sum((x - mean) ** 2 for x in d) / n),
        "median": srt[mid] if n % 2 else (srt[mid - 1] + srt[mid]) / 2,
        "p95": srt[math.ceil(0.95 * n) - 1],
        "count": n,
        "within_tolerance": None if tol is None else sum(1 for x in d if x <= tol) / n,
    }


@action("inspect", access="read", timeout=300)
def compare_meshes(ctx: Ctx, test: Entity, reference: Entity, samples: int = 2000,
                   tolerance: Length | None = None, symmetric: bool = False) -> dict:
    """Deviation statistics from a mesh or BRep body (test) to a reference mesh or BRep body, optionally both ways."""
    ctx.require_design()
    if isinstance(samples, bool) or not isinstance(samples, int) or not 1 <= samples <= _MAX_SAMPLES:
        raise ActionError(INVALID_PARAMS, "samples must be an integer from 1 to %d" % _MAX_SAMPLES)
    f = adsk.fusion
    for e in (test, reference):
        if not isinstance(e, (f.MeshBody, f.BRepBody)):
            raise ActionError(INVALID_PARAMS, "compare_meshes takes mesh or BRep bodies, got %s"
                              % type(e).__name__)
    if not any(isinstance(e, f.MeshBody) for e in (test, reference)):
        raise ActionError(INVALID_PARAMS, "compare_meshes needs at least one mesh body",
                          hints=["use measure_distance for two BRep bodies"])
    t_nodes, t_tris = _triangles(test)
    r_nodes, r_tris = _triangles(reference)
    if len(r_tris) > _MAX_REFERENCE_TRIANGLES:
        raise ActionError(INVALID_PARAMS, "reference mesh too large (%d triangles, limit %d)"
                          % (len(r_tris), _MAX_REFERENCE_TRIANGLES))
    if symmetric and len(t_tris) > _MAX_REFERENCE_TRIANGLES:
        raise ActionError(INVALID_PARAMS, "test mesh too large for symmetric comparison (%d triangles, limit %d)"
                          % (len(t_tris), _MAX_REFERENCE_TRIANGLES))
    tol = tolerance.cm if tolerance is not None else None

    def direction(nodes, tris, other_nodes, other_tris):
        pts = _sample_points(nodes, tris, samples)
        d = _deviation(pts, other_nodes, other_tris)
        st = _stats(d, tol)
        worst = pts[d.index(st["max"])]
        for key in ("min", "max", "mean", "rms", "std", "median", "p95"):
            st[key] = ctx.length_out(st[key])
        st["max_point"] = [ctx.length_out(v) for v in worst]
        return st

    forward = direction(t_nodes, t_tris, r_nodes, r_tris)
    reverse = direction(r_nodes, r_tris, t_nodes, t_tris) if symmetric else None
    return {
        "test": ctx.ref(test),
        "reference": ctx.ref(reference),
        "samples": samples,
        "forward": forward,
        "reverse": reverse,
        "hausdorff": max(forward["max"], reverse["max"]) if symmetric else None,
    }


# --- live scenarios (design § 3.2) -----------------------------------------------------------------------

import pathlib  # noqa: E402
import tempfile  # noqa: E402

from .live_common import _MM, _do, _fresh, _lc, _near  # noqa: E402


async def _i(session, act, params=None):
    return await _do(session, "inspect", act, params or {})


async def _box(session, w, d, h, at=None):
    body = await _lc().box(session, w, d, h)
    if at is not None:
        await _do(session, "model", "move", {"bodies": [body], "translate": at})
    return body


async def _summary(session):
    async with _fresh(session):
        await _box(session, 10, 10, 10)
        r = await _i(session, "summary")
        assert r["design"]["bodies"] == 1 and r["design"]["sketches"] == 1 and r["design"]["components"] == 1, r


async def _object_info(session):
    async with _fresh(session):
        body = await _box(session, 10, 10, 10)
        face = (await _i(session, "list_faces", {"body": body}))["items"][0]["ref"]
        assert (await _i(session, "object_info", {"entity": body}))["type"] == "BRepBody"
        assert (await _i(session, "object_info", {"entity": face}))["type"] == "BRepFace"


async def _list_bodies(session):
    async with _fresh(session):
        await _box(session, 10, 10, 10)
        await _box(session, 10, 10, 10, [30, 0, 0])
        page = await _i(session, "list_bodies", {"limit": 1})
        assert len(page["items"]) == 1 and page["next_offset"] == 1 and page["total"] == 2, page
        ref = page["items"][0]["ref"]
        assert (await _i(session, "object_info", {"entity": ref}))["type"] == "BRepBody"


async def _list_faces(session):
    async with _fresh(session):
        body = await _box(session, 20, 10, 5)
        items = (await _i(session, "list_faces", {"body": body}))["items"]
        assert len(items) == 6 and all(f["surface_type"] == "plane" for f in items), items
        for f in items:
            n = sorted(abs(c) for c in f["normal"])
            assert _near(n[2], 1, 1e-6) and _near(n[0], 0, 1e-6) and _near(n[1], 0, 1e-6), f


async def _list_edges(session):
    async with _fresh(session):
        body = await _box(session, 20, 10, 5)
        items = (await _i(session, "list_edges", {"body": body}))["items"]
        assert len(items) == 12 and all(e["curve_type"] == "line" for e in items), items
        assert _near(sum(e["length"] for e in items), 140, 1e-6), items


async def _geometry(session):
    async with _fresh(session):
        body = await _box(session, 20, 10, 5)
        face = (await _i(session, "list_faces", {"body": body}))["items"][0]["ref"]
        r = await _i(session, "geometry", {"entity": face})
        assert len(r["edges"]) == 4 and len(r["adjacent_faces"]) == 4, r


async def _bounds(session):
    async with _fresh(session):
        body = await _box(session, 20, 10, 5)
        for precise in (False, True):
            r = await _i(session, "bounds", {"entity": body, "precise": precise})
            assert all(_near(a, b) for a, b in zip(r["size"], (20, 10, 5))), r


async def _measure_distance(session):
    async with _fresh(session):
        a = await _box(session, 10, 10, 10)
        b = await _box(session, 10, 10, 10, [40, 0, 0])
        assert _near((await _i(session, "measure_distance", {"a": a, "b": b}))["distance"], 30, 1e-6)


async def _measure_angle(session):
    async with _fresh(session):
        body = await _box(session, 20, 10, 5)
        faces = (await _i(session, "list_faces", {"body": body}))["items"]
        first = await _i(session, "geometry", {"entity": faces[0]["ref"]})
        other = faces[first["adjacent_faces"][0]]["ref"]
        r = await _i(session, "measure_angle", {"a": faces[0]["ref"], "b": other})
        assert _near(r["angle"], 90, 1e-6), r


async def _physical_properties(session):
    async with _fresh(session):
        body = await _box(session, 20, 10, 5)
        item = (await _i(session, "physical_properties", {"entities": [body]}))["items"][0]
        assert _near(item["volume"], 1000, 1e-6) and _near(item["area"], 700, 1e-6), item
        assert abs(item["mass"] - item["volume"] * item["density"]) <= 1e-9 * max(1.0, item["mass"]), item


async def _interference(session):
    async with _fresh(session):
        await _box(session, 10, 10, 10)
        await _box(session, 10, 10, 10, [5, 0, 0])
        r = await _i(session, "interference")
        assert r["total"] == 1 and _near(r["items"][0]["volume"], 500, 1e-3), r
        assert (await _i(session, "summary"))["design"]["bodies"] == 2


async def _compare_meshes(session):
    async with _fresh(session):
        five = await _box(session, 20, 10, 5)
        six = await _box(session, 20, 10, 6)
        meshes = []
        with tempfile.TemporaryDirectory() as tmp:
            for name, body in (("five", five), ("six", six)):
                path = str(pathlib.Path(tmp) / (name + ".stl"))
                await _do(session, "document", "export", {"path": path, "format": "stl", "body": body})
                meshes.append((await _do(session, "document", "import_mesh", {"path": path, "unit": "mm"}))["meshes"][0])
        own = await _i(session, "compare_meshes", {"test": meshes[0], "reference": five})
        assert own["forward"]["max"] < 0.001, own
        diff = await _i(session, "compare_meshes", {"test": meshes[1], "reference": meshes[0]})
        assert _near(diff["forward"]["max"], 1, 0.001), diff


async def _get_selection(session):
    assert (await _i(session, "get_selection"))["total"] == 0


async def _section_analysis(session):
    async with _fresh(session):
        await _box(session, 10, 10, 10)
        env = await _lc().call(session, "inspect", "section_analysis", {"plane": "XY", "offset": 2}, units=_MM)
        if not env["ok"]:
            assert env["error"]["code"] == "UNSUPPORTED", env
            print("section_analysis: UNSUPPORTED")
            return
        first = env["result"]["analysis"]
        assert first["name"], first
        second = (await _do(session, "inspect", "section_analysis", {"plane": "XY", "offset": 2}))["analysis"]
        assert second["index"] == first["index"] + 1, (first, second)


LIVE_SCENARIOS = [(f.__name__[1:], "inspect." + f.__name__[1:], f) for f in (
    _summary, _object_info, _list_bodies, _list_faces, _list_edges, _geometry, _bounds, _measure_distance,
    _measure_angle, _physical_properties, _interference, _compare_meshes, _get_selection, _section_analysis)]
