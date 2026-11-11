"""Solid feature actions (design § 4): API call sequence, option handling and parameter errors."""
import pytest
from model_fakes import env, env_direct, plane_geo  # noqa: F401

from adsk import fusion

from defusion.errors import INVALID_PARAMS, UNSUPPORTED

FO = fusion.FeatureOperations
POS, NEG = fusion.ExtentDirections.PositiveExtentDirection, fusion.ExtentDirections.NegativeExtentDirection
SO, PD = fusion.SweepOrientationTypes, fusion.PatternDistanceType


def extrude_env(env):
    sk, prof = env.new_profile()
    return sk, env.profile(sk)


def last_input(env, kind):
    return env.coll(kind).created[-1]


# --- extrude ---------------------------------------------------------------------------------


def test_extrude_distance_defaults(env):
    sk, p = extrude_env(env)
    out = env.call("extrude", profile=p, distance=5)
    inp = last_input(env, "extrudeFeatures")
    assert inp.args == (sk.profiles.item(0), FO.NewBodyFeatureOperation)
    assert inp.calls("setOneSideExtent") == [(("DistanceExtentDefinition", "5 mm"), POS, "0 deg")]
    assert inp.sets("participantBodies") == []
    assert out["feature"]["type"] == "FakeFeature" and out["bodies"] == []


def test_extrude_flip_taper_name_and_expression(env):
    sk, p = extrude_env(env)
    env.design.add_parameter("d1", 1.0)
    out = env.call("extrude", profile=p, distance="d1*2", flip=True, taper_angle=5, operation="cut", name="Cutter")
    inp = last_input(env, "extrudeFeatures")
    assert inp.calls("setOneSideExtent") == [(("DistanceExtentDefinition", "d1*2"), NEG, "5 deg")]
    assert inp.args[1] == FO.CutFeatureOperation
    assert out["feature"]["name"] == "Cutter"


def test_extrude_symmetric(env):
    sk, p = extrude_env(env)
    env.call("extrude", profile=p, extent="symmetric", distance=8, full_length=False, taper_angle="2 deg")
    assert last_input(env, "extrudeFeatures").calls("setSymmetricExtent") == [("8 mm", False, "2 deg")]
    env.call("extrude", profile=p, extent="symmetric", distance=8)
    assert last_input(env, "extrudeFeatures").calls("setSymmetricExtent") == [("8 mm", True, "0 deg")]


def test_extrude_two_sided(env):
    sk, p = extrude_env(env)
    env.call("extrude", profile=p, extent="two_sided", distance=3, distance_two=4, taper_angle=1, taper_angle_two=2)
    assert last_input(env, "extrudeFeatures").calls("setTwoSidesExtent") == [
        (("DistanceExtentDefinition", "3 mm"), ("DistanceExtentDefinition", "4 mm"), "1 deg", "2 deg")]


def test_extrude_to_object(env):
    sk, p = extrude_env(env)
    b = env.body("Target", faces=1)
    face = b.faces.item(0)
    env.call("extrude", profile=p, extent="to_object", to_entity=env.r(face), to_offset=2, flip=True)
    assert last_input(env, "extrudeFeatures").calls("setOneSideExtent") == [
        (("ToEntityExtentDefinition", face, False, "2 mm"), NEG, "0 deg")]


def test_extrude_to_object_rejects_wrong_kind_and_missing(env):
    sk, p = extrude_env(env)
    other = env.body("T", edges=1)
    e = env.err("extrude", profile=p, extent="to_object", to_entity=env.r(other.edges.item(0)))
    assert e.code == INVALID_PARAMS and "to_entity accepts" in e.message
    assert "to_entity is required" in env.err("extrude", profile=p, extent="to_object").message


@pytest.mark.parametrize("kw,missing", [
    ({}, "distance"),
    ({"extent": "symmetric"}, "distance"),
    ({"extent": "two_sided", "distance": 1}, "distance_two"),
])
def test_extrude_required_parameters(env, kw, missing):
    sk, p = extrude_env(env)
    e = env.err("extrude", profile=p, **kw)
    assert e.code == INVALID_PARAMS and missing + " is required" in e.message
    assert not env.coll("extrudeFeatures").created


@pytest.mark.parametrize("kw,name", [({"distance": 0}, "distance"), ({"distance": -1}, "distance"),
                                     ({"extent": "two_sided", "distance": 1, "distance_two": 0}, "distance_two")])
def test_extrude_distances_must_be_positive(env, kw, name):
    sk, p = extrude_env(env)
    e = env.err("extrude", profile=p, **kw)
    assert e.code == INVALID_PARAMS and e.message == name + " must be > 0"


def test_extrude_participant_bodies_rules(env):
    sk, p = extrude_env(env)
    b = env.body("B")
    for op in ("new_body", "new_component"):
        e = env.err("extrude", profile=p, distance=1, operation=op, participant_bodies=[env.r(b)])
        assert e.code == INVALID_PARAMS and "participant_bodies applies only" in e.message
    env.call("extrude", profile=p, distance=1, operation="join", participant_bodies=[env.r(b)])
    assert last_input(env, "extrudeFeatures").participantBodies == [b]


