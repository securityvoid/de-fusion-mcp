"""Construction geometry and UCS (design § 9): per-method call sequences, missing inputs and kind checks."""
import pytest
from model_fakes import Geo, cyl_geo, env, env_direct, plane_geo  # noqa: F401

from adsk import core, fusion

from defusion.errors import INVALID_PARAMS, UNSUPPORTED, WRONG_DESIGN_TYPE


def sketch_points(env, n, comp=None):
    sk = (comp or env.comp).add_sketch("P")
    return [sk.add_point() for _ in range(n)]


def round_face(env, geo=None):
    b = env.body("B")
    return b.add_face(geo or cyl_geo())


# --- construction_plane ----------------------------------------------------------------------


def test_plane_offset(env):
    out = env.call("construction_plane", method="offset", plane="XZ", offset=7, name="Off")
    inp = env.last("constructionPlanes")
    assert inp.calls("setByOffset") == [(env.comp.xZConstructionPlane, "7 mm")]
    assert out["plane"]["name"] == "Off"
    env.call("construction_plane", method="offset", plane="XY")
    assert env.last("constructionPlanes").calls("setByOffset") == [(env.comp.xYConstructionPlane, "0 mm")]


def test_plane_angle(env):
    env.call("construction_plane", method="angle", axis="Z", angle=30, plane="XY")
    assert env.last("constructionPlanes").calls("setByAngle") == [
        (env.comp.zConstructionAxis, "30 deg", env.comp.xYConstructionPlane)]


def test_plane_midplane(env):
    env.call("construction_plane", method="midplane", plane="XY", plane_two="XZ")
    assert env.last("constructionPlanes").calls("setByTwoPlanes") == [
        (env.comp.xYConstructionPlane, env.comp.xZConstructionPlane)]


def test_plane_three_points(env):
    pts = sketch_points(env, 3)
    env.call("construction_plane", method="three_points", points=[env.r(p) for p in pts])
    assert env.last("constructionPlanes").calls("setByThreePoints") == [tuple(pts)]
    v = env.body("B").add_vertex()
    env.call("construction_plane", method="three_points",
             points=[env.r(v), env.r(pts[0]), env.r(env.comp.originConstructionPoint)])


@pytest.mark.parametrize("n", [2, 4])
def test_plane_three_points_needs_three(env, n):
    e = env.err("construction_plane", method="three_points", points=[env.r(p) for p in sketch_points(env, n)])
    assert e.code == INVALID_PARAMS and e.message == "points needs exactly 3 entries"


def test_plane_points_kind_checked(env):
    b = env.body("B", edges=3)
    e = env.err("construction_plane", method="three_points", points=[env.r(x) for x in b.edges])
    assert e.code == INVALID_PARAMS and "points accepts" in e.message
    assert not env.coll("constructionPlanes").created


def test_plane_tangent(env):
    f = round_face(env)
    env.call("construction_plane", method="tangent", face=env.r(f), angle=15, plane="XY")
    assert env.last("constructionPlanes").calls("setByTangent") == [(f, "15 deg", env.comp.xYConstructionPlane)]
    cone = round_face(env, Geo(surfaceType=core.SurfaceTypes.ConeSurfaceType))
    env.call("construction_plane", method="tangent", face=env.r(cone), plane="XY")


def test_plane_tangent_rejects_planar_face(env):
    f = round_face(env, plane_geo())
    e = env.err("construction_plane", method="tangent", face=env.r(f), plane="XY")
    assert e.code == INVALID_PARAMS and e.message == "face must be cylindrical or conical"


def test_plane_along_path(env):
    ln = env.comp.add_sketch("S").add_line()
    env.call("construction_plane", method="along_path", path=env.r(ln))
    assert env.last("constructionPlanes").calls("setByDistanceOnPath") == [(ln, "0.5")]
    env.call("construction_plane", method="along_path", path=env.r(ln), position=1)
    assert env.last("constructionPlanes").calls("setByDistanceOnPath") == [(ln, "1")]


@pytest.mark.parametrize("pos", [-0.1, 1.1])
def test_plane_along_path_position_range(env, pos):
    ln = env.comp.add_sketch("S").add_line()
    e = env.err("construction_plane", method="along_path", path=env.r(ln), position=pos)
    assert e.code == INVALID_PARAMS and e.message == "position must be between 0 and 1"


def test_plane_along_path_kind(env):
    b = env.body("B", faces=1)
    e = env.err("construction_plane", method="along_path", path=env.r(b.faces.item(0)))
    assert e.code == INVALID_PARAMS and "path accepts" in e.message


@pytest.mark.parametrize("method,kw,missing", [
    ("offset", {}, "plane"),
    ("angle", {"plane": "XY"}, "axis"),
    ("angle", {}, "axis, plane"),
    ("midplane", {"plane": "XY"}, "plane_two"),
    ("three_points", {}, "points"),
    ("tangent", {"plane": "XY"}, "face"),
    ("along_path", {}, "path"),
])
def test_plane_missing_inputs_are_named(env, method, kw, missing):
    e = env.err("construction_plane", method=method, **kw)
    assert e.code == INVALID_PARAMS and e.message == "method %s needs %s" % (method, missing)
    assert not env.coll("constructionPlanes").created


