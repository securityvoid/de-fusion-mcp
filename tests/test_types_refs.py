import math
from types import SimpleNamespace
from typing import Annotated

import adsk
import pytest
from adsk import core, fusion
from pydantic import BaseModel, TypeAdapter, ValidationError

from defusion import types
from defusion.errors import ActionError
from defusion.types import (
    Angle, Axis, Body, CamOperation, CamSetup, Component, ContactSet, Edge, Entity, Face, Feature,
    Length, MeshBody, Occurrence, Plane, Point2, Profile, Resolver, Sketch, TimelineItem, resolve_params,
)


@pytest.fixture
def ctx(fake_design):
    return SimpleNamespace(design=fake_design, document=None, app=None)


def res(tp, raw, ctx, path="p", params=None):
    spec = TypeAdapter(tp).validate_python(raw)
    return spec.resolve(Resolver(ctx, params, path))


def code(tp, raw, ctx, **kw):
    with pytest.raises(ActionError) as e:
        res(tp, raw, ctx, **kw)
    return e.value


@pytest.fixture
def model(fake_design):
    root = fake_design.rootComponent
    sub = root.add_occurrence("Occ1", "SubComp")
    sub_comp = sub.component
    b1 = root.add_body("Body1")
    b2 = sub_comp.add_body("Body2")
    b3 = sub_comp.add_body("Body1")  # same name as root's Body1 -> ambiguous design-wide
    sk = root.add_sketch("Sketch1", origin=(0, 0, 0.5))
    return SimpleNamespace(root=root, sub=sub, sub_comp=sub_comp, b1=b1, b2=b2, b3=b3, sk=sk)


# ---- Ref forms (REQ-017) ---------------------------------------------------------------

def test_token(model, ctx):
    assert res(Body, {"token": model.b2.entityToken}, ctx) is model.b2


def test_token_first_match_of_expected_type(model, ctx):
    assert res(Entity, {"token": model.sk.entityToken}, ctx) is model.sk


def test_name_whole_design(model, ctx):
    assert res(Body, {"name": "Body2"}, ctx) is model.b2
    assert res(Sketch, {"name": "Sketch1"}, ctx) is model.sk


def test_name_scoped_to_component(model, ctx):
    assert res(Body, {"name": "Body1", "component": "Root"}, ctx) is model.b1
    assert res(Body, {"name": "Body1", "component": {"name": "SubComp"}}, ctx) is model.b3


def test_bare_string_name_then_token(model, ctx):
    assert res(Body, "Body2", ctx) is model.b2
    assert res(Body, model.b2.entityToken, ctx) is model.b2


def test_body_index_for_face_edge_vertex(model, ctx):
    f0, f1 = model.b2.add_face(), model.b2.add_face()
    e0 = model.b2.add_edge()
    v0 = model.b2.add_vertex()
    assert res(Face, {"body": "Body2", "index": 1}, ctx) is f1
    assert res(Edge, {"body": {"token": model.b2.entityToken}, "index": 0}, ctx) is e0
    assert res(types.Vertex, {"body": "Body2", "index": 0}, ctx) is v0
    assert f0 is not f1
    assert code(Face, {"body": "Body2", "index": 5}, ctx).code == "INVALID_PARAMS"


def test_body_index_form_rejected_for_other_kinds(model, ctx):
    assert code(Body, {"body": "Body2", "index": 0}, ctx).code == "INVALID_PARAMS"


def test_features_params_and_other_kinds_by_name(model, ctx, fake_design):
    feat = model.root.add_feature("Extrude1")
    p = fake_design.add_parameter("d1", 1.0)
    mesh = model.root.add_mesh_body("Mesh1")
    jo = model.root.add_joint_origin("JO1")
    rg = model.root.add_rigid_group("RG1")
    j = model.root.add_joint("J1")
    cp = model.root.add_construction_plane("CP1")
    ca = model.root.add_construction_axis("CA1")
    for tp, name, want in [
        (Feature, "Extrude1", feat), (types.Parameter, "d1", p), (MeshBody, "Mesh1", mesh),
        (types.JointOrigin, "JO1", jo), (types.RigidGroup, "RG1", rg), (types.Joint, "J1", j),
        (types.ConstructionPlane, "CP1", cp), (types.ConstructionAxis, "CA1", ca),
        (Occurrence, "Occ1", model.sub), (Component, "SubComp", model.sub_comp),
    ]:
        assert res(tp, name, ctx) is want, name


