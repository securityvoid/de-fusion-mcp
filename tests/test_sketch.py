"""sketch tool tests (fake adsk + tests/fakes/sketch.py)."""
import importlib
import math
import pathlib
import sys
import types as pytypes

import pytest
from adsk import core, fusion

from defusion import actions
from defusion.context import Ctx
from defusion.errors import ActionError
from defusion.units import OutputUnits

import sketch as fk  # tests/fakes/sketch.py (is on the pythonpath)

_TOOLS_DIR = pathlib.Path(__file__).resolve().parent.parent / "De-Fusion" / "defusion" / "tools"


@pytest.fixture(scope="module")
def sk_mod():
    """defusion.tools.sketch imported without the sibling tool modules, in an isolated registry."""
    pkg = pytypes.ModuleType("defusion.tools")
    pkg.__path__ = [str(_TOOLS_DIR)]
    names = ("defusion.tools", "defusion.tools.sketch")
    saved = {k: sys.modules.get(k) for k in names}
    sys.modules["defusion.tools"] = pkg
    sys.modules.pop("defusion.tools.sketch", None)
    with actions.isolated_registry() as reg:
        mod = importlib.import_module("defusion.tools.sketch")
        mod.REGISTRY = reg
        yield mod
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


@pytest.fixture
def S(sk_mod):
    return sk_mod


def make_ctx(design, length="mm", angle="deg"):
    return Ctx(None, None, None, design, set(), [], OutputUnits(length, angle))


@pytest.fixture
def ctx(fake_design):
    return make_ctx(fake_design)


@pytest.fixture
def sk(fake_design):
    return fk.new_sketch(fake_design.rootComponent)


def P(x, y):
    return core.Point3D.create(x, y, 0)


def err(code, fn, *a, **kw):
    with pytest.raises(ActionError) as e:
        fn(*a, **kw)
    assert e.value.code == code, e.value.message
    return e.value


# ---- TASK-SK-001: helpers ---------------------------------------------------------------------


def test_helpers_xy_converts_to_output_units(S, fake_design):
    c = make_ctx(fake_design, length="in")
    assert S.xy(c, P(2.54, 5.08)) == pytest.approx([1.0, 2.0])
    assert S.xy(make_ctx(fake_design), P(0.5, 1)) == pytest.approx([5.0, 10.0])


def test_helpers_kind_and_type_names(S, sk):
    line = fk.SketchLine(sk, sk._new_point(0, 0), sk._new_point(1, 0))
    assert S.kind(line) == "SketchLine"
    assert S.type_name(line) == "line"
    assert S.type_name(fk.SketchConicCurve(sk)) == "conic"
    assert S.type_name(fk.SketchPoint(sk)) == "SketchPoint"
    assert S.type_name(fk.SketchFixedSpline(sk)) == "fixed_spline"


def test_helpers_check_owned_rejects_other_sketch(S, ctx, fake_design, sk):
    other = fk.new_sketch(fake_design.rootComponent, "Other")
    mine = fk.SketchLine(sk, sk._new_point(0, 0), sk._new_point(1, 0))
    foreign = fk.SketchLine(other, other._new_point(0, 0), other._new_point(1, 0))
    S.check_owned(ctx, sk, [mine], "curves")
    S.check_owned(ctx, sk, mine, "curve")
    e = err("INVALID_PARAMS", S.check_owned, ctx, sk, [mine, foreign], "curves")
    assert "curves" in e.message and foreign.entityToken in e.message and "Sketch1" in e.message
    point = other._new_point(1, 1)
    err("INVALID_PARAMS", S.check_owned, ctx, sk, [point], "points")


def test_helpers_fully_constrained(S, sk):
    assert S.fully_constrained(sk) is False
    sk.isFullyConstrained = True
    assert S.fully_constrained(sk) is True
    a = fk.SketchLine(sk, sk._new_point(0, 0), sk._new_point(1, 0))
    b = fk.SketchLine(sk, sk._new_point(0, 0), sk._new_point(1, 0))
    sk._curves.extend([a, b])
    del sk.isFullyConstrained  # older build: fall back to the curves
    a.isFullyConstrained = True
    b.isReference = True
    assert S.fully_constrained(sk) is True
    a.isFullyConstrained = False
    assert S.fully_constrained(sk) is False


def test_helpers_curve_summary_and_draw_result(S, ctx, sk):
    ln = sk.sketchCurves.sketchLines.addByTwoPoints(P(0, 0), P(1, 0))
    c = sk.sketchCurves.sketchCircles.addByCenterRadius(P(0, 0), 1)
    s = S.curve_summary(ctx, ln)
    assert s["type"] == "line" and s["ref"]["token"] == ln.entityToken
    assert set(s["points"]) == {"start", "end"}
    assert set(S.curve_summary(ctx, c)["points"]) == {"center"}
    r = S.draw_result(ctx, sk, [ln, c])
    assert r["sketch"]["token"] == sk.entityToken and len(r["curves"]) == 2
    assert r["profiles"] == sk.profiles.count == 1


def test_helpers_live_scenarios_declared(S):
    assert isinstance(S.LIVE_SCENARIOS, list)


def test_live_scenarios_cover_actions(S):
    import inspect

    names = {a.name for a in S.REGISTRY.for_tool("sketch")}
    assert len(names) == 28
    assert sorted(n for n, _, _ in S.LIVE_SCENARIOS) == sorted(names)
    assert all(tag == "sketch." + n for n, tag, _ in S.LIVE_SCENARIOS)
    assert all(inspect.iscoroutinefunction(fn) for _, _, fn in S.LIVE_SCENARIOS)


# ---- TASK-SK-002: list, info, describe -----------------------------------------------------------


def rect(sk, w=2.0, h=1.0):
    return list(sk.sketchCurves.sketchLines.addTwoPointRectangle(P(0, 0), P(w, h)))


def test_list_root_and_child_component(S, ctx, fake_design):
    root = fake_design.rootComponent
    sub = root.add_occurrence("Occ", "Child").component
    a, b = fk.new_sketch(root, "A"), fk.new_sketch(root, "B")
    c = fk.new_sketch(sub, "C", plane=root.xYConstructionPlane)
    rect(a)
    r = S.list_sketches(ctx)
    assert r["total"] == 3 and [i["ref"]["name"] for i in r["items"]] == ["A", "B", "C"]
    item = r["items"][0]
    assert set(item) == {"ref", "component", "plane", "profiles", "curves", "fully_constrained",
                         "visible"}
    assert (item["component"], item["plane"], item["profiles"], item["curves"]) == ("Root", None, 1, 4)
    assert r["items"][2]["plane"]["name"] == "XY" and r["items"][2]["component"] == "Child"
    only = S.list_sketches(ctx, component=sub)
    assert only["total"] == 1 and only["items"][0]["ref"]["token"] == c.entityToken
    page = S.list_sketches(ctx, offset=1, limit=1)
    assert [i["ref"]["name"] for i in page["items"]] == ["B"] and page["next_offset"] == 2


def test_info_rectangle_acceptance(S, ctx, sk):
    lines = rect(sk, 2.0, 1.0)  # 20 x 10 mm
    gc = sk.geometricConstraints
    gc.addHorizontal(lines[0])
    gc.addVertical(lines[1])
    d = S.info(ctx, sk)
    assert [c["type"] for c in d["curves"]] == ["line"] * 4
    assert d["profiles"] == [{"index": 0, "area": pytest.approx(200.0), "loops": 1}]
    assert [c["type"] for c in d["constraints"]] == ["horizontal", "vertical"]
    assert d["sketch"]["origin"] == [0.0, 0.0, 0.0] and d["units"] == {"length": "mm", "angle": "deg"}
    assert d["points"][0]["origin"] is True and len(d["points"]) == 5
    assert d["curves"][0]["start"] == 1 and d["curves"][0]["end"] == 2
    assert d["curves"][0]["construction"] is False
    p = S.info(ctx, sk, section="curves", limit=2)
    assert set(p) == {"sketch", "units", "curves"}
    assert {k: p["curves"][k] for k in ("total", "offset", "next_offset")} == {
        "total": 4, "offset": 0, "next_offset": 2}
    assert len(p["curves"]["items"]) == 2
    assert S.info(ctx, sk, section="curves", offset=2, limit=2)["curves"]["next_offset"] is None


def test_describe_frame_and_units(S, fake_design, sk_mod):
    sk = fk.new_sketch(fake_design.rootComponent, "Up", origin=(0, 0, 0.5))
    sk.referencePlane = fake_design.rootComponent.xYConstructionPlane
    d = S.describe(make_ctx(fake_design, length="in"), sk)
    assert d["sketch"]["origin"] == pytest.approx([0, 0, 0.5 / 2.54])
    assert d["sketch"]["x_direction"] == [1, 0, 0] and d["sketch"]["plane"]["name"] == "XY"
    assert d["sketch"]["is_parametric"] is True and d["units"]["length"] == "in"


def test_describe_curve_types(S, ctx, sk):
    c = sk.sketchCurves
    ln = c.sketchLines.addByTwoPoints(P(0, 0), P(1, 0))
    ci = c.sketchCircles.addByCenterRadius(P(5, 5), 0.5)
    ar = c.sketchArcs.addByCenterStartSweep(P(0, 0), P(1, 0), math.pi / 2)
    neg = c.sketchArcs.addByCenterStartSweep(P(0, 0), P(1, 0), -math.pi / 2)
    fs = c.sketchFittedSplines.add(core.ObjectCollection([P(0, 0), P(1, 1), P(2, 0)]))
    fs.isClosed = True
    cp = c.sketchControlPointSplines.add(core.ObjectCollection([P(0, 0), P(1, 1), P(2, 0), P(3, 1)]), 3)
    odd = fk.SketchConicCurve(sk)
    odd.centerSketchPoint = sk._new_point(9, 9)
    sk._curves.append(odd)
    d = S.describe(ctx, sk)["curves"]
    assert [x["type"] for x in d] == ["line", "circle", "arc", "arc", "fitted_spline",
                                      "control_point_spline", "conic"]
    assert d[0]["start"] == 1 and d[0]["end"] == 2
    assert d[1]["radius"] == pytest.approx(5.0) and d[1]["center"] == 3
    assert d[2]["sweep"] == pytest.approx(90.0) and d[3]["sweep"] == pytest.approx(90.0)
    assert d[2]["radius"] == pytest.approx(10.0) and {"center", "start", "end"} <= set(d[2])
    assert d[4]["closed"] is True and len(d[4]["fit_points"]) == 3
    assert d[5]["degree"] == 3 and d[5]["closed"] is False and len(d[5]["control_points"]) == 4
    assert d[6]["replayable"] is False and "center" in d[6]
    assert "replayable" not in d[0]


def test_describe_every_constraint_class(S, ctx, fake_design, sk):
    l1, l2 = rect(sk)[:2]
    c1 = sk.sketchCurves.sketchCircles.addByCenterRadius(P(5, 5), 1)
    c2 = sk.sketchCurves.sketchCircles.addByCenterRadius(P(5, 5), 2)
    p1, p2 = sk._new_point(3, 3), sk._new_point(4, 4)
    g = sk.geometricConstraints
    g.addCoincident(p1, l1)
    g.addHorizontal(l1)
    g.addHorizontalPoints(p1, p2)
    g.addVertical(l1)
    g.addVerticalPoints(p1, p2)
    g.addParallel(l1, l2)
    g.addPerpendicular(l1, l2)
    g.addCollinear(l1, l2)
    g.addTangent(l1, c1)
    g.addEqual(c1, c2)
    g.addConcentric(c1, c2)
    g.addMidPoint(p1, l1)
    g.addSymmetry(p1, p2, l2)
    g.addSmooth(l1, l2)
    g.addCurvature(l1, l2)
    sk._gcs.append(fk.UnknownConstraint(sk))
    other = fk.new_sketch(fake_design.rootComponent, "Other")
    g.addCoincident(other._new_point(0, 0), l1)
    d = S.describe(ctx, sk)["constraints"]
    got = [(x["type"], x["points"], x["curves"]) for x in d[:15]]
    i = lambda o: [c.entityToken for c in sk.sketchPoints].index(o.entityToken)  # noqa: E731
    j = lambda o: [c.entityToken for c in sk.sketchCurves].index(o.entityToken)  # noqa: E731
    assert got == [
        ("coincident", [i(p1)], [j(l1)]), ("horizontal", [], [j(l1)]),
        ("horizontal", [i(p1), i(p2)], []), ("vertical", [], [j(l1)]),
        ("vertical", [i(p1), i(p2)], []), ("parallel", [], [j(l1), j(l2)]),
        ("perpendicular", [], [j(l1), j(l2)]), ("collinear", [], [j(l1), j(l2)]),
        ("tangent", [], [j(l1), j(c1)]), ("equal", [], [j(c1), j(c2)]),
        ("concentric", [], [j(c1), j(c2)]), ("midpoint", [i(p1)], [j(l1)]),
        ("symmetry", [i(p1), i(p2)], []), ("smooth", [], [j(l1), j(l2)]),
        ("curvature", [], [j(l1), j(l2)]),
    ]
    assert d[12]["symmetry_line"] == j(l2) and [x["index"] for x in d] == list(range(17))
    assert d[15] == {"index": 15, "type": "UnknownConstraint", "points": [], "curves": [],
                     "replayable": False}
    assert d[16]["replayable"] is False and d[0].get("replayable") is None


