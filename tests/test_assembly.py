import pytest
from adsk import core, fusion

from defusion import actions, units
from defusion.context import Ctx
from defusion.errors import ActionError
from defusion.tools import assembly  # noqa: F401  (registers the actions)


@pytest.fixture
def ctx(fake_app, fake_design):
    fake_app.activeDocument = core.Document("Doc", {"DesignProductType": fake_design})
    return Ctx(fake_app, fake_app.userInterface, fake_app.activeDocument, fake_design,
               {a.key for a in actions.registry()}, [], units.OutputUnits("mm", "deg"))


def run(ctx, action_name, /, **params):
    return actions.call_inline(ctx, "assembly", action_name, params)


def err(ctx, action_name, /, **params):
    with pytest.raises(ActionError) as e:
        run(ctx, action_name, **params)
    return e.value


@pytest.fixture
def asm(fake_design):
    root = fake_design.rootComponent
    a = root.add_occurrence("A:1", "A")
    p = root.add_occurrence("P:1", "P")
    q = p.component.add_occurrence("Q:1", "Q")
    p._path, q._path = "P:1", "P:1+Q:1"
    a.component.add_body("Body1")
    a.component.add_sketch("S1")
    return root, a, p, q


# ---- list_components -----------------------------------------------------------------------

def test_list_components_root_first_with_counts(ctx, asm):
    out = run(ctx, "list_components")
    names = [i["component"]["name"] for i in out["items"]]
    assert names == ["Root", "A", "P", "Q"]
    root, a = out["items"][0], out["items"][1]
    assert root["is_root"] and root["occurrences"] == 0
    assert (a["bodies"], a["sketches"], a["occurrences"], a["joints"], a["as_built_joints"]) == (1, 1, 1, 0, 0)
    assert a["part_number"] == "A" and not a["is_root"]


def test_list_components_paging(ctx, asm):
    out = run(ctx, "list_components", limit=2)
    assert len(out["items"]) == 2 and out["total"] == 4 and out["next_offset"] == 2
    assert run(ctx, "list_components", offset=2, limit=2)["next_offset"] is None


def test_list_components_skips_duplicate_root(ctx, asm, fake_design):
    fake_design._subcomponents.insert(0, fake_design.rootComponent)
    assert run(ctx, "list_components")["total"] == 4


# ---- list_occurrences ----------------------------------------------------------------------

def test_list_occurrences_depth_parent_from_paths(ctx, asm):
    items = {i["path"]: i for i in run(ctx, "list_occurrences")["items"]}
    assert set(items) == {"A:1", "P:1", "P:1+Q:1"}
    assert items["A:1"]["depth"] == 1 and items["A:1"]["parent"] is None
    q = items["P:1+Q:1"]
    assert q["depth"] == 2 and q["parent"] == "P:1"
    assert q["component"]["name"] == "Q" and q["occurrence"]["name"] == "Q:1"
    assert q["grounded"] is False and q["light_bulb"] is True and q["visible"] is True


def test_list_occurrences_non_recursive(ctx, asm):
    paths = [i["path"] for i in run(ctx, "list_occurrences", recursive=False)["items"]]
    assert paths == ["A:1", "P:1"]


def test_list_occurrences_component_scope_keeps_absolute_path(ctx, asm):
    root, a, p, q = asm
    out = run(ctx, "list_occurrences", component={"name": "P"})
    assert [i["path"] for i in out["items"]] == ["P:1+Q:1"]
    assert out["items"][0]["depth"] == 2 and out["items"][0]["parent"] == "P:1"


def test_list_occurrences_origin_converted(ctx, asm):
    root, a, p, q = asm
    a.transform2.coordinate_system[0].z = 2.54
    ctx.output = units.OutputUnits("in", "deg")
    t = run(ctx, "list_occurrences", recursive=False)["items"][0]["transform"]
    assert t["origin"] == pytest.approx([0, 0, 1])
    assert t["x_axis"] == [1, 0, 0] and t["z_axis"] == [0, 0, 1]


def test_list_occurrences_paging(ctx, asm):
    out = run(ctx, "list_occurrences", limit=1)
    assert out["total"] == 3 and out["next_offset"] == 1


# ---- contact_get ---------------------------------------------------------------------------

