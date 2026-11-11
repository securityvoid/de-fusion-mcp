"""Coil action (design § 5): validation, exact text commands, cleanup, naming and the request attribute."""
import json
import math

import pytest
from adsk import core, fusion
from model_fakes import env, env_direct, Env  # noqa: F401

from defusion.errors import FUSION_ERROR, INVALID_PARAMS, TEXT_COMMAND_FAILED, UNSUPPORTED, WRONG_DESIGN_TYPE

BASE = dict(diameter=20, section_size=1)
SIZES = {
    "revolution_height": dict(revolutions=5, height=10),
    "revolution_pitch": dict(revolutions=5, pitch=4),
    "height_pitch": dict(height=10, pitch=4),
    "spiral": dict(revolutions=5, pitch=4),
}
SIZE_CMDS = {
    "revolution_height": ["Commands.SetDouble CoilRevolutions 5.0", "Commands.SetDouble CoilHeight 1.0"],
    "revolution_pitch": ["Commands.SetDouble CoilRevolutions 5.0", "Commands.SetDouble CoilPitch 0.4"],
    "height_pitch": ["Commands.SetDouble CoilHeight 1.0", "Commands.SetDouble CoilPitch 0.4"],
    "spiral": ["Commands.SetDouble CoilRevolutions 5.0", "Commands.SetDouble CoilPitch 0.4"],
}
SIZE_TYPE = {"revolution_height": "infoRevolutionAndHeight", "revolution_pitch": "infoRevolutionAndPitch",
             "height_pitch": "infoHeightAndPitch", "spiral": "infoSpiral"}


def sent(env):
    cmds = env.app.commands
    return cmds[cmds.index("Commands.Start Coil"):]


@pytest.mark.parametrize("coil_type", list(SIZES))
def test_exact_command_sequence_per_coil_type(env, coil_type):
    env.call("coil", coil_type=coil_type, **BASE, **SIZES[coil_type])
    expected = (["Commands.Start Coil",
                 "Commands.SetString infoSizeType " + SIZE_TYPE[coil_type],
                 "Commands.SetString infoSectionType infoCircular",
                 "Commands.SetString infoSectionPosition infoOnCenter",
                 "Commands.SetDouble SectionSize 0.1"]
                + SIZE_CMDS[coil_type]
                + ([] if coil_type == "spiral" else ["Commands.SetDouble CoilTaperAngle 0.0"])
                + ["Commands.SetString infoBooleanType infoNewBodyType", "NuCommands.CommitCmd"])
    assert sent(env) == expected


@pytest.mark.parametrize("section,spelled", [
    ("circular", "infoCircular"), ("square", "infoRectanglar"),
    ("triangular_external", "infoTrianglar"), ("triangular_internal", "infoTrianglar2")])
def test_section_commands(env, section, spelled):
    env.call("coil", **BASE, **SIZES["revolution_height"], section=section)
    assert "Commands.SetString infoSectionType " + spelled in sent(env)


@pytest.mark.parametrize("pos,spelled", [("inside", "infoInside"), ("on_center", "infoOnCenter"),
                                         ("outside", "infoOutside")])
def test_section_position_commands(env, pos, spelled):
    env.call("coil", **BASE, **SIZES["revolution_height"], section_position=pos)
    assert "Commands.SetString infoSectionPosition " + spelled in sent(env)


@pytest.mark.parametrize("op,spelled", [("new_body", "infoNewBodyType"), ("join", "infoJoinType"),
                                        ("cut", "infoCutType"), ("intersect", "infoIntersectType")])
def test_operation_commands(env, op, spelled):
    env.call("coil", **BASE, **SIZES["revolution_height"], operation=op)
    assert "Commands.SetString infoBooleanType " + spelled in sent(env)


def test_taper_and_clockwise_commands(env):
    env.call("coil", **BASE, **SIZES["revolution_height"], taper_angle=5, clockwise=True)
    cmds = sent(env)
    assert "Commands.SetDouble CoilTaperAngle %r" % (5 * (math.pi / 180)) in cmds
    assert cmds[-2:] == ["Commands.SetBool CoilFlipRotation 1", "NuCommands.CommitCmd"]
    n = len(env.app.commands)
    env.call("coil", **BASE, **SIZES["revolution_height"])
    assert not any("CoilFlipRotation" in c for c in env.app.commands[n:])