def test_describe_every_dimension_class(S, ctx, sk):
    l1 = sk.sketchCurves.sketchLines.addByTwoPoints(P(0, 0), P(4, 0))
    l2 = sk.sketchCurves.sketchLines.addByTwoPoints(P(0, 0), P(0, 3))
    ci = sk.sketchCurves.sketchCircles.addByCenterRadius(P(9, 9), 0.7)
    sd = sk.sketchDimensions
    A = fk.DimensionOrientations.AlignedDimensionOrientation
    H = fk.DimensionOrientations.HorizontalDimensionOrientation
    V = fk.DimensionOrientations.VerticalDimensionOrientation
    a, b = l1.startSketchPoint, l1.endSketchPoint
    sd.addDistanceDimension(a, b, A, P(1, 1), True)
    sd.addDistanceDimension(a, b, H, P(1, 1), True)
    sd.addDistanceDimension(a, b, V, P(1, 1), True)
    sd.addRadialDimension(ci, P(8, 8), True)
    sd.addDiameterDimension(ci, P(8, 8), True)
    sd.addAngularDimension(l1, l2, P(1, 1), True)
    sd.addOffsetDimension(l1, l2, P(1, 1), True)
    sd.addRadialDimension(ci, P(8, 8), False)
    sk._dims.append(fk.UnknownDimension(sk, True, P(0, 0), 1.0, "cm"))
    d = S.describe(ctx, sk)["dimensions"]
    assert [x["type"] for x in d[:8]] == ["distance", "horizontal", "vertical", "radial", "diameter",
                                         "angular", "offset", "radial"]
    assert d[0]["value"] == pytest.approx(40.0) and d[0]["expression"] == "4.0 cm"
    assert d[0]["parameter"] == "d1" and d[0]["driving"] is True and d[0]["text_point"] == [10.0, 10.0]
    assert d[3]["value"] == pytest.approx(7.0) and d[4]["value"] == pytest.approx(14.0)
    assert d[5]["value"] == pytest.approx(90.0)  # angle_out: degrees
    assert d[0]["points"] != [] and d[3]["curves"] == [2] and d[5]["curves"] == [0, 1]
    assert d[6]["curves"] == [0, 1]
    assert (d[7]["parameter"], d[7]["expression"], d[7]["driving"]) == (None, None, False)
    assert d[7]["value"] == pytest.approx(7.0)  # driven: from dim.value
    sk._dims[7].__dict__.pop("value")
    assert S.describe(ctx, sk)["dimensions"][7]["value"] is None
    assert d[8] == {"index": 8, "type": "UnknownDimension", "points": [], "curves": [],
                    "replayable": False}


def test_describe_large_sketch_size_hint(S, ctx, sk):
    for i in range(300):
        sk.sketchCurves.sketchLines.addByTwoPoints(P(i, 0), P(i, 1))
    lines = list(sk.sketchCurves)
    for i in range(300):
        sk.geometricConstraints.addParallel(lines[i], lines[(i + 1) % 300])
    e = err("RESULT_TOO_LARGE", S.info, ctx, sk)
    assert any("section" in h for h in e.hints)
    page = S.info(ctx, sk, section="constraints", limit=100)
    assert page["constraints"]["total"] == 300 and len(page["constraints"]["items"]) == 100


def test_info_section_profiles_and_dimension_expression(S, ctx, sk):
    rect(sk)
    r = S.info(ctx, sk, section="profiles")
    assert r["profiles"]["items"][0]["area"] == pytest.approx(200.0)
    assert S.info(ctx, sk, section="texts")["texts"]["total"] == 0


# ---- helpers for action tests -------------------------------------------------------------------


def run(S, ctx, _action, **params):
    """Validate and resolve ``params`` like the call pipeline, then run the action function."""
    from defusion import types

    act = S.REGISTRY.get("sketch", _action)
    validated = act.model.model_validate(params)
    kwargs = types.resolve_params(ctx, act, validated)
    kwargs.pop("confirm", None)
    kwargs.pop("preserve_read_only", None)
    return act.fn(ctx, **kwargs)


# ---- TASK-SK-003: create, delete -------------------------------------------------------------------


def test_create_origin_plane_in_given_component_with_name(S, ctx, fake_design):
    root = fake_design.rootComponent
    child = root.add_occurrence("Occ", "Child").component
    r = run(S, ctx, "create", plane="XZ", component={"name": "Child"}, name="Base")
    plane, occ = child.sketch_adds[0]
    assert plane is child.xZConstructionPlane and occ is None
    sk = child.sketches[0]
    assert sk.name == "Base" and r["sketch"]["token"] == sk.entityToken
    assert r["plane"]["name"] == "XZ" and r["origin"] == [0.0, 0.0, 0.0]
    assert r["x_direction"] == [1, 0, 0] and r["y_direction"] == [0, 1, 0]
    assert not hasattr(root, "sketch_adds")


def test_create_defaults_to_active_component(S, ctx, fake_design):
    run(S, ctx, "create", plane="XY")
    assert fake_design.rootComponent.sketch_adds[0][0] is fake_design.rootComponent.xYConstructionPlane


def test_create_on_construction_plane(S, ctx, fake_design):
    cp = fake_design.rootComponent.add_construction_plane("Offset")
    r = run(S, ctx, "create", plane={"token": cp.entityToken})
    assert fake_design.rootComponent.sketch_adds[0][0] is cp and r["plane"]["name"] == "Offset"


def test_create_on_planar_face_and_cylindrical_face(S, ctx, fake_design):
    root = fake_design.rootComponent
    body = root.add_body("B")
    flat, round_ = fk.plane_face(body), fk.cylinder_face(body)
    r = run(S, ctx, "create", plane={"token": flat.entityToken})
    assert root.sketch_adds[0][0] is flat and r["plane"]["token"] == flat.entityToken
    # create_sketch itself guards a face whose surface is not a plane
    e = err("INVALID_PARAMS", S.create_sketch, ctx, round_, None, None)
    assert "plane" in e.message and "planar" in e.message
    assert len(root.sketch_adds) == 1


def test_create_face_in_occurrence_passes_it_when_component_is_root(S, ctx, fake_design):
    root = fake_design.rootComponent
    face = fk.plane_face(root.add_body("B"))
    face.assemblyContext = "OCC"
    S.create_sketch(ctx, face, None, None)
    assert root.sketch_adds[0] == (face, "OCC")
    child = root.add_occurrence("Occ", "Child").component
    S.create_sketch(ctx, face, child, None)
    assert child.sketch_adds[0] == (face, None)


def test_delete_returns_prior_ref_and_fails_on_false(S, ctx, sk):
    ref = ctx.ref(sk)
    r = run(S, ctx, "delete", sketch={"token": sk.entityToken}, confirm=True)
    assert r == {"deleted": ref} and sk.called("deleteMe") == [(sk,)]
    sk.delete_ok = False
    err("FUSION_ERROR", run, S, ctx, "delete", sketch={"token": sk.entityToken}, confirm=True)


# ---- TASK-SK-004: rectangle, circle, line, polygon ------------------------------------------------


def xyz(p):
    return (round(p.x, 9), round(p.y, 9))


def calls(sk, name):
    """Recorded calls with Point3D arguments turned into (x, y) tuples (cm)."""
    return [tuple(xyz(a) if isinstance(a, core.Point3D) else a for a in c) for c in sk.called(name)]


def test_rectangle_exact_call_per_mode(S, ctx, sk):
    r = run(S, ctx, "rectangle", sketch="Sketch1", mode="two_point", p1=[0, 0], p2=[20, 10])
    assert calls(sk, "lines.addTwoPointRectangle") == [((0, 0), (2.0, 1.0))]
    assert [c["type"] for c in r["curves"]] == ["line"] * 4 and r["profiles"] == 1
    assert r["sketch"]["token"] == sk.entityToken
    run(S, ctx, "rectangle", sketch="Sketch1", mode="center", p1=[0, 0], p2=[5, 5])
    assert calls(sk, "lines.addCenterPointRectangle") == [((0, 0), (0.5, 0.5))]
    run(S, ctx, "rectangle", sketch="Sketch1", mode="three_point", p1=[0, 0], p2=[10, 0], p3=[5, 4])
    assert calls(sk, "lines.addThreePointRectangle") == [((0, 0), (1.0, 0), (0.5, 0.4))]
    assert sk.called("isComputeDeferred") == [(True,), (False,)] * 3


def test_rectangle_misuse_and_zero_size(S, ctx, sk):
    kw = dict(sketch="Sketch1", p1=[0, 0], p2=[20, 10])
    e = err("INVALID_PARAMS", run, S, ctx, "rectangle", mode="three_point", **kw)
    assert "p3" in e.message
    e = err("INVALID_PARAMS", run, S, ctx, "rectangle", mode="two_point", p3=[1, 1], **kw)
    assert "p3" in e.message
    err("INVALID_PARAMS", run, S, ctx, "rectangle", sketch="Sketch1", mode="two_point", p1=[0, 0], p2=[0, 0])
    err("INVALID_PARAMS", run, S, ctx, "rectangle", sketch="Sketch1", mode="two_point", p1=[0, 0], p2=[5, 0])
    err("INVALID_PARAMS", run, S, ctx, "rectangle", sketch="Sketch1", mode="center", p1=[3, 3], p2=[3, 9])
    err("INVALID_PARAMS", run, S, ctx, "rectangle", sketch="Sketch1", mode="three_point", p1=[1, 1],
        p2=[1, 1], p3=[4, 4])
    assert sk.calls == []


def test_circle_exact_call_per_mode(S, ctx, sk):
    r = run(S, ctx, "circle", sketch="Sketch1", mode="center_radius", center=[10, 20], radius=5)
    assert calls(sk, "circles.addByCenterRadius") == [((1.0, 2.0), 0.5)]
    assert r["curves"][0]["type"] == "circle" and r["profiles"] == 1
    run(S, ctx, "circle", sketch="Sketch1", mode="center_radius", center=[0, 0], diameter=30)
    assert calls(sk, "circles.addByCenterRadius")[1] == ((0, 0), 1.5)
    run(S, ctx, "circle", sketch="Sketch1", mode="two_point", p1=[0, 0], p2=[10, 0])
    assert calls(sk, "circles.addByTwoPoints") == [((0, 0), (1.0, 0))]
    run(S, ctx, "circle", sketch="Sketch1", mode="three_point", p1=[10, 0], p2=[0, 10], p3=[-10, 0])
    assert calls(sk, "circles.addByThreePoints") == [((1.0, 0), (0, 1.0), (-1.0, 0))]


def test_circle_parameter_errors(S, ctx, sk):
    base = dict(sketch="Sketch1")
    e = err("INVALID_PARAMS", run, S, ctx, "circle", mode="center_radius", center=[0, 0], radius=1,
            diameter=2, **base)
    assert "exactly one" in e.message
    err("INVALID_PARAMS", run, S, ctx, "circle", mode="center_radius", center=[0, 0], **base)
    err("INVALID_PARAMS", run, S, ctx, "circle", mode="center_radius", radius=1, **base)
    err("INVALID_PARAMS", run, S, ctx, "circle", mode="center_radius", center=[0, 0], radius=0, **base)
    e = err("INVALID_PARAMS", run, S, ctx, "circle", mode="two_point", p1=[0, 0], p2=[1, 0],
            radius=3, p3=[2, 2], **base)
    assert "radius" in e.message and "p3" in e.message
    err("INVALID_PARAMS", run, S, ctx, "circle", mode="three_point", p1=[0, 0], p2=[1, 0], **base)
    # collinear three points: Fusion raises, the pipeline turns that into FUSION_ERROR
    with pytest.raises(RuntimeError):
        run(S, ctx, "circle", mode="three_point", p1=[0, 0], p2=[1, 0], p3=[2, 0], **base)


def test_line_polyline_shares_points_and_closed(S, ctx, sk):
    r = run(S, ctx, "line", sketch="Sketch1", points=[[0, 0], [10, 0], [10, 10]])
    lines = list(sk.sketchCurves)
    assert len(lines) == 2 and lines[0].endSketchPoint is lines[1].startSketchPoint
    assert r["profiles"] == 0
    sk2 = fk.new_sketch(sk.parentComponent, "S2")
    r = run(S, ctx, "line", sketch="S2", points=[[0, 0], [10, 0], [10, 10]], closed=True)
    ls = list(sk2.sketchCurves)
    assert len(ls) == 3 and ls[2].endSketchPoint is ls[0].startSketchPoint and r["profiles"] == 1
    assert calls(sk2, "lines.addByTwoPoints")[0] == ((0, 0), (1.0, 0))
    assert sk2.called("lines.addByTwoPoints")[1][0] is ls[0].endSketchPoint