def test_contact_get_modes(ctx, fake_design):
    assert run(ctx, "contact_get")["analysis"] == "off"
    fake_design.isContactAnalysisEnabled = True
    assert run(ctx, "contact_get")["analysis"] == "all"
    fake_design.isContactSetAnalysis = True
    assert run(ctx, "contact_get")["analysis"] == "contact_sets"


def test_contact_get_null_without_attribute(ctx):
    assert run(ctx, "contact_get")["contact_sets"] is None


def test_contact_get_sets_with_members(ctx, asm, fake_design):
    root, a, p, q = asm
    sets = fake_design.enable_contact_sets()
    sets.add([a, a.component.bRepBodies.item(0)])
    sets._items[0].name = "C1"
    sets._items[0].isSuppressed = True
    [cs] = run(ctx, "contact_get")["contact_sets"]
    assert cs["name"] == "C1" and cs["suppressed"] is True
    assert [m["type"] for m in cs["members"]] == ["Occurrence", "BRepBody"]
    assert cs["contact_set"]["type"] == "ContactSet"


def test_contact_get_members_fall_back_to_entities_then_null(ctx, asm, fake_design):
    root, a, p, q = asm
    sets = fake_design.enable_contact_sets()
    one = fusion.ContactSet("X")
    one.isSuppressed = False
    one.entities = core.Collection([a])
    two = fusion.ContactSet("Y")
    two.isSuppressed = False
    sets._items.extend([one, two])
    first, second = run(ctx, "contact_get")["contact_sets"]
    assert [m["name"] for m in first["members"]] == ["A:1"]
    assert second["members"] is None


def test_assembly_actions_need_a_design(ctx):
    ctx.design = None
    assert err(ctx, "list_components").code == "NO_DESIGN"
    assert err(ctx, "contact_get").code == "NO_DESIGN"


# ---- create_component ----------------------------------------------------------------------

def test_create_component_empty(ctx, fake_design):
    out = run(ctx, "create_component")
    assert out["bodies"] == [] and out["component"]["type"] == "Component"
    assert out["occurrence"]["type"] == "Occurrence"
    assert fake_design.rootComponent.occurrences.count == 1


def test_create_component_named(ctx, fake_design):
    out = run(ctx, "create_component", name="Arm")
    assert out["component"]["name"] == "Arm"
    assert fake_design.rootComponent.occurrences.item(0).component.name == "Arm"


def test_create_component_under_parent(ctx, asm, monkeypatch):
    root, a, p, q = asm
    calls = []
    orig = fusion.Occurrence.createForAssemblyContext
    monkeypatch.setattr(fusion.Occurrence, "createForAssemblyContext",
                        lambda self, occ: calls.append(occ) or orig(self, occ))
    out = run(ctx, "create_component", parent={"name": "A:1"})
    assert calls == [a]
    assert a.component.occurrences.count == 1 and root.occurrences.count == 2
    new = a.component.occurrences.item(0)
    assert new.component.entityToken == out["component"]["token"]


def test_create_component_with_bodies_returns_new_refs(ctx, asm):
    root, a, p, q = asm
    b1, b2 = root.add_body("B1"), root.add_body("B2")
    out = run(ctx, "create_component", name="C", bodies=[{"token": b1.entityToken}, {"token": b2.entityToken}])
    comp = root.occurrences.item(root.occurrences.count - 1).component
    assert [b.name for b in comp.bRepBodies] == ["B1", "B2"]
    assert root.bRepBodies.count == 0
    assert [r["token"] for r in out["bodies"]] == [b.entityToken for b in comp.bRepBodies]
    assert out["bodies"][0]["token"] != b1.entityToken


def test_create_component_empty_bodies_list_errors(ctx):
    e = err(ctx, "create_component", bodies=[])
    assert e.code == "INVALID_PARAMS" and "omit bodies" in e.hints[0]


def test_create_component_unsupported_checked_before_creation(ctx, fake_design, monkeypatch):
    b = fake_design.rootComponent.add_body("B1")
    monkeypatch.delattr(fusion.BRepBody, "moveToComponent")
    assert err(ctx, "create_component", bodies=[{"token": b.entityToken}]).code == "UNSUPPORTED"
    assert fake_design.rootComponent.occurrences.count == 0


# ---- geometry (_geometry) ------------------------------------------------------------------

KP = fusion.JointKeyPointTypes


def geo(**kw):
    return assembly.JointGeo.model_construct(**kw)


