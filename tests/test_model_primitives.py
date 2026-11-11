"""Primitives (design § 6): sketch geometry, the extrude/revolve they drive, naming and validation."""
import math

import pytest
from model_fakes import env, env_direct  # noqa: F401

from adsk import fusion

from defusion.errors import INVALID_PARAMS

FO = fusion.FeatureOperations
POS = fusion.ExtentDirections.PositiveExtentDirection


def xyz(p):
    return pytest.approx((p.x, p.y, p.z))


def test_cylinder_geometry_and_extrude(env):
    out = env.call("cylinder", diameter=20, height=30, center=[5, 3])
    sk = env.comp.sketches.item(0)
    (c,) = sk.sketchCurves.sketchCircles.made
    assert xyz(c.center) == (0.5, 0.3, 0) and c.radius == pytest.approx(1.0)
    assert sk.name == "Cylinder sketch" and sk.plane is env.comp.xYConstructionPlane
    inp = env.last("extrudeFeatures")
    assert inp.args == (sk.profiles.item(0), FO.NewBodyFeatureOperation)
    assert inp.calls("setOneSideExtent") == [(("DistanceExtentDefinition", "30 mm"), POS, "0 deg")]
    assert out["sketch"] == env.ctx.ref(sk) and out["feature"]["type"] == "FakeFeature"


def test_cylinder_plane_operation_participants_and_component(env):
    b = env.body("B")
    sub = env.comp.add_occurrence("O").component
    env.call("cylinder", diameter=2, height=1, plane="XZ", operation="join", participant_bodies=[env.r(b)])
    assert env.comp.sketches.item(0).plane is env.comp.xZConstructionPlane
    inp = env.last("extrudeFeatures")
    assert inp.args[1] == FO.JoinFeatureOperation and inp.participantBodies == [b]
    env.call("cylinder", diameter=2, height=1, component=env.r(sub), plane=env.r(env.comp.yZConstructionPlane))
    assert sub.sketches.count == 1 and sub.sketches.item(0).plane is sub.yZConstructionPlane
    assert sub.features.extrudeFeatures.added and not env.comp.features.extrudeFeatures.added[1:]


def test_cylinder_naming_sets_sketch_feature_and_body(env):
    body = env.body("Orig")
    env.coll("extrudeFeatures").next_bodies = [body]
    out = env.call("cylinder", diameter=2, height=1, name="Peg")
    assert env.comp.sketches.item(0).name == "Peg sketch"
    assert env.coll("extrudeFeatures").features[0].name == "Peg" and body.name == "Peg"
    assert out["feature"]["name"] == "Peg" and out["bodies"][0]["name"] == "Peg"


def test_sphere_arc_line_and_revolve(env):
    env.call("sphere", diameter=20, center=[1, 2])
    sk = env.comp.sketches.item(0)
    (arc,) = sk.sketchCurves.sketchArcs.made
    assert xyz(arc.center) == (0.1, 0.2, 0) and xyz(arc.start) == (1.1, 0.2, 0) and arc.sweep == math.pi
    (line,) = sk.sketchCurves.sketchLines.made
    assert line.start is arc.startSketchPoint and line.end is arc.endSketchPoint and not line.isConstruction
    assert sk.name == "Sphere sketch"
    inp = env.last("revolveFeatures")
    assert inp.args == (sk.profiles.item(0), line, FO.NewBodyFeatureOperation)
    assert inp.calls("setAngleExtent") == [(False, "360 deg")]


def test_torus_axis_circle_and_revolve(env):
    env.call("torus", major_diameter=40, minor_diameter=10, center=[1, 2], name="Ring")
    sk = env.comp.sketches.item(0)
    (axis,) = sk.sketchCurves.sketchLines.made
    assert axis.isConstruction is True
    assert xyz(axis.start) == (0.1, 0.2 - 2.0, 0) and xyz(axis.end) == (0.1, 0.2 + 2.0, 0)
    (c,) = sk.sketchCurves.sketchCircles.made
    assert xyz(c.center) == (0.1 + 2.0, 0.2, 0) and c.radius == pytest.approx(0.5)
    inp = env.last("revolveFeatures")
    assert inp.args == (sk.profiles.item(0), axis, FO.NewBodyFeatureOperation)
    assert inp.calls("setAngleExtent") == [(False, "360 deg")]
    assert sk.name == "Ring sketch"


@pytest.mark.parametrize("action,kw,msg", [
    ("cylinder", dict(diameter=0, height=1), "diameter must be > 0"),
    ("cylinder", dict(diameter=1, height=-1), "height must be > 0"),
    ("cylinder", dict(diameter=1, height=1, center=[1]), "center needs 2 values"),
    ("cylinder", dict(diameter=1, height=1, participant_bodies=[]), "participant_bodies applies only"),
    ("sphere", dict(diameter=0), "diameter must be > 0"),
    ("sphere", dict(diameter=1, center=[1, 2, 3]), "center needs 2 values"),
    ("sphere", dict(diameter=1, participant_bodies=[]), "participant_bodies applies only"),
    ("torus", dict(major_diameter=0, minor_diameter=1), "major_diameter must be > 0"),
    ("torus", dict(major_diameter=5, minor_diameter=0), "minor_diameter must be > 0"),
    ("torus", dict(major_diameter=5, minor_diameter=5), "minor_diameter must be < major_diameter"),
    ("torus", dict(major_diameter=5, minor_diameter=6), "minor_diameter must be < major_diameter"),
])
def test_validation_happens_before_any_change(env, action, kw, msg):
    e = env.err(action, **kw)
    assert e.code == INVALID_PARAMS and msg in e.message
    assert env.comp.sketches.count == 0