def test_line_errors(S, ctx, sk):
    e = err("INVALID_PARAMS", run, S, ctx, "line", sketch="Sketch1", points=[[0, 0], [1, 1]], closed=True)
    assert "3" in e.message
    e = err("INVALID_PARAMS", run, S, ctx, "line", sketch="Sketch1", points=[[0, 0], [1, 1], [1, 1]])
    assert "points[2] equals points[1]" in e.message
    with pytest.raises(Exception, match="at least 2"):  # the schema (min_length=2) rejects it
        run(S, ctx, "line", sketch="Sketch1", points=[[0, 0]])
    assert sk.calls == []


def test_line_rectangle_circle_polygon_construction_flag(S, ctx, sk):
    r = run(S, ctx, "line", sketch="Sketch1", points=[[0, 0], [10, 0], [10, 10]], construction=True)
    assert [c.isConstruction for c in sk.sketchCurves] == [True, True] and len(r["curves"]) == 2
    run(S, ctx, "circle", sketch="Sketch1", mode="two_point", p1=[0, 0], p2=[1, 0], construction=True)
    run(S, ctx, "rectangle", sketch="Sketch1", mode="two_point", p1=[0, 0], p2=[1, 1], construction=True)
    run(S, ctx, "polygon", sketch="Sketch1", center=[0, 0], sides=3, radius=1, construction=True)
    assert all(c.isConstruction for c in sk.sketchCurves)
    run(S, ctx, "line", sketch="Sketch1", points=[[0, 0], [1, 0]])
    assert list(sk.sketchCurves)[-1].isConstruction is False


@pytest.mark.parametrize("mode,rotation,expect_first", [
    ("inscribed", 0, (1.0, 0.0)),
    ("inscribed", 15, (math.cos(math.radians(15)), math.sin(math.radians(15)))),
])
def test_polygon_inscribed_vertices_and_equal_constraints(S, ctx, sk, mode, rotation, expect_first):
    r = run(S, ctx, "polygon", sketch="Sketch1", center=[0, 0], sides=4, radius=10, mode=mode,
            rotation=rotation)
    lines = list(sk.sketchCurves)
    assert len(lines) == 4 and len(r["curves"]) == 4 and r["profiles"] == 1
    first = lines[0].startSketchPoint.geometry
    assert (first.x, first.y) == pytest.approx(expect_first)
    a0 = math.radians(rotation)
    for k, ln in enumerate(lines):
        g = ln.startSketchPoint.geometry
        assert (g.x, g.y) == pytest.approx((math.cos(a0 + k * math.pi / 2), math.sin(a0 + k * math.pi / 2)))
    eq = sk.called("gc.addEqual")
    assert len(eq) == 3 and all(a is lines[0] and b is lines[i + 1] for i, (a, b) in enumerate(eq))


def test_polygon_circumscribed_and_center_offset(S, ctx, sk):
    run(S, ctx, "polygon", sketch="Sketch1", center=[10, 20], sides=6, radius=10,
        mode="circumscribed", rotation=0)
    pts = [(l.startSketchPoint.geometry.x, l.startSketchPoint.geometry.y) for l in sk.sketchCurves]
    R = 1.0 / math.cos(math.pi / 6)
    a0 = math.pi / 6
    assert pts[0] == pytest.approx((1.0 + R * math.cos(a0), 2.0 + R * math.sin(a0)))
    # an edge midpoint lies at angle `rotation` (0): midpoint of the edge between vertex 5 and 0
    p5, p0 = pts[5], pts[0]
    assert ((p5[0] + p0[0]) / 2, (p5[1] + p0[1]) / 2) == pytest.approx((1.0 + 1.0, 2.0))
    assert len(sk.called("gc.addEqual")) == 5
    err("INVALID_PARAMS", run, S, ctx, "polygon", sketch="Sketch1", center=[0, 0], sides=3, radius=0)


# ---- TASK-SK-005: arc, spline -------------------------------------------------------------------


def test_arc_both_modes_exact_calls_in_radians(S, ctx, sk):
    r = run(S, ctx, "arc", sketch="Sketch1", mode="three_point", start=[10, 0], point=[0, 10], end=[-10, 0])
    assert calls(sk, "arcs.addByThreePoints") == [((1.0, 0), (0, 1.0), (-1.0, 0))]
    assert r["curves"][0]["type"] == "arc" and set(r["curves"][0]["points"]) == {"center", "start", "end"}
    run(S, ctx, "arc", sketch="Sketch1", mode="center_start_sweep", center=[0, 0], start=[10, 0], sweep=90)
    (c, s, sw), = calls(sk, "arcs.addByCenterStartSweep")
    assert (c, s) == ((0, 0), (1.0, 0)) and sw == pytest.approx(math.pi / 2)
    run(S, ctx, "arc", sketch="Sketch1", mode="center_start_sweep", center=[0, 0], start=[10, 0], sweep="-45 deg")
    assert calls(sk, "arcs.addByCenterStartSweep")[1][2] == pytest.approx(-math.pi / 4)


def test_arc_sweep_zero_and_full_rejected_and_misuse(S, ctx, sk):
    kw = dict(sketch="Sketch1", mode="center_start_sweep", center=[0, 0], start=[10, 0])
    for bad in (0, 360, -360, 400):
        e = err("INVALID_PARAMS", run, S, ctx, "arc", sweep=bad, **kw)
        assert "sweep" in e.message
    run(S, ctx, "arc", sweep=359, **kw)
    e = err("INVALID_PARAMS", run, S, ctx, "arc", sketch="Sketch1", mode="center_start_sweep", start=[1, 0],
            center=[0, 0], sweep=30, end=[0, 1])
    assert "end" in e.message
    err("INVALID_PARAMS", run, S, ctx, "arc", sketch="Sketch1", mode="three_point", start=[1, 0], point=[0, 1])
    err("INVALID_PARAMS", run, S, ctx, "arc", sketch="Sketch1", mode="three_point", start=[1, 0], point=[0, 1],
        end=[-1, 0], sweep=30)
    err("INVALID_PARAMS", run, S, ctx, "arc", sketch="Sketch1", mode="center_start_sweep", start=[1, 0],
        center=[0, 0])


def test_spline_fit_closed(S, ctx, sk):
    r = run(S, ctx, "spline", sketch="Sketch1", mode="fit", points=[[0, 0], [10, 10], [20, 0]], closed=True)
    sp = list(sk.sketchCurves)[0]
    assert sp.isClosed is True and r["curves"][0]["type"] == "fitted_spline"
    assert len(r["curves"][0]["points"]["fit"]) == 3
    (coll,), = sk.called("fitted.add")
    assert [xyz(p) for p in coll] == [(0, 0), (1.0, 1.0), (2.0, 0)]
    err("INVALID_PARAMS", run, S, ctx, "spline", sketch="Sketch1", mode="fit", points=[[0, 0], [1, 1]], closed=True)


def test_spline_control_degree_and_closed_and_unsupported(S, ctx, sk):
    pts = [[0, 0], [10, 10], [20, 0], [30, 10]]
    r = run(S, ctx, "spline", sketch="Sketch1", mode="control", points=pts)
    (coll, degree), = sk.called("control.add")
    assert degree == 3 and len(coll) == 4 and r["curves"][0]["type"] == "control_point_spline"
    e = err("INVALID_PARAMS", run, S, ctx, "spline", sketch="Sketch1", mode="control", points=pts[:3])
    assert "4" in e.message
    err("INVALID_PARAMS", run, S, ctx, "spline", sketch="Sketch1", mode="control", points=pts, degree=5)
    run(S, ctx, "spline", sketch="Sketch1", mode="control", points=pts, closed=True)
    assert list(sk.sketchCurves)[-1].isClosed is True
    # a build whose isClosed setter raises
    fk.SketchControlPointSpline.closed_raises = True
    try:
        e = err("UNSUPPORTED", run, S, ctx, "spline", sketch="Sketch1", mode="control", points=pts, closed=True)
    finally:
        fk.SketchControlPointSpline.closed_raises = False
    assert "closed control-point" in e.message
    # a build without sketchControlPointSplines
    sk.sketchCurves._missing = frozenset({"sketchControlPointSplines"})
    n = len(sk._curves)
    err("UNSUPPORTED", run, S, ctx, "spline", sketch="Sketch1", mode="control", points=pts)
    assert len(sk._curves) == n


# ---- TASK-SK-006: constraint, auto_constrain, dimension ----------------------------------------------


@pytest.fixture
def geo(sk):
    """Two lines, two circles and loose points of ``sk``."""
    c = sk.sketchCurves
    l1 = c.sketchLines.addByTwoPoints(P(0, 0), P(4, 0))
    l2 = c.sketchLines.addByTwoPoints(P(0, 1), P(4, 1))
    c1 = c.sketchCircles.addByCenterRadius(P(10, 10), 1)
    c2 = c.sketchCircles.addByCenterRadius(P(10, 10), 2)
    spl = c.sketchFittedSplines.add(core.ObjectCollection([P(0, 5), P(1, 6), P(2, 5)]))
    p1, p2 = sk._new_point(7, 7), sk._new_point(8, 8)
    sk.calls.clear()
    return type("G", (), dict(l1=l1, l2=l2, c1=c1, c2=c2, spl=spl, p1=p1, p2=p2))


def tok(o):
    return {"token": o.entityToken}


def cons(S, ctx, type_, curves=(), points=(), **kw):
    return run(S, ctx, "constraint", sketch="Sketch1", type=type_, curves=[tok(c) for c in curves],
               points=[tok(p) for p in points], **{k: tok(v) for k, v in kw.items()})


def test_constraint_each_type_exact_call(S, ctx, sk, geo):
    g = geo
    cases = [
        ("coincident", dict(points=[g.p1, g.p2]), "gc.addCoincident", (g.p1, g.p2)),
        ("coincident", dict(points=[g.p1], curves=[g.l1]), "gc.addCoincident", (g.p1, g.l1)),
        ("horizontal", dict(curves=[g.l1]), "gc.addHorizontal", (g.l1,)),
        ("horizontal", dict(points=[g.p1, g.p2]), "gc.addHorizontalPoints", (g.p1, g.p2)),
        ("vertical", dict(curves=[g.l1]), "gc.addVertical", (g.l1,)),
        ("vertical", dict(points=[g.p1, g.p2]), "gc.addVerticalPoints", (g.p1, g.p2)),
        ("parallel", dict(curves=[g.l1, g.l2]), "gc.addParallel", (g.l1, g.l2)),
        ("perpendicular", dict(curves=[g.l1, g.l2]), "gc.addPerpendicular", (g.l1, g.l2)),
        ("collinear", dict(curves=[g.l1, g.l2]), "gc.addCollinear", (g.l1, g.l2)),
        ("tangent", dict(curves=[g.l1, g.c1]), "gc.addTangent", (g.l1, g.c1)),
        ("equal", dict(curves=[g.c1, g.c2]), "gc.addEqual", (g.c1, g.c2)),
        ("concentric", dict(curves=[g.c1, g.c2]), "gc.addConcentric", (g.c1, g.c2)),
        ("midpoint", dict(points=[g.p1], curves=[g.l1]), "gc.addMidPoint", (g.p1, g.l1)),
        ("symmetry", dict(points=[g.p1, g.p2], symmetry_line=g.l1), "gc.addSymmetry", (g.p1, g.p2, g.l1)),
        ("symmetry", dict(curves=[g.c1, g.c2], symmetry_line=g.l1), "gc.addSymmetry", (g.c1, g.c2, g.l1)),
        ("smooth", dict(curves=[g.l1, g.spl]), "gc.addSmooth", (g.l1, g.spl)),
        ("curvature", dict(curves=[g.spl, g.l1]), "gc.addCurvature", (g.spl, g.l1)),
    ]
    for type_, kw, call, args in cases:
        sk.calls.clear()
        r = cons(S, ctx, type_, **kw)
        assert sk.called(call) == [args], (type_, call)
        assert r["constraint"]["type"] == type_ and r["constraint"]["ref"]["token"]
        assert r["fully_constrained"] is False and r["sketch"]["token"] == sk.entityToken
    assert len(sk.geometricConstraints) == len(cases)


def test_constraint_rejected_forms(S, ctx, sk, geo):
    g = geo
    bad = [
        ("coincident", dict(points=[g.p1])), ("coincident", dict(curves=[g.l1, g.l2])),
        ("horizontal", dict(curves=[g.c1])), ("horizontal", dict(curves=[g.l1], points=[g.p1])),
        ("vertical", dict(points=[g.p1])), ("parallel", dict(curves=[g.l1, g.c1])),
        ("perpendicular", dict(curves=[g.l1])), ("collinear", dict(points=[g.p1, g.p2])),
        ("tangent", dict(curves=[g.l1])), ("equal", dict(points=[g.p1, g.p2])),
        ("concentric", dict(curves=[g.l1, g.l2])), ("midpoint", dict(curves=[g.l1])),
        ("symmetry", dict(points=[g.p1, g.p2])), ("symmetry", dict(points=[g.p1, g.p2], symmetry_line=g.c1)),
        ("smooth", dict(curves=[g.l1, g.l2])), ("curvature", dict(curves=[g.l1])),
        ("fix", {}),
    ]
    for type_, kw in bad:
        e = err("INVALID_PARAMS", cons, S, ctx, type_, **kw)
        assert type_ in e.message, (type_, e.message)
    assert sk.calls == [] and len(sk.geometricConstraints) == 0


