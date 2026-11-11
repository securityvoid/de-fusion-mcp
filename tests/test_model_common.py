"""Tests for the shared model helpers (design § 3)."""
import pytest
from adsk import core, fusion
from model_fakes import Env, env, plane_geo  # noqa: F401

from defusion.errors import INVALID_PARAMS, UNSUPPORTED
from defusion.tools import model_common as mc
from defusion.types import Value
from helpers import raises_action


def test_coll_keeps_order(env):
    a, b = env.body("A"), env.body("B")
    assert list(mc.coll([b, a])) == [b, a]


def test_owner_variants(env):
    sub = env.comp.add_occurrence("O").component
    body = env.body("B", comp=sub, faces=1, edges=1)
    sk, prof = env.new_profile(sub)
    pt = sk.add_point()
    curve = sk.add_line()
    assert mc.owner(body) is sub
    assert mc.owner(body.faces.item(0)) is sub and mc.owner(body.edges.item(0)) is sub
    assert mc.owner(prof) is sub and mc.owner(pt) is sub and mc.owner(curve) is sub
    # real construction planes and axes have `.component`, not parentComponent
    assert mc.owner(env.comp.xYConstructionPlane) is env.comp
    assert mc.owner(type("Plane", (), {"parent": sub})()) is sub


def test_same_owner_rejects_two_components(env):
    sub = env.comp.add_occurrence("O").component
    a, b = env.body("A"), env.body("B", comp=sub)
    assert mc.same_owner(env.ctx, [a, env.body("C")], "bodies") is env.comp
    e = raises_action(mc.same_owner, env.ctx, [a, b], "bodies")
    assert e.code == INVALID_PARAMS and e.message == "all bodies must belong to one component"


def test_kind(env):
    body = env.body("B", faces=1)
    feat = env.comp.add_feature("F")
    feat.healthState = 0
    sk = env.comp.add_sketch("S")
    assert mc.kind(body) == "BRepBody" and mc.kind(body.faces.item(0)) == "BRepFace"
    assert mc.kind(feat) == "Feature"
    assert mc.kind(sk) == "Sketch"  # timelineObject but no healthState
    plane = env.comp.add_construction_plane("P")
    plane.healthState = 0  # real construction planes have a timeline object and a health state
    assert mc.kind(plane) == "ConstructionPlane"


def test_expect(env):
    body = env.body("B", faces=1)
    sk, _ = env.new_profile()
    line = sk.add_line()
    mc.expect(env.ctx, [body], ("BRepBody",), "x")
    mc.expect(env.ctx, [line], ("SketchCurve", "BRepEdge"), "path")
    e = raises_action(mc.expect, env.ctx, [body.faces.item(0)], ("BRepBody",), "x")
    assert e.code == INVALID_PARAMS and "x accepts BRepBody; got BRepFace" in e.message
    assert raises_action(mc.expect, env.ctx, [sk.add_point()], ("SketchCurve",), "path").code == INVALID_PARAMS


def test_uniform(env):
    body = env.body("B", faces=1)
    feat = env.comp.add_feature("F")
    feat.healthState = 0
    groups = ("bodies", "features")
    assert mc.uniform(env.ctx, [body], groups, "entities") == "bodies"
    assert mc.uniform(env.ctx, [feat], groups, "entities") == "features"
    assert raises_action(mc.uniform, env.ctx, [body, feat], groups, "entities").code == INVALID_PARAMS
    assert raises_action(mc.uniform, env.ctx, [body.faces.item(0)], groups, "entities").code == INVALID_PARAMS


def test_require_api(env):
    mc.require_api(env.ctx, env.comp, "features", "x")
    e = raises_action(mc.require_api, env.ctx, env.comp, "nope", "Widget.nope")
    assert e.code == UNSUPPORTED and e.message == "Widget.nope is not available in this Fusion build"


