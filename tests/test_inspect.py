"""inspect tool unit tests (fake adsk)."""
import math
import random

import pytest
from adsk import core, fusion

from defusion.context import Ctx
from defusion.types import Value
from defusion.tools import inspect as ins
from defusion.units import OutputUnits
from helpers import raises_action


def make_ctx(app, out=None):
    return Ctx.for_app(app, [], [], out or OutputUnits.for_call(None))


@pytest.fixture
def ctx(fake_app, fake_design):
    return make_ctx(fake_app)


def P(x, y, z):
    return core.Point3D(x, y, z)


def V(x, y, z):
    return core.Vector3D(x, y, z)


def bbox(lo, hi):
    return core.BoundingBox3D(P(*lo), P(*hi))


# ---- helpers ------------------------------------------------------------------------------------


def test_helpers_pt_converts_units(fake_app):
    c = make_ctx(fake_app, OutputUnits.for_call({"length": "mm"}))
    assert ins._pt(c, P(1, 2, 3)) == pytest.approx([10, 20, 30])


def test_helpers_vec_normalises_a_copy():
    v = V(0, 3, 4)
    assert ins._vec(v) == pytest.approx([0, 0.6, 0.8])
    assert (v.x, v.y, v.z) == (0, 3, 4)


def test_helpers_bbox(ctx):
    d = ins._bbox(ctx, bbox((0, 0, 0), (2, 1, 0.5)))
    assert d["min"] == [0, 0, 0] and d["max"] == pytest.approx([20, 10, 5])
    assert d["size"] == pytest.approx([20, 10, 5]) and d["center"] == pytest.approx([10, 5, 2.5])
    assert ins._bbox(ctx, None) is None


def test_helpers_bodies_whole_design_and_component(fake_design):
    root = fake_design.rootComponent
    b = root.add_body("B")
    m = root.add_mesh_body("M")
    occ = root.add_occurrence("O")
    proxy = fusion.BRepBody("P", occ.component)
    occ._path = "O:1"
    occ.bRepBodies = core.Collection([proxy])
    occ.meshBodies = core.Collection([])
    got = ins._bodies(fake_design)
    assert got == [(b, "brep", ""), (m, "mesh", ""), (proxy, "brep", "O:1")]
    assert ins._bodies(fake_design, root) == [(b, "brep", ""), (m, "mesh", "")]


def test_helpers_index_of_by_token(fake_design):
    body = fake_design.rootComponent.add_body("B")
    f0, f1 = body.add_face(), body.add_face()
    other = fusion.BRepFace(body)
    assert ins._index_of(body.faces, f1) == 1
    assert ins._index_of(body.faces, f0) == 0
    assert ins._index_of(body.faces, other) is None
    cache = {}
    assert ins._index_of(body.faces, f1, cache) == 1 and len(cache) == 2


# ---- summary ------------------------------------------------------------------------------------


def _full_design(app, parametric=True):
    design = fusion.Design(parametric=parametric)
    doc = core.Document("Doc", {"DesignProductType": design})
    doc.isSaved, doc.isModified = False, True
    app.activeDocument = doc
    design.add_parameter("d1", 1.0)
    design.add_parameter("d2", 2.0)
    root = design.rootComponent
    root.add_body("B")
    root.add_sketch("S")
    root.add_feature("E")
    return design


def test_summary_parametric(fake_app):
    design = _full_design(fake_app)
    design.designIntent = fusion.DesignIntentTypes.PartDesignIntentType
    r = ins.summary(make_ctx(fake_app))
    assert r["document"] == {"name": "Doc", "saved": False, "modified": True, "version": None, "file_id": None}
    d = r["design"]
    assert d["type"] == "parametric" and d["intent"] == "part" and d["units"] == "mm"
    assert d["active_component"]["name"] == "Root"
    assert (d["components"], d["occurrences"], d["bodies"], d["mesh_bodies"]) == (1, 0, 1, 0)
    assert (d["sketches"], d["features"], d["parameters"]) == (1, 1, 2)
    assert (d["timeline"], d["timeline_marker"]) == (2, 2)


def test_summary_direct_has_no_timeline_and_no_intent(fake_app):
    _full_design(fake_app, parametric=False)
    d = ins.summary(make_ctx(fake_app))["design"]
    assert d["type"] == "direct" and d["intent"] is None
    assert d["timeline"] is None and d["timeline_marker"] is None


def test_summary_counts_subcomponents(fake_app):
    design = _full_design(fake_app)
    design.rootComponent.add_occurrence("O").component.add_body("X")
    design._subcomponents.append(design.rootComponent)  # real allComponents includes the root
    assert ins.summary(make_ctx(fake_app))["design"]["components"] == 2
    assert ins.summary(make_ctx(fake_app))["design"]["bodies"] == 2


def test_summary_datafile(fake_app):
    _full_design(fake_app)
    df = core.Base()
    df.versionNumber, df.id = 3, "urn:1"
    fake_app.activeDocument.dataFile = df
    d = ins.summary(make_ctx(fake_app))["document"]
    assert (d["version"], d["file_id"]) == (3, "urn:1")


def test_summary_no_document(fake_app):
    assert raises_action(ins.summary, make_ctx(fake_app)).code == "NO_DOCUMENT"


def test_summary_non_design_document(fake_app):
    fake_app.activeDocument = core.Document("Drawing", {})
    assert ins.summary(make_ctx(fake_app))["design"] is None


# ---- object_info --------------------------------------------------------------------------------


def info(ctx, obj):
    return ins.object_info(ctx, obj)


def test_object_info_body(ctx, fake_design):
    root = fake_design.rootComponent
    b = root.add_body("B", bbox=((0, 0, 0), (2, 1, 0.5)))
    b.isSolid, b.isVisible, b.volume, b.area = True, True, 1.0, 7.0
    b.add_face(), b.add_face(), b.add_edge(), b.add_vertex()
    b.material = core.Base()
    b.material.name = "Steel"
    b.assemblyContext = None
    r = info(ctx, b)
    assert r["type"] == "BRepBody" and r["ref"]["token"] == b.entityToken
    p = r["properties"]
    assert (p["is_solid"], p["faces"], p["edges"], p["vertices"], p["material"]) == (True, 2, 1, 1, "Steel")
    assert p["volume"] == pytest.approx(1000) and p["area"] == pytest.approx(700)
    assert p["appearance"] is None  # failing read -> None
    assert p["occurrence_path"] == ""
    assert r["bbox"]["size"] == pytest.approx([20, 10, 5])
    assert r["parent"]["name"] == "Root"


def test_object_info_failing_read_gives_none_for_that_key_only(ctx, fake_design):
    b = fake_design.rootComponent.add_body("B")
    b._bbox_error = True
    r = info(ctx, b)
    assert r["bbox"] is None and r["properties"]["is_visible"] is True


def test_object_info_mesh_body(ctx, fake_design):
    m = fake_design.rootComponent.add_mesh_body("M")
    m.isVisible = True
    m.mesh = core.Base()
    m.mesh.nodeCount, m.mesh.triangleCount = 8, 12
    r = info(ctx, m)
    assert r["type"] == "MeshBody"
    assert r["properties"] == {"is_visible": True, "nodes": 8, "triangles": 12}
    assert r["parent"]["name"] == "Root"