def test_constraint_fix_sets_is_fixed(S, ctx, sk, geo):
    r = cons(S, ctx, "fix", curves=[geo.l1, geo.c1], points=[geo.p1])
    assert r["constraint"] == {"type": "fix", "fixed": 3}
    assert geo.l1.isFixed and geo.c1.isFixed and geo.p1.isFixed and not geo.l2.isFixed


def test_constraint_foreign_entity_and_curvature_unsupported_and_fusion_error(S, ctx, fake_design, sk, geo):
    other = fk.new_sketch(fake_design.rootComponent, "Other")
    stray = other.sketchCurves.sketchLines.addByTwoPoints(P(0, 0), P(1, 1))
    e = err("INVALID_PARAMS", cons, S, ctx, "tangent", curves=[geo.l1, stray])
    assert "curves" in e.message and stray.entityToken in e.message
    sk.geometricConstraints._missing = frozenset({"addCurvature"})
    err("UNSUPPORTED", cons, S, ctx, "curvature", curves=[geo.l1, geo.l2])
    sk.fail["gc.addTangent"] = RuntimeError("over-constrained")
    with pytest.raises(RuntimeError, match="over-constrained"):  # the pipeline reports FUSION_ERROR
        cons(S, ctx, "tangent", curves=[geo.l1, geo.c1])


def test_constraint_auto_constrain_result_options_and_mapping(S, ctx, sk):
    from adsk import fusion

    res = fk.AutoConstrainResult(added=[1, 2], dims=[3], moved=[4, 5, 6], full=True)
    sk.results["autoConstrain"] = res
    expect = {"thorough": 1, "fast": 2, "adjust": 3}
    for option, n in expect.items():
        r = run(S, ctx, "auto_constrain", sketch="Sketch1", result=option)
        assert sk.called("autoConstrain")[-1][0].resultOption == n
        assert r == {"sketch": ctx.ref(sk), "constraints_added": 2, "dimensions_added": 1, "moved": 3,
                     "fully_constrained": True}
    assert fusion.AutoConstrainResultTypes.Option1AutoConstrainResultType == 1
    assert run(S, ctx, "auto_constrain", sketch="Sketch1")["constraints_added"] == 2  # default thorough
    assert sk.called("autoConstrain")[-1][0].resultOption == 1


def test_constraint_auto_constrain_none_result_and_missing_api(S, ctx, sk):
    e = err("FUSION_ERROR", run, S, ctx, "auto_constrain", sketch="Sketch1", result="adjust")
    assert "not eligible" in e.message
    sk._missing = frozenset({"createAutoConstrainInput"})
    e = err("UNSUPPORTED", run, S, ctx, "auto_constrain", sketch="Sketch1")
    assert "2026" in e.message


def _unit(x, y):
    n = math.hypot(x, y)
    return x / n, y / n


def dim(S, ctx, type_, **kw):
    for k in ("curves", "points"):
        if k in kw:
            kw[k] = [tok(o) for o in kw[k]]
    return run(S, ctx, "dimension", sketch="Sketch1", type=type_, **kw)


def test_dimension_distance_types_call_and_default_text_point(S, ctx, sk, geo):
    g = geo
    O = fk.DimensionOrientations
    p, q = sk._new_point(0, 0), sk._new_point(3, 4)
    sk.calls.clear()
    for type_, orient, expect_t in (
        ("horizontal", O.HorizontalDimensionOrientation, (1.5, 2.5)),
        ("vertical", O.VerticalDimensionOrientation, (2.0, 2.0)),
        ("distance", O.AlignedDimensionOrientation, (1.5 - 0.4, 2.0 + 0.3)),
    ):
        r = dim(S, ctx, type_, points=[p, q])
        a, b, o, t, driving = sk.called("sd.addDistanceDimension")[-1]
        assert (a, b, o, driving) == (p, q, orient, True)
        assert (t.x, t.y) == pytest.approx(expect_t)
        assert r["dimension"]["type"] == type_ and r["dimension"]["driving"] is True
    r = dim(S, ctx, "horizontal", curves=[g.l1], value=25)
    a, b, o, t, d = sk.called("sd.addDistanceDimension")[-1]
    assert (a, b) == (g.l1.startSketchPoint, g.l1.endSketchPoint)
    assert r["dimension"]["value"] == pytest.approx(25.0) and r["dimension"]["expression"] == "25 mm"
    assert r["dimension"]["parameter"] == "d4"
    assert ("expression", "d4", "25 mm") in sk.calls
    r = dim(S, ctx, "vertical", points=[p, q], text_point=[5, 6])
    t = sk.called("sd.addDistanceDimension")[-1][3]
    assert (t.x, t.y) == pytest.approx((0.5, 0.6))


def test_dimension_radial_diameter_offset(S, ctx, sk, geo):
    g = geo
    r = dim(S, ctx, "radial", curves=[g.c1], value="7 mm")
    c, t, driving = sk.called("sd.addRadialDimension")[-1]
    assert c is g.c1 and driving
    assert (t.x, t.y) == pytest.approx((10 + 1.5 * math.sqrt(0.5),) * 2)
    assert r["dimension"]["value"] == pytest.approx(7.0)
    r = dim(S, ctx, "diameter", curves=[g.c2])
    assert r["dimension"]["value"] == pytest.approx(40.0) and sk.called("sd.addDiameterDimension")
    r = dim(S, ctx, "offset", curves=[g.l1, g.l2])
    l1, l2, t, d = sk.called("sd.addOffsetDimension")[-1]
    assert (l1, l2) == (g.l1, g.l2) and (t.x, t.y) == pytest.approx((2.0 + 0.5, 0.5))


def test_dimension_misuse(S, ctx, sk, geo):
    g = geo
    p, q = sk._new_point(0, 0), sk._new_point(3, 4)
    for type_, kw in (
        ("angular", dict(curves=[g.l1, g.l2], value=5)),
        ("radial", dict(curves=[g.c1], angle=30)),
        ("radial", dict(curves=[g.c1], driven=True, value=5)),
        ("angular", dict(curves=[g.l1, g.l2], driven=True, angle=30)),
        ("radial", dict(curves=[g.l1])), ("distance", dict(points=[p])),
        ("distance", dict(curves=[g.c1])), ("offset", dict(curves=[g.l1, g.c1])),
        ("angular", dict(curves=[g.l1])),
    ):
        err("INVALID_PARAMS", dim, S, ctx, type_, **kw)
    assert sk.calls == []
    # parallel lines for angular
    e = err("INVALID_PARAMS", dim, S, ctx, "angular", curves=[g.l1, g.l2])
    assert "parallel" in e.message
    # offset of non-parallel lines
    lx = sk.sketchCurves.sketchLines.addByTwoPoints(P(0, 0), P(0, 3))
    err("INVALID_PARAMS", dim, S, ctx, "offset", curves=[g.l1, lx])


def test_dimension_driven_has_no_parameter(S, ctx, sk, geo):
    r = dim(S, ctx, "radial", curves=[geo.c1], driven=True)
    assert r["dimension"] == {"type": "radial", "parameter": None, "value": pytest.approx(10.0),
                              "expression": None, "driving": False}
    assert sk.called("sd.addRadialDimension")[-1][2] is False


def test_dimension_angular_default_text_point_and_quadrant(S, ctx, sk):
    c = sk.sketchCurves.sketchLines
    # two lines crossing at both midpoints: X = (0, 0); far ends (2, 0) and (0, 3), (-2, 0)... use an X cross
    a = c.addByTwoPoints(P(-2, -2), P(2, 2))
    b = c.addByTwoPoints(P(-3, 3), P(3, -3))
    sk.calls.clear()
    r = dim(S, ctx, "angular", curves=[a, b], angle=30)
    l1, l2, t, driving = sk.called("sd.addAngularDimension")[-1]
    assert math.hypot(t.x, t.y) == pytest.approx(0.5)  # crossing at both midpoints: 5 mm from X
    assert r["dimension"]["value"] == pytest.approx(30.0) and r["dimension"]["expression"] == "30 deg"
    # the fake measures in the quadrant containing T: the 90 degree one here
    r = dim(S, ctx, "angular", curves=[a, b])
    assert r["dimension"]["value"] == pytest.approx(90.0)
    # an explicit text point in the opposite-quadrant pair gives the same 90 here; use non-perpendicular lines
    d = c.addByTwoPoints(P(0, 0), P(4, 0))
    e = c.addByTwoPoints(P(0, 0), P(4, 2))
    t_in = dim(S, ctx, "angular", curves=[d, e])["dimension"]["value"]
    tp = sk.called("sd.addAngularDimension")[-1][2]
    u1, u2 = (1.0, 0.0), _unit(4, 2)
    bis = _unit(u1[0] + u2[0], u1[1] + u2[1])
    assert (tp.x, tp.y) == pytest.approx((0.5 * bis[0], 0.5 * bis[1]))
    t_out = dim(S, ctx, "angular", curves=[d, e], text_point=[-10, 5])["dimension"]["value"]
    assert t_in == pytest.approx(math.degrees(math.atan2(2, 4)))
    assert t_out == pytest.approx(180 - math.degrees(math.atan2(2, 4)))


# ---- TASK-SK-007: offset, trim, extend, project, set_construction -----------------------------------


def line_in(sk, a=(0, 0), b=(1, 0)):
    return sk.sketchCurves.sketchLines.addByTwoPoints(P(*a), P(*b))


def test_offset_call_direction_point_and_cm_distance(S, ctx, sk):
    a, b = line_in(sk), line_in(sk, (1, 0), (1, 1))
    new = fk.SketchLine(sk, sk._new_point(0, 1), sk._new_point(1, 1))
    sk.results["offset"] = [new]
    r = run(S, ctx, "offset", sketch="Sketch1", curves=[tok(a), tok(b)], distance=2.5,
            side_point=[5, 5], construction=True)
    (coll, pt_, dist), = sk.called("offset")
    assert list(coll) == [a, b] and (pt_.x, pt_.y) == (0.5, 0.5) and dist == pytest.approx(0.25)
    assert [c["ref"]["token"] for c in r["curves"]] == [new.entityToken] and new.isConstruction
    err("INVALID_PARAMS", run, S, ctx, "offset", sketch="Sketch1", curves=[tok(a)], distance=0,
        side_point=[0, 0])
    err("INVALID_PARAMS", run, S, ctx, "offset", sketch="Sketch1", curves=[tok(a)], distance=-1,
        side_point=[0, 0])
    assert len(sk.called("offset")) == 1


def test_trim_and_extend_create_constraints_and_return_remaining(S, ctx, fake_design, sk):
    c = line_in(sk)
    left = line_in(sk, (0, 0), (0.5, 0))
    sk.results["trim"] = [left]
    r = run(S, ctx, "trim", sketch="Sketch1", curve=tok(c), point=[3, 0])
    (cv, p, flag), = sk.called("trim")
    assert cv is c and (p.x, p.y) == pytest.approx((0.3, 0)) and flag is True
    assert [x["ref"]["token"] for x in r["curves"]] == [left.entityToken]
    sk.results["trim"] = []
    assert run(S, ctx, "trim", sketch="Sketch1", curve=tok(c), point=[3, 0])["curves"] == []
    sk.results["extend"] = [c]
    r = run(S, ctx, "extend", sketch="Sketch1", curve=tok(c), point=[10, 0])
    (cv, p, flag), = sk.called("extend")
    assert cv is c and flag is True and len(r["curves"]) == 1
    other = fk.new_sketch(fake_design.rootComponent, "Other")
    foreign = line_in(other)
    for name in ("trim", "extend"):
        e = err("INVALID_PARAMS", run, S, ctx, name, sketch="Sketch1", curve=tok(foreign), point=[0, 0])
        assert foreign.entityToken in e.message and "curve" in e.message
    assert len(sk.called("trim")) == 2 and len(sk.called("extend")) == 1


@pytest.fixture
def brep(fake_design):
    root = fake_design.rootComponent
    body = root.add_body("Box")
    return type("B", (), dict(body=body, edge=body.add_edge(), face=body.add_face(), body2=root.add_body("B2")))


def test_project_per_entity_kind_linked(S, ctx, fake_design, sk, brep):
    other = fk.new_sketch(fake_design.rootComponent, "Other")
    oc, op = line_in(other), other._new_point(2, 2)
    out = fk.SketchLine(sk, sk._new_point(0, 0), sk._new_point(1, 1))
    outp = sk._new_point(5, 5)
    sk.results["project"] = [out, outp]
    r = run(S, ctx, "project", sketch="Sketch1", edges=[tok(brep.edge)], faces=[tok(brep.face)],
            bodies=[tok(brep.body)], curves=[tok(oc)], points=[tok(op)])
    assert [a[0] for a in sk.called("project")] == [brep.edge, brep.face, brep.body, oc, op]
    assert len(r["curves"]) == 5 and len(r["points"]) == 5 and r["curves"][0]["type"] == "line"
    assert r["points"][0]["token"] == outp.entityToken and r["profiles"] == 0


