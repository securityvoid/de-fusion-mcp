"""Design settings and destructive actions (design § 12, § 13)."""
import pytest
from model_fakes import Geo, env, env_direct  # noqa: F401

from adsk import core, fusion

from defusion.errors import CONFIRM_REQUIRED, INVALID_PARAMS, UNSUPPORTED

DU = fusion.DistanceUnits
DT = fusion.DesignTypes


# --- units -----------------------------------------------------------------------------------


def test_units_set_then_get_round_trip(env):
    env.design.fusionUnitsManager = Geo(distanceDisplayUnits=DU.MillimeterDistanceUnits)
    assert env.call("units_get") == {"default_length_units": "mm", "display_length_units": "mm",
                                     "output_units": {"length": "mm", "angle": "deg"}}
    for unit, member in (("in", "InchDistanceUnits"), ("ft", "FootDistanceUnits"), ("m", "MeterDistanceUnits"),
                         ("cm", "CentimeterDistanceUnits"), ("mm", "MillimeterDistanceUnits")):
        assert env.call("units_set", length=unit) == {"display_length_units": unit}
        assert env.design.fusionUnitsManager.distanceDisplayUnits == getattr(DU, member)
        assert env.call("units_get")["display_length_units"] == unit


def test_units_get_unknown_display_unit_is_null(env):
    env.design.fusionUnitsManager = Geo(distanceDisplayUnits="weird")
    assert env.call("units_get")["display_length_units"] is None
    assert env.err("units_set", length="yd").code == INVALID_PARAMS


# --- configurations --------------------------------------------------------------------------


def table(rows=2):
    def mk(i):
        return Geo(id="r%d" % i, name="Row %d" % i)

    rs = core.Collection([mk(i) for i in range(rows)])
    cols = core.Collection([Geo(title="Size", objectType="adsk::fusion::ConfigurationThemeColumn"),
                            Geo(title="Qty", objectType="adsk::fusion::ConfigurationParameterColumn")])
    return Geo(rows=rs, columns=cols, activeRow=rs.item(1))


def test_config_inspect_unsupported_unconfigured_and_configured(env):
    e = env.err("config_inspect")
    assert e.code == UNSUPPORTED and "Design.configurationTopTable is not available" in e.message
    env.design.configurationTopTable = None
    assert env.call("config_inspect") == {"configured": False, "active_row": None, "columns": [], "items": [],
                                          "total": 0, "offset": 0, "next_offset": None}
    env.design.configurationTopTable = table(3)
    out = env.call("config_inspect", limit=2)
    assert out["configured"] is True and out["active_row"] == {"id": "r1", "name": "Row 1"}
    assert out["columns"] == [{"title": "Size", "type": "ConfigurationThemeColumn"},
                              {"title": "Qty", "type": "ConfigurationParameterColumn"}]
    assert out["items"] == [{"id": "r0", "name": "Row 0"}, {"id": "r1", "name": "Row 1"}]
    assert out["total"] == 3 and out["next_offset"] == 2


def test_create_configured_design(env):
    assert env.err("create_configured_design").code == UNSUPPORTED
    made = []
    env.design.createConfiguredDesign = lambda: made.append(1) or table(1)
    env.design.isConfiguredDesign = False
    assert env.call("create_configured_design") == {"configured": True, "rows": 1} and made == [1]
    env.design.isConfiguredDesign = True
    e = env.err("create_configured_design")
    assert e.code == INVALID_PARAMS and e.message == "design is already configured" and made == [1]


# --- attributes and tokens -------------------------------------------------------------------


def test_find_attributes_shape_truncation_and_paging(env):
    b = env.body("B")
    a1 = core.Attribute("defusion", "request", "x" * 2500)
    a1.parent = b
    a2 = core.Attribute("defusion", "other", "short")
    a2.parent = None
    seen = []
    env.design.findAttributes = lambda g, n: seen.append((g, n)) or [a1, a2]
    out = env.call("find_attributes")
    assert seen == [("defusion", "")]
    assert out["items"][0] == {"group": "defusion", "name": "request", "value": "x" * 2000, "truncated": True,
                               "parent": env.ctx.ref(b)}
    assert out["items"][1] == {"group": "defusion", "name": "other", "value": "short", "parent": None}
    env.call("find_attributes", group="g", attribute="n", offset=1, limit=1)
    assert seen[-1] == ("g", "n")
    exact = core.Attribute("defusion", "k", "y" * 2000)
    exact.parent = None
    env.design.findAttributes = lambda g, n: [exact]
    assert "truncated" not in env.call("find_attributes")["items"][0]


def test_find_by_token(env):
    b = env.body("B")
    assert env.call("find_by_token", token=b.entityToken) == {"entities": [env.ctx.ref(b)]}
    assert env.call("find_by_token", token="nope") == {"entities": []}