def test_extrude_uses_owner_component_of_the_profile(env):
    sub = env.comp.add_occurrence("O").component
    sk, prof = env.new_profile(sub)
    env.call("extrude", profile=env.profile(sk), distance=1)
    assert sub.features.extrudeFeatures.added and not env.coll("extrudeFeatures").added


def test_extrude_requires_design(env):
    env.ctx.design = None
    assert env.err("extrude", profile={"sketch": "x"}, distance=1).code in ("NO_DESIGN",)


# --- revolve ---------------------------------------------------------------------------------


def test_revolve_defaults_and_options(env):
    sk, p = extrude_env(env)
    env.call("revolve", profile=p, axis="Z")
    inp = last_input(env, "revolveFeatures")
    assert inp.args == (sk.profiles.item(0), env.comp.zConstructionAxis, FO.NewBodyFeatureOperation)
    assert inp.calls("setAngleExtent") == [(False, "360 deg")]
    env.call("revolve", profile=p, axis="Z", extent="symmetric", angle=90, operation="join")
    assert last_input(env, "revolveFeatures").calls("setAngleExtent") == [(True, "90 deg")]
    env.call("revolve", profile=p, axis="X", extent="two_sided", angle=30, angle_two="45 deg")
    assert last_input(env, "revolveFeatures").calls("setTwoSideAngleExtent") == [("30 deg", "45 deg")]


def test_revolve_errors(env):
    sk, p = extrude_env(env)
    assert "angle_two is required" in env.err("revolve", profile=p, axis="Z", extent="two_sided").message
    assert env.err("revolve", profile=p, axis="Z", angle=0).message == "angle must be > 0"
    e = env.err("revolve", profile=p, axis="Z", participant_bodies=[env.r(env.body("B"))])
    assert e.code == INVALID_PARAMS


# --- sweep -----------------------------------------------------------------------------------


def curve(env, sk=None):
    return (sk or env.new_profile()[0]).add_line()


def test_sweep_path_options(env):
    sk, p = extrude_env(env)
    path, rail = curve(env, sk), curve(env, sk)
    env.call("sweep", profile=p, path=[env.r(path)], chain=False, guide_rail=[env.r(rail)], twist_angle=10,
             orientation="parallel", operation="join")
    inp = last_input(env, "sweepFeatures")
    assert env.comp.features.created_paths == [("Path", [path], False), ("Path", [rail], False)]
    assert inp.args[1] == ("Path", [path], False) and inp.args[2] == FO.JoinFeatureOperation
    assert inp.sets("orientation") == [SO.ParallelOrientationType]
    assert inp.sets("guideRail") == [("Path", [rail], False)] and inp.sets("twistAngle") == ["10 deg"]


def test_sweep_defaults_set_no_rail_or_twist(env):
    sk, p = extrude_env(env)
    env.call("sweep", profile=p, path=[env.r(curve(env, sk))])
    inp = last_input(env, "sweepFeatures")
    assert inp.sets("orientation") == [SO.PerpendicularOrientationType]
    assert inp.sets("guideRail") == [] and inp.sets("twistAngle") == []


def test_sweep_path_kinds_checked(env):
    sk, p = extrude_env(env)
    pt = sk.add_point()
    assert "path accepts" in env.err("sweep", profile=p, path=[env.r(pt)]).message
    assert "guide_rail accepts" in env.err("sweep", profile=p, path=[env.r(curve(env, sk))],
                                           guide_rail=[env.r(pt)]).message
    edge_body = env.body("B", edges=1)
    env.call("sweep", profile=p, path=[env.r(edge_body.edges.item(0))])  # BRepEdge allowed


# --- loft ------------------------------------------------------------------------------------


def test_loft_sections_rails_and_flags(env):
    p1, p2 = [extrude_env(env)[1] for _ in range(2)]
    sk = env.new_profile()[0]
    r1, r2 = curve(env, sk), curve(env, sk)
    env.call("loft", sections=[p1, p2], rails=[env.r(r1), env.r(r2)], solid=False, closed=True, name="L")
    inp = last_input(env, "loftFeatures")
    assert inp.args == (FO.NewBodyFeatureOperation,)
    assert len(inp.loftSections.calls("add")) == 2
    assert inp.centerLineOrRails.calls("addRail") == [(r1,), (r2,)]
    assert inp.sets("isSolid") == [False] and inp.sets("isClosed") == [True]


def test_loft_centerline(env):
    p1, p2 = [extrude_env(env)[1] for _ in range(2)]
    c = curve(env)
    env.call("loft", sections=[p1, p2], centerline=env.r(c))
    assert last_input(env, "loftFeatures").centerLineOrRails.calls("addCenterLine") == [(c,)]


def test_loft_errors(env):
    p1, p2 = [extrude_env(env)[1] for _ in range(2)]
    c = curve(env)
    assert "at least 2 sections" in env.err("loft", sections=[p1]).message
    assert env.err("loft", sections=[p1, p2], rails=[env.r(c)], centerline=env.r(c)).code == INVALID_PARAMS
    pt = env.new_profile()[0].add_point()
    assert "rails accepts" in env.err("loft", sections=[p1, p2], rails=[env.r(pt)]).message
    assert "centerline accepts" in env.err("loft", sections=[p1, p2], centerline=env.r(pt)).message
    assert env.err("loft", sections=[p1, p2], participant_bodies=[env.r(env.body("B"))]).code == INVALID_PARAMS