def test_participants(env):
    inp = env.coll("extrudeFeatures").createInput(None, 0)
    mc.participants(env.ctx, inp, None)
    assert inp.sets("participantBodies") == []
    b = env.body("B")
    mc.participants(env.ctx, inp, (b,))
    assert inp.participantBodies == [b]
    env.coll("extrudeFeatures").hide_input("participantBodies")
    inp2 = env.coll("extrudeFeatures").createInput(None, 0)
    assert raises_action(mc.participants, env.ctx, inp2, [b]).code == UNSUPPORTED


def test_positive(env):
    mc.positive(env.ctx, Value("1 mm", 0.1, "length"), "d")
    for v in (Value("0 mm", 0, "length"), Value("-1 deg", -0.1, "angle"), Value("0", 0.0, "expr")):
        e = raises_action(mc.positive, env.ctx, v, "d")
        assert e.code == INVALID_PARAMS and e.message == "d must be > 0"


def test_origin_plane(env):
    sub = env.comp.add_occurrence("O").component
    assert mc.origin_plane(env.comp, env.comp.xZConstructionPlane) is env.comp.xZConstructionPlane
    assert mc.origin_plane(sub, env.comp.xZConstructionPlane) is sub.xZConstructionPlane
    assert mc.origin_plane(sub, env.comp.yZConstructionPlane) is sub.yZConstructionPlane
    other = env.comp.add_construction_plane("P")
    assert mc.origin_plane(sub, other) is other


def test_activated_switches_and_restores(env):
    occ = env.comp.add_occurrence("O")
    with mc.activated(env.ctx, occ.component):
        assert env.design.activeComponent is occ.component
    assert env.design.activeComponent is env.comp and env.design.activeOccurrence is None


def test_activated_restores_previous_occurrence_on_exception(env):
    a, b = env.comp.add_occurrence("A"), env.comp.add_occurrence("B")
    a.activate()
    with pytest.raises(RuntimeError):
        with mc.activated(env.ctx, b.component):
            assert env.design.activeComponent is b.component
            raise RuntimeError("boom")
    assert env.design.activeOccurrence is a and env.design.activeComponent is a.component


def test_activated_root_and_noop(env):
    a = env.comp.add_occurrence("A")
    a.activate()
    with mc.activated(env.ctx, env.comp):
        assert env.design.activeComponent is env.comp
    assert env.design.activeOccurrence is a
    with mc.activated(env.ctx, a.component):
        assert env.design.activeOccurrence is a


def test_activated_without_occurrence(env):
    orphan = fusion.Component("Orphan", env.design)
    e = raises_action(lambda: mc.activated(env.ctx, orphan).__enter__())
    assert e.code == INVALID_PARAMS and e.message == "component has no occurrence"


def test_finish_result_and_name(env):
    feat = env.coll("extrudeFeatures").add(None)
    b = env.body("Out")
    feat.bodies = [b]
    out = mc.finish(env.ctx, feat, "My", sketch="s")
    assert feat.name == "My"
    assert out == {"feature": env.ctx.ref(feat), "bodies": [env.ctx.ref(b)], "sketch": "s"}
    assert mc.finish(env.ctx, feat, None)["feature"]["name"] == "My"


def test_cells_hint(env):
    class Cell:
        def __init__(self, lo, hi):
            self.cellBody = type("B", (), {"boundingBox": core.BoundingBox3D(core.Point3D(*lo), core.Point3D(*hi))})()

    cells = core.Collection([Cell((0, 0, 0), (2, 2, 2)), Cell((1, 0, 0), (3, 4, 6))])
    assert mc.cells_hint(env.ctx, cells) == [
        "cell 0: center [10.0, 10.0, 10.0]", "cell 1: center [20.0, 20.0, 30.0]"]


def test_val_and_vi_handle_resolved_values_and_raw_defaults(env):
    v = Value("5 mm", 0.5, "length")
    assert mc.val(env.ctx, v, "length") is v
    assert mc.val(env.ctx, 0, "angle").expr == "0 deg"
    assert mc.val(env.ctx, "2 in", "length").number == pytest.approx(5.08)
    assert mc.val(env.ctx, 3, "expr").expr == "3"
    assert mc.vi(env.ctx, v).value == "5 mm"
    assert mc.vi(env.ctx, 0, "angle").value == "0 deg"