def test_sketch_curve_and_point_by_token(model, ctx):
    line, pt = model.sk.add_line(), model.sk.add_point()
    assert res(types.SketchCurve, {"token": line.entityToken}, ctx) is line
    assert res(types.SketchPoint, pt.entityToken, ctx) is pt


# ---- Errors (REQ-020) -----------------------------------------------------------------

def test_not_found(model, ctx):
    for raw in ("nope", {"name": "nope"}, {"token": "zzz"}):
        assert code(Body, raw, ctx).code == "NOT_FOUND"


def test_ambiguous_lists_candidates(model, ctx):
    e = code(Body, {"name": "Body1"}, ctx)
    assert e.code == "AMBIGUOUS"
    assert {c["token"] for c in e.extra["candidates"]} == {model.b1.entityToken, model.b3.entityToken}
    assert {c["component"] for c in e.extra["candidates"]} == {"Root", "SubComp"}


def test_wrong_type(model, ctx):
    assert code(Body, {"token": model.sk.entityToken}, ctx).code == "INVALID_PARAMS"
    assert code(Body, "Sketch1", ctx).code == "INVALID_PARAMS"


def test_no_design():
    e = code(Body, "x", SimpleNamespace(design=None, document=None))
    assert e.code == "NO_DESIGN"


@pytest.mark.parametrize("bad", ["", {}, {"bogus": 1}, {"name": ""}, 5, {"body": "b"}, {"index": 0}])
def test_bad_shape_rejected_at_validation(bad):
    with pytest.raises(ValidationError):
        TypeAdapter(Body).validate_python(bad)


# ---- Round trip (REQ-021) -------------------------------------------------------------------

def test_ref_round_trip(model, ctx):
    ref = types.entity_ref(model.b2)
    assert ref == {"type": "BRepBody", "name": "Body2", "token": model.b2.entityToken, "component": "SubComp"}
    assert res(Body, ref, ctx) is model.b2
    assert res(Body, types.entity_ref(model.b1), ctx) is model.b1


def test_entity_ref_for_face_and_component(model):
    f = model.b2.add_face()
    assert types.entity_ref(f)["component"] == "SubComp"
    assert types.entity_ref(model.root)["component"] is None


# ---- Entity / MeshBody / ContactSet / TimelineItem (REQ-044) -------------------------------------


def test_entity_accepts_the_base_class_of_each_adsk_module(model, ctx, monkeypatch):
    """Real Fusion has a separate ``Base`` per adsk module; a BRepBody derives only from ``adsk.fusion.Base``."""
    class FusionBase:  # noqa: B903
        pass

    class Entity2(FusionBase):
        entityToken = "tok"

    monkeypatch.setattr(fusion, "Base", FusionBase, raising=False)
    assert isinstance(Entity2(), types._classes(("Entity",)))
    assert not isinstance(Entity2(), (core.Base,))

def test_entity_resolves_mesh_body_and_sketch(model, ctx):
    mesh = model.root.add_mesh_body("Mesh1")
    assert res(Entity, "Mesh1", ctx) is mesh
    assert res(Entity, "Sketch1", ctx) is model.sk
    assert code(Body, "Mesh1", ctx).code == "INVALID_PARAMS"


def test_contact_set_unsupported_without_attribute(model, ctx):
    assert code(ContactSet, "cs", ctx).code == "UNSUPPORTED"


def test_contact_set_resolves_when_supported(model, ctx, fake_design):
    cs = fusion.ContactSet("CS1")
    fake_design._register(cs)
    fake_design.contactSets = core.Collection([cs])
    assert res(ContactSet, "CS1", ctx) is cs
    assert res(ContactSet, {"token": cs.entityToken}, ctx) is cs


def test_timeline_item_forms(model, ctx, fake_design):
    assert res(TimelineItem, {"index": 0}, ctx) is fake_design.timeline.item(0)
    assert res(TimelineItem, "Sketch1", ctx) is model.sk.timelineObject
    assert code(TimelineItem, {"index": 9}, ctx).code == "NOT_FOUND"
    assert code(TimelineItem, "Body2", ctx).code == "INVALID_PARAMS"  # a body has no timeline item


def test_timeline_index_direct_design_not_found():
    d = fusion.Design(parametric=False)
    assert code(TimelineItem, {"index": 0}, SimpleNamespace(design=d, document=None)).code == "NOT_FOUND"