def test_spiral_sends_no_taper_command(env):
    env.call("coil", **BASE, **SIZES["spiral"], coil_type="spiral")
    assert not any("CoilTaperAngle" in c for c in env.app.commands)


def test_section_size_is_sent_before_size_inputs(env):
    env.call("coil", **BASE, **SIZES["revolution_height"])
    cmds = sent(env)
    assert cmds.index("Commands.SetDouble SectionSize 0.1") < cmds.index("Commands.SetDouble CoilRevolutions 5.0")


def test_sketch_circle_selection_and_result(env):
    out = env.call("coil", diameter=20, center=[5, 3], plane="XZ", name="Spring", **SIZES["revolution_height"])
    sk = env.comp.sketches.item(0)
    assert sk.name == "Spring base" and sk.plane is env.comp.xZConstructionPlane
    (circle,) = sk.sketchCurves.sketchCircles.made
    assert (circle.center.x, circle.center.y, circle.center.z) == pytest.approx((0.5, 0.3, 0)) and circle.radius == 1.0
    assert env.start_selection == [circle]
    coil = env.comp.features.coilFeatures.item(0)
    assert coil.name == "Spring"
    assert out == {"feature": env.ctx.ref(coil), "bodies": [], "sketch": env.ctx.ref(sk), "parameters": []}


def test_default_sketch_name_and_plane(env):
    env.call("coil", **BASE, **SIZES["revolution_height"])
    sk = env.comp.sketches.item(0)
    assert sk.name == "Coil base" and sk.plane is env.comp.xYConstructionPlane


# --- validation ------------------------------------------------------------------------------------


def untouched(env):
    assert env.app.commands == [] and env.comp.sketches.count == 0 and env.terminated == 0


@pytest.mark.parametrize("coil_type,kw,msg", [
    ("revolution_height", dict(revolutions=5), "height is required for coil_type revolution_height"),
    ("revolution_height", dict(revolutions=5, height=10, pitch=4), "pitch is not used by coil_type revolution_height"),
    ("revolution_pitch", dict(revolutions=5, pitch=4, height=10), "height is not used by coil_type revolution_pitch"),
    ("height_pitch", dict(height=10, pitch=4, revolutions=3), "revolutions is not used by coil_type height_pitch"),
    ("height_pitch", dict(pitch=4), "height is required for coil_type height_pitch"),
    ("spiral", dict(revolutions=5), "pitch is required for coil_type spiral"),
    ("spiral", dict(revolutions=5, pitch=4, height=1), "height is not used by coil_type spiral"),
])
def test_size_inputs_per_type(env, coil_type, kw, msg):
    e = env.err("coil", coil_type=coil_type, **BASE, **kw)
    assert e.code == INVALID_PARAMS and e.message == msg
    untouched(env)


@pytest.mark.parametrize("kw,msg", [
    (dict(diameter=0), "diameter must be > 0"),
    (dict(section_size=0), "section_size must be > 0"),
    (dict(revolutions=0), "revolutions must be > 0"),
    (dict(height=-1), "height must be > 0"),
    (dict(center=[1]), "center needs 2 values"),
    (dict(taper_angle=90), "taper_angle must be between -90 and 90 deg"),
    (dict(taper_angle=-90), "taper_angle must be between -90 and 90 deg"),
])
def test_value_validation(env, kw, msg):
    args = {**BASE, **SIZES["revolution_height"], **kw}
    e = env.err("coil", **args)
    assert e.code == INVALID_PARAMS and msg in e.message
    untouched(env)


def test_section_size_must_not_exceed_pitch(env):
    e = env.err("coil", diameter=20, section_size=5, coil_type="revolution_pitch", **SIZES["revolution_pitch"])
    assert e.message == "section_size must not exceed pitch"
    env.call("coil", diameter=20, section_size=4, coil_type="revolution_pitch", **SIZES["revolution_pitch"])  # equal is allowed