def test_project_unlinked_with_and_without_project2(S, ctx, sk, brep):
    sk.results["project2"] = []
    run(S, ctx, "project", sketch="Sketch1", edges=[tok(brep.edge)], faces=[tok(brep.face)], linked=False)
    (coll, linked), = sk.called("project2")
    assert list(coll) == [brep.edge, brep.face] and linked is False and sk.called("project") == []
    sk._missing = frozenset({"project2"})
    err("UNSUPPORTED", run, S, ctx, "project", sketch="Sketch1", edges=[tok(brep.edge)], linked=False)


def test_project_cut_edges_and_intersect(S, ctx, sk, brep):
    sk.results["projectCutEdges"] = []
    run(S, ctx, "project", sketch="Sketch1", mode="cut_edges", bodies=[tok(brep.body), tok(brep.body2)])
    assert [a[0] for a in sk.called("projectCutEdges")] == [brep.body, brep.body2]
    err("INVALID_PARAMS", run, S, ctx, "project", sketch="Sketch1", mode="cut_edges",
        bodies=[tok(brep.body)], edges=[tok(brep.edge)])
    err("INVALID_PARAMS", run, S, ctx, "project", sketch="Sketch1", mode="cut_edges", faces=[tok(brep.face)])
    sk.results["intersectWithSketchPlane"] = []
    run(S, ctx, "project", sketch="Sketch1", mode="intersect", bodies=[tok(brep.body)],
        faces=[tok(brep.face)])
    (coll,), = sk.called("intersectWithSketchPlane")
    assert list(coll) == [brep.face, brep.body]


def test_project_rejects_own_sketch_and_empty_and_names_rejected_body(S, ctx, sk, brep):
    mine = line_in(sk)
    err("INVALID_PARAMS", run, S, ctx, "project", sketch="Sketch1", curves=[tok(mine)])
    err("INVALID_PARAMS", run, S, ctx, "project", sketch="Sketch1", points=[tok(sk._new_point(1, 1))])
    e = err("INVALID_PARAMS", run, S, ctx, "project", sketch="Sketch1")
    assert "at least one" in e.message
    sk.fail["project"] = RuntimeError("rejected")
    e = err("FUSION_ERROR", run, S, ctx, "project", sketch="Sketch1", bodies=[tok(brep.body)])
    assert "Box" in e.message
    with pytest.raises(RuntimeError):  # other entities: the raw Fusion error goes through
        run(S, ctx, "project", sketch="Sketch1", edges=[tok(brep.edge)])


def test_set_construction_both_values_and_foreign(S, ctx, fake_design, sk):
    a, b = line_in(sk), line_in(sk, (0, 1), (1, 1))
    r = run(S, ctx, "set_construction", sketch="Sketch1", curves=[tok(a), tok(b)])
    assert a.isConstruction and b.isConstruction and len(r["curves"]) == 2
    run(S, ctx, "set_construction", sketch="Sketch1", curves=[tok(a)], value=False)
    assert not a.isConstruction and b.isConstruction
    foreign = line_in(fk.new_sketch(fake_design.rootComponent, "Other"))
    err("INVALID_PARAMS", run, S, ctx, "set_construction", sketch="Sketch1", curves=[tok(foreign)])


# ---- TASK-SK-008: replay ---------------------------------------------------------------------------


def _desc(**kw):
    d = {"units": {"length": "mm", "angle": "deg"}, "points": [], "curves": [],
         "constraints": [], "dimensions": []}
    d.update(kw)
    return d


def _pt(i, x, y, **kw):
    return dict(index=i, xy=[x, y], **kw)


def test_replay_shares_points_and_maps_origin(S, ctx, sk):
    d = _desc(
        points=[_pt(0, 0, 0, origin=True), _pt(1, 10, 0), _pt(2, 10, 10)],
        curves=[dict(index=0, type="line", start=0, end=1), dict(index=1, type="line", start=1, end=2)],
    )
    r = S.replay_into(ctx, sk, d)
    lines = [c for c in sk._curves if c.objectType.endswith('SketchLine')]
    assert len(lines) == 2 and len(r["curves"]) == 2 and r["skipped"] == []
    assert lines[0].startSketchPoint is sk.originPoint
    assert lines[0].endSketchPoint is lines[1].startSketchPoint
    assert len(sk._points) == 3  # origin + 2 new; no duplicates
    assert xyz(lines[1].endSketchPoint.geometry) == pytest.approx((1.0, 1.0))
    assert sk.called("points.add") == []


def test_replay_loose_points_flags_and_every_curve_type(S, ctx, sk):
    d = _desc(
        points=[_pt(0, 0, 0), _pt(1, 5, 5, fixed=True), _pt(2, 20, 0), _pt(3, 30, 0), _pt(4, 20, 5),
                _pt(5, 40, 0), _pt(6, 41, 1), _pt(7, 42, 0), _pt(8, 50, 0), _pt(9, 51, 1), _pt(10, 52, 0)],
        curves=[
            dict(index=0, type="circle", center=2, radius=3, construction=True),
            dict(index=1, type="arc", center=3, start=4, end=3, radius=5, sweep=90),
            dict(index=2, type="fitted_spline", fit_points=[5, 6, 7], closed=True),
            dict(index=3, type="control_point_spline", control_points=[8, 9, 10], degree=2),
        ],
    )
    r = S.replay_into(ctx, sk, d)
    circ = [c for c in sk._curves if c.objectType.endswith('SketchCircle')][0]
    assert circ.isConstruction is True and circ.radius == pytest.approx(0.3)
    assert sk.called("arcs.addByThreePoints")
    assert [c for c in sk._curves if c.objectType.endswith('SketchFittedSpline')][0].isClosed is True
    assert sk.called("control.add")[0][1] == 2
    fixed = [p for p in sk._points if p.isFixed]
    assert len(fixed) == 1 and xyz(fixed[0].geometry) == pytest.approx((0.5, 0.5))
    assert r["skipped"] == [] and len(r["curves"]) == 4


def test_replay_skips_unreplayable_curve_and_dependent_constraint(S, ctx, sk):
    d = _desc(
        points=[_pt(0, 0, 0), _pt(1, 10, 0)],
        curves=[dict(index=0, type="line", start=0, end=1),
                dict(index=1, type="conic", replayable=False)],
        constraints=[dict(index=0, type="horizontal", curves=[0]),
                     dict(index=1, type="tangent", curves=[0, 1])],
    )
    r = S.replay_into(ctx, sk, d)
    assert r["constraints"] == 1 and r["curves"][1] is None
    assert [(s["kind"], s["index"]) for s in r["skipped"]] == [("curve", 1), ("constraint", 1)]
    assert "conic" in r["skipped"][0]["reason"]


def test_replay_fusion_exception_in_constraint_is_skipped_with_message(S, ctx, sk):
    d = _desc(
        points=[_pt(0, 0, 0), _pt(1, 10, 0)],
        curves=[dict(index=0, type="line", start=0, end=1)],
        constraints=[dict(index=0, type="horizontal", curves=[0])],
    )
    sk.fail["gc.addHorizontal"] = RuntimeError("over-constrained")
    r = S.replay_into(ctx, sk, d)
    assert r["constraints"] == 0
    assert r["skipped"][0]["kind"] == "constraint" and "over-constrained" in r["skipped"][0]["reason"]


def test_replay_dimension_value_expression_and_units_in(S, ctx, sk):
    d = _desc(
        units={"length": "in", "angle": "deg"},
        points=[_pt(0, 0, 0), _pt(1, 1, 0)],
        curves=[dict(index=0, type="line", start=0, end=1)],
        dimensions=[dict(index=0, type="distance", points=[0, 1], value=2.0, driving=True)],
    )
    r = S.replay_into(ctx, sk, d)
    line = [c for c in sk._curves if c.objectType.endswith('SketchLine')][0]
    assert line.endSketchPoint.geometry.x == pytest.approx(2.54)
    dim = sk._dims[0]
    assert dim.parameter.expression == "2 in" and r["dimensions"] == 1


def test_replay_action_requires_exactly_one_of_sketch_and_plane(S, ctx, fake_design, sk):
    err("INVALID_PARAMS", run, S, ctx, "replay", description=_desc())
    err("INVALID_PARAMS", run, S, ctx, "replay", description=_desc(), sketch="Sketch1",
        plane={"token": fake_design.rootComponent.xYConstructionPlane.entityToken})


def test_replay_action_on_new_plane_and_bad_description(S, ctx, fake_design):
    plane = fake_design.rootComponent.xYConstructionPlane
    d = _desc(points=[_pt(0, 0, 0), _pt(1, 10, 0)], curves=[dict(index=0, type="line", start=0, end=1)])
    r = run(S, ctx, "replay", description=d, plane={"token": plane.entityToken}, name="Rebuilt")
    assert r["sketch"]["name"] == "Rebuilt" and len(r["curves"]) == 1
    err("INVALID_PARAMS", run, S, ctx, "replay", description={"points": [{"index": "x"}]},
        plane={"token": plane.entityToken})


def test_replay_round_trip_describe_equal(S, ctx, fake_design, sk):
    c = sk.sketchCurves
    a = c.sketchLines.addByTwoPoints(P(0, 0), P(4, 0))
    b = c.sketchLines.addByTwoPoints(a.endSketchPoint, P(4, 2))
    c.sketchCircles.addByCenterRadius(P(10, 10), 1).isConstruction = True
    sk.geometricConstraints.addHorizontal(a)
    sk.sketchDimensions.addDistanceDimension(
        a.startSketchPoint, a.endSketchPoint, 0, P(2, -1), True).parameter.expression = "40 mm"
    before = S.describe(ctx, sk)
    new = fk.new_sketch(fake_design.rootComponent, name="Sketch2")
    S.replay_into(ctx, new, before)
    after = S.describe(ctx, new)

    def strip(d):
        return {k: [{x: y for x, y in i.items() if x != "ref"} for i in v] if isinstance(v, list) else v
                for k, v in d.items() if k not in ("sketch", "ref")}

    assert strip(after)["curves"] == strip(before)["curves"]
    assert strip(after)["constraints"] == strip(before)["constraints"]
    assert strip(after)["dimensions"] == strip(before)["dimensions"]


def test_replay_action_rejects_component_or_name_with_existing_sketch(S, ctx, sk):
    err("INVALID_PARAMS", run, S, ctx, "replay", description=_desc(), sketch="Sketch1", name="X")


def test_replay_dimension_on_skipped_curve_is_skipped(S, ctx, sk):
    d = _desc(
        points=[_pt(0, 0, 0)],
        curves=[dict(index=0, type="conic", replayable=False)],
        dimensions=[dict(index=0, type="radial", curves=[0], value=1.0)],
    )
    r = S.replay_into(ctx, sk, d)
    assert r["dimensions"] == 0 and r["skipped"][-1] == {"kind": "dimension", "index": 0, "reason": "entity skipped"}
    assert sk._dims == []


# ---- TASK-SK-011: ellipse, slot, point, text ----------------------------------------------------


def test_ellipse_exact_call_profile_and_construction(S, ctx, sk):
    r = run(S, ctx, "ellipse", sketch="Sketch1", center=[0, 0], major_point=[20, 0], point=[0, 10],
            construction=False)
    assert calls(sk, "ellipses.add") == [((0, 0), (2.0, 0), (0, 1.0))]
    e = sk._curves[0]
    assert (e.majorAxisRadius, e.minorAxisRadius) == pytest.approx((2.0, 1.0))
    assert r["curves"][0]["type"] == "ellipse" and r["profiles"] == 1
    run(S, ctx, "ellipse", sketch="Sketch1", center=[0, 0], major_point=[20, 0], point=[0, 10],
        construction=True)
    assert sk._curves[1].isConstruction is True and sk._curves[0].isConstruction is False


def test_ellipse_degenerate_inputs_rejected_before_fusion(S, ctx, sk):
    for kw in (dict(center=[1, 1], major_point=[1, 1], point=[5, 5]),
               dict(center=[0, 0], major_point=[20, 0], point=[5, 0]),
               dict(center=[0, 0], major_point=[20, 0], point=[-5, 0]),
               dict(center=[0, 0], major_point=[0, 20], point=[0, 5])):
        err("INVALID_PARAMS", run, S, ctx, "ellipse", sketch="Sketch1", **kw)
    assert sk.called("ellipses.add") == []


def test_ellipse_fusion_failure_is_fusion_error(S, ctx, sk):
    sk.fail["ellipses.add"] = RuntimeError("bad ellipse")
    with pytest.raises(RuntimeError):
        run(S, ctx, "ellipse", sketch="Sketch1", center=[0, 0], major_point=[20, 0], point=[0, 10])