# --- fillet, chamfer, shell, mirror ----------------------------------------------------------------

from adsk import core  # noqa: E402

from defusion.errors import FUSION_ERROR, WRONG_DESIGN_TYPE  # noqa: E402


def body_with(env, faces=0, edges=0, name="B", comp=None):
    return env.body(name, comp=comp, faces=faces, edges=edges)


def edges_of(env, n=2, comp=None):
    b = body_with(env, edges=n, comp=comp)
    return b, [b.edges.item(i) for i in range(n)]


def test_fillet_edge_set_inputs(env):
    b, es = edges_of(env)
    out = env.call("fillet", edges=[env.r(e) for e in es], radius=2, tangent_chain=False, name="Round")
    inp = last_input(env, "filletFeatures")
    assert inp.edgeSetInputs.calls("addConstantRadiusEdgeSet") == [(es, "2 mm", False)]
    assert out["feature"]["name"] == "Round"


def test_fillet_fallback_without_edge_set_inputs(env):
    b, es = edges_of(env, 1)
    env.coll("filletFeatures").hide_input("edgeSetInputs")
    env.call("fillet", edges=[env.r(es[0])], radius=1)
    assert last_input(env, "filletFeatures").calls("addConstantRadiusEdgeSet") == [(es, "1 mm", True)]


def test_fillet_errors(env):
    b, es = edges_of(env, 1)
    assert env.err("fillet", edges=[env.r(es[0])], radius=0).message == "radius must be > 0"
    sub = env.comp.add_occurrence("O").component
    _, other = edges_of(env, 1, comp=sub)
    e = env.err("fillet", edges=[env.r(es[0]), env.r(other[0])], radius=1)
    assert e.message == "all edges must belong to one component"
    assert sub.features.filletFeatures.created == [] and env.coll("filletFeatures").created == []


def test_chamfer_edge_sets(env):
    b, es = edges_of(env)
    r = [env.r(e) for e in es]
    env.call("chamfer", edges=r)
    assert last_input(env, "chamferFeatures").chamferEdgeSets.calls("addEqualDistanceChamferEdgeSet") == [(es, "1 mm", True)]
    env.call("chamfer", edges=r, chamfer_type="two_distances", distance=2, distance_two=3, flip=True, tangent_chain=False)
    assert last_input(env, "chamferFeatures").chamferEdgeSets.calls("addTwoDistancesChamferEdgeSet") == [
        (es, "2 mm", "3 mm", True, False)]
    env.call("chamfer", edges=r, chamfer_type="distance_angle", distance=2, angle=30)
    assert last_input(env, "chamferFeatures").chamferEdgeSets.calls("addDistanceAndAngleChamferEdgeSet") == [
        (es, "2 mm", "30 deg", False, True)]


def test_chamfer_create_input_fallback_ignores_flip(env):
    b, es = edges_of(env)
    r = [env.r(e) for e in es]
    env.coll("chamferFeatures").hide("createInput2")
    env.call("chamfer", edges=r, distance=2)
    inp = last_input(env, "chamferFeatures")
    assert inp.args == (es, True) and inp.calls("setToEqualDistance") == [("2 mm",)]
    env.call("chamfer", edges=r, chamfer_type="two_distances", distance_two=3, flip=True)
    assert last_input(env, "chamferFeatures").calls("setToTwoDistances") == [("1 mm", "3 mm")]
    env.call("chamfer", edges=r, chamfer_type="distance_angle", angle=45)
    assert last_input(env, "chamferFeatures").calls("setToDistanceAndAngle") == [("1 mm", "45 deg")]


@pytest.mark.parametrize("kw,msg", [
    ({"chamfer_type": "two_distances"}, "distance_two is required"),
    ({"chamfer_type": "distance_angle"}, "angle is required"),
    ({"chamfer_type": "distance_angle", "angle": 90}, "angle must be between 0 and 90 deg"),
    ({"chamfer_type": "distance_angle", "angle": 0}, "angle must be between 0 and 90 deg"),
    ({"distance": 0}, "distance must be > 0"),
    ({"chamfer_type": "two_distances", "distance_two": -1}, "distance_two must be > 0"),
])
def test_chamfer_errors(env, kw, msg):
    b, es = edges_of(env, 1)
    e = env.err("chamfer", edges=[env.r(es[0])], **kw)
    assert e.code == INVALID_PARAMS and msg in e.message


def test_shell_faces_and_bodies_directions(env):
    b = body_with(env, faces=2)
    fs = [b.faces.item(0)]
    env.call("shell", thickness=2, faces=[env.r(fs[0])], tangent_chain=False)
    inp = last_input(env, "shellFeatures")
    assert inp.args == (fs, False) and inp.sets("insideThickness") == ["2 mm"] and inp.sets("outsideThickness") == []
    env.call("shell", thickness=2, bodies=[env.r(b)], direction="outside")
    inp = last_input(env, "shellFeatures")
    assert inp.args == ([b], True) and inp.sets("outsideThickness") == ["2 mm"] and inp.sets("insideThickness") == []
    env.call("shell", thickness=1, bodies=[env.r(b)], direction="both")
    inp = last_input(env, "shellFeatures")
    assert inp.sets("insideThickness") == ["1 mm"] and inp.sets("outsideThickness") == ["1 mm"]