def test_plane_in_component_maps_origin_plane(env):
    sub = env.comp.add_occurrence("O").component
    env.call("construction_plane", method="offset", plane=env.r(env.comp.yZConstructionPlane), offset=1,
             component=env.r(sub))
    assert sub.constructionPlanes.created[-1].calls("setByOffset") == [(sub.yZConstructionPlane, "1 mm")]
    env.call("construction_plane", method="midplane", plane=env.r(env.comp.yZConstructionPlane),
             plane_two=env.r(env.comp.xZConstructionPlane), component=env.r(sub))
    assert sub.constructionPlanes.created[-1].calls("setByTwoPlanes") == [
        (sub.yZConstructionPlane, sub.xZConstructionPlane)]
    assert not env.coll("constructionPlanes").created


def test_construction_requires_parametric(env_direct):
    e = env_direct.err("construction_plane", method="offset", plane="XY")
    assert e.code == WRONG_DESIGN_TYPE
    e = env_direct.err("construction_axis", method="two_planes", plane="XY", plane_two="XZ")
    assert e.code == WRONG_DESIGN_TYPE


# --- construction_axis -----------------------------------------------------------------------


def test_axis_two_planes(env):
    out = env.call("construction_axis", method="two_planes", plane="XY", plane_two="XZ", name="Ax")
    assert env.last("constructionAxes").calls("setByTwoPlanes") == [
        (env.comp.xYConstructionPlane, env.comp.xZConstructionPlane)]
    assert out["axis"]["name"] == "Ax"


def test_axis_two_points(env):
    pts = sketch_points(env, 2)
    env.call("construction_axis", method="two_points", points=[env.r(p) for p in pts])
    assert env.last("constructionAxes").calls("setByTwoPoints") == [tuple(pts)]
    e = env.err("construction_axis", method="two_points", points=[env.r(p) for p in sketch_points(env, 3)])
    assert e.message == "points needs exactly 2 entries"


def test_axis_edge_must_be_linear(env):
    b = env.body("B")
    straight, curved = b.add_edge(core.Line3D()), b.add_edge(cyl_geo())
    env.call("construction_axis", method="edge", edge=env.r(straight))
    assert env.last("constructionAxes").calls("setByEdge") == [(straight,)]
    e = env.err("construction_axis", method="edge", edge=env.r(curved))
    assert e.code == INVALID_PARAMS and e.message == "edge must be linear"


def test_axis_circular_face(env):
    f = round_face(env)
    env.call("construction_axis", method="circular_face", face=env.r(f))
    assert env.last("constructionAxes").calls("setByCircularFace") == [(f,)]
    e = env.err("construction_axis", method="circular_face", face=env.r(round_face(env, plane_geo())))
    assert e.message == "face must be cylindrical or conical"


def test_axis_normal_to_face(env):
    f = env.body("B", faces=1).faces.item(0)
    p = sketch_points(env, 1)[0]
    env.call("construction_axis", method="normal_to_face", face=env.r(f), point=env.r(p))
    assert env.last("constructionAxes").calls("setByNormalToFaceAtPoint") == [(f, p)]
    other = env.body("O", edges=1).edges.item(0)
    e = env.err("construction_axis", method="normal_to_face", face=env.r(f), point=env.r(other))
    assert e.code == INVALID_PARAMS and "point accepts" in e.message


@pytest.mark.parametrize("method,kw,missing", [
    ("two_planes", {"plane": "XY"}, "plane_two"),
    ("two_points", {}, "points"),
    ("edge", {}, "edge"),
    ("circular_face", {}, "face"),
    ("normal_to_face", {}, "face, point"),
])
def test_axis_missing_inputs_are_named(env, method, kw, missing):
    e = env.err("construction_axis", method=method, **kw)
    assert e.code == INVALID_PARAMS and e.message == "method %s needs %s" % (method, missing)
    assert not env.coll("constructionAxes").created


# --- ucs -------------------------------------------------------------------------------------


def with_ucs(env, comp=None, hide=()):
    from model_fakes import FeatColl, S

    comp = comp or env.comp
    c = FeatColl(comp, "userCoordinateSystems",
                 {"createInput": S((), angleX=None, angleY=None, angleZ=None)})
    c.hide_input(*hide)
    comp.userCoordinateSystems = c
    return c


def test_ucs_unsupported_without_api(env):
    e = env.err("ucs", origin=env.r(env.comp.originConstructionPoint))
    assert e.code == UNSUPPORTED and "Component.userCoordinateSystems is not available" in e.message


def test_ucs_sets_only_non_zero_angles(env):
    c = with_ucs(env)
    p = env.comp.originConstructionPoint
    out = env.call("ucs", origin=env.r(p), angle_x=10, angle_z="5 deg", name="U")
    inp = c.created[-1]
    assert inp.args == (("UCSGeometry", p),)
    assert inp.sets("angleX") == ["10 deg"] and inp.sets("angleZ") == ["5 deg"] and inp.sets("angleY") == []
    assert out["ucs"]["name"] == "U" and c.added == [inp]


def test_ucs_missing_angle_member_is_unsupported(env):
    c = with_ucs(env, hide=("angleY",))
    e = env.err("ucs", origin=env.r(env.comp.originConstructionPoint), angle_y=3)
    assert e.code == UNSUPPORTED and "UserCoordinateSystemInput.angleY" in e.message
    assert not c.added
    env.call("ucs", origin=env.r(env.comp.originConstructionPoint))  # zero angle never touches angleY


def test_ucs_origin_kind_and_works_in_direct_design(env, env_direct):
    with_ucs(env)
    b = env.body("B", edges=1)
    e = env.err("ucs", origin=env.r(b.edges.item(0)))
    assert e.code == INVALID_PARAMS and "origin accepts" in e.message
    with_ucs(env_direct)
    env_direct.call("ucs", origin=env_direct.r(env_direct.comp.originConstructionPoint))