def test_spiral_taper_rejected(env):
    e = env.err("coil", coil_type="spiral", taper_angle=5, **BASE, **SIZES["spiral"])
    assert e.message == "spiral coils have no taper"
    untouched(env)


def test_direct_design_rejected_before_any_change(env_direct):
    e = env_direct.err("coil", **BASE, **SIZES["revolution_height"])
    assert e.code == WRONG_DESIGN_TYPE
    assert env_direct.app.commands == [] and env_direct.comp.sketches.count == 0


def test_participant_bodies_rules(env, monkeypatch):
    b = env.body("B")
    e = env.err("coil", **BASE, **SIZES["revolution_height"], participant_bodies=[env.r(b)])
    assert e.code == INVALID_PARAMS
    monkeypatch.delattr(fusion.CoilFeature, "participantBodies")
    e = env.err("coil", **BASE, **SIZES["revolution_height"], participant_bodies=[env.r(b)], operation="join")
    assert e.code == UNSUPPORTED
    untouched(env)


# --- failure and cleanup ---------------------------------------------------------------------------


def test_text_command_failure_terminates_and_deletes_sketch(env):
    env.app.text_command_errors["CoilPitch"] = RuntimeError("bad")
    e = env.err("coil", coil_type="revolution_pitch", **BASE, **SIZES["revolution_pitch"])
    assert e.code == TEXT_COMMAND_FAILED
    assert env.terminated == 1 and env.comp.sketches.item(0).deleted
    assert env.comp.features.coilFeatures.count == 0


def test_cleanup_ignores_cleanup_exceptions(env):
    env.app.text_command_errors["Start Coil"] = RuntimeError("no")
    orig_add = env.comp.sketches.__class__.add

    def add(self, plane):
        sk = orig_add(self, plane)
        sk.delete_error = RuntimeError("cannot delete")
        return sk

    env.comp.sketches.__class__.add = add
    try:
        env._terminate = None
        env.app.userInterface.terminateActiveCommand = lambda: (_ for _ in ()).throw(RuntimeError("t"))
        e = env.err("coil", **BASE, **SIZES["revolution_height"])
    finally:
        env.comp.sketches.__class__.add = orig_add
    assert e.code == TEXT_COMMAND_FAILED


def test_other_exception_becomes_fusion_error(env):
    env.app.userInterface.activeSelections.add = lambda e: (_ for _ in ()).throw(RuntimeError("selection broke"))
    e = env.err("coil", **BASE, **SIZES["revolution_height"])
    assert e.code == FUSION_ERROR and e.message == "coil not created: selection broke"
    assert env.terminated == 1 and env.comp.sketches.item(0).deleted


def test_count_not_increased_is_a_failure(env):
    env.commit_makes_coil = False
    e = env.err("coil", **BASE, **SIZES["revolution_height"])
    assert e.code == FUSION_ERROR and e.message == "coil not created"
    assert env.terminated == 1 and env.comp.sketches.item(0).deleted


# --- participants, components, request attribute ----------------------------------------------------


def test_participant_bodies_roll_then_restore(env):
    b = env.body("B")
    env.call("coil", **BASE, **SIZES["revolution_height"], operation="join", participant_bodies=[env.r(b)])
    assert env.log == [("rollTo", True), ("participantBodies", [b]), ("moveToEnd",)]


def test_no_roll_without_participant_bodies(env):
    env.call("coil", **BASE, **SIZES["revolution_height"], operation="join")
    assert env.log == []


def test_component_is_activated_and_restored(env):
    occ = env.comp.add_occurrence("O")
    sub = occ.component
    out = env.call("coil", **BASE, **SIZES["revolution_height"], component=env.r(sub))
    assert sub.sketches.count == 1 and env.comp.sketches.count == 0
    assert sub.features.coilFeatures.count == 1 and out["feature"]["component"] == sub.name
    assert env.design.activeComponent is env.comp
    plane = sub.sketches.item(0).plane
    assert plane is sub.xYConstructionPlane  # default origin plane of the target component