@pytest.mark.parametrize("mode,p1,p2", [
    ("center_to_center", [0, 0], [30, 0]),
    ("overall", [0, 0], [30, 0]),
])
def test_slot_curve_calls_shared_points_and_constraints(S, ctx, sk, mode, p1, p2):
    r = run(S, ctx, "slot", sketch="Sketch1", mode=mode, p1=p1, p2=p2, width=10)
    types_ = [c["type"] for c in r["curves"]]
    assert types_ == ["line", "arc", "line", "arc"]
    l1, l2, arc_b, arc_a = sk._curves
    r_cm = 0.5
    ax, bx = (0, 3) if mode == "center_to_center" else (0.5, 2.5)
    assert calls(sk, "lines.addByTwoPoints") == [
        ((ax, r_cm), (bx, r_cm)), ((bx, -r_cm), (ax, -r_cm))]
    arcs = sk.called("arcs.addByThreePoints")
    assert arcs[0][0] is l1.endSketchPoint and arcs[0][2] is l2.startSketchPoint
    assert arcs[1][0] is l2.endSketchPoint and arcs[1][2] is l1.startSketchPoint
    assert (arcs[0][1].x, arcs[0][1].y) == pytest.approx((bx + r_cm, 0))
    assert (arcs[1][1].x, arcs[1][1].y) == pytest.approx((ax - r_cm, 0))
    assert [(a[0], a[1]) for a in sk.called("gc.addTangent")] == [
        (l1, arc_b), (l2, arc_b), (l2, arc_a), (l1, arc_a)]
    assert [tuple(a) for a in sk.called("gc.addEqual")] == [(arc_a, arc_b)]
    assert sk.isComputeDeferred is False


def test_slot_overall_too_short_and_width_zero_rejected(S, ctx, sk):
    base = dict(sketch="Sketch1", p1=[0, 0], p2=[10, 0])
    err("INVALID_PARAMS", run, S, ctx, "slot", mode="overall", width=10, **base)
    err("INVALID_PARAMS", run, S, ctx, "slot", mode="overall", width=12, **base)
    err("INVALID_PARAMS", run, S, ctx, "slot", mode="center_to_center", width=0, **base)
    err("INVALID_PARAMS", run, S, ctx, "slot", mode="center_to_center", width=2,
        sketch="Sketch1", p1=[1, 1], p2=[1, 1])
    assert sk._curves == []
    run(S, ctx, "slot", mode="overall", width=9, **base)  # just long enough
    assert len(sk._curves) == 4


def test_slot_construction_marks_all_curves(S, ctx, sk):
    run(S, ctx, "slot", sketch="Sketch1", mode="center_to_center", p1=[0, 0], p2=[30, 0], width=10,
        construction=True)
    assert all(c.isConstruction for c in sk._curves)


def test_point_adds_each_point_in_order(S, ctx, sk):
    r = run(S, ctx, "point", sketch="Sketch1", points=[[5, 5], [10, -3]])
    assert calls(sk, "points.add") == [((0.5, 0.5),), ((1.0, -0.3),)]
    assert [x for p in r["points"] for x in p["xy"]] == pytest.approx([5, 5, 10, -3])
    assert len(r["points"]) == 2 and r["profiles"] == 0 and r["sketch"]["name"] == "Sketch1"
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        run(S, ctx, "point", sketch="Sketch1", points=[])


def _box(S, ctx, **kw):
    return run(S, ctx, "text", sketch="Sketch1", **kw)


def test_text_box_input_calls_default_corner_and_result(S, ctx, sk):
    r = _box(S, ctx, content="AB", height=5, position=[0, 0], font="Mono")
    assert sk.called("texts.createInput2")[0] == ("AB", 0.5)
    p, q, h, v, spacing = sk.called("text.setAsMultiLine")[0]
    assert xyz(p) + xyz(q) == pytest.approx((0, 0, 1.0, 0.75))
    assert h is core.HorizontalAlignments.LeftHorizontalAlignment
    assert v is core.VerticalAlignments.BottomVerticalAlignment and spacing == 0
    assert r["text"] == {"ref": r["text"]["ref"], "content": "AB", "height": pytest.approx(5.0),
                         "font": "Mono", "angle": pytest.approx(0.0), "mode": "box"}
    assert sk._texts[0].fontName == "Mono"


def test_text_box_multiline_corner_explicit_corner_alignment_and_angle(S, ctx, sk):
    _box(S, ctx, content="ab\nabcd\nx", height=2, position=[1, 1])
    q = sk.called("text.setAsMultiLine")[0][1]
    assert (q.x, q.y) == pytest.approx((0.1 + 4 * 0.2, 0.1 + 3 * 1.5 * 0.2))
    _box(S, ctx, content="Z", height=2, position=[0, 0], box_corner=[10, 5],
         horizontal_align="right", vertical_align="top", angle=30)
    p, q, h, v, _s = sk.called("text.setAsMultiLine")[1]
    assert xyz(q) == pytest.approx((1.0, 0.5))
    assert h is core.HorizontalAlignments.RightHorizontalAlignment
    assert v is core.VerticalAlignments.TopVerticalAlignment
    assert sk._texts[1].angle == pytest.approx(math.radians(30))
    _box(S, ctx, content="M", height=2, position=[0, 0], horizontal_align="center",
         vertical_align="middle")
    _p, _q, h, v, _s = sk.called("text.setAsMultiLine")[2]
    assert h is core.HorizontalAlignments.CenterHorizontalAlignment
    assert v is core.VerticalAlignments.MiddleVerticalAlignment


def test_text_along_path_and_fit_on_path_calls(S, ctx, sk):
    ln = line_in(sk, (0, 0), (10, 0))
    r = _box(S, ctx, content="Hi", height=3, mode="along_path", path=tok(ln), above=False,
             horizontal_align="center")
    path, above, h, spacing = sk.called("text.setAsAlongPath")[0]
    assert path is ln and above is False and spacing == 0
    assert h is core.HorizontalAlignments.CenterHorizontalAlignment and r["text"]["mode"] == "along_path"
    r = _box(S, ctx, content="Hi", height=3, mode="fit_on_path", path=tok(ln))
    assert sk.called("text.setAsFitOnPath")[0] == (ln, True) and r["text"]["mode"] == "fit_on_path"


def test_text_parameter_misuse_per_mode(S, ctx, sk, fake_design):
    ln = line_in(sk)
    bad = [
        dict(content="A", height=0, position=[0, 0], box_corner=[5, 5]),
        dict(content="A", height=5),                                   # box needs position
        dict(content="A", height=5, position=[0, 0], path=tok(ln)),    # box with path
        dict(content="A", height=5, position=[0, 0], box_corner=[0, 5]),   # zero width
        dict(content="A", height=5, position=[0, 0], box_corner=[5, 0]),   # zero height
        dict(content="A", height=5, mode="along_path"),                # needs path
        dict(content="A", height=5, mode="along_path", path=tok(ln), position=[0, 0]),
        dict(content="A", height=5, mode="fit_on_path", path=tok(ln), box_corner=[1, 1]),
        dict(content="A", height=5, mode="along_path", path=tok(ln), angle=10),
    ]
    for kw in bad:
        err("INVALID_PARAMS", _box, S, ctx, **kw)
    other = fk.new_sketch(fake_design.rootComponent, name="Sketch2")
    foreign = line_in(other)
    err("INVALID_PARAMS", _box, S, ctx, content="A", height=5, mode="along_path", path=tok(foreign))
    assert sk.called("texts.add") == []
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        run(S, ctx, "text", sketch="Sketch1", content="", height=5, position=[0, 0])


def test_text_unsupported_without_createinput2_or_fit_on_path(S, ctx, sk):
    ln = line_in(sk)
    sk.sketchTexts._missing = frozenset({"createInput2"})
    err("UNSUPPORTED", _box, S, ctx, content="A", height=5, position=[0, 0])
    sk.sketchTexts._missing = frozenset()
    orig = fk.SketchTextInput._missing
    fk.SketchTextInput._missing = frozenset({"setAsFitOnPath"})
    try:
        err("UNSUPPORTED", _box, S, ctx, content="A", height=5, mode="fit_on_path", path=tok(ln))
    finally:
        fk.SketchTextInput._missing = orig
    assert sk._texts == []


# ---- TASK-SK-012: fillet, delete_constraint, delete_dimension -----------------------------------


def _fil(S, ctx, a, b, **kw):
    return run(S, ctx, "fillet", sketch="Sketch1", curve_one=tok(a), curve_two=tok(b), **kw)


def test_fillet_default_ends_pick_closest_pair_and_exact_call(S, ctx, sk):
    a = line_in(sk, (0, 20), (0, 0))
    b = line_in(sk, (0, 0), (20, 0))
    r = _fil(S, ctx, a, b, radius=5)
    c1, p1, c2, p2, rad = sk.called("arcs.addFillet")[0]
    assert (c1, c2) == (a, b) and rad == pytest.approx(0.5)
    assert xyz(p1) == pytest.approx((0, 0)) and xyz(p2) == pytest.approx((0, 0))
    assert p1 is a.endSketchPoint.geometry and p2 is b.startSketchPoint.geometry
    assert r["curves"][0]["type"] == "arc"
    # far ends are chosen when those are the closest pair
    c = line_in(sk, (50, 0), (60, 0))
    d = line_in(sk, (70, 5), (62, 5))
    _fil(S, ctx, c, d, radius=1)
    p1, p2 = sk.called("arcs.addFillet")[1][1], sk.called("arcs.addFillet")[1][3]
    assert xyz(p1) == pytest.approx((60, 0)) and xyz(p2) == pytest.approx((62, 5))


def test_fillet_explicit_points_passed_through(S, ctx, sk):
    a, b = line_in(sk, (0, 20), (0, 0)), line_in(sk, (0, 0), (20, 0))
    _fil(S, ctx, a, b, radius=5, point_one=[0, 10], point_two=[10, 0])
    _, p1, _, p2, _r = sk.called("arcs.addFillet")[0]
    assert xyz(p1) == pytest.approx((0, 1.0)) and xyz(p2) == pytest.approx((1.0, 0))


def test_fillet_misuse_rejected(S, ctx, sk):
    a, b = line_in(sk, (0, 20), (0, 0)), line_in(sk, (0, 0), (20, 0))
    err("INVALID_PARAMS", _fil, S, ctx, a, b, radius=0)
    err("INVALID_PARAMS", _fil, S, ctx, a, a, radius=1)
    err("INVALID_PARAMS", _fil, S, ctx, a, b, radius=1, point_one=[0, 1])
    err("INVALID_PARAMS", _fil, S, ctx, a, b, radius=1, point_two=[0, 1])
    odd = fk.SketchConicCurve(sk)
    sk._curves.append(odd)
    e = err("INVALID_PARAMS", _fil, S, ctx, a, odd, radius=1)
    assert "point_one and point_two are required" in e.message
    assert sk.called("arcs.addFillet") == []


def test_fillet_foreign_curve_rejected(S, ctx, fake_design, sk):
    other = fk.new_sketch(fake_design.rootComponent, name="Sketch2")
    err("INVALID_PARAMS", _fil, S, ctx, line_in(sk), line_in(other), radius=1)


def _two_constraints(sk):
    a = line_in(sk)
    sk.geometricConstraints.addHorizontal(a)
    sk.geometricConstraints.addVertical(line_in(sk, (0, 0), (0, 1)))
    sk.geometricConstraints.addEqual(a, line_in(sk, (3, 3), (4, 3)))
    sk.calls.clear()
    return a


def test_delete_constraint_descending_order_result_and_types(S, ctx, sk):
    _two_constraints(sk)
    gcs = list(sk._gcs)
    r = run(S, ctx, "delete_constraint", sketch="Sketch1", indices=[0, 2], confirm=True)
    assert sk.called("deleteMe") == [(gcs[2],), (gcs[0],)]
    assert r["deleted"] == [{"index": 2, "type": "equal"}, {"index": 0, "type": "horizontal"}]
    assert sk._gcs == [gcs[1]] and r["fully_constrained"] is False


def test_delete_constraint_rejections_delete_nothing(S, ctx, sk):
    _two_constraints(sk)
    err("INVALID_PARAMS", run, S, ctx, "delete_constraint", sketch="Sketch1", indices=[-1], confirm=True)
    sk._gcs[2].isDeletable = False
    for idx in ([0, 0], [3], [0, 2]):
        err("INVALID_PARAMS", run, S, ctx, "delete_constraint", sketch="Sketch1", indices=idx,
            confirm=True)
    assert sk.called("deleteMe") == [] and len(sk._gcs) == 3


def test_delete_constraint_false_return_is_fusion_error(S, ctx, sk):
    _two_constraints(sk)
    sk._gcs[0].delete_ok = False
    err("FUSION_ERROR", run, S, ctx, "delete_constraint", sketch="Sketch1", indices=[0], confirm=True)