def resolve(ctx, **kw):
    fusion.JointGeometry.created.clear()
    return assembly._geometry(ctx, geo(**kw))


class Parts:
    """Entities inside occurrence A:1 (component A) plus root-owned ones."""


@pytest.fixture
def parts(asm, fake_design):
    root, a, p, q = asm
    ac = a.component
    plane = core.Plane()
    plane.surfaceType = core.SurfaceTypes.PlaneSurfaceType
    cyl = core.Cylinder()
    cyl.surfaceType = core.SurfaceTypes.CylinderSurfaceType
    body = ac.add_body("AB")
    sk = ac.add_sketch("AS")
    o = Parts()
    o.root, o.a, o.p, o.q, o.ac = root, a, p, q, ac
    o.pface, o.cface = body.add_face(plane), body.add_face(cyl)
    o.edge, o.vertex = body.add_edge(), body.add_vertex()
    o.curve, o.point = sk.add_line(), sk.add_point()
    o.jo = ac.add_joint_origin("JO")
    o.root_face = root.add_body("RB").add_face(plane)
    return o


def test_geometry_planar_face_with_keypoint(ctx, parts):
    jg, occ = resolve(ctx, face=parts.pface, keypoint="end")
    assert jg.kind == "planar_face" and jg.args == (None, KP.EndKeyPoint) and jg.entity.nativeObject is parts.pface
    assert occ is parts.a


def test_geometry_nonplanar_face(ctx, parts):
    jg, _ = resolve(ctx, face=parts.cface)
    assert jg.kind == "nonplanar_face" and jg.args == (KP.CenterKeyPoint,)


def test_geometry_edge_and_sketch_curve_use_curve(ctx, parts):
    jg, _ = resolve(ctx, edge=parts.edge, keypoint="middle")
    assert jg.kind == "curve" and jg.args == (KP.MiddleKeyPoint,)
    jg, _ = resolve(ctx, sketch_curve=parts.curve, keypoint="start")
    assert jg.kind == "curve" and jg.args == (KP.StartKeyPoint,)


def test_geometry_vertex_and_sketch_point_use_point(ctx, parts):
    for field, entity in (("vertex", parts.vertex), ("sketch_point", parts.point)):
        jg, occ = resolve(ctx, **{field: entity})
        assert jg.kind == "point" and jg.args == () and occ is parts.a


def test_geometry_origin_of_and_root_origin(ctx, parts):
    jg, occ = resolve(ctx, origin_of=parts.a)
    assert jg.kind == "point" and occ is parts.a
    assert jg.entity.assemblyContext is parts.a and jg.entity.nativeObject is parts.ac.originConstructionPoint
    jg, occ = resolve(ctx, root_origin=True)
    assert jg.entity is parts.root.originConstructionPoint and occ is None


def test_geometry_joint_origin_passed_through_without_createby(ctx, parts):
    jo, occ = resolve(ctx, joint_origin=parts.jo, keypoint="end")
    assert fusion.JointGeometry.created == []
    assert jo.nativeObject is parts.jo and jo.assemblyContext is parts.a and occ is parts.a


def test_geometry_requires_exactly_one_field(ctx, parts):
    assert err_geo(ctx).code == "INVALID_PARAMS"
    assert err_geo(ctx, face=parts.pface, edge=parts.edge).code == "INVALID_PARAMS"
    assert err_geo(ctx, root_origin=True, origin_of=parts.a).code == "INVALID_PARAMS"


def err_geo(ctx, **kw):
    with pytest.raises(ActionError) as e:
        assembly._geometry(ctx, geo(**kw))
    return e.value


def test_geometry_proxy_assembly_context_used_as_is(ctx, parts):
    other = parts.p
    proxy = parts.pface.createForAssemblyContext(other)
    jg, occ = resolve(ctx, face=proxy)
    assert jg.entity is proxy and occ is other


def test_geometry_proxy_explicit_occurrence(ctx, parts):
    second = fusion.Occurrence("A:2", parts.ac, parts.root)
    parts.root._occ.append(second)
    jg, occ = resolve(ctx, face=parts.pface, occurrence=second)
    assert occ is second and jg.entity.assemblyContext is second


def test_geometry_proxy_wrong_occurrence(ctx, parts):
    e = err_geo(ctx, face=parts.pface, occurrence=parts.p)
    assert e.code == "INVALID_PARAMS"