def test_object_info_face_edge_vertex(ctx, fake_design):
    b = fake_design.rootComponent.add_body("B")
    f0, f1 = b.add_face(), b.add_face()
    e = b.add_edge()
    v = b.add_vertex()
    f1.area = 2.0
    f1.geometry = core.Plane()
    f1.geometry.surfaceType = core.SurfaceTypes.PlaneSurfaceType
    f1.pointOnFace = P(0, 0, 0)
    f1.evaluator = core.Base()
    f1.evaluator.getNormalAtPoint = lambda p: (True, V(0, 0, 2))
    rf = info(ctx, f1)
    assert rf["type"] == "BRepFace" and rf["properties"]["index"] == 1
    assert rf["properties"]["surface"] == {"type": "plane", "normal": [0, 0, 1]}
    assert rf["properties"]["area"] == pytest.approx(200)
    assert rf["parent"]["token"] == b.entityToken
    e.length = 1.5
    e.geometry = core.Line3D()
    e.geometry.curveType = core.Curve3DTypes.Line3DCurveType
    re_ = info(ctx, e)
    assert re_["type"] == "BRepEdge" and re_["properties"]["index"] == 0
    assert re_["properties"]["length"] == pytest.approx(15) and re_["properties"]["curve"] == {"type": "line"}
    v.geometry = P(1, 2, 3)
    rv = info(ctx, v)
    assert rv["type"] == "BRepVertex" and rv["properties"]["point"] == pytest.approx([10, 20, 30])
    assert rv["properties"]["index"] == 0 and f0 is not None


def test_object_info_component_parent_and_counts(ctx, fake_design):
    root = fake_design.rootComponent
    occ = root.add_occurrence("O")
    comp = occ.component
    comp.partNumber, comp.description = "PN", "d"
    comp.add_body("X")
    occ.assemblyContext = None
    r = info(ctx, comp)
    assert r["type"] == "Component"
    assert r["properties"]["bodies"] == 1 and r["properties"]["part_number"] == "PN"
    assert r["parent"]["name"] == "Root"
    assert info(ctx, root)["parent"] is None
    # nested: parent is the component of the occurrence's assemblyContext
    outer = root.add_occurrence("Outer")
    occ.assemblyContext = outer
    outer.component.name = "OuterComp"
    assert info(ctx, comp)["parent"]["name"] == "OuterComp"


def test_object_info_occurrence(ctx, fake_design):
    root = fake_design.rootComponent
    occ = root.add_occurrence("O")
    occ._path, occ.isVisible, occ.isGrounded, occ.isLightBulbOn = "O:1", True, False, True
    occ.transform2 = core.Base()
    occ.transform2.asArray = lambda: [1, 0, 0, 1.0, 0, 1, 0, 2.0, 0, 0, 1, 3.0, 0, 0, 0, 1]
    occ.assemblyContext = None
    r = info(ctx, occ)
    assert r["type"] == "Occurrence"
    assert r["properties"]["transform"] == pytest.approx([1, 0, 0, 10, 0, 1, 0, 20, 0, 0, 1, 30, 0, 0, 0, 1])
    assert r["properties"]["full_path"] == "O:1" and r["properties"]["is_grounded"] is False
    assert r["parent"]["name"] == "Root"


def test_object_info_sketch(ctx, fake_design):
    s = fake_design.rootComponent.add_sketch("S")
    s.add_profile()
    s.sketchCurves, s.sketchPoints = core.Collection([1, 2, 3]), core.Collection([1])
    s.isFullyConstrained, s.isVisible = True, False
    s.referencePlane = fake_design.rootComponent.xYConstructionPlane
    r = info(ctx, s)
    assert r["type"] == "Sketch" and r["bbox"] is None
    assert r["properties"]["profiles"] == 1 and r["properties"]["curves"] == 3
    assert r["properties"]["points"] == 1 and r["properties"]["is_fully_constrained"] is True
    assert r["properties"]["reference_plane"]["name"] == "XY"
    assert r["parent"]["name"] == "Root"


def test_object_info_feature_timeline_index_and_direct_mode(ctx, fake_design, fake_app):
    f = fake_design.rootComponent.add_feature("E")
    f.healthState, f.isSuppressed = 0, False
    r = info(ctx, f)
    assert r["type"] == "ExtrudeFeature"
    assert r["properties"]["timeline_index"] == 0 and r["properties"]["is_suppressed"] is False
    assert r["parent"]["name"] == "Root"
    direct = fusion.Design(parametric=False)
    g = direct.rootComponent.add_feature("G")
    fake_app.activeDocument = core.Document("D", {"DesignProductType": direct})
    assert info(make_ctx(fake_app), g)["properties"]["timeline_index"] is None


def test_object_info_parameter_units(ctx, fake_design):
    p = fake_design.add_parameter("d1", 2.0)
    p.expression, p.unit, p.comment = "20 mm", "mm", "c"
    r = info(ctx, p)
    assert r["type"] == "UserParameter" and r["bbox"] is None and r["parent"] is None
    assert r["properties"] == {"expression": "20 mm", "value": pytest.approx(20), "unit": "mm", "comment": "c"}
    a = fake_design.add_parameter("a", 3.141592653589793 / 2, user=False)
    a.expression, a.unit, a.comment = "90 deg", "deg", ""
    assert info(ctx, a)["properties"]["value"] == pytest.approx(90)
    n = fake_design.add_parameter("n", 5.0)
    n.expression, n.unit, n.comment = "5", "", ""
    assert info(ctx, n)["properties"]["value"] == 5.0


def test_object_info_construction_and_joint(ctx, fake_design):
    root = fake_design.rootComponent
    pl = root.add_construction_plane("CP")
    pl.isVisible = True
    pl.definition = core.Base()
    r = info(ctx, pl)
    assert r["properties"] == {"is_visible": True, "definition": "Base"} or r["properties"]["is_visible"] is True
    assert r["parent"]["name"] == "Root"
    j = root.add_joint("J")
    j.isSuppressed = False
    j.jointMotion = core.Base()
    j.jointMotion.jointType = 0
    o1, o2 = root.add_occurrence("A"), root.add_occurrence("B")
    j.occurrenceOne, j.occurrenceTwo = o1, o2
    rj = info(ctx, j)
    assert rj["type"] == "Joint"
    assert rj["properties"]["occurrence_one"]["name"] == "A"
    assert rj["properties"]["occurrence_two"]["name"] == "B"
    assert rj["properties"]["is_suppressed"] is False and rj["parent"]["name"] == "Root"


def test_object_info_other_type_gives_name(ctx, fake_design):
    jo = fake_design.rootComponent.add_joint_origin("JO")
    r = info(ctx, jo)
    assert r["type"] == "JointOrigin" and r["properties"] == {"name": "JO"} and r["parent"] is None


def test_object_info_requires_design(fake_app):
    assert raises_action(ins.object_info, make_ctx(fake_app), core.Base()).code == "NO_DESIGN"


# ---- topology builders -----------------------------------------------------------------------------


def surf(kind, **attrs):
    g = core.Base()
    g.surfaceType = getattr(core.SurfaceTypes, kind + "SurfaceType")
    for k, v in attrs.items():
        setattr(g, k, v)
    return g


def add_face(body, geometry, area=1.0, normal=(0, 0, 1), centroid=(0, 0, 0)):
    f = body.add_face(geometry)
    f.area = area
    f.centroid = P(*centroid)
    f.pointOnFace = P(*centroid)
    f.evaluator = core.Base()
    f.evaluator.getNormalAtPoint = lambda p, n=normal: (True, V(*n))
    return f