@pytest.fixture
def cam_ctx(fake_app, fake_design):
    cam = adsk.cam.CAM()
    s1, s2 = cam.add_setup("Setup1"), cam.add_setup("Setup2")
    o1, o2, o3 = s1.add_operation("Face1"), s1.add_operation("Adaptive1"), s2.add_operation("Face1")
    doc = core.Document("d", {"DesignProductType": fake_design, "CAMProductType": cam})
    fake_app.activeProduct = cam
    return SimpleNamespace(design=fake_design, document=doc, s1=s1, s2=s2, o1=o1, o2=o2, o3=o3)


def test_cam_setup_forms(cam_ctx):
    c = cam_ctx
    assert res(CamSetup, "Setup1", c) is c.s1
    assert res(CamSetup, {"name": "Setup2"}, c) is c.s2
    assert res(CamSetup, {"token": c.s2.entityToken}, c) is c.s2
    assert res(CamSetup, {"type": "Setup", "name": "Setup1"}, c) is c.s1
    assert code(CamSetup, "Nope", c).code == "NOT_FOUND"


def test_cam_operation_forms(cam_ctx):
    c = cam_ctx
    assert res(CamOperation, "Adaptive1", c) is c.o2
    assert res(CamOperation, {"name": "Face1", "setup": "Setup2"}, c) is c.o3
    assert res(CamOperation, {"type": "Operation", "name": "Face1", "setup": "Setup1"}, c) is c.o1
    assert res(CamOperation, {"token": c.o3.entityToken}, c) is c.o3
    e = code(CamOperation, "Face1", c)
    assert e.code == "AMBIGUOUS" and len(e.extra["candidates"]) == 2
    assert code(CamOperation, {"name": "Face1", "setup": "Nope"}, c).code == "NOT_FOUND"
    assert code(CamOperation, {"name": "Adaptive1", "setup": "Setup2"}, c).code == "NOT_FOUND"


def test_cam_without_product(ctx, fake_design):
    c = SimpleNamespace(design=fake_design, document=core.Document("d", {"DesignProductType": fake_design}))
    assert code(CamSetup, "Setup1", c).code == "NOT_FOUND"
    assert code(CamSetup, "Setup1", SimpleNamespace(design=None, document=None)).code == "NOT_FOUND"


@pytest.mark.parametrize("bad", [{}, {"setup": "s"}, {"name": "a", "extra": "x"}, 3])
def test_cam_bad_shape(bad):
    with pytest.raises(ValidationError):
        TypeAdapter(CamOperation).validate_python(bad)
    with pytest.raises(ValidationError):
        TypeAdapter(CamSetup).validate_python({"name": "a", "setup": "s"})


# ---- Plane / Axis (REQ-018, REQ-047) ---------------------------------------------------------------

def test_plane_literals(model, ctx):
    assert res(Plane, "XY", ctx) is model.root.xYConstructionPlane
    assert res(Plane, "XZ", ctx) is model.root.xZConstructionPlane
    assert res(Plane, "YZ", ctx) is model.root.yZConstructionPlane
    assert res(Axis, "X", ctx) is model.root.xConstructionAxis
    assert res(Axis, "Y", ctx) is model.root.yConstructionAxis
    assert res(Axis, "Z", ctx) is model.root.zConstructionAxis


def test_origin_names_use_component_param_else_active(model, ctx, fake_design):
    assert res(Plane, "XY", ctx, params={"component": model.sub_comp}) is model.sub_comp.xYConstructionPlane
    fake_design.activeComponent = model.sub_comp
    assert res(Plane, "XY", ctx) is model.sub_comp.xYConstructionPlane
    assert res(Axis, "Z", ctx, params={"component": None}) is model.sub_comp.zConstructionAxis


def test_plane_from_refs(model, ctx):
    cp = model.root.add_construction_plane("CP1")
    planar = model.b1.add_face(core.Plane())
    curved = model.b1.add_face(core.Cylinder())
    assert res(Plane, "CP1", ctx) is cp
    assert res(Plane, {"token": planar.entityToken}, ctx) is planar
    assert res(Plane, {"body": {"token": model.b1.entityToken}, "index": 0}, ctx) is planar
    assert code(Plane, {"token": curved.entityToken}, ctx).code == "INVALID_PARAMS"
    assert code(Plane, {"token": model.b1.entityToken}, ctx).code == "INVALID_PARAMS"


def test_axis_from_refs(model, ctx):
    ca = model.root.add_construction_axis("CA1")
    linear = model.b1.add_edge(core.Line3D())
    curved = model.b1.add_edge(core.Cylinder())
    line = model.sk.add_line()
    assert res(Axis, "CA1", ctx) is ca
    assert res(Axis, {"token": linear.entityToken}, ctx) is linear
    assert res(Axis, {"token": line.entityToken}, ctx) is line
    assert code(Axis, {"token": curved.entityToken}, ctx).code == "INVALID_PARAMS"
    assert code(Axis, {"token": model.sk.entityToken}, ctx).code == "INVALID_PARAMS"