def test_geometry_proxy_root_entity_is_native(ctx, parts):
    jg, occ = resolve(ctx, face=parts.root_face)
    assert jg.entity is parts.root_face and occ is None


def test_geometry_proxy_none_found(ctx, parts):
    parts.root._occ.remove(parts.a)
    assert err_geo(ctx, face=parts.pface).code == "NOT_FOUND"


def test_geometry_proxy_ambiguous_lists_candidates(ctx, parts):
    second = fusion.Occurrence("A:2", parts.ac, parts.root)
    parts.root._occ.append(second)
    e = err_geo(ctx, face=parts.pface)
    assert e.code == "AMBIGUOUS" and len(e.extra["candidates"]) == 2


def test_geometry_invalid_keypoint_is_fusion_error_with_hint(ctx, parts, monkeypatch):
    def boom(curve, kp):
        raise RuntimeError("bad keypoint")

    monkeypatch.setattr(fusion.JointGeometry, "createByCurve", staticmethod(boom))
    e = err_geo(ctx, edge=parts.edge, keypoint="start")
    assert e.code == "FUSION_ERROR" and "bad keypoint" in e.message and "faces use center" in e.hints[0]


# ---- joint and limits ----------------------------------------------------------------------

D = fusion.JointDirections


@pytest.fixture
def two(fake_design):
    root = fake_design.rootComponent
    a, b = root.add_occurrence("A:1", "A"), root.add_occurrence("B:1", "B")
    return root, a, b


def mkjoint(ctx, two, type="rigid", **kw):
    root, a, b = two
    return run(ctx, "joint", type=type, geometry_one={"origin_of": {"token": a.entityToken}},
               geometry_two={"origin_of": {"token": b.entityToken}}, **kw)


def last_input(two):
    return two[0]._joints[-1].input


@pytest.mark.parametrize("type,motion", [
    ("rigid", ("rigid",)),
    ("revolute", ("revolute", D.XAxisJointDirection)),
    ("slider", ("slider", D.XAxisJointDirection)),
    ("cylindrical", ("cylindrical", D.XAxisJointDirection)),
    ("pin_slot", ("pin_slot", D.XAxisJointDirection, D.YAxisJointDirection)),
    ("planar", ("planar", D.XAxisJointDirection)),
    ("ball", ("ball", D.XAxisJointDirection, D.YAxisJointDirection)),
])
def test_joint_motion_call_per_type(ctx, two, type, motion):
    out = mkjoint(ctx, two, type, axis="x")
    assert last_input(two).motion == motion and out["type"] == type


def test_joint_second_axis_explicit_and_default(ctx, two):
    mkjoint(ctx, two, "pin_slot", axis="z", second_axis="x")
    assert last_input(two).motion == ("pin_slot", D.ZAxisJointDirection, D.XAxisJointDirection)
    mkjoint(ctx, two, "ball")  # axis z -> second defaults to x
    assert last_input(two).motion == ("ball", D.ZAxisJointDirection, D.XAxisJointDirection)
    mkjoint(ctx, two, "ball", axis="y")  # second defaults to x
    assert last_input(two).motion[2] == D.XAxisJointDirection


def test_joint_second_axis_misuse(ctx, two):
    e = err(ctx, "joint", type="revolute", second_axis="x",
            geometry_one={"origin_of": {"token": two[1].entityToken}},
            geometry_two={"origin_of": {"token": two[2].entityToken}})
    assert e.code == "INVALID_PARAMS" and not two[0]._joints


def test_joint_offset_angle_flip_and_result(ctx, two):
    out = mkjoint(ctx, two, "revolute", offset="25.4 mm", angle="90 deg", flip=True, name="Hinge")
    inp = last_input(two)
    assert (inp.offset.value, inp.angle.value, inp.isFlipped) == ("25.4 mm", "90 deg", True)
    assert out["offset"] == pytest.approx(25.4) and out["angle"] == pytest.approx(90)
    assert out["flipped"] is True and out["joint"]["name"] == "Hinge"
    assert out["occurrence_one"]["name"] == "A:1" and out["occurrence_two"]["name"] == "B:1"
    assert out["limits"] == {"rotation": {"min": None, "max": None, "rest": None}}