def curve(kind, **attrs):
    g = core.Base()
    g.curveType = getattr(core.Curve3DTypes, kind + "CurveType")
    for k, v in attrs.items():
        setattr(g, k, v)
    return g


def vertex(body, x, y, z):
    v = body.add_vertex()
    v.geometry = P(x, y, z)
    return v


def add_edge(body, geometry, length, start=None, end=None):
    e = body.add_edge(geometry)
    e.length = length
    e.startVertex, e.endVertex = start, end
    return e


def new_box(root, name="Box", w=2.0, d=1.0, h=0.5, origin=(0, 0, 0)):
    """A fake box body: 6 plane faces, 8 vertices and 12 line edges, sizes in cm."""
    x0, y0, z0 = origin
    b = root.add_body(name, bbox=(origin, (x0 + w, y0 + d, z0 + h)))
    b.isSolid, b.isVisible, b.volume, b.area = True, True, w * d * h, 2 * (w * d + d * h + w * h)
    plane = lambda: surf("Plane", origin=P(0, 0, 0), uDirection=V(1, 0, 0))
    for n, a in (((1, 0, 0), d * h), ((-1, 0, 0), d * h), ((0, 1, 0), w * h),
                 ((0, -1, 0), w * h), ((0, 0, 1), w * d), ((0, 0, -1), w * d)):
        add_face(b, plane(), a, n, (x0 + w / 2, y0 + d / 2, z0 + h / 2))
    vs = [vertex(b, x0 + dx * w, y0 + dy * d, z0 + dz * h) for dx in (0, 1) for dy in (0, 1) for dz in (0, 1)]
    for i in range(12):
        add_edge(b, curve("Line3D", startPoint=P(0, 0, 0), endPoint=P(1, 0, 0)), [w, d, h][i % 3],
                 vs[i % 8], vs[(i + 1) % 8])
    return b


# ---- list_bodies --------------------------------------------------------------------------------


def test_list_bodies_rows_and_page_keys(ctx, fake_design):
    root = fake_design.rootComponent
    b = new_box(root, "B")
    m = root.add_mesh_body("M")
    m.isVisible = False
    r = ins.list_bodies(ctx)
    assert set(r) == {"items", "total", "offset", "next_offset"} and r["total"] == 2
    first, second = r["items"]
    assert first["ref"]["token"] == b.entityToken and first["kind"] == "brep"
    assert (first["is_solid"], first["faces"], first["edges"]) == (True, 6, 12)
    assert first["volume"] == pytest.approx(1000) and first["area"] == pytest.approx(700)
    assert first["occurrence_path"] == ""
    assert second["kind"] == "mesh" and second["is_visible"] is False
    assert (second["is_solid"], second["volume"], second["area"], second["faces"], second["edges"]) == (None,) * 5


def test_list_bodies_limit_gives_next_offset(ctx, fake_design):
    root = fake_design.rootComponent
    new_box(root, "A"), new_box(root, "B")
    r = ins.list_bodies(ctx, limit=1)
    assert len(r["items"]) == 1 and r["next_offset"] == 1 and r["total"] == 2
    assert ins.list_bodies(ctx, offset=1, limit=1)["next_offset"] is None


def test_list_bodies_component_scope_and_proxies(ctx, fake_design):
    root = fake_design.rootComponent
    new_box(root, "RootBody")
    occ = root.add_occurrence("O")
    occ._path = "O:1"
    inner = new_box(occ.component, "Inner")
    occ.bRepBodies, occ.meshBodies = core.Collection([inner]), core.Collection([])
    rows = ins.list_bodies(ctx)["items"]
    assert [(x["ref"]["name"], x["occurrence_path"]) for x in rows] == [("RootBody", ""), ("Inner", "O:1")]
    only = ins.list_bodies(ctx, component=root)["items"]
    assert [x["ref"]["name"] for x in only] == ["RootBody"]


def test_list_bodies_units_in_inches(fake_app, fake_design):
    new_box(fake_design.rootComponent, "B")
    c = make_ctx(fake_app, OutputUnits.for_call({"length": "in"}))
    r = ins.list_bodies(c)["items"][0]
    assert r["area"] == pytest.approx(7.0 / 6.4516) and r["volume"] == pytest.approx(1.0 / 16.387064)


class _Exploding(fusion.BRepBody):
    isVisible = property(lambda s: (_ for _ in ()).throw(RuntimeError("row built")), lambda s, v: None)


def test_list_bodies_builds_rows_only_for_page_slice(ctx, fake_design):
    root = fake_design.rootComponent
    new_box(root, "A")
    root._bodies.append(_Exploding("B", root))
    assert ins.list_bodies(ctx, limit=1)["total"] == 2
    with pytest.raises(AttributeError):  # the second row is built only when requested
        ins.list_bodies(ctx, offset=1, limit=1)


def test_list_bodies_requires_design(fake_app):
    assert raises_action(ins.list_bodies, make_ctx(fake_app)).code == "NO_DESIGN"


# ---- list_faces ---------------------------------------------------------------------------------


def test_list_faces_box_planes_and_normals(ctx, fake_design):
    b = new_box(fake_design.rootComponent)
    r = ins.list_faces(ctx, b)
    assert r["total"] == 6 and set(r) == {"items", "total", "offset", "next_offset"}
    normals = sorted(tuple(i["normal"]) for i in r["items"])
    assert normals == sorted([(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)])
    f0 = r["items"][0]
    assert f0["index"] == 0 and f0["surface_type"] == "plane" and f0["ref"]["token"] == b.faces.item(0).entityToken
    assert f0["area"] == pytest.approx(50.0) and f0["centroid"] == pytest.approx([10, 5, 2.5])
    assert "type" not in f0


def test_list_faces_area_scales_with_units(fake_app, fake_design):
    b = new_box(fake_design.rootComponent)
    cm = ins.list_faces(make_ctx(fake_app, OutputUnits.for_call({"length": "cm"})), b)["items"][0]["area"]
    inch = ins.list_faces(make_ctx(fake_app, OutputUnits.for_call({"length": "in"})), b)["items"][0]["area"]
    assert inch == pytest.approx(cm / 6.4516)


def test_list_faces_curved_surface_extras(ctx, fake_design):
    b = fake_design.rootComponent.add_body("C")
    add_face(b, surf("Cylinder", radius=0.5, axis=V(0, 0, 2), origin=P(1, 0, 0)))
    add_face(b, surf("Cone", radius=0.5, axis=V(0, 0, 1), halfAngle=0.5, origin=P(0, 0, 0)))
    add_face(b, surf("Sphere", radius=2.0, origin=P(0, 1, 0)))
    add_face(b, surf("Torus", majorRadius=3.0, minorRadius=1.0, axis=V(1, 0, 0), origin=P(0, 0, 1)))
    add_face(b, surf("Nurbs"))
    add_face(b, surf("EllipticalCylinder"))
    add_face(b, surf("EllipticalCone"))
    cyl, cone, sph, tor, nurbs, ecyl, econe = ins.list_faces(ctx, b)["items"]
    assert cyl["surface_type"] == "cylinder" and cyl["radius"] == pytest.approx(5)
    assert cyl["axis"] == [0, 0, 1] and cyl["origin"] == pytest.approx([10, 0, 0])
    assert cone["surface_type"] == "cone" and cone["half_angle"] == pytest.approx(28.6478897565)
    assert cone["radius"] == pytest.approx(5)
    assert sph["surface_type"] == "sphere" and sph["radius"] == pytest.approx(20)
    assert sph["center"] == pytest.approx([0, 10, 0])
    assert tor["surface_type"] == "torus" and tor["major_radius"] == pytest.approx(30)
    assert tor["minor_radius"] == pytest.approx(10) and tor["axis"] == [1, 0, 0]
    assert tor["center"] == pytest.approx([0, 0, 10])
    assert [x["surface_type"] for x in (nurbs, ecyl, econe)] == ["nurbs", "elliptical_cylinder", "elliptical_cone"]