def test_shell_errors(env):
    b = body_with(env, faces=1)
    f = env.r(b.faces.item(0))
    assert "exactly one of faces or bodies" in env.err("shell", thickness=1).message
    assert "exactly one of faces or bodies" in env.err("shell", thickness=1, faces=[f], bodies=[env.r(b)]).message
    assert env.err("shell", thickness=0, faces=[f]).message == "thickness must be > 0"


def test_mirror_bodies_and_features(env):
    b = body_with(env)
    env.call("mirror", entities=[env.r(b)], plane="YZ", combine=True)
    inp = last_input(env, "mirrorFeatures")
    assert inp.args == ([b], env.comp.yZConstructionPlane) and inp.sets("isCombine") == [True]
    f = env.feature()
    env.call("mirror", entities=[env.r(f)], plane="XY")
    assert last_input(env, "mirrorFeatures").sets("isCombine") == []


def test_mirror_rules(env_direct):
    env = env_direct
    b = body_with(env)
    f = env.feature()
    assert env.err("mirror", entities=[env.r(f)], plane="XY").code == WRONG_DESIGN_TYPE
    assert env.err("mirror", entities=[env.r(b), env.r(f)], plane="XY").code == INVALID_PARAMS
    env.call("mirror", entities=[env.r(b)], plane="XY")


def test_mirror_combine_with_features_rejected(env):
    f = env.feature()
    e = env.err("mirror", entities=[env.r(f)], plane="XY", combine=True)
    assert e.code == INVALID_PARAMS and "combine applies only to bodies" in e.message


# --- hole ------------------------------------------------------------------------------------


def planar_face(env):
    b = body_with(env, faces=1)
    return b.faces.item(0)


def test_hole_simple_on_face_with_depth(env):
    f = planar_face(env)
    env.call("hole", face=env.r(f), point=[1, 2, 3], depth=10, name="H")
    inp = last_input(env, "holeFeatures")
    assert inp.args == ("5 mm",)
    pt = inp.calls("setPositionByPoint")[0]
    assert pt[0] is f and (pt[1].x, pt[1].y, pt[1].z) == pytest.approx((0.1, 0.2, 0.3))
    assert inp.calls("setDistanceExtent") == [("10 mm",)] and inp.sets("tipAngle") == ["118 deg"]


def test_hole_through_all_counterbore_and_countersink(env):
    f = planar_face(env)
    env.call("hole", hole_type="counterbore", diameter=4, counterbore_diameter=8, counterbore_depth=2,
             through_all=True, face=env.r(f), point=[0, 0, 0])
    inp = last_input(env, "holeFeatures")
    assert inp.args == ("4 mm", "8 mm", "2 mm")
    assert inp.calls("setAllExtent") == [(POS,)]
    env.call("hole", hole_type="countersink", diameter=4, countersink_diameter=9, countersink_angle=82,
             depth=3, face=env.r(f), point=[0, 0, 0], tip_angle=100)
    inp = last_input(env, "holeFeatures")
    assert inp.args == ("4 mm", "9 mm", "82 deg") and inp.sets("tipAngle") == ["100 deg"]


def test_hole_sketch_points_and_participants(env):
    sk = env.comp.add_sketch("S")
    pts = [sk.add_point(), sk.add_point()]
    b = body_with(env)
    env.call("hole", sketch_points=[env.r(p) for p in pts], depth=5, participant_bodies=[env.r(b)])
    inp = last_input(env, "holeFeatures")
    assert inp.calls("setPositionBySketchPoints") == [(pts,)] and inp.participantBodies == [b]


@pytest.mark.parametrize("kw,msg", [
    ({"depth": 5}, "either face and point, or sketch_points"),
    ({"depth": 5, "face": "F"}, "face and point go together"),
    ({"depth": 5, "point": [0, 0, 0]}, "face and point go together"),
    ({"face": "F", "point": [0, 0, 0]}, "exactly one of depth or through_all"),
    ({"face": "F", "point": [0, 0, 0], "depth": 1, "through_all": True}, "exactly one of depth or through_all"),
    ({"face": "F", "point": [0, 0, 0], "depth": 1, "hole_type": "counterbore"}, "counterbore_diameter is required"),
    ({"face": "F", "point": [0, 0, 0], "depth": 1, "hole_type": "counterbore", "counterbore_diameter": 5,
      "counterbore_depth": 1}, "counterbore_diameter must be > diameter"),
    ({"face": "F", "point": [0, 0, 0], "depth": 1, "hole_type": "counterbore", "counterbore_diameter": 8,
      "counterbore_depth": 0}, "counterbore_depth must be > 0"),
    ({"face": "F", "point": [0, 0, 0], "depth": 1, "hole_type": "countersink"}, "countersink_diameter is required"),
    ({"face": "F", "point": [0, 0, 0], "depth": 1, "hole_type": "countersink", "countersink_diameter": 5},
     "countersink_diameter must be > diameter"),
    ({"face": "F", "point": [0, 0, 0], "depth": 1, "diameter": 0}, "diameter must be > 0"),
])
def test_hole_errors(env, kw, msg):
    f = planar_face(env)
    if kw.get("face") == "F":
        kw = dict(kw, face=env.r(f))
    if "sketch_points" in kw:
        pass
    e = env.err("hole", **kw)
    assert e.code == INVALID_PARAMS and msg in e.message