def test_origin_plane_of_other_component_is_mapped(env):
    sub = env.comp.add_occurrence("O").component
    env.call("coil", **BASE, **SIZES["revolution_height"], component=env.r(sub), plane=env.r(env.comp.yZConstructionPlane))
    assert sub.sketches.item(0).plane is sub.yZConstructionPlane


def request_of(env, feat):
    return json.loads(feat.attributes.itemByName("defusion", "request").value)


def test_request_attribute_exact_dict(env):
    b = env.body("B")
    env.call("coil", diameter=20, center=[1, "d1"], plane="XZ", coil_type="revolution_pitch", revolutions=5,
             pitch="4 mm", taper_angle="5 deg", section="square", section_position="inside",
             section_size="1.5 mm", clockwise=True, operation="join", participant_bodies=[env.r(b)], name="Spring") if False else None
    env.design.add_parameter("d1", 0.2)
    env.call("coil", diameter=20, center=[1, "d1"], plane="XZ", coil_type="revolution_pitch", revolutions=5,
             pitch="4 mm", taper_angle="5 deg", section="square", section_position="inside",
             section_size="1.5 mm", clockwise=True, operation="join", participant_bodies=[env.r(b)], name="Spring")
    feat = env.comp.features.coilFeatures.item(0)
    assert request_of(env, feat) == {
        "format": "defusion.request/1", "tool": "model", "action": "coil",
        "params": {
            "plane": "XZ", "center": ["1 mm", "d1"], "diameter": "20 mm", "coil_type": "revolution_pitch",
            "revolutions": "5", "height": None, "pitch": "4 mm", "taper_angle": "5 deg", "section": "square",
            "section_position": "inside", "section_size": "1.5 mm", "clockwise": True, "operation": "join",
            "participant_bodies": [env.ctx.ref(b)], "component": None, "name": "Spring"}}


def test_request_attribute_defaults_and_refs(env):
    sub = env.comp.add_occurrence("O").component
    cp = sub.add_construction_plane("Mid")
    env.call("coil", diameter=20, plane=env.r(cp), component=env.r(sub), **SIZES["revolution_height"])
    req = request_of(env, sub.features.coilFeatures.item(0))["params"]
    assert req["plane"] == env.ctx.ref(cp) and req["component"] == env.ctx.ref(sub)
    assert req["center"] == ["0 mm", "0 mm"] and req["taper_angle"] == "0 deg" and req["section_size"] == "1 mm"
    assert req["height"] == "10 mm" and req["revolutions"] == "5" and req["pitch"] is None
    assert req["participant_bodies"] is None and req["name"] is None and req["clockwise"] is False
    assert req["plane"] == {"type": "ConstructionPlane", "name": "Mid", "token": cp.entityToken, "component": sub.name}


def test_request_attribute_is_replayable(env):
    env.call("coil", diameter=20, coil_type="spiral", revolutions=5, pitch=4, section_size=1)
    params = request_of(env, env.comp.features.coilFeatures.item(0))["params"]
    env.call("coil", **params)
    assert env.comp.features.coilFeatures.count == 2


def test_parameters_are_those_created_by_the_coil(env):
    def hook(_orig=env.design._timeline):
        pass

    coil_cmd = env.app.executeTextCommand

    def with_params(cmd):
        out = coil_cmd(cmd)
        if cmd == "NuCommands.CommitCmd":
            feat = env.comp.features.coilFeatures.item(0)
            mine = [("d12", "20 mm", "Diameter"), ("d13", "5", "Revolutions")]
            for n, ex, role in mine:
                p = env.design.add_parameter(n, 1.0, user=False)
                p.createdBy, p.expression, p.role = feat, ex, role
            other = env.design.add_parameter("d99", 1.0, user=False)
            other.createdBy, other.expression, other.role = env.comp.add_sketch("X"), "1 mm", "Other"
            user = env.design.add_parameter("u1", 1.0, user=True)
            user.createdBy, user.expression, user.role = feat, "1", "Nope"
        return out

    env.app.executeTextCommand = with_params
    out = env.call("coil", **BASE, **SIZES["revolution_height"])
    assert out["parameters"] == [{"name": "d12", "expression": "20 mm", "role": "Diameter"},
                                 {"name": "d13", "expression": "5", "role": "Revolutions"}]