def test_list_faces_paging_and_slice_only(ctx, fake_design):
    b = new_box(fake_design.rootComponent)
    del b._faces[5].__dict__["area"]  # rows beyond the slice are never built
    r = ins.list_faces(ctx, b, offset=2, limit=2)
    assert [i["index"] for i in r["items"]] == [2, 3] and r["next_offset"] == 4


# ---- list_edges ---------------------------------------------------------------------------------


def test_list_edges_box_lines_sum(ctx, fake_design):
    b = new_box(fake_design.rootComponent)
    r = ins.list_edges(ctx, b)
    assert r["total"] == 12 and {e["curve_type"] for e in r["items"]} == {"line"}
    assert sum(e["length"] for e in r["items"]) == pytest.approx(4 * (20 + 10 + 5))
    e0 = r["items"][0]
    assert e0["index"] == 0 and e0["ref"]["token"] == b.edges.item(0).entityToken
    assert e0["start"] == pytest.approx([0, 0, 0]) and e0["end"] == pytest.approx([0, 0, 5])
    assert "center" not in e0


def test_list_edges_circle_and_closed_edge_without_vertices(ctx, fake_design):
    b = fake_design.rootComponent.add_body("C")
    add_edge(b, curve("Circle3D", center=P(1, 2, 3), radius=0.25), 1.5)
    add_edge(b, curve("Arc3D", center=P(0, 0, 0), radius=1.0), 1.0, vertex(b, 1, 0, 0), vertex(b, 0, 1, 0))
    add_edge(b, curve("Ellipse3D"), 1.0)
    add_edge(b, curve("NurbsCurve3D"), 1.0)
    add_edge(b, curve("InfiniteLine3D"), 1.0)
    circle, arc, ell, nurbs, other = ins.list_edges(ctx, b)["items"]
    assert circle["curve_type"] == "circle" and circle["center"] == pytest.approx([10, 20, 30])
    assert circle["radius"] == pytest.approx(2.5) and circle["start"] is None and circle["end"] is None
    assert arc["curve_type"] == "arc" and arc["start"] == pytest.approx([10, 0, 0]) and arc["radius"] == pytest.approx(10)
    assert [ell["curve_type"], nurbs["curve_type"], other["curve_type"]] == ["ellipse", "nurbs", "other"]


def test_list_edges_paging(ctx, fake_design):
    b = new_box(fake_design.rootComponent)
    r = ins.list_edges(ctx, b, offset=10, limit=5)
    assert [e["index"] for e in r["items"]] == [10, 11] and r["next_offset"] is None


# ---- geometry -----------------------------------------------------------------------------------