# --- visibility ------------------------------------------------------------------------------


def test_visibility_set_and_get_targets(env):
    b = env.body("B")
    b.isLightBulbOn = True
    face = env.body("F", faces=1).faces.item(0)
    face.isVisible = True  # a face has isVisible only
    out = env.call("visibility_set", targets=[env.r(b), env.r(face)], visible=False)
    assert b.isLightBulbOn is False and face.isVisible is False
    assert out["entities"][0] == {"target": env.ctx.ref(b), "light_bulb": False, "visible": b.isVisible}
    assert out["entities"][1]["light_bulb"] is None and out["entities"][1]["visible"] is False
    assert env.call("visibility_get", targets=[env.r(b)])["entities"][0]["light_bulb"] is False


def test_visibility_prefers_light_bulb_over_is_visible(env):
    b = env.body("B")
    b.isLightBulbOn, b.isVisible = True, True
    env.call("visibility_set", targets=[env.r(b)], visible=False)
    assert b.isLightBulbOn is False and b.isVisible is True


def test_visibility_set_target_without_visibility_changes_nothing(env):
    ok = env.body("OK")
    ok.isLightBulbOn = True
    sk = env.comp.add_sketch("S").add_point()
    for attr in ("isLightBulbOn", "isVisible"):
        if hasattr(sk, attr):
            delattr(sk, attr)
    e = env.err("visibility_set", targets=[env.r(ok), env.r(sk)], visible=False)
    assert e.code == INVALID_PARAMS and "has no visibility to set" in e.message
    assert ok.isLightBulbOn is True


def test_visibility_flags_get_set_and_unknown(env):
    env.design.objectVisibility = Geo(isOriginVisible=True, isSketchesVisible=False, name="x", count=3, isDepth=3)
    assert env.call("visibility_get") == {"flags": {"isOriginVisible": True, "isSketchesVisible": False}}
    out = env.call("visibility_set", flag="isSketchesVisible", value=True)
    assert out == {"flags": {"isOriginVisible": True, "isSketchesVisible": True}}
    e = env.err("visibility_set", flag="isBogus", value=True)
    assert e.code == INVALID_PARAMS and e.hints == ["isOriginVisible", "isSketchesVisible"]


def test_visibility_flags_unsupported_without_object_visibility(env):
    assert env.err("visibility_get").code == UNSUPPORTED


@pytest.mark.parametrize("kw", [{}, {"visible": True}, {"flag": "isOriginVisible"}, {"value": True},
                                {"flag": "isOriginVisible", "value": True, "visible": True},
                                {"flag": "isOriginVisible", "value": True, "targets": []},
                                {"targets": []}])
def test_visibility_set_requires_exactly_one_form(env, kw):
    env.design.objectVisibility = Geo(isOriginVisible=True)
    e = env.err("visibility_set", **kw)
    assert e.code == INVALID_PARAMS and e.message == "give exactly one of (targets and visible) or (flag and value)"


# --- selection sets --------------------------------------------------------------------------


def test_selection_sets(env):
    assert env.err("selection_sets_list").code == UNSUPPORTED
    sets = []

    class Sets(list):
        def add(self, ents, name):
            s = Geo(name=name, entities=ents)
            self.append(s)
            sets.append((ents, name))
            return s

    env.design.selectionSets = Sets()
    b1, b2 = env.body("B1"), env.body("B2")
    assert env.call("selection_set_create", name="S", entities=[env.r(b1), env.r(b2)]) == {"name": "S", "count": 2}
    assert sets == [([b1, b2], "S")]
    out = env.call("selection_sets_list")
    assert out["items"] == [{"name": "S", "count": 2, "entities": [env.ctx.ref(b1), env.ctx.ref(b2)]}]


# --- design type / intent --------------------------------------------------------------------


def test_set_design_type_needs_confirm_and_reports_changes(env):
    assert env.err("set_design_type", design_type="direct").code == CONFIRM_REQUIRED
    assert env.design.designType == DT.ParametricDesignType
    assert env.call("set_design_type", design_type="direct", confirm=True) == {"design_type": "direct", "changed": True}
    assert env.design.designType == DT.DirectDesignType
    assert env.call("set_design_type", design_type="direct", confirm=True)["changed"] is False
    assert env.call("set_design_type", design_type="parametric", confirm=True)["changed"] is True
    assert env.design.designType == DT.ParametricDesignType


def test_set_design_type_same_type_assigns_nothing(env):
    writes = []
    cls = type(env.design)

    class Recording(cls):
        designType = property(lambda s: s.__dict__["_t"], lambda s, v: (writes.append(v), s.__dict__.update(_t=v)))

    t = env.design.designType
    env.design.__class__ = Recording
    env.design.__dict__["_t"] = t
    env.call("set_design_type", design_type="parametric", confirm=True)
    assert writes == []
    env.call("set_design_type", design_type="direct", confirm=True)
    assert writes == [DT.DirectDesignType]