def test_hole_rejects_both_position_forms_and_nonplanar_face(env):
    sk = env.comp.add_sketch("S")
    f = planar_face(env)
    both = env.err("hole", face=env.r(f), point=[0, 0, 0], sketch_points=[env.r(sk.add_point())], depth=1)
    assert "either face and point, or sketch_points" in both.message
    b = env.body("Cyl")
    from model_fakes import cyl_geo
    cf = b.add_face(cyl_geo())
    e = env.err("hole", face=env.r(cf), point=[0, 0, 0], depth=1)
    assert e.code == INVALID_PARAMS and "planar" in e.message


# --- patterns --------------------------------------------------------------------------------


def test_rect_pattern_two_directions(env):
    b = body_with(env)
    out = env.call("rect_pattern", entities=[env.r(b)], direction_one="X", quantity_one=3, distance_one=10,
                   direction_two="Y", quantity_two=2, distance_two=20, distance_type="extent", symmetric=True)
    inp = last_input(env, "rectangularPatternFeatures")
    assert inp.args == ([b], env.comp.xConstructionAxis, "3", "10 mm", PD.ExtentPatternDistanceType)
    assert inp.calls("setDirectionTwo") == [(env.comp.yConstructionAxis, "2", "20 mm")]
    assert inp.sets("isSymmetricInDirectionOne") == [True] and inp.sets("isSymmetricInDirectionTwo") == [True]
    assert "pattern_count" not in out


def test_rect_pattern_one_direction_and_pattern_count(env):
    b = body_with(env)
    env.coll("rectangularPatternFeatures").next_bodies = []
    out = env.call("rect_pattern", entities=[env.r(b)], direction_one="X", quantity_one=4, distance_one=5)
    inp = last_input(env, "rectangularPatternFeatures")
    assert inp.args[4] == PD.SpacingPatternDistanceType and inp.calls("setDirectionTwo") == []
    assert inp.sets("isSymmetricInDirectionTwo") == [] and "pattern_count" not in out


def test_pattern_count_reported_when_available(env):
    b = body_with(env)
    coll = env.coll("circularPatternFeatures")
    orig = coll.add

    def add(inp):
        feat = orig(inp)
        feat.patternElements = core.Collection([1, 2, 3])
        return feat
    coll.add = add
    out = env.call("circ_pattern", entities=[env.r(b)], axis="Z", quantity=3)
    assert out["pattern_count"] == 3


@pytest.mark.parametrize("q1,q2,ok", [(501, 1, False), (30, 20, False), (25, 20, True), (500, 1, True), (2, 250, True)])
def test_rect_pattern_cap(env, q1, q2, ok):
    b = body_with(env)
    kw = dict(entities=[env.r(b)], direction_one="X", quantity_one=q1, distance_one=1,
              direction_two="Y", quantity_two=q2, distance_two=1)
    if ok:
        env.call("rect_pattern", **kw)
    else:
        e = env.err("rect_pattern", **kw)
        assert e.code == INVALID_PARAMS and e.message == "pattern is capped at 500 instances; split it"


@pytest.mark.parametrize("q,ok", [(501, False), (500, True)])
def test_circ_pattern_cap(env, q, ok):
    b = body_with(env)
    kw = dict(entities=[env.r(b)], axis="Z", quantity=q)
    if ok:
        env.call("circ_pattern", **kw)
    else:
        e = env.err("circ_pattern", **kw)
        assert e.code == INVALID_PARAMS and e.message == "pattern is capped at 500 instances; split it"


def test_circ_pattern_options(env):
    b = body_with(env)
    env.call("circ_pattern", entities=[env.r(b)], axis="Z", quantity=6, total_angle=180, symmetric=True)
    inp = last_input(env, "circularPatternFeatures")
    assert inp.args == ([b], env.comp.zConstructionAxis)
    assert (inp.sets("quantity"), inp.sets("totalAngle"), inp.sets("isSymmetric")) == (["6"], ["180 deg"], [True])


@pytest.mark.parametrize("kw,msg", [
    ({"quantity_one": 1}, "quantity_one must be an integer >= 2"),
    ({"quantity_one": 2.5}, "quantity_one must be an integer >= 2"),
    ({"quantity_one": 3, "direction_two": "Y"}, "distance_two is required"),
    ({"quantity_one": 3, "quantity_two": 0}, "quantity_two must be an integer >= 1"),
])
def test_rect_pattern_errors(env, kw, msg):
    b = body_with(env)
    e = env.err("rect_pattern", entities=[env.r(b)], direction_one="X", distance_one=1, **kw)
    assert e.code == INVALID_PARAMS and msg in e.message