def topo_box(root, name="T"):
    """A box with real adjacency: 6 faces (+x,-x,+y,-y,+z,-z), 12 edges, 8 vertices."""
    b = new_box(root, name)
    b.lumps, b.shells = core.Collection([1]), core.Collection([1])
    faces = list(b.faces)
    for f in faces:
        f.edges = core.Collection([])
        f.loops = core.Collection([1])
    for e in b.edges:
        e.faces = core.Collection([])
    k = 0
    for fa, fb in [(a, c) for a in range(6) for c in range(a + 1, 6) if a // 2 != c // 2]:
        e = b.edges.item(k)
        k += 1
        for f in (faces[fa], faces[fb]):
            e.faces._items.append(f)
            f.edges._items.append(e)
    for v in b.vertices:
        v.edges = core.Collection([])
    for e in b.edges:
        for v in (e.startVertex, e.endVertex):
            v.edges._items.append(e)
    return b


def test_geometry_face_plane_edges_and_adjacency(ctx, fake_design):
    b = topo_box(fake_design.rootComponent)
    r = ins.geometry(ctx, b.faces.item(0))
    assert r["index"] == 0 and r["ref"]["token"] == b.faces.item(0).entityToken
    assert r["surface"]["type"] == "plane" and r["surface"]["normal"] == [1, 0, 0]
    assert r["surface"]["origin"] == [0, 0, 0] and r["surface"]["u_direction"] == [1, 0, 0]
    assert len(r["edges"]) == 4 and sorted(r["adjacent_faces"]) == [2, 3, 4, 5]
    assert r["loops"] == 1 and r["area"] == pytest.approx(50) and r["centroid"] == pytest.approx([10, 5, 2.5])
    assert r["perimeter"] == pytest.approx(sum(b.edges.item(i).length for i in r["edges"]) * 10)


def test_geometry_face_adjacent_deduplicated(ctx, fake_design):
    b = topo_box(fake_design.rootComponent)
    f0, f2 = b.faces.item(0), b.faces.item(2)
    extra = b.edges.item(0)  # already shared by f0 and f2; list it twice on f0
    f0.edges._items.append(extra)
    r = ins.geometry(ctx, f0)
    assert sorted(r["adjacent_faces"]) == [2, 3, 4, 5] and len(r["edges"]) == 5 and f2 is not None


def test_geometry_face_curved_full_parameters(ctx, fake_design):
    b = topo_box(fake_design.rootComponent)
    f = b.faces.item(0)
    f.geometry = surf("Cylinder", radius=0.5, axis=V(0, 0, 1), origin=P(1, 0, 0))
    r = ins.geometry(ctx, f)["surface"]
    assert r["type"] == "cylinder" and r["radius"] == pytest.approx(5) and r["origin"] == pytest.approx([10, 0, 0])
    f.geometry = surf("Nurbs", controlPointCountU=2, controlPointCountV=2)
    f.geometry.getData = lambda: (True, [P(0, 0, 0), P(1, 0, 0), P(0, 1, 0), P(1, 1, 0)], 1, 1)
    n = ins.geometry(ctx, f)["surface"]
    assert n["degree_u"] == 1 and n["degree_v"] == 1 and len(n["control_points"]) == 4
    assert n["control_point_counts"] == [2, 2] and n["control_points"][1] == pytest.approx([10, 0, 0])


def test_geometry_edge(ctx, fake_design):
    b = topo_box(fake_design.rootComponent)
    e = b.edges.item(0)
    r = ins.geometry(ctx, e)
    assert r["index"] == 0 and r["curve"]["type"] == "line" and r["curve"]["direction"] == [1, 0, 0]
    assert r["length"] == pytest.approx(20) and len(r["faces"]) == 2 and len(r["vertices"]) == 2
    assert r["start"] == pytest.approx([0, 0, 0])
    e.geometry = curve("Arc3D", center=P(0, 0, 0), radius=1.0, normal=V(0, 0, 3), startAngle=0.0, endAngle=1.5707963267948966)
    c = ins.geometry(ctx, e)["curve"]
    assert c["normal"] == [0, 0, 1] and c["end_angle"] == pytest.approx(90) and c["start_angle"] == 0
    e.geometry = curve("Circle3D", center=P(0, 0, 0), radius=1.0, normal=V(1, 0, 0))
    assert ins.geometry(ctx, e)["curve"]["normal"] == [1, 0, 0]
    e.geometry = curve("Ellipse3D", majorRadius=2.0, minorRadius=1.0)
    c = ins.geometry(ctx, e)["curve"]
    assert c["major_radius"] == pytest.approx(20) and c["minor_radius"] == pytest.approx(10)
    e.geometry = curve("NurbsCurve3D")
    e.geometry.getData = lambda: (True, [P(0, 0, 0), P(1, 1, 1)], 3)
    c = ins.geometry(ctx, e)["curve"]
    assert c["degree"] == 3 and c["control_points"][1] == pytest.approx([10, 10, 10])


def test_geometry_vertex(ctx, fake_design):
    b = topo_box(fake_design.rootComponent)
    v = b.vertices.item(0)
    r = ins.geometry(ctx, v)
    assert r["index"] == 0 and r["point"] == pytest.approx([0, 0, 0])
    assert r["edges"] == [i for i, e in enumerate(b.edges) if v in (e.startVertex, e.endVertex)]


def test_geometry_body(ctx, fake_design):
    b = topo_box(fake_design.rootComponent)
    r = ins.geometry(ctx, b)
    assert r["kind"] == "brep" and r["is_solid"] is True and r["lumps"] == 1 and r["shells"] == 1
    assert (r["faces"], r["edges"], r["vertices"]) == (6, 12, 8)
    assert r["surface_types"] == {"plane": 6} and r["bbox"]["size"] == pytest.approx([20, 10, 5])
    assert r["volume"] == pytest.approx(1000)


def test_geometry_other_entity_is_invalid_params(ctx, fake_design):
    s = fake_design.rootComponent.add_sketch("S")
    e = raises_action(ins.geometry, ctx, s)
    assert e.code == "INVALID_PARAMS" and "body, face, edge or vertex" in e.message
    assert raises_action(ins.geometry, ctx, fake_design.rootComponent.add_mesh_body("M")).code == "INVALID_PARAMS"


# ---- bounds -------------------------------------------------------------------------------------


def test_bounds_default_body_and_whole_design(ctx, fake_design):
    root = fake_design.rootComponent
    b = new_box(root)
    r = ins.bounds(ctx, b)
    assert r["precise"] is False and r["ref"]["token"] == b.entityToken
    assert r["size"] == pytest.approx([20, 10, 5]) and r["center"] == pytest.approx([10, 5, 2.5])
    assert r["min"] == [0, 0, 0] and r["max"] == pytest.approx([20, 10, 5])
    root.boundingBox = bbox((-1, -1, -1), (1, 1, 1))
    whole = ins.bounds(ctx)
    assert whole["ref"] is None and whole["size"] == pytest.approx([20, 20, 20])


def test_bounds_object_without_bounding_box_is_invalid_params(ctx, fake_design):
    s = fake_design.rootComponent.add_sketch("S")
    assert raises_action(ins.bounds, ctx, s).code == "INVALID_PARAMS"


def test_bounds_precise_uses_oriented_box(fake_app, ctx, fake_design):
    b = new_box(fake_design.rootComponent)
    fake_app.measureManager.obb = core.OrientedBoundingBox3D(P(1, 0.5, 0.25), 2.0, 1.0, 0.5)
    r = ins.bounds(ctx, b, precise=True)
    assert r["precise"] is True and r["size"] == pytest.approx([20, 10, 5])
    assert r["min"] == pytest.approx([0, 0, 0]) and r["max"] == pytest.approx([20, 10, 5])
    assert r["center"] == pytest.approx([10, 5, 2.5])
    kind, entity, ld, wd = fake_app.measureManager.args[0]
    assert entity is b and (ld.x, ld.y, ld.z, wd.x, wd.y, wd.z) == (1, 0, 0, 0, 1, 0)


def test_bounds_precise_unsupported_without_api(fake_app, ctx, fake_design):
    b = new_box(fake_design.rootComponent)
    fake_app.measureManager = core.Base()
    assert raises_action(ins.bounds, ctx, b, precise=True).code == "UNSUPPORTED"
    del fake_app.measureManager
    assert raises_action(ins.bounds, ctx, b, precise=True).code == "UNSUPPORTED"


# ---- measure ------------------------------------------------------------------------------------


def test_measure_distance(fake_app, ctx, fake_design):
    a, b = new_box(fake_design.rootComponent, "A"), new_box(fake_design.rootComponent, "B")
    fake_app.measureManager.distance = core.MeasureResults(3.0, P(2, 0, 0), P(5, 0, 0.5))
    r = ins.measure_distance(ctx, a, b)
    assert r["distance"] == pytest.approx(30) and r["point_a"] == pytest.approx([20, 0, 0])
    assert r["point_b"] == pytest.approx([50, 0, 5]) and r["delta"] == pytest.approx([30, 0, 5])
    assert fake_app.measureManager.args == [("distance", a, b)]


def test_measure_distance_inches(fake_app, fake_design):
    a = new_box(fake_design.rootComponent)
    fake_app.measureManager.distance = core.MeasureResults(2.54, P(0, 0, 0), P(2.54, 0, 0))
    c = make_ctx(fake_app, OutputUnits.for_call({"length": "in"}))
    assert ins.measure_distance(c, a, a)["distance"] == pytest.approx(1.0)


def test_measure_angle_two_entities_in_default_and_radians(fake_app, ctx, fake_design):
    b = topo_box(fake_design.rootComponent)
    fake_app.measureManager.angle = core.MeasureResults(1.5707963267948966, P(0, 0, 0), P(1, 0, 0))
    r = ins.measure_angle(ctx, b.faces.item(0), b.faces.item(2))
    assert r["angle"] == pytest.approx(90) and len(r["points"]) == 2
    assert fake_app.measureManager.args[-1] == ("angle", b.faces.item(0), b.faces.item(2))
    rad = ins.measure_angle(make_ctx(fake_app, OutputUnits.for_call({"angle": "rad"})), b.faces.item(0), b.faces.item(2))
    assert rad["angle"] == pytest.approx(1.5707963267948966)


def test_measure_angle_three_points(fake_app, ctx, fake_design):
    v = topo_box(fake_design.rootComponent).vertices
    fake_app.measureManager.angle = core.MeasureResults(0.5, P(0, 0, 0), P(1, 0, 0), P(1, 1, 0))
    r = ins.measure_angle(ctx, v.item(0), v.item(1), v.item(2))
    assert len(r["points"]) == 3 and r["points"][2] == pytest.approx([10, 10, 0])
    assert len(fake_app.measureManager.args[-1]) == 4


def test_measure_angle_fusion_error_keeps_message(fake_app, ctx, fake_design):
    b = new_box(fake_design.rootComponent)
    fake_app.measureManager.angle = RuntimeError("parallel cylinders")
    e = raises_action(ins.measure_angle, ctx, b, b)
    assert e.code == "FUSION_ERROR" and "parallel cylinders" in e.message


# ---- physical_properties -----------------------------------------------------------------------


def phys(mass, vol, area, com, moments=(1, 2, 3, 4, 5, 6), principal=(7, 8, 9)):
    return fusion.PhysicalProperties(mass, vol, area, P(*com), moments, principal)


def with_props(body, props):
    levels = []
    body.getPhysicalProperties = lambda level: (levels.append(level), props)[1]
    return levels


def test_physical_properties_single_item(fake_app, ctx, fake_design):
    b = new_box(fake_design.rootComponent)
    levels = with_props(b, phys(2.7, 1.0, 7.0, (1, 0.5, 0.25)))
    r = ins.physical_properties(ctx, [b])
    assert "total" not in r and len(r["items"]) == 1
    i = r["items"][0]
    assert i["accuracy"] == "very_high" and levels == [fusion.CalculationAccuracy.VeryHighCalculationAccuracy]
    assert i["ref"]["token"] == b.entityToken
    assert i["volume"] == pytest.approx(1000) and i["area"] == pytest.approx(700) and i["mass"] == 2.7
    assert i["mass"] == pytest.approx(i["volume"] * i["density"], rel=1e-9)
    assert i["density"] == pytest.approx(2.7e-3) and i["center_of_mass"] == pytest.approx([10, 5, 2.5])
    assert i["moments"] == {"xx": 100, "yy": 200, "zz": 300, "xy": 400, "yz": 500, "xz": 600}
    assert i["principal_moments"] == pytest.approx([700, 800, 900])
    assert i["principal_axes"] == [[1, 0, 0], [0, 1, 0], [0, 0, 1]]


def test_physical_properties_accuracy_levels(ctx, fake_design):
    b = new_box(fake_design.rootComponent)
    levels = with_props(b, phys(1, 1, 1, (0, 0, 0)))
    for a in ("low", "medium", "high"):
        assert ins.physical_properties(ctx, [b], accuracy=a)["items"][0]["accuracy"] == a
    assert levels == [fusion.CalculationAccuracy.LowCalculationAccuracy,
                      fusion.CalculationAccuracy.MediumCalculationAccuracy,
                      fusion.CalculationAccuracy.HighCalculationAccuracy]


def test_physical_properties_totals(ctx, fake_design):
    root = fake_design.rootComponent
    a, b = new_box(root, "A"), new_box(root, "B")
    with_props(a, phys(1.0, 1.0, 6.0, (0, 0, 0), (1, 1, 1, 0, 0, 0)))
    with_props(b, phys(3.0, 2.0, 10.0, (4, 0, 8), (2, 3, 4, 1, 1, 1)))
    r = ins.physical_properties(ctx, [a, b])
    t = r["total"]
    assert t["mass"] == 4.0 and t["volume"] == pytest.approx(3000) and t["area"] == pytest.approx(1600)
    assert t["density"] == pytest.approx(4.0 / 3.0 / 1000)
    assert t["center_of_mass"] == pytest.approx([30, 0, 60])  # mass-weighted: (3*4)/4, (3*8)/4 cm -> mm
    assert t["moments"] == {"xx": 300, "yy": 400, "zz": 500, "xy": 100, "yz": 100, "xz": 100}
    assert "principal_moments" not in t


def test_physical_properties_total_density_null_for_zero_volume(ctx, fake_design):
    root = fake_design.rootComponent
    a, b = new_box(root, "A"), new_box(root, "B")
    with_props(a, phys(0.0, 0.0, 0.0, (0, 0, 0)))
    with_props(b, phys(0.0, 0.0, 0.0, (0, 0, 0)))
    t = ins.physical_properties(ctx, [a, b])["total"]
    assert t["density"] is None and t["center_of_mass"] is None


def test_physical_properties_fallback_reports_low(ctx, fake_design):
    b = new_box(fake_design.rootComponent)
    b.physicalProperties = phys(1.0, 1.0, 6.0, (0, 0, 0))
    assert not hasattr(b, "getPhysicalProperties")
    assert ins.physical_properties(ctx, [b], accuracy="high")["items"][0]["accuracy"] == "low"


def test_physical_properties_default_is_whole_design(ctx, fake_design):
    root = fake_design.rootComponent
    with_props(root, phys(5.0, 1.0, 1.0, (0, 0, 0)))
    r = ins.physical_properties(ctx)
    assert len(r["items"]) == 1 and r["items"][0]["ref"]["name"] == "Root"


def test_physical_properties_wrong_type_is_invalid_params(ctx, fake_design):
    s = fake_design.rootComponent.add_sketch("S")
    e = raises_action(ins.physical_properties, ctx, [s])
    assert e.code == "INVALID_PARAMS" and "Sketch" in e.message


# ---- interference -------------------------------------------------------------------------------


def hit(a, b, vol=0.5):
    body = core.Base()
    body.volume = vol
    body.boundingBox = bbox((1, 0, 0), (1.5, 1, 1))
    return fusion.InterferenceResult(a, b, body)


def test_interference_pairs_and_no_bodies_added(fake_design, ctx):
    root = fake_design.rootComponent
    a, b = new_box(root, "A"), new_box(root, "B")
    fake_design._interference = [hit(a, b)]
    r = ins.interference(ctx, [a, b], include_coincident_faces=True)
    assert r["checked"] == 2 and r["total"] == 1 and set(r) >= {"items", "total", "offset", "next_offset"}
    p = r["items"][0]
    assert p["a"]["token"] == a.entityToken and p["b"]["token"] == b.entityToken
    assert p["volume"] == pytest.approx(500) and p["bbox"]["size"] == pytest.approx([5, 10, 10])
    assert fake_design._last_interference_input.areCoincidentFacesIncluded is True
    assert list(fake_design._last_interference_input.entities) == [a, b]
    assert fake_design._interference[0].corresponding_calls == 0
    assert root.bRepBodies.count == 2


def test_interference_default_entities_are_visible_brep_bodies(fake_design, ctx):
    root = fake_design.rootComponent
    a, b = new_box(root, "A"), new_box(root, "B")
    hidden = new_box(root, "H")
    hidden.isVisible = False
    root.add_mesh_body("M")
    fake_design._interference = []
    r = ins.interference(ctx)
    assert r["checked"] == 2 and r["items"] == [] and r["total"] == 0
    assert list(fake_design._last_interference_input.entities) == [a, b]
    assert ins.interference(ctx, [a, b], include_coincident_faces=False)["checked"] == 2


def test_interference_pairs_are_paged(fake_design, ctx):
    root = fake_design.rootComponent
    a, b, c = (new_box(root, n) for n in "ABC")
    fake_design._interference = [hit(a, b), hit(a, c), hit(b, c)]
    r = ins.interference(ctx, [a, b, c], limit=2)
    assert len(r["items"]) == 2 and r["next_offset"] == 2 and r["total"] == 3
    assert len(ins.interference(ctx, [a, b, c], offset=2)["items"]) == 1


def test_interference_single_entity_is_invalid_params(fake_design, ctx):
    a = new_box(fake_design.rootComponent)
    assert raises_action(ins.interference, ctx, [a]).code == "INVALID_PARAMS"
    assert raises_action(ins.interference, ctx).code == "INVALID_PARAMS"  # one visible body by default


def test_interference_wrong_entity_type(fake_design, ctx):
    root = fake_design.rootComponent
    e = raises_action(ins.interference, ctx, [new_box(root), root.add_sketch("S")])
    assert e.code == "INVALID_PARAMS" and "Sketch" in e.message


def test_interference_201_bodies_invalid_200_succeed(fake_design, ctx):
    root = fake_design.rootComponent
    bodies = [root.add_body("b%d" % i) for i in range(200)]
    fake_design._interference = []
    assert ins.interference(ctx)["checked"] == 200
    root.add_body("b200")
    e = raises_action(ins.interference, ctx)
    assert e.code == "INVALID_PARAMS" and "201" in e.message and "entities" in e.hints[0]
    assert len(bodies) == 200


# ---- get_selection ------------------------------------------------------------------------------


def test_selection_two_items(fake_app, ctx, fake_design):
    b = new_box(fake_design.rootComponent)
    f, e = b.faces.item(0), b.edges.item(0)
    fake_app.userInterface.activeSelections = core.Collection(
        [core.Selection(f, P(1, 0, 0)), core.Selection(e)])
    r = ins.get_selection(ctx)
    assert r["total"] == 2 and set(r) == {"items", "total", "offset", "next_offset"}
    assert [i["ref"]["token"] for i in r["items"]] == [f.entityToken, e.entityToken]
    assert r["items"][0]["point"] == pytest.approx([10, 0, 0]) and r["items"][1]["point"] is None
    assert ins.get_selection(ctx, limit=1)["next_offset"] == 1


def test_selection_empty_and_no_design_needed(fake_app):
    fake_app.activeDocument = None
    r = ins.get_selection(make_ctx(fake_app))
    assert r["total"] == 0 and r["items"] == []


# ---- section_analysis ---------------------------------------------------------------------------


def two_mm():
    return Value("2 mm", 0.2, "length")


def test_section_analysis_adds_and_indexes(ctx, fake_design):
    fake_design.analyses = fusion.Analyses()
    xy = fake_design.rootComponent.xYConstructionPlane
    r = ins.section_analysis(ctx, xy, two_mm(), name="Cut")["analysis"]
    assert r == {"name": "Cut", "index": 0, "plane": "XY", "offset": pytest.approx(2), "flipped": False}
    r2 = ins.section_analysis(ctx, xy, two_mm())["analysis"]
    assert r2["index"] == 1 and r2["name"] == "Analysis2"
    added = fake_design.analyses.sectionAnalyses._items[0].input
    assert added.offset == 0.2 and added.plane is xy and added.isFlipped is False


def test_section_analysis_flip_and_default_offset(ctx, fake_design):
    fake_design.analyses = fusion.Analyses()
    comp_plane = fake_design.rootComponent.add_construction_plane("P")
    r = ins.section_analysis(ctx, comp_plane, flip=True)["analysis"]
    assert r["flipped"] is True and r["offset"] == 0 and r["plane"]["name"] == "P"
    assert fake_design.analyses.sectionAnalyses._items[0].input.isFlipped is True


def test_section_analysis_unsupported_without_api(ctx, fake_design):
    xy = fake_design.rootComponent.xYConstructionPlane
    assert raises_action(ins.section_analysis, ctx, xy, two_mm()).code == "UNSUPPORTED"
    fake_design.analyses = core.Base()
    assert raises_action(ins.section_analysis, ctx, xy, two_mm()).code == "UNSUPPORTED"


def test_section_analysis_flip_unsupported_without_is_flipped(ctx, fake_design):
    fake_design.analyses = fusion.Analyses(flip_supported=False)
    xy = fake_design.rootComponent.xYConstructionPlane
    e = raises_action(ins.section_analysis, ctx, xy, two_mm(), flip=True)
    assert e.code == "UNSUPPORTED" and "flip" in e.message
    assert fake_design.analyses.sectionAnalyses.count == 0
    assert ins.section_analysis(ctx, xy, two_mm())["analysis"]["flipped"] is False


def test_section_analysis_is_a_write_action():
    from defusion import actions

    assert actions.registry().get("inspect", "section_analysis").access == "write"
    assert {a.access for a in actions.registry().for_tool("inspect") if a.name != "section_analysis"} == {"read"}


# ---- compare_meshes -----------------------------------------------------------------------------

_BOX_TRIS = [0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7, 0, 1, 5, 0, 5, 4, 3, 7, 6, 3, 6, 2, 0, 4, 7, 0, 7, 3, 1, 2, 6, 1, 6, 5]


def box_mesh(w, d, h, origin=(0, 0, 0)):
    x0, y0, z0 = origin
    coords = []
    for z in (0, h):
        for x, y in ((0, 0), (w, 0), (w, d), (0, d)):
            coords += [x0 + x, y0 + y, z0 + z]
    return fusion.TriangleMesh(coords, _BOX_TRIS)


def mesh_body(root, name, mesh):
    m = root.add_mesh_body(name)
    m.displayMesh = mesh
    return m


def brep_with_mesh(root, name, mesh):
    b = new_box(root, name)
    b.meshManager = fusion.MeshManager(mesh)
    return b


SQUARE = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
SQUARE_TRIS = [(0, 1, 2), (0, 2, 3)]


def test_deviation_point_above_unit_square():
    assert ins._deviation([(0.5, 0.5, 1.0)], SQUARE, SQUARE_TRIS) == pytest.approx([1.0], abs=1e-12)


def test_deviation_beyond_edge_and_corner_is_euclidean():
    d = ins._deviation([(2, 0.5, 0), (2, 2, 1), (-1, -1, 0), (0.5, 0.5, 0)], SQUARE, SQUARE_TRIS)
    assert d == pytest.approx([1.0, math.sqrt(3), math.sqrt(2), 0.0], abs=1e-12)


def test_deviation_matches_brute_force_over_200_triangles():
    rnd = random.Random(7)
    nodes = [(rnd.uniform(0, 10), rnd.uniform(0, 10), rnd.uniform(0, 10)) for _ in range(120)]
    tris = [tuple(rnd.sample(range(120), 3)) for _ in range(200)]
    pts = [(rnd.uniform(-5, 15), rnd.uniform(-5, 15), rnd.uniform(-5, 15)) for _ in range(150)]
    got = ins._deviation(pts, nodes, tris)
    for p, g in zip(pts, got):
        want = min(math.sqrt(ins._tri_dist2(p, nodes[a], nodes[b], nodes[c])) for a, b, c in tris)
        assert g == pytest.approx(want, abs=1e-12)


def test_deviation_no_triangles_is_invalid_params():
    assert raises_action(ins._deviation, [(0, 0, 0)], [], []).code == "INVALID_PARAMS"


def test_stats_hand_computed():
    s = ins._stats([1.0, 2.0, 3.0, 4.0], 2.5)
    assert s["min"] == 1 and s["max"] == 4 and s["mean"] == 2.5 and s["count"] == 4
    assert s["rms"] == pytest.approx(math.sqrt(7.5)) and s["std"] == pytest.approx(math.sqrt(1.25))
    assert s["median"] == 2.5 and s["p95"] == 4 and s["within_tolerance"] == 0.5
    assert ins._stats([5.0, 1.0, 3.0])["median"] == 3.0
    assert ins._stats([1.0])["within_tolerance"] is None


def test_stats_p95_nearest_rank():
    assert ins._stats([float(i) for i in range(1, 101)])["p95"] == 95.0
    assert ins._stats([float(i) for i in range(1, 21)])["p95"] == 19.0


def test_sample_points_all_then_stride():
    pts = ins._sample_points(SQUARE, SQUARE_TRIS, 100)
    assert pts[:4] == SQUARE and len(pts) == 6
    assert pts[4] == pytest.approx((2 / 3, 1 / 3, 0))
    assert ins._sample_points(SQUARE, SQUARE_TRIS, 3) == [pts[0], pts[2], pts[4]]
    assert len(ins._sample_points(SQUARE, SQUARE_TRIS, 5)) == 3  # stride 2


def test_triangles_proxy_transform_only_when_bbox_mismatches(fake_design):
    root = fake_design.rootComponent
    shift = core.Base()
    shift.transform2 = core.Base()
    shift.transform2.asArray = lambda: [1, 0, 0, 5, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    local = box_mesh(1, 1, 1)
    # nodes local, bounding box world: transformed
    b = brep_with_mesh(root, "P", local)
    b.assemblyContext = shift
    b._bbox = ((5, 0, 0), (6, 1, 1))
    nodes, _ = ins._triangles(b)
    assert min(n[0] for n in nodes) == 5 and max(n[0] for n in nodes) == 6
    # nodes already world: untouched
    w = brep_with_mesh(root, "W", box_mesh(1, 1, 1, origin=(5, 0, 0)))
    w.assemblyContext = shift
    w._bbox = ((5, 0, 0), (6, 1, 1))
    nodes, _ = ins._triangles(w)
    assert min(n[0] for n in nodes) == 5 and max(n[0] for n in nodes) == 6
    # no assembly context: untouched even if the box differs
    n0 = brep_with_mesh(root, "N", local)
    n0.assemblyContext = None
    n0._bbox = ((5, 0, 0), (6, 1, 1))
    assert min(n[0] for n in ins._triangles(n0)[0]) == 0


def test_triangles_brep_uses_very_high_quality(fake_design):
    b = brep_with_mesh(fake_design.rootComponent, "B", box_mesh(1, 1, 1))
    ins._triangles(b)
    assert b.meshManager.calculator.quality == fusion.TriangleMeshQualityOptions.VeryHighQualityTriangleMesh


def test_triangles_polygon_mesh_fallback(fake_design):
    m = fake_design.rootComponent.add_mesh_body("M")
    pm = core.Base()
    pm.nodeCoordinatesAsDouble = [0, 0, 0, 1, 0, 0, 0, 1, 0]
    pm.triangleNodeIndices = [0, 1, 2]
    m.mesh = pm
    nodes, tris = ins._triangles(m)
    assert nodes == [(0, 0, 0), (1, 0, 0), (0, 1, 0)] and tris == [(0, 1, 2)]


def test_compare_meshes_unsupported_without_accessors(ctx, fake_design):
    root = fake_design.rootComponent
    bare = root.add_mesh_body("Bare")
    other = mesh_body(root, "O", box_mesh(1, 1, 1))
    e = raises_action(ins.compare_meshes, ctx, bare, other)
    assert e.code == "UNSUPPORTED" and "MeshBody" in e.message
    brep = new_box(root, "NoMesh")
    assert raises_action(ins.compare_meshes, ctx, other, brep).code == "UNSUPPORTED"


def test_compare_meshes_two_brep_is_invalid_params(ctx, fake_design):
    a, b = new_box(fake_design.rootComponent, "A"), new_box(fake_design.rootComponent, "B")
    e = raises_action(ins.compare_meshes, ctx, a, b)
    assert e.code == "INVALID_PARAMS" and "measure_distance" in e.hints[0]


def test_compare_meshes_rejects_other_types_and_bad_samples(ctx, fake_design):
    root = fake_design.rootComponent
    m = mesh_body(root, "M", box_mesh(1, 1, 1))
    assert raises_action(ins.compare_meshes, ctx, m, root.add_sketch("S")).code == "INVALID_PARAMS"
    assert raises_action(ins.compare_meshes, ctx, m, m, samples=0).code == "INVALID_PARAMS"
    assert raises_action(ins.compare_meshes, ctx, m, m, samples=20001).code == "INVALID_PARAMS"
    assert ins.compare_meshes(ctx, m, m, samples=20000)["samples"] == 20000


def test_compare_meshes_mesh_vs_own_brep(ctx, fake_design):
    root = fake_design.rootComponent
    m = mesh_body(root, "M", box_mesh(2, 1, 0.5))
    b = brep_with_mesh(root, "B", box_mesh(2, 1, 0.5))
    r = ins.compare_meshes(ctx, m, b)
    assert r["forward"]["max"] < 0.001 and r["reverse"] is None and r["hausdorff"] is None
    assert r["test"]["token"] == m.entityToken and r["reference"]["token"] == b.entityToken


def test_compare_meshes_six_mm_vs_five_mm(ctx, fake_design):
    root = fake_design.rootComponent
    tall = mesh_body(root, "T", box_mesh(2, 1, 0.6))
    flat = mesh_body(root, "F", box_mesh(2, 1, 0.5))
    r = ins.compare_meshes(ctx, tall, flat, tolerance=Value("0.5 mm", 0.05, "length"), symmetric=True)
    f = r["forward"]
    assert f["max"] == pytest.approx(1.0, abs=0.001) and f["min"] == pytest.approx(0.0, abs=1e-9)
    assert f["count"] == 8 + 12 and f["max_point"][2] == pytest.approx(6.0)
    assert 0 < f["within_tolerance"] < 1
    assert r["reverse"]["max"] == pytest.approx(1.0, abs=0.001) and r["hausdorff"] == pytest.approx(1.0, abs=0.001)
    assert ins.compare_meshes(ctx, tall, flat)["forward"]["within_tolerance"] is None


def test_compare_meshes_units_and_sample_cap(fake_app, fake_design):
    root = fake_design.rootComponent
    tall = mesh_body(root, "T", box_mesh(2, 1, 0.6))
    flat = mesh_body(root, "F", box_mesh(2, 1, 0.5))
    c = make_ctx(fake_app, OutputUnits.for_call({"length": "in"}))
    r = ins.compare_meshes(c, tall, flat, samples=5)
    assert r["forward"]["max"] == pytest.approx(0.1 / 2.54, abs=1e-3) and r["forward"]["count"] <= 5


def test_compare_meshes_reference_too_large(ctx, fake_design, monkeypatch):
    root = fake_design.rootComponent
    a, b = mesh_body(root, "A", box_mesh(1, 1, 1)), mesh_body(root, "B", box_mesh(1, 1, 1))
    monkeypatch.setattr(ins, "_MAX_REFERENCE_TRIANGLES", 5)
    e = raises_action(ins.compare_meshes, ctx, a, b)
    assert e.code == "INVALID_PARAMS" and "reference mesh too large" in e.message


def test_deviation_degenerate_triangles_use_their_edges():
    nodes = [(0, 0, 0), (1, 0, 0), (2, 0, 0)]
    d = ins._deviation([(1, 1, 0), (3, 0, 0), (0.5, 0, 0)], nodes, [(0, 1, 2)])
    assert d == pytest.approx([1.0, 1.0, 0.0], abs=1e-12)
    sliver = [(0, 0, 0), (1, 0, 0)]  # two coincident corners: the closest-point formula divides by zero
    assert ins._deviation([(0.5, 1, 0)], sliver, [(0, 0, 1)]) == pytest.approx([1.0], abs=1e-12)


def test_compare_meshes_symmetric_directions_differ(ctx, fake_design):
    root = fake_design.rootComponent
    small = mesh_body(root, "S", box_mesh(2, 1, 0.5))
    big = mesh_body(root, "B", box_mesh(3, 1, 0.5))
    r = ins.compare_meshes(ctx, small, big, symmetric=True)
    assert r["forward"]["max"] < 5.0  # the small end cap sits 1.67 mm inside the big box
    assert r["reverse"]["max"] == pytest.approx(10.0) and r["hausdorff"] == pytest.approx(10.0)
    assert r["reverse"]["max_point"][0] == pytest.approx(30.0)