def test_set_design_intent(env, monkeypatch):
    assert env.err("set_design_intent", intent="part").code == UNSUPPORTED
    env.design.designIntent = fusion.DesignIntentTypes.PartDesignIntentType
    assert env.call("set_design_intent", intent="part") == {"design_intent": "part", "changed": False}
    assert env.call("set_design_intent", intent="assembly") == {"design_intent": "assembly", "changed": True}
    assert env.design.designIntent == fusion.DesignIntentTypes.AssemblyDesignIntentType
    monkeypatch.delattr(fusion, "DesignIntentTypes")
    assert env.err("set_design_intent", intent="hybrid").code == UNSUPPORTED


# --- root, recompute, assembly modeling ------------------------------------------------------


def test_activate_root(env):
    occ = env.comp.add_occurrence("O")
    env.design.activeComponent, env.design.activeOccurrence = occ.component, occ
    assert env.call("activate_root") == {"active_component": env.ctx.ref(env.comp)}
    assert env.design.activeComponent is env.comp


def test_compute_all_lists_only_unhealthy_items(env):
    ran = []
    env.design.computeAll = lambda: ran.append(1)
    healthy, warn, err = env.feature("H"), env.feature("W"), env.feature("E")
    warn.timelineObject.healthState = fusion.FeatureHealthStates.WarningFeatureHealthState
    err.timelineObject.healthState = fusion.FeatureHealthStates.ErrorFeatureHealthState
    out = env.call("compute_all")
    assert ran == [1]
    assert out == {"timeline_health": [
        {"item": env.ctx.ref(warn), "health": "WarningFeatureHealthState"},
        {"item": env.ctx.ref(err), "health": "ErrorFeatureHealthState"}]}
    assert healthy is not None


def test_compute_all_direct_design_is_empty(env_direct):
    ran = []
    env_direct.design.computeAll = lambda: ran.append(1)
    assert env_direct.call("compute_all") == {"timeline_health": []} and ran == [1]


def test_set_modeling_in_assembly(env):
    assert env.err("set_modeling_in_assembly", enabled=True).code == UNSUPPORTED
    env.design.isModelingInAssemblyEnabled = False
    assert env.call("set_modeling_in_assembly", enabled=True) == {"enabled": True}
    assert env.design.isModelingInAssemblyEnabled is True


# --- remove_body / delete_entities -----------------------------------------------------------


def test_remove_body_parametric_uses_remove_feature(env):
    b = env.body("B")
    out = env.call("remove_body", body=env.r(b), confirm=True)
    rf = env.coll("removeFeatures")
    assert rf.added == [b] and out == {"removed": env.ctx.ref(b), "feature": env.ctx.ref(rf.features[0])}
    assert env.err("remove_body", body=env.r(b)).code == CONFIRM_REQUIRED


def test_remove_body_direct_deletes(env_direct):
    b = env_direct.body("B")
    deleted = []
    b.deleteMe = lambda: deleted.append(1) or True
    ref = env_direct.ctx.ref(b)
    out = env_direct.call("remove_body", body=env_direct.r(b), confirm=True)
    assert deleted == [1] and out == {"removed": ref, "feature": None}


def test_delete_entities_outcomes_in_order(env):
    sk = env.comp.add_sketch("S")
    plane = env.comp.add_construction_plane("P")
    stale = env.body("Stale")
    stale.isValid = False
    stale.deleteMe = lambda: pytest.fail("an invalid entity is not deleted again")
    plain = env.comp.originConstructionPoint
    refuse = env.body("Refuse")
    refuse.deleteMe = lambda: False
    boom = env.body("Boom")

    def explode():
        raise RuntimeError("locked")

    boom.deleteMe = explode
    for e in (sk, plane):
        e.deleteMe = lambda: True
    if hasattr(plain, "deleteMe"):
        del plain.deleteMe
    targets = [sk, plane, stale, plain, refuse, boom]
    refs = [env.ctx.ref(t) for t in targets]
    out = env.call("delete_entities", targets=[env.r(t) for t in targets], confirm=True)
    assert out["deleted"] == [refs[0], refs[1], refs[2]]
    assert out["failed"] == [{"target": refs[3], "reason": "not deletable"},
                             {"target": refs[4], "reason": "deleteMe returned False"},
                             {"target": refs[5], "reason": "locked"}]


def test_delete_entities_requires_confirm(env):
    sk = env.comp.add_sketch("S")
    assert env.err("delete_entities", targets=[env.r(sk)]).code == CONFIRM_REQUIRED