def test_axis_sketch_line_empty_sketch(model, ctx):
    e = code(Axis, {"sketch": "Sketch1", "line": 0}, ctx)
    assert e.message == "p: line 0 is out of range; sketch 'Sketch1' has no lines"


def test_axis_sketch_line(model, ctx):
    model.sk.add_line()
    l1 = model.sk.add_line()
    model.sk.add_line()
    assert res(Axis, {"sketch": {"name": "Sketch1"}, "line": 1}, ctx) is l1
    assert res(Axis, {"sketch": "Sketch1", "line": 2}, ctx) is model.sk.sketchCurves.sketchLines.item(2)
    for n in (9, 3, -1):
        e = code(Axis, {"sketch": "Sketch1", "line": n}, ctx)
        assert e.code == "INVALID_PARAMS"
        assert e.message == "p: line %d is out of range; sketch 'Sketch1' has 3 lines (0-2)" % n
    assert code(Axis, {"sketch": {"token": model.b1.entityToken}, "line": 0}, ctx).code == "INVALID_PARAMS"


@pytest.mark.parametrize("bad", [{"sketch": "s"}, {"sketch": "s", "line": True}, {"sketch": "s", "line": "1"},
                                 {"sketch": "s", "line": 1, "x": 1}])
def test_axis_sketch_line_bad_shape(bad):
    with pytest.raises(ValidationError):
        TypeAdapter(Axis).validate_python(bad)


# ---- Profile (REQ-019) ------------------------------------------------------------------------------

def test_profile(model, ctx):
    p0 = model.sk.add_profile()
    assert res(Profile, {"sketch": "Sketch1"}, ctx) is p0  # one profile: index may be omitted
    p1 = model.sk.add_profile()
    assert res(Profile, {"sketch": "Sketch1", "index": 1}, ctx) is p1
    coll = res(Profile, {"sketch": {"name": "Sketch1"}, "index": [0, 1]}, ctx)
    assert isinstance(coll, core.ObjectCollection) and list(coll) == [p0, p1]
    assert code(Profile, {"sketch": "Sketch1"}, ctx).code == "INVALID_PARAMS"  # two profiles
    assert code(Profile, {"sketch": "Sketch1", "index": 2}, ctx).code == "INVALID_PARAMS"
    assert code(Profile, {"sketch": "Sketch1", "index": [0, 5]}, ctx).code == "INVALID_PARAMS"


def test_profile_out_of_range_message_and_hint(model, ctx):
    model.sk.add_profile()
    model.sk.add_profile()
    e = code(Profile, {"sketch": "Sketch1", "index": 2}, ctx)
    assert e.message == "p: profile 2 is out of range; sketch 'Sketch1' has 2 profiles"
    assert e.hints == ["use an index in 0-1"]


def test_profile_list(model, ctx):
    a0, a1 = model.sk.add_profile(), model.sk.add_profile()
    sk2 = model.root.add_sketch("Sketch2")
    b0 = sk2.add_profile()
    coll = res(Profile, [{"sketch": "Sketch2"}, {"sketch": "Sketch1", "index": 1}], ctx)
    assert isinstance(coll, core.ObjectCollection) and list(coll) == [b0, a1]
    coll = res(Profile, [{"sketch": "Sketch1", "index": [1, 0]}, {"sketch": "Sketch2", "index": 0}], ctx)
    assert list(coll) == [a1, a0, b0]
    e = code(Profile, [{"sketch": "Sketch2"}, {"sketch": "Sketch1", "index": 5}], ctx)
    assert e.code == "INVALID_PARAMS"
    assert e.message == "entry 1: p: profile 5 is out of range; sketch 'Sketch1' has 2 profiles"
    assert e.hints == ["use an index in 0-1"]


@pytest.mark.parametrize("bad", [[], [{}], [{"sketch": "s", "index": -1}], ["s"]])
def test_profile_list_bad_shape(bad):
    with pytest.raises(ValidationError):
        TypeAdapter(Profile).validate_python(bad)


@pytest.mark.parametrize("bad", [{}, {"index": 0}, {"sketch": "s", "index": -1}, {"sketch": "s", "index": []},
                                 {"sketch": "s", "index": True}, "s", {"sketch": "s", "x": 1}])