def test_delete_dimension_result_types_and_parameter_name(S, ctx, sk):
    c = sk.sketchCurves.sketchCircles.addByCenterRadius(P(0, 0), 1)
    sd = sk.sketchDimensions
    sd.addRadialDimension(c, P(2, 2), True)
    sd.addRadialDimension(c, P(2, 2), False)
    sk.calls.clear()
    dims = list(sk._dims)
    r = run(S, ctx, "delete_dimension", sketch="Sketch1", indices=[0, 1], confirm=True)
    assert sk.called("deleteMe") == [(dims[1],), (dims[0],)]
    assert r["deleted"] == [{"index": 1, "type": "radial", "parameter": None},
                            {"index": 0, "type": "radial", "parameter": dims[0].parameter.name}]
    assert sk._dims == [] and c.radius == 1


def test_delete_dimension_rejections_and_false_return(S, ctx, sk):
    c = sk.sketchCurves.sketchCircles.addByCenterRadius(P(0, 0), 1)
    sk.sketchDimensions.addRadialDimension(c, P(2, 2), True)
    sk._dims[0].isDeletable = False
    err("INVALID_PARAMS", run, S, ctx, "delete_dimension", sketch="Sketch1", indices=[0], confirm=True)
    err("INVALID_PARAMS", run, S, ctx, "delete_dimension", sketch="Sketch1", indices=[1], confirm=True)
    err("INVALID_PARAMS", run, S, ctx, "delete_dimension", sketch="Sketch1", indices=[0, 0], confirm=True)
    sk._dims[0].isDeletable = True
    sk._dims[0].delete_ok = False
    err("FUSION_ERROR", run, S, ctx, "delete_dimension", sketch="Sketch1", indices=[0], confirm=True)


def test_delete_requires_confirm(S, ctx, sk):
    for name in ("delete_constraint", "delete_dimension"):
        assert S.REGISTRY.get("sketch", name).access == "destructive"


def test_constraint_and_dimension_type_helpers(S, sk):
    a = line_in(sk)
    h = sk.geometricConstraints.addHorizontal(a)
    assert S.constraint_type(h) == "horizontal"
    assert S.constraint_type(fk.UnknownConstraint(sk)) == "UnknownConstraint"
    d = sk.sketchDimensions.addDistanceDimension(
        a.startSketchPoint, a.endSketchPoint, fusion.DimensionOrientations.VerticalDimensionOrientation,
        P(0, 1), True)
    assert S.dimension_type(d) == "vertical"
    assert S.dimension_type(fk.UnknownDimension(sk, True, P(0, 0), 1, "cm")) == "UnknownDimension"


# ---- TASK-SK-013: mirror, pattern (reflect) -------------------------------------------------------


def test_reflect_math(S):
    r = S.reflect(P(1, 2), P(0, 0), (1, 0))
    assert xyz(r) == pytest.approx((1, -2))
    h = math.sqrt(0.5)
    assert xyz(S.reflect(P(1, 0), P(0, 0), (h, h))) == pytest.approx((0, 1))
    assert xyz(S.reflect(P(5, 1), P(3, 0), (0, 1))) == pytest.approx((1, 1))
    assert xyz(S.reflect(P(3, 7), P(3, 0), (0, 1))) == pytest.approx((3, 7))


def _mir(S, ctx, curves, line, **kw):
    return run(S, ctx, "mirror", sketch="Sketch1", curves=[tok(c) for c in curves], line=tok(line), **kw)


def _axis(sk, x=0.0):
    return line_in(sk, (x, -1), (x, 1))


def test_mirror_line_acceptance_symmetry_calls(S, ctx, sk):
    axis = _axis(sk)
    ln = line_in(sk, (1, 0), (2, 1))
    sk.calls.clear()
    r = _mir(S, ctx, [ln], axis)
    assert calls(sk, "lines.addByTwoPoints") == [((-1, 0), (-2, 1))]
    sym = sk.called("gc.addSymmetry")
    assert len(sym) == 2 and all(c[2] is axis for c in sym)
    assert (sym[0][0], sym[1][0]) == (ln.startSketchPoint, ln.endSketchPoint)
    assert r["constraints"] == 2 and len(r["curves"]) == 1 and sk.isComputeDeferred is False


def test_mirror_chain_shares_points_and_line_points_reused(S, ctx, sk):
    axis = _axis(sk)
    a = line_in(sk, (0, 0), (1, 1))     # start lies on the axis
    b = sk.sketchCurves.sketchLines.addByTwoPoints(a.endSketchPoint, P(2, 0))
    r = _mir(S, ctx, [a, b], axis)
    na, nb = sk._curves[-2], sk._curves[-1]
    assert na.startSketchPoint is a.startSketchPoint            # on the axis: reused
    assert nb.startSketchPoint is na.endSketchPoint             # chain stays connected
    assert xyz(na.endSketchPoint.geometry) == pytest.approx((-1, 1))
    assert r["constraints"] == 2                                # end of a, end of b; none for the on-axis point


def test_mirror_circle_adds_equal_and_copies_construction(S, ctx, sk):
    axis = _axis(sk, 3)
    c = sk.sketchCurves.sketchCircles.addByCenterRadius(P(1, 2), 0.5)
    c.isConstruction = True
    sk.calls.clear()
    r = _mir(S, ctx, [c], axis)
    assert calls(sk, "circles.addByCenterRadius") == [((5, 2), 0.5)]
    assert len(sk.called("gc.addSymmetry")) == 1 and len(sk.called("gc.addEqual")) == 1
    assert r["constraints"] == 2 and sk._curves[-1].isConstruction is True


def test_mirror_arc_reverses_ends(S, ctx, sk):
    axis = _axis(sk, 3)
    arc = sk.sketchCurves.sketchArcs.addByCenterStartSweep(P(0, 0), P(1, 0), math.pi / 2)
    sk.calls.clear()
    _mir(S, ctx, [arc], axis)
    (s, m, e), = calls(sk, "arcs.addByThreePoints")
    assert s == pytest.approx((6, 1)) and e == pytest.approx((5, 0))
    h = math.sqrt(0.5)
    assert m == pytest.approx((6 - h, h))
    assert xyz(sk._curves[-1].centerSketchPoint.geometry) == pytest.approx((6, 0))


def test_mirror_ellipse_and_splines(S, ctx, sk):
    axis = _axis(sk, 5)
    e = sk.sketchCurves.sketchEllipses.add(P(0, 0), P(2, 0), P(0, 1))
    fit = sk.sketchCurves.sketchFittedSplines.add(core.ObjectCollection([P(0, 5), P(1, 6), P(2, 5)]))
    fit.isClosed = True
    cp = sk.sketchCurves.sketchControlPointSplines.add(
        core.ObjectCollection([P(0, 8), P(1, 9), P(2, 8), P(3, 9)]), 3)
    sk.calls.clear()
    _mir(S, ctx, [e, fit, cp], axis)
    assert calls(sk, "ellipses.add") == [((10, 0), (8, 0), (10, 1))]
    assert [c for p in sk.called("fitted.add")[0][0] for c in xyz(p)] == pytest.approx([10, 5, 9, 6, 8, 5])
    cpa = sk.called("control.add")[0]
    assert [c for p in cpa[0] for c in xyz(p)] == pytest.approx([10, 8, 9, 9, 8, 8, 7, 9]) and cpa[1] == 3
    assert sk._curves[-2].isClosed is True and sk._curves[-1].isClosed is False


def test_mirror_constrain_false_and_rejections(S, ctx, fake_design, sk):
    axis = _axis(sk)
    ln = line_in(sk, (1, 0), (2, 1))
    r = _mir(S, ctx, [ln], axis, constrain=False)
    assert sk.called("gc.addSymmetry") == [] and r["constraints"] == 0
    arc = sk.sketchCurves.sketchArcs.addByCenterStartSweep(P(5, 5), P(6, 5), 1.0)
    conic = fk.SketchConicCurve(sk)
    sk._curves.append(conic)
    n = len(sk._curves)
    err("INVALID_PARAMS", _mir, S, ctx, [ln], arc)
    err("INVALID_PARAMS", _mir, S, ctx, [ln, axis], axis)
    e = err("INVALID_PARAMS", _mir, S, ctx, [ln, conic], axis)
    assert "conic" in e.message
    other = fk.new_sketch(fake_design.rootComponent, name="Sketch2")
    err("INVALID_PARAMS", _mir, S, ctx, [line_in(other)], axis)
    assert len(sk._curves) == n


def _pat(S, ctx, curves, **kw):
    return run(S, ctx, "pattern", sketch="Sketch1", curves=[tok(c) for c in curves], **kw)


def test_pattern_rectangular_matrices_with_direction(S, ctx, sk):
    ln = line_in(sk)
    r = _pat(S, ctx, [ln], type="rectangular", count_x=2, spacing_x=10, count_y=2, spacing_y=20,
             direction=30)
    t = [(m.translation.x, m.translation.y) for _l, m in sk.called("copy")]
    c30, s30 = math.cos(math.radians(30)), math.sin(math.radians(30))
    assert [v for pr in t for v in pr] == pytest.approx(
        [-2 * s30, 2 * c30, c30, s30, c30 - 2 * s30, s30 + 2 * c30])
    assert r["instances"] == 3 and r["curves"] == 3
    assert sk.called("copy")[0][0] == [ln]


def test_pattern_circular_matrices_full_and_partial(S, ctx, sk):
    ln = line_in(sk)
    _pat(S, ctx, [ln], type="circular", center=[10, 20], count=4)
    rots = [m.rotation for _l, m in sk.called("copy")]
    assert [r[0] for r in rots] == pytest.approx([math.pi / 2, math.pi, 3 * math.pi / 2])
    assert rots[0][1] == (0, 0, 1) and rots[0][2] == pytest.approx((1.0, 2.0, 0))
    sk.calls.clear()
    _pat(S, ctx, [ln], type="circular", center=[0, 0], count=4, total_angle=90)
    assert [m.rotation[0] for _l, m in sk.called("copy")] == pytest.approx(
        [math.radians(30), math.radians(60), math.radians(90)])


def test_pattern_parameter_misuse_per_type(S, ctx, sk):
    ln = line_in(sk)
    for kw in (
        dict(type="rectangular"),                                      # 1 x 1
        dict(type="rectangular", count_x=2),                           # spacing_x missing
        dict(type="rectangular", count_y=2),                           # spacing_y missing
        dict(type="rectangular", count_x=2, spacing_x=5, center=[0, 0]),
        dict(type="rectangular", count_x=2, spacing_x=5, count=3),
        dict(type="circular", count=3),                                # center missing
        dict(type="circular", center=[0, 0]),                          # count missing
        dict(type="circular", center=[0, 0], count=3, spacing_x=5),
        dict(type="circular", center=[0, 0], count=3, count_x=2),
        dict(type="circular", center=[0, 0], count=3, total_angle=0),
        dict(type="circular", center=[0, 0], count=3, total_angle=361),
    ):
        err("INVALID_PARAMS", _pat, S, ctx, [ln], **kw)
    assert sk.called("copy") == []


def test_pattern_instance_limit_and_unsupported(S, ctx, sk):
    ln = line_in(sk)
    err("INVALID_PARAMS", _pat, S, ctx, [ln], type="rectangular", count_x=502, spacing_x=1)
    err("INVALID_PARAMS", _pat, S, ctx, [ln], type="circular", center=[0, 0], count=502)
    assert sk.called("copy") == []
    r = _pat(S, ctx, [ln], type="rectangular", count_x=501, spacing_x=1)
    assert r["instances"] == 500
    sk._missing = frozenset({"copy"})
    err("UNSUPPORTED", _pat, S, ctx, [ln], type="circular", center=[0, 0], count=3)


def test_mirror_control_spline_points_are_always_reflected_not_shared(S, ctx, sk):
    axis = _axis(sk, 5)
    cp = sk.sketchCurves.sketchControlPointSplines.add(
        core.ObjectCollection([P(5, 8), P(6, 9), P(7, 8), P(8, 9)]), 3)
    sk.calls.clear()
    _mir(S, ctx, [cp], axis)
    first = sk.called("control.add")[0][0][0]
    assert isinstance(first, core.Point3D) and xyz(first) == pytest.approx((5, 8))


# ---- TASK-SK-014: describe / replay for ellipses, texts and owned curves ----------------------------


def test_describe_ellipse_without_axis_lines(S, ctx, sk):
    sk.sketchCurves.sketchEllipses.add(P(0, 0), P(2, 0), P(0, 1))
    d = S.describe(ctx, sk)
    c = d["curves"][0]
    assert c["type"] == "ellipse" and "replayable" not in c and "owner" not in c
    assert c["major_radius"] == pytest.approx(20.0) and c["minor_radius"] == pytest.approx(10.0)
    assert c["major_axis"] == pytest.approx([1.0, 0.0]) and c["center"] == 1
    assert S.owned_curves(sk) == {}