def test_pattern_entity_groups(env_direct):
    env = env_direct
    b = body_with(env, faces=1)
    f = env.feature()
    env.call("circ_pattern", entities=[env.r(b.faces.item(0))], axis="Z", quantity=3)  # faces allowed
    assert env.err("circ_pattern", entities=[env.r(f)], axis="Z", quantity=3).code == WRONG_DESIGN_TYPE
    assert env.err("circ_pattern", entities=[env.r(b), env.r(b.faces.item(0))], axis="Z", quantity=3).code == INVALID_PARAMS


# --- draft, split, offset ----------------------------------------------------------------------


def test_draft_single_and_two_angles(env):
    b = body_with(env, faces=1)
    f = b.faces.item(0)
    env.call("draft", faces=[env.r(f)], pull_plane="XY", angle=3, flip=True, tangent_chain=False)
    inp = last_input(env, "draftFeatures")
    assert inp.args == ([f], env.comp.xYConstructionPlane, False)
    assert inp.calls("setSingleAngle") == [(False, "3 deg")] and inp.sets("isDirectionFlipped") == [True]
    env.call("draft", faces=[env.r(f)], pull_plane="XY", angle=3, angle_two=4)
    assert last_input(env, "draftFeatures").calls("setTwoAngles") == [("3 deg", "4 deg")]


def test_split_body_and_split_face(env):
    b = body_with(env, faces=1)
    plane = env.comp.add_construction_plane("P")
    env.call("split_body", bodies=[env.r(b)], tool=env.r(plane), extend_tool=False)
    assert last_input(env, "splitBodyFeatures").args == ([b], plane, False)
    f = b.faces.item(0)
    env.call("split_face", faces=[env.r(f)], tool=env.r(plane))
    assert last_input(env, "splitFaceFeatures").args == ([f], plane, True)
    line = env.comp.add_sketch("S").add_line()
    env.call("split_body", bodies=[env.r(b)], tool=env.r(line))


def test_split_tool_kind_checked(env):
    b = body_with(env, faces=1)
    pt = env.comp.add_construction_point("Pt")
    assert "tool accepts" in env.err("split_body", bodies=[env.r(b)], tool=env.r(pt)).message
    assert "tool accepts" in env.err("split_face", faces=[env.r(b.faces.item(0))], tool=env.r(pt)).message


def test_offset_faces(env):
    b = body_with(env, faces=1)
    f = b.faces.item(0)
    env.call("offset_faces", faces=[env.r(f)], distance=-2)
    assert last_input(env, "offsetFacesFeatures").args == ([f], "-2 mm")
    assert env.err("offset_faces", faces=[env.r(f)], distance=0).code == INVALID_PARAMS
    env.coll("offsetFacesFeatures").hide("createInput")
    assert env.err("offset_faces", faces=[env.r(f)], distance=1).code == "UNSUPPORTED"


# --- scale, move, combine, rename -----------------------------------------------------------------


def test_scale_uniform_default_base_point(env):
    b = body_with(env)
    env.call("scale", entities=[env.r(b)], factor=2)
    inp = last_input(env, "scaleFeatures")
    assert inp.args == ([b], env.comp.originConstructionPoint, "2") and inp.calls("setToNonUniform") == []


def test_scale_non_uniform_with_base_point(env):
    b = body_with(env)
    v = b.add_vertex()
    env.call("scale", entities=[env.r(b)], factors=[1, 2, 3], base_point=env.r(v))
    inp = last_input(env, "scaleFeatures")
    assert inp.args[1] is v and inp.calls("setToNonUniform") == [("1", "2", "3")]


def test_scale_sketch_and_errors(env):
    sk = env.comp.add_sketch("S")
    b = body_with(env, faces=1)
    env.call("scale", entities=[env.r(sk)], factor=0.5)
    assert "factors applies only to bodies" in env.err("scale", entities=[env.r(sk)], factors=[1, 1, 1]).message
    assert "exactly 3" in env.err("scale", entities=[env.r(b)], factors=[1, 1]).message
    assert env.err("scale", entities=[env.r(b)], factor=0).message == "factor must be > 0"
    assert env.err("scale", entities=[env.r(b)], factors=[1, -1, 1]).message == "factors must be > 0"
    assert "entities accepts" in env.err("scale", entities=[env.r(b.faces.item(0))]).message
    assert "base_point accepts" in env.err("scale", entities=[env.r(b)], base_point=env.r(b)).message


def matrix_ops(inp):
    return inp.calls("defineAsFreeMove")[0][0].ops


def test_move_translate(env):
    b = body_with(env)
    env.call("move", bodies=[env.r(b)], translate=[10, 0, 5])
    inp = last_input(env, "moveFeatures")
    assert inp.args == ([b],)
    (op,) = matrix_ops(inp)
    assert op[0] == "transformBy" and op[1].translation.x == pytest.approx(1.0) and op[1].translation.z == pytest.approx(0.5)