def test_joint_defaults_leave_offset_and_angle_unset(ctx, two):
    out = mkjoint(ctx, two)
    assert last_input(two).offset is None and last_input(two).angle is None
    assert out["offset"] == 0 and out["angle"] == 0 and out["flipped"] is False and out["limits"] is None


def test_joint_same_occurrence_error(ctx, two):
    a = {"origin_of": {"token": two[1].entityToken}}
    assert err(ctx, "joint", type="rigid", geometry_one=a, geometry_two=a).code == "INVALID_PARAMS"
    root = {"root_origin": True}
    assert err(ctx, "joint", type="rigid", geometry_one=root, geometry_two=root).code == "INVALID_PARAMS"
    assert not two[0]._joints


def test_joint_geometry_roles(ctx, two):
    mkjoint(ctx, two)
    inp = last_input(two)
    assert inp.geometry_one.entity.assemblyContext is two[1]
    assert inp.geometry_two.entity.assemblyContext is two[2]


@pytest.mark.parametrize("type", ["revolute", "cylindrical", "pin_slot"])
def test_limits_rotation_enabled_flags_and_internal_values(ctx, two, type):
    out = mkjoint(ctx, two, type, limits={"rotation_min": -45, "rotation_max": 90, "rotation_rest": 0})
    lim = two[0]._joints[-1].jointMotion.rotationLimits
    assert (lim.isMinimumValueEnabled, lim.isMaximumValueEnabled, lim.isRestValueEnabled) == (True,) * 3
    assert (lim.minimumValue, lim.maximumValue, lim.restValue) == pytest.approx(
        (-0.7853981633974483, 1.5707963267948966, 0))
    assert out["limits"]["rotation"] == pytest.approx({"min": -45, "max": 90, "rest": 0})


@pytest.mark.parametrize("type", ["slider", "cylindrical", "pin_slot"])
def test_limits_slide_values_in_cm_and_reported_in_output_units(ctx, two, type):
    out = mkjoint(ctx, two, type, limits={"slide_min": "10 mm", "slide_max": "2 cm"})
    lim = two[0]._joints[-1].jointMotion.slideLimits
    assert (lim.minimumValue, lim.maximumValue) == pytest.approx((1.0, 2.0))
    assert lim.isMinimumValueEnabled and lim.isMaximumValueEnabled and not lim.isRestValueEnabled
    assert out["limits"]["slide"] == pytest.approx({"min": 10, "max": 20, "rest": None})


def test_limits_only_applicable_objects_reported(ctx, two):
    assert list(mkjoint(ctx, two, "slider")["limits"]) == ["slide"]
    assert list(mkjoint(ctx, two, "revolute")["limits"]) == ["rotation"]
    assert list(mkjoint(ctx, two, "pin_slot")["limits"]) == ["rotation", "slide"]
    for t in ("rigid", "planar", "ball"):
        assert mkjoint(ctx, two, t)["limits"] is None


@pytest.mark.parametrize("type,field,value", [
    ("revolute", "slide_min", "1 mm"), ("slider", "rotation_max", 10),
    ("rigid", "rotation_min", 1), ("planar", "slide_rest", "1 mm"), ("ball", "rotation_rest", 1),
])
def test_limits_rejected_fields_create_nothing(ctx, two, type, field, value):
    e = err(ctx, "joint", type=type, limits={field: value},
            geometry_one={"origin_of": {"token": two[1].entityToken}},
            geometry_two={"origin_of": {"token": two[2].entityToken}})
    assert e.code == "INVALID_PARAMS" and field in e.message and type in e.message
    assert not two[0]._joints


def test_limits_min_not_less_than_max(ctx, two):
    for lim in ({"rotation_min": 90, "rotation_max": 90}, {"rotation_min": 10, "rotation_max": 5}):
        e = err(ctx, "joint", type="revolute", limits=lim,
                geometry_one={"origin_of": {"token": two[1].entityToken}},
                geometry_two={"origin_of": {"token": two[2].entityToken}})
        assert e.code == "INVALID_PARAMS"
    e = err(ctx, "joint", type="slider", limits={"slide_min": "2 cm", "slide_max": "10 mm"},
            geometry_one={"origin_of": {"token": two[1].entityToken}},
            geometry_two={"origin_of": {"token": two[2].entityToken}})
    assert e.code == "INVALID_PARAMS" and not two[0]._joints