def test_profile_bad_shape(bad):
    with pytest.raises(ValidationError):
        TypeAdapter(Profile).validate_python(bad)


def test_profile_with_no_profiles_needs_index(model, ctx):
    assert code(Profile, {"sketch": "Sketch1"}, ctx).code == "INVALID_PARAMS"


# ---- Point2 (REQ-045) ----------------------------------------------------------------------------------

def test_point2_two_items_sketch_space(model, ctx):
    p = res(Point2, [10, 5], ctx)
    assert (p.x, p.y, p.z) == (pytest.approx(1.0), pytest.approx(0.5), 0)


def test_point2_three_items_via_sketch(model, ctx):
    p = res(Point2, [0, 0, 5], ctx, params={"sketch": model.sk})  # plane offset 0.5 cm in Z
    assert (p.x, p.y, p.z) == (0, 0, 0)
    q = res(Point2, [10, 0, 7], ctx, params={"sketch": model.sk})
    assert q.z == 0 and q.x == pytest.approx(1.0)


def test_point2_three_items_without_sketch(model, ctx):
    assert code(Point2, [1, 2, 3], ctx).code == "INVALID_PARAMS"


@pytest.mark.parametrize("bad", [[1], [1, 2, 3, 4], "x", {"x": 1}])
def test_point2_bad_shape(bad):
    with pytest.raises(ValidationError):
        TypeAdapter(Point2).validate_python(bad)


# ---- resolve_params: unions and nested (REQ-046) ----------------------------------------------------------

class Inner(BaseModel):
    bodies: list[Body]
    length: Length
    note: str = ""


class Outer(BaseModel):
    inner: Inner
    item: int | TimelineItem
    plane: Plane
    sketch: Sketch
    pt: Point2
    component: Component | None = None
    n: int = 1


def test_resolve_params_resolves_unions_and_nested(model, ctx):
    m = Outer.model_validate({
        "inner": {"bodies": ["Body2", {"token": model.b3.entityToken}], "length": "1 in"},
        "item": {"index": 0}, "plane": "XY", "sketch": "Sketch1", "pt": [0, 0, 5],
        "component": "SubComp",
    })
    out = resolve_params(ctx, SimpleNamespace(tool="t", name="a"), m)
    assert list(out) == ["inner", "item", "plane", "sketch", "pt", "component", "n"]
    assert out["inner"].bodies == [model.b2, model.b3]
    assert out["inner"].length.cm == pytest.approx(2.54)
    assert out["item"] is ctx.design.timeline.item(0)
    assert out["component"] is model.sub_comp
    assert out["plane"] is model.sub_comp.xYConstructionPlane  # component resolved first
    assert (out["pt"].x, out["pt"].z) == (0, 0)  # sketch resolved first
    assert out["n"] == 1


def test_union_int_branch_passes_through(model, ctx):
    m = Outer.model_validate({"inner": {"bodies": [], "length": 1}, "item": 3, "plane": "XY",
                              "sketch": "Sketch1", "pt": [0, 0]})
    out = resolve_params(ctx, SimpleNamespace(tool="t", name="a"), m)
    assert out["item"] == 3 and isinstance(out["item"], int)


def test_resolve_params_dict_and_lists_and_errors(model, ctx):
    spec = TypeAdapter(list[Body]).validate_python(["Body2"])
    out = resolve_params(ctx, None, {"a": spec, "b": 5})
    assert out == {"a": [model.b2], "b": 5}
    bad = TypeAdapter(list[Body]).validate_python(["Body2", "nope"])
    with pytest.raises(ActionError) as e:
        resolve_params(ctx, None, {"bodies": bad})
    assert "bodies[1]" in e.value.message


def test_uses_refs():
    assert types.uses_refs(Body) and types.uses_refs(list[Body]) and types.uses_refs(int | TimelineItem)
    assert types.uses_refs(Outer) and types.uses_refs(Plane) and types.uses_refs(CamSetup)
    assert not types.uses_refs(Length) and not types.uses_refs(int) and not types.uses_refs(list[int])
    assert not types.uses_refs(Annotated[int, "x"]) and not types.uses_refs(types.FileName)


def test_ref_shape_ignores_null_name_and_component():
    from defusion.types import _ref_shape

    assert _ref_shape({"type": "SketchLine", "name": None, "token": "t", "component": None}) == {
        "type": "SketchLine", "token": "t"}
    with pytest.raises(ValueError):
        _ref_shape({"type": "SketchLine", "name": None})