def test_move_rotate_then_translate_uses_axis_geometry(env):
    import math

    from model_fakes import Geo
    b = body_with(env)
    ax = env.comp.add_construction_axis("A")
    ax.geometry = Geo(origin=core.Point3D.create(1, 2, 3), direction=core.Vector3D.create(0, 0, 1))
    env.call("move", bodies=[env.r(b)], rotate_axis=env.r(ax), angle=90, translate=[10, 0, 0])
    rot, tr = matrix_ops(last_input(env, "moveFeatures"))
    assert rot[0] == "setToRotation" and rot[1] == pytest.approx(math.pi / 2)
    assert (rot[3].x, rot[3].y, rot[3].z) == (1, 2, 3) and tr[0] == "transformBy"


def test_move_rotate_about_edge_and_sketch_line(env):
    from model_fakes import Geo
    b = body_with(env, edges=1)
    e = b.edges.item(0)
    e.geometry = core.Line3D()
    e.geometry.startPoint, e.geometry.endPoint = core.Point3D.create(0, 0, 0), core.Point3D.create(0, 0, 4)
    env.call("move", bodies=[env.r(b)], rotate_axis=env.r(e), angle=10)
    (rot,) = matrix_ops(last_input(env, "moveFeatures"))
    assert (rot[2].x, rot[2].y, rot[2].z) == (0, 0, 1)
    ln = env.comp.add_sketch("S").add_line()
    ln.worldGeometry = Geo(startPoint=core.Point3D.create(1, 1, 0), endPoint=core.Point3D.create(1, 3, 0))
    env.call("move", bodies=[env.r(b)], rotate_axis=env.r(ln), angle=10)
    (rot,) = matrix_ops(last_input(env, "moveFeatures"))
    assert (rot[2].x, rot[2].y, rot[2].z) == (0, 1, 0) and rot[3].y == 1


def test_move_create_input_fallback(env):
    b = body_with(env)
    env.coll("moveFeatures").hide("createInput2")
    env.call("move", bodies=[env.r(b)], translate=[1, 1, 1])
    inp = last_input(env, "moveFeatures")
    assert inp.args[0] == [b] and inp.args[1].ops[0][0] == "transformBy"


@pytest.mark.parametrize("kw", [{}, {"angle": 10}, {"rotate_axis": "Z"}])
def test_move_requires_translate_or_axis_with_angle(env, kw):
    b = body_with(env)
    assert env.err("move", bodies=[env.r(b)], **kw).code == INVALID_PARAMS


def test_combine(env):
    a, t1, t2 = body_with(env, name="A"), body_with(env, name="T1"), body_with(env, name="T2")
    env.call("combine", target=env.r(a), tools=[env.r(t1), env.r(t2)], operation="cut", keep_tools=True)
    inp = last_input(env, "combineFeatures")
    assert inp.args == (a, [t1, t2])
    assert (inp.sets("operation"), inp.sets("isKeepToolBodies"), inp.sets("isNewComponent")) == (
        [FO.CutFeatureOperation], [True], [False])
    env.call("combine", target=env.r(a), tools=[env.r(t1)])
    assert last_input(env, "combineFeatures").sets("operation") == [FO.JoinFeatureOperation]
    assert env.err("combine", target=env.r(a), tools=[env.r(t1), env.r(a)]).code == INVALID_PARAMS


class _Nameless(core.Base):
    def __init__(self):
        self.entityToken = "nameless-1"


def _locked(message):
    class Locked(core.Entity):
        def __init__(self):
            self.__dict__["entityToken"] = "locked-%s" % message
            self.__dict__["attributes"] = core.Attributes()

        name = property(lambda s: "x", lambda s, v: (_ for _ in ()).throw(RuntimeError(message)))

    return Locked()


def test_rename_returns_previous_and_stored_name(env):
    b = body_with(env, name="Old")
    out = env.call("rename", target=env.r(b), name="New")
    assert out["previous"] == "Old" and out["name"] == "New" and b.name == "New"
    assert out["target"]["token"] == b.entityToken


def test_rename_errors(env):
    b = body_with(env, name="Old")
    assert env.err("rename", target=env.r(b), name="  ").message == "name must not be empty"
    occ = env.comp.add_occurrence("O")
    e = env.err("rename", target=env.r(occ), name="X")
    assert e.code == INVALID_PARAMS and e.hints == ["rename its component instead"]
    nameless = env.design._register(_Nameless())
    assert "has no name" in env.err("rename", target=env.r(nameless), name="X").message
    ro = env.design._register(_locked("Name is read-only"))
    e = env.err("rename", target=env.r(ro), name="X")
    assert e.code == INVALID_PARAMS and e.message == "Locked name is read-only"
    br = env.design._register(_locked("kaboom"))
    assert env.err("rename", target=env.r(br), name="X").code == FUSION_ERROR


# --- threads ---------------------------------------------------------------------------------


from model_fakes import Geo  # noqa: E402

from defusion.errors import UNSUPPORTED  # noqa: E402


def cyl_face(env, internal=True, radius=0.25, comp=None):
    b = env.body("Cyl", comp=comp)
    f = b.add_face(Geo(surfaceType=core.SurfaceTypes.CylinderSurfaceType, origin=core.Point3D.create(0, 0, 0),
                       axis=core.Vector3D.create(0, 0, 1), radius=radius))
    f.pointOnFace = core.Point3D.create(radius, 0, 0)
    normal = core.Vector3D.create(-1 if internal else 1, 0, 0)
    f.evaluator = Geo(getNormalAtPoint=lambda p: (True, normal))
    return f