# ---- as_built_joint ------------------------------------------------------------------------

def refs(*occs):
    return [{"token": o.entityToken} for o in occs]


def test_as_built_rigid_without_geometry(ctx, two):
    root, a, b = two
    out = run(ctx, "as_built_joint", type="rigid", occurrence_one=refs(a)[0], occurrence_two=refs(b)[0],
              name="Fixed")
    j = root._abj[0]
    assert j.input.geometry is None and j.input.motion == ("rigid",) and j.name == "Fixed"
    assert out["type"] == "rigid" and out["joint"]["name"] == "Fixed" and out["limits"] is None
    assert out["occurrence_one"]["name"] == "A:1" and out["occurrence_two"]["name"] == "B:1"
    assert not {"offset", "angle", "flipped"} & set(out)


def test_as_built_revolute_with_origin_geometry_axes_and_limits(ctx, two):
    root, a, b = two
    out = run(ctx, "as_built_joint", type="revolute", occurrence_one=refs(a)[0], occurrence_two=refs(b)[0],
              geometry={"origin_of": refs(a)[0]}, axis="y", limits={"rotation_min": 0, "rotation_max": 180})
    inp = root._abj[0].input
    assert inp.motion == ("revolute", D.YAxisJointDirection) and inp.geometry.kind == "point"
    assert out["limits"]["rotation"] == pytest.approx({"min": 0, "max": 180, "rest": None})


def test_as_built_non_rigid_needs_geometry(ctx, two):
    e = err(ctx, "as_built_joint", type="slider", occurrence_one=refs(two[1])[0], occurrence_two=refs(two[2])[0])
    assert e.code == "INVALID_PARAMS" and "slider" in e.message and not two[0]._abj


def test_as_built_same_occurrence(ctx, two):
    e = err(ctx, "as_built_joint", type="rigid", occurrence_one=refs(two[1])[0], occurrence_two=refs(two[1])[0])
    assert e.code == "INVALID_PARAMS"


def test_as_built_second_axis_misuse_and_limits_rejected(ctx, two):
    kw = dict(occurrence_one=refs(two[1])[0], occurrence_two=refs(two[2])[0], geometry={"root_origin": True})
    assert err(ctx, "as_built_joint", type="slider", second_axis="x", **kw).code == "INVALID_PARAMS"
    assert err(ctx, "as_built_joint", type="slider", limits={"rotation_min": 1}, **kw).code == "INVALID_PARAMS"
    assert not two[0]._abj


def test_as_built_joint_origin_passed_as_is(ctx, two):
    root, a, b = two
    jo = a.component.add_joint_origin("JO1")
    run(ctx, "as_built_joint", type="revolute", occurrence_one=refs(a)[0], occurrence_two=refs(b)[0],
        geometry={"joint_origin": {"token": jo.entityToken}})
    assert root._abj[0].input.geometry.nativeObject is jo


def test_as_built_joint_origin_rejected_is_unsupported(ctx, two, monkeypatch):
    root, a, b = two
    jo = a.component.add_joint_origin("JO1")

    def boom(self, one, two_, g):
        raise RuntimeError("bad input")

    monkeypatch.setattr(fusion.AsBuiltJoints, "createInput", boom)
    e = err(ctx, "as_built_joint", type="revolute", occurrence_one=refs(a)[0], occurrence_two=refs(b)[0],
            geometry={"joint_origin": {"token": jo.entityToken}})
    assert e.code == "UNSUPPORTED" and "joint origin" in e.message
    # other createInput failures are not masked
    with pytest.raises(RuntimeError):
        run(ctx, "as_built_joint", type="revolute", occurrence_one=refs(a)[0], occurrence_two=refs(b)[0],
            geometry={"root_origin": True})


# ---- rigid_group ---------------------------------------------------------------------------

def test_rigid_group_add_call_and_result(ctx, two):
    root, a, b = two
    out = run(ctx, "rigid_group", occurrences=refs(a, b), include_children=False, name="Lid")
    g = root._rg[0]
    assert g.includeChildren is False and list(g.occurrences) == [a, b] and g.name == "Lid"
    assert out["name"] == "Lid" and out["rigid_group"]["type"] == "RigidGroup"
    assert [o["name"] for o in out["occurrences"]] == ["A:1", "B:1"]