def test_describe_ellipse_axis_lines_are_owned_with_owned_constraints(S, ctx, sk):
    sk.axis_lines = True
    e = sk.sketchCurves.sketchEllipses.add(P(0, 0), P(2, 0), P(0, 1))
    free = line_in(sk, (5, 5), (6, 5))
    major, minor = sk._curves[0], sk._curves[1]
    sk.geometricConstraints.addHorizontal(major)
    sk.geometricConstraints.addHorizontal(free)
    sk.geometricConstraints.addCoincident(e.centerSketchPoint, free)
    d = S.describe(ctx, sk)
    assert d["curves"][0]["owner"] == {"kind": "ellipse", "index": 2, "role": "major"}
    assert d["curves"][1]["owner"] == {"kind": "ellipse", "index": 2, "role": "minor"}
    assert "owner" not in d["curves"][2] and "owner" not in d["curves"][3]
    owned = [c.get("owned") for c in d["constraints"]]
    assert owned == [True, None, True]
    assert S.owned_curves(sk) == {major.entityToken: {"kind": "ellipse", "index": 2, "role": "major"},
                                  minor.entityToken: {"kind": "ellipse", "index": 2, "role": "minor"}}


def test_describe_each_text_definition_class(S, ctx, fake_design, sk):
    ln = line_in(sk, (0, 0), (10, 0))
    run(S, ctx, "text", sketch="Sketch1", content="AB", height=5, position=[0, 0],
        horizontal_align="center", vertical_align="top", font="Mono")
    run(S, ctx, "text", sketch="Sketch1", content="Hi", height=3, mode="along_path", path=tok(ln),
        above=False, horizontal_align="right")
    run(S, ctx, "text", sketch="Sketch1", content="Fit", height=3, mode="fit_on_path", path=tok(ln))
    box, along, fit = S.describe(ctx, sk)["texts"]
    assert (box["index"], box["mode"], box["content"], box["font"]) == (0, "box", "AB", "Mono")
    assert box["height"] == pytest.approx(5.0) and box["angle"] == pytest.approx(0.0)
    assert box["horizontal_align"] == "center" and box["vertical_align"] == "top"
    assert box["path"] is None and box["above"] is None and "replayable" not in box
    assert box["box"][0] == pytest.approx([0, 0]) and box["box"][1] == pytest.approx([10, 7.5])
    assert along["mode"] == "along_path" and along["path"] == 0 and along["above"] is False
    assert along["horizontal_align"] == "right" and along["vertical_align"] is None
    assert along["box"] is None
    assert fit["mode"] == "fit_on_path" and fit["horizontal_align"] is None and fit["box"] is None
    assert fit["path"] == 0 and fit["above"] is True
    # unknown definition class and a path outside the sketch are not replayable
    sk._texts[1].definition = fk.FitOnPathTextDefinition(
        line_in(fk.new_sketch(fake_design.rootComponent, name="Sketch2")), True)
    sk._texts[2].definition = object.__new__(fk.SketchTextInput)
    t = S.describe(ctx, sk)["texts"]
    assert t[1]["replayable"] is False and t[1]["path"] is None
    assert t[2]["replayable"] is False and t[2]["mode"] is None
    del sk._texts[2].definition
    assert S.describe(ctx, sk)["texts"][2]["replayable"] is False


def test_describe_text_box_corners_of_rotated_text(S, ctx, sk):
    run(S, ctx, "text", sketch="Sketch1", content="Z", height=2, position=[0, 0], box_corner=[40, 10],
        angle=30)
    t = S.describe(ctx, sk)["texts"][0]
    assert t["angle"] == pytest.approx(30.0)
    assert t["box"][0] == pytest.approx([0, 0], abs=1e-9) and t["box"][1] == pytest.approx([40, 10])


def test_describe_box_text_rectangle_lines_are_owned(S, ctx, sk):
    run(S, ctx, "text", sketch="Sketch1", content="AB", height=5, position=[0, 0])
    free = line_in(sk, (20, 20), (21, 20))
    rect_line = sk._texts[0].definition.rectangleLines[2]
    sk.geometricConstraints.addHorizontal(rect_line)
    sk.geometricConstraints.addHorizontal(free)
    d = S.describe(ctx, sk)
    assert [c.get("owner") for c in d["curves"][:4]] == [
        {"kind": "text", "index": 0, "role": k} for k in range(4)]
    assert d["curves"][4].get("owner") is None
    assert [c.get("owned") for c in d["constraints"]] == [True, None]
    assert S.info(ctx, sk, section="texts")["texts"]["total"] == 1


def _replay_target(fake_design, name="Target", axis_lines=False):
    t = fk.new_sketch(fake_design.rootComponent, name=name)
    t.axis_lines = axis_lines
    return t


def test_replay_ellipse_maps_owned_axis_lines_without_drawing_them(S, ctx, fake_design, sk):
    sk.axis_lines = True
    e = sk.sketchCurves.sketchEllipses.add(P(1, 1), P(3, 1), P(1, 2))
    sk.geometricConstraints.addHorizontal(sk._curves[0])        # owned: not replayed
    free = line_in(sk, (5, 5), (6, 5))
    sk.geometricConstraints.addHorizontal(free)
    d = S.describe(ctx, sk)
    new = _replay_target(fake_design, axis_lines=True)
    r = S.replay_into(ctx, new, d)
    assert len(new.called("ellipses.add")) == 1
    assert calls(new, "ellipses.add") == [((1, 1), (3, 1), (1, 2))]
    assert len(new.called("lines.addByTwoPoints")) == 1          # only the free line is drawn
    assert r["constraints"] == 1 and r["skipped"] == []
    assert all(c is not None for c in r["curves"]) and len(r["curves"]) == 4
    ell = [c for c in new._curves if isinstance(c, fk.SketchEllipse)][0]
    assert r["curves"][0]["token"] == ell.majorAxisLine.entityToken
    assert r["curves"][1]["token"] == ell.minorAxisLine.entityToken


def test_replay_ellipse_without_axis_lines_and_unmapped_owned_curve_skipped(S, ctx, fake_design, sk):
    sk.sketchCurves.sketchEllipses.add(P(0, 0), P(2, 0), P(0, 1))
    d = S.describe(ctx, sk)
    d["curves"].append({"index": 1, "type": "line", "start": 0, "end": 0,
                        "owner": {"kind": "ellipse", "index": 0, "role": "major"}})
    new = _replay_target(fake_design)
    r = S.replay_into(ctx, new, d)
    assert r["curves"][1] is None
    assert r["skipped"] == [{"kind": "curve", "index": 1, "reason": "owner not recreated"}]


def test_replay_texts_all_modes_and_unmapped_path_skipped(S, ctx, fake_design, sk):
    ln = line_in(sk, (0, 0), (10, 0))
    run(S, ctx, "text", sketch="Sketch1", content="AB", height=5, position=[0, 0], box_corner=[30, 10],
        angle=30, horizontal_align="center", vertical_align="middle", font="Mono")
    run(S, ctx, "text", sketch="Sketch1", content="Hi", height=3, mode="along_path", path=tok(ln),
        above=False, horizontal_align="right")
    run(S, ctx, "text", sketch="Sketch1", content="Fit", height=3, mode="fit_on_path", path=tok(ln))
    d = S.describe(ctx, sk)
    d["texts"].append(dict(d["texts"][1], index=3, path=99))
    d["texts"].append(dict(d["texts"][0], index=4, replayable=False))
    new = _replay_target(fake_design)
    r = S.replay_into(ctx, new, d)
    assert r["texts"] == 3
    assert r["skipped"] == [{"kind": "text", "index": 3, "reason": "entity skipped"},
                            {"kind": "text", "index": 4, "reason": "text not replayable"}]
    box = new.called("text.setAsMultiLine")[0]
    assert xyz(box[0]) + xyz(box[1]) == pytest.approx((0, 0, 3.0, 1.0))
    assert box[2] is core.HorizontalAlignments.CenterHorizontalAlignment
    assert box[3] is core.VerticalAlignments.MiddleVerticalAlignment
    assert new._texts[0].angle == pytest.approx(math.radians(30)) and new._texts[0].fontName == "Mono"
    along = new.called("text.setAsAlongPath")[0]
    assert along[0] is [c for c in new._curves if isinstance(c, fk.SketchLine)][0]
    assert along[1] is False
    assert along[2] is core.HorizontalAlignments.RightHorizontalAlignment
    assert new.called("text.setAsFitOnPath")[0][1] is True


def test_replay_text_unsupported_is_skipped_with_message(S, ctx, fake_design, sk):
    run(S, ctx, "text", sketch="Sketch1", content="AB", height=5, position=[0, 0])
    d = S.describe(ctx, sk)
    new = _replay_target(fake_design)
    new.sketchTexts._missing = frozenset({"createInput2"})
    r = S.replay_into(ctx, new, d)
    assert r["texts"] == 0
    assert r["skipped"][0]["kind"] == "text" and r["skipped"][0]["reason"].startswith("fusion: ")
    assert "createInput2" in r["skipped"][0]["reason"]


def test_replay_text_box_owned_lines_map_and_constraints_on_them_replay(S, ctx, fake_design, sk):
    run(S, ctx, "text", sketch="Sketch1", content="AB", height=5, position=[0, 0])
    sk.sketchDimensions.addDistanceDimension(
        sk._texts[0].definition.rectangleLines[0].startSketchPoint,
        sk._texts[0].definition.rectangleLines[0].endSketchPoint, 0, P(0, 3), True)
    d = S.describe(ctx, sk)
    assert d["dimensions"][0]["curves"] == [] and len(d["dimensions"][0]["points"]) == 2
    new = _replay_target(fake_design)
    r = S.replay_into(ctx, new, d)
    assert r["texts"] == 1 and r["dimensions"] == 1 and r["skipped"] == []
    rect = new._texts[0].definition.rectangleLines
    assert [c["token"] for c in r["curves"]] == [x.entityToken for x in rect]
    assert len(new.called("lines.addByTwoPoints")) == 0
    dim_pts = new._dims[0].entityOne, new._dims[0].entityTwo
    assert dim_pts == (rect[0].startSketchPoint, rect[0].endSketchPoint)


def test_describe_replay_describe_round_trip_with_texts_and_standalone_point(S, ctx, fake_design, sk):
    sk.axis_lines = True
    c = sk.sketchCurves
    a = c.sketchLines.addByTwoPoints(P(0, 0), P(4, 0))
    c.sketchEllipses.add(P(10, 10), P(12, 10), P(10, 11))
    run(S, ctx, "text", sketch="Sketch1", content="AB", height=5, position=[0, 0], box_corner=[30, 10],
        angle=30)
    run(S, ctx, "text", sketch="Sketch1", content="On", height=3, mode="along_path", path=tok(a))
    sk.sketchPoints.add(P(7, 7))
    sk.geometricConstraints.addHorizontal(a)
    before = S.describe(ctx, sk)
    new = _replay_target(fake_design, axis_lines=True)
    r = S.replay_into(ctx, new, before)
    after = S.describe(ctx, new)

    def strip(d):
        return {k: [{x: y for x, y in i.items() if x != "ref"} for i in v]
                for k, v in d.items() if k in ("points", "curves", "constraints", "dimensions", "texts")}

    sb, sa = strip(before), strip(after)
    assert sa["texts"] == sb["texts"] and sa["curves"] == sb["curves"]
    assert sa["constraints"] == sb["constraints"] and len(sa["points"]) == len(sb["points"])
    assert [p["xy"] for p in sa["points"]] == [p["xy"] for p in sb["points"]]
    assert r["texts"] == 2 and r["skipped"] == []


def test_replay_ellipse_with_diagonal_major_axis_uses_left_normal_for_minor(S, ctx, fake_design, sk):
    sk.sketchCurves.sketchEllipses.add(P(0, 0), P(1, 1), P(-0.5, 0.5))
    d = S.describe(ctx, sk)
    new = _replay_target(fake_design)
    S.replay_into(ctx, new, d)
    (c, m, p), = calls(new, "ellipses.add")
    assert c + m + p == pytest.approx((0, 0, 1, 1, -0.5, 0.5))


# --- live-found Fusion quirks ---------------------------------------------------------------------


def test_reference_plane_is_none_when_fusion_raises(sk_mod):
    class OnFace:
        @property
        def referencePlane(self):
            raise RuntimeError("referencePlane is a BRefFace")

    assert sk_mod.reference_plane(OnFace()) is None


def test_text_angle_falls_back_to_first_box_line_when_getter_raises(sk_mod):
    pt = lambda x, y: pytypes.SimpleNamespace(geometry=pytypes.SimpleNamespace(x=x, y=y))

    class T:
        definition = pytypes.SimpleNamespace(rectangleLines=[
            pytypes.SimpleNamespace(startSketchPoint=pt(0, 0), endSketchPoint=pt(1, 1))])

        @property
        def angle(self):
            raise RuntimeError("InternalValidationError")

    assert sk_mod.text_angle(T()) == pytest.approx(math.pi / 4)
    T.definition = pytypes.SimpleNamespace(rectangleLines=[
        pytypes.SimpleNamespace(startSketchPoint=pt(0, 0), endSketchPoint=pt(1, -1))])
    assert sk_mod.text_angle(T()) == pytest.approx(7 * math.pi / 4)