def tquery(env):
    return env.coll("threadFeatures").threadDataQuery


def test_thread_query_levels(env):
    q = tquery(env)
    assert env.call("thread_query") == {"level": "types", "items": q.allThreadTypes, "total": 2, "offset": 0,
                                        "next_offset": None}
    assert env.call("thread_query", thread_type="ISO Metric profile")["level"] == "sizes"
    r = env.call("thread_query", thread_type="ISO Metric profile", size="5")
    assert (r["level"], r["items"]) == ("designations", ["M5x0.8"])
    r = env.call("thread_query", thread_type="ISO Metric profile", size="5", designation="M5x0.8", internal=True)
    assert (r["level"], r["items"]) == ("classes", ["6H", "6g"])
    assert q.calls[-1] == ("allClasses", True, "ISO Metric profile", "M5x0.8")


def test_thread_query_pages_and_unknown_type_hints(env):
    r = env.call("thread_query", offset=1, limit=1)
    assert r["items"] == ["ISO Metric profile"] and r["next_offset"] is None
    tquery(env).allThreadTypes = ["T%d" % i for i in range(30)]
    e = env.err("thread_query", thread_type="Nope")
    assert e.code == INVALID_PARAMS and e.hints == ["T%d" % i for i in range(20)]


def test_list_threads_reads_every_component(env):
    sub = env.comp.add_occurrence("O").component
    info = Geo(threadType="ISO Metric profile", threadDesignation="M5x0.8", threadClass="6H", isInternal=True,
               isRightHanded=True)
    for comp, n in ((env.comp, "T1"), (sub, "T2")):
        f = comp.features.threadFeatures.add(None)
        f.name = n
        f.threadInfo, f.isModeled, f.isFullLength = info, False, True
        f.faces = core.Collection([1, 2])
    r = env.call("list_threads")
    assert r["total"] == 2 and [i["feature"]["name"] for i in r["items"]] == ["T1", "T2"]
    assert r["items"][0] | {"feature": None} == {
        "feature": None, "thread_type": "ISO Metric profile", "designation": "M5x0.8", "thread_class": "6H",
        "internal": True, "modeled": False, "full_length": True, "right_handed": True, "face_count": 2}
    assert env.call("list_threads", offset=1, limit=1)["items"][0]["feature"]["name"] == "T2"


def test_thread_defaults_from_recommendation(env):
    f = cyl_face(env, internal=True)
    out = env.call("thread", faces=[env.r(f)])
    q = tquery(env)
    assert q.calls == [("recommendThreadData", 0.5, True, "ISO Metric profile")]
    tf = env.coll("threadFeatures")
    info = tf.created[0]
    assert info.args == (True, "ISO Metric profile", "M5x0.8", "6H")
    assert info.sets("isRightHanded") == [True]
    inp = tf.created[1]
    assert inp.args[0] == [f] and inp.sets("isModeled") == [False] and inp.sets("isFullLength") == [True]
    assert inp.sets("threadLength") == []
    assert (out["designation"], out["thread_class"], out["internal"]) == ("M5x0.8", "6H", True)


def test_thread_external_detected_from_normal(env):
    f = cyl_face(env, internal=False)
    assert env.call("thread", faces=[env.r(f)])["internal"] is False
    assert tquery(env).calls[0][2] is False


def test_thread_explicit_values_and_partial_length(env):
    f = cyl_face(env)
    out = env.call("thread", faces=[env.r(f)], thread_type="X", designation="M3", thread_class="6g", internal=False,
                   modeled=True, full_length=False, length=8, offset=1, name="Th")
    assert tquery(env).calls == []
    assert env.coll("threadFeatures").created[0].args == (False, "X", "M3", "6g")
    inp = env.coll("threadFeatures").created[1]
    assert inp.sets("isModeled") == [True] and inp.sets("threadLength") == ["8 mm"] and inp.sets("threadOffset") == ["1 mm"]
    assert out["feature"]["name"] == "Th"


def test_thread_class_falls_back_to_first_class_for_given_designation(env):
    f = cyl_face(env)
    out = env.call("thread", faces=[env.r(f)], designation="M5x0.8")
    assert out["thread_class"] == "6H" and tquery(env).calls == [("allClasses", True, "ISO Metric profile", "M5x0.8")]


def test_thread_errors(env):
    f = cyl_face(env)
    plane = planar_face(env)
    assert env.err("thread", faces=[env.r(plane)]).message == "faces must be cylindrical"
    assert "length is required" in env.err("thread", faces=[env.r(f)], full_length=False).message
    tquery(env).recommend = (False, "", "")
    e = env.err("thread", faces=[env.r(f)])
    assert e.code == INVALID_PARAMS and e.message == "no standard thread fits diameter 5.0"


def test_thread_left_hand_unsupported_without_member(env):
    f = cyl_face(env)
    env.coll("threadFeatures").hide_input("isRightHanded")
    assert env.err("thread", faces=[env.r(f)], right_handed=False).code == UNSUPPORTED
    env.call("thread", faces=[env.r(f)])  # right-handed needs no member