def test_rigid_group_include_children_defaults_true(ctx, two):
    run(ctx, "rigid_group", occurrences=refs(two[1], two[2]))
    assert two[0]._rg[0].includeChildren is True


def test_rigid_group_needs_two_distinct(ctx, two):
    assert err(ctx, "rigid_group", occurrences=refs(two[1])).code == "INVALID_PARAMS"
    assert err(ctx, "rigid_group", occurrences=refs(two[1], two[1])).code == "INVALID_PARAMS"
    assert not two[0]._rg


# ---- contact_set ---------------------------------------------------------------------------

@pytest.fixture
def cs_design(fake_design):
    fake_design.enable_contact_sets()
    return fake_design


def test_contact_set_three_analysis_modes(ctx, cs_design):
    out = run(ctx, "contact_set", analysis="all")
    assert out == {"analysis": "all", "created": None, "updated": None}
    assert cs_design.isContactAnalysisEnabled and not cs_design.isContactSetAnalysis
    assert run(ctx, "contact_set", analysis="contact_sets")["analysis"] == "contact_sets"
    assert cs_design.isContactAnalysisEnabled and cs_design.isContactSetAnalysis
    assert run(ctx, "contact_set", analysis="off")["analysis"] == "off"
    assert not cs_design.isContactAnalysisEnabled
    assert run(ctx, "contact_set", analysis="all")["analysis"] == "all"
    assert not cs_design.isContactSetAnalysis


def test_contact_set_analysis_works_without_contact_sets_attribute(ctx, fake_design):
    assert run(ctx, "contact_set", analysis="all")["analysis"] == "all"


def test_contact_set_create_occurrences_then_bodies(ctx, cs_design, two):
    root, a, b = two
    body = root.add_body("RB")
    out = run(ctx, "contact_set", create={"name": "C1", "occurrences": refs(a, b),
                                          "bodies": [{"token": body.entityToken}]})
    cs = cs_design._contact_sets[0]
    assert cs.name == "C1" and list(cs.occurrencesAndBodies) == [a, b, body]
    assert out["created"]["name"] == "C1" and out["updated"] is None


def test_contact_set_suppress_and_enable(ctx, cs_design, two):
    run(ctx, "contact_set", create={"name": "C1", "occurrences": refs(two[1], two[2])})
    out = run(ctx, "contact_set", set="C1", enabled=False)
    assert cs_design._contact_sets[0].isSuppressed is True and out["updated"]["name"] == "C1"
    assert out["created"] is None
    run(ctx, "contact_set", set="C1", enabled=True)
    assert cs_design._contact_sets[0].isSuppressed is False


def test_contact_set_runs_analysis_create_then_set_in_order(ctx, cs_design, two):
    out = run(ctx, "contact_set", analysis="contact_sets",
              create={"name": "C1", "occurrences": refs(two[1], two[2])})
    assert out["analysis"] == "contact_sets" and out["created"]["name"] == "C1"


def test_contact_set_create_unsupported_changes_nothing(ctx, fake_design, two):
    e = err(ctx, "contact_set", analysis="all", create={"name": "C1", "occurrences": refs(two[1], two[2])})
    assert e.code == "UNSUPPORTED" and not fake_design.isContactAnalysisEnabled


def test_contact_set_empty_call_errors(ctx, cs_design):
    assert err(ctx, "contact_set").code == "INVALID_PARAMS"


def test_contact_set_set_and_enabled_pair(ctx, cs_design, two):
    run(ctx, "contact_set", create={"name": "C1", "occurrences": refs(two[1], two[2])})
    assert err(ctx, "contact_set", set="C1").code == "INVALID_PARAMS"
    assert err(ctx, "contact_set", analysis="all", enabled=True).code == "INVALID_PARAMS"
    assert cs_design._contact_sets[0].isSuppressed is False


def test_contact_set_create_needs_two_members(ctx, cs_design, two):
    assert err(ctx, "contact_set", create={"name": "C1", "occurrences": refs(two[1])}).code == "INVALID_PARAMS"
    assert not cs_design._contact_sets
    assert err(ctx, "contact_set", analysis="all", create={"name": "C1"}).code == "INVALID_PARAMS"
    assert not cs_design.isContactAnalysisEnabled


def test_contact_set_unknown_set_is_not_found(ctx, cs_design):
    assert err(ctx, "contact_set", set="nope", enabled=False).code == "NOT_FOUND"
