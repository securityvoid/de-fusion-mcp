"""Sheet metal actions (design § 8): call sequences, validation and one UNSUPPORTED test per detected member."""
import pytest
from model_fakes import Geo, env, env_direct  # noqa: F401

from adsk import core, fusion

from defusion.errors import INVALID_PARAMS, NOT_FOUND, UNSUPPORTED


def sheet(env, faces=0, edges=0, comp=None, is_sheet=True):
    b = env.body("SM", comp=comp, faces=faces, edges=edges)
    b.isSheetMetal = is_sheet
    return b


def rules(env, lib=(), design=()):
    mk = lambda names: core.Collection([Geo(name=n) for n in names])  # noqa: E731
    env.design.librarySheetMetalRules, env.design.designSheetMetalRules = mk(lib), mk(design)
    return env.design.librarySheetMetalRules, env.design.designSheetMetalRules


# --- flange ----------------------------------------------------------------------------------


def test_edge_flange_sequence(env):
    b = sheet(env, edges=2)
    es = [b.edges.item(0), b.edges.item(1)]
    out = env.call("sheet_flange", edges=[env.r(e) for e in es], height=12, angle=45, bend_position="outside",
                   name="Fl")
    inp = env.last("flangeFeatures")
    assert inp.args == (es, "12 mm") and inp.sets("angle") == ["45 deg"]
    assert inp.sets("bendPositionType") == [fusion.BendPositionTypes.OutsideBendPositionType]
    assert out["feature"]["name"] == "Fl"


def test_edge_flange_defaults_do_not_set_bend_position(env):
    b = sheet(env, edges=1)
    env.call("sheet_flange", edges=[env.r(b.edges.item(0))], height=5)
    inp = env.last("flangeFeatures")
    assert inp.sets("angle") == ["90 deg"] and inp.sets("bendPositionType") == []


@pytest.mark.parametrize("pos", ["inside", "outside", "adjacent", "tangent"])
def test_bend_position_maps_to_enum(env, pos):
    b = sheet(env, edges=1)
    env.call("sheet_flange", edges=[env.r(b.edges.item(0))], height=5, bend_position=pos)
    assert env.last("flangeFeatures").sets("bendPositionType") == [
        getattr(fusion.BendPositionTypes, pos.capitalize() + "BendPositionType")]


def test_base_flange_sequence(env):
    sk, _ = env.new_profile()
    env.call("sheet_flange", profile=env.profile(sk))
    inp = env.last("flangeFeatures")
    assert inp.args == ([sk.profiles.item(0)],) and inp.sets("angle") == []
    assert env.coll("flangeFeatures").added == [inp]


@pytest.mark.parametrize("kw", [dict(height=1), dict(angle=45), dict(bend_position="inside")])
def test_base_flange_rejects_edge_options(env, kw):
    sk, _ = env.new_profile()
    e = env.err("sheet_flange", profile=env.profile(sk), **kw)
    assert e.code == INVALID_PARAMS and "apply only to an edge flange" in e.message
    assert not env.coll("flangeFeatures").created


def test_flange_needs_exactly_one_of_edges_or_profile(env):
    b = sheet(env, edges=1)
    sk, _ = env.new_profile()
    assert env.err("sheet_flange", height=1).message == "give exactly one of edges or profile"
    e = env.err("sheet_flange", edges=[env.r(b.edges.item(0))], profile=env.profile(sk), height=1)
    assert e.message == "give exactly one of edges or profile"


def test_edge_flange_requires_height_and_sheet_body(env):
    b = sheet(env, edges=1)
    assert env.err("sheet_flange", edges=[env.r(b.edges.item(0))]).message == "height is required for an edge flange"
    plain = sheet(env, edges=1, is_sheet=False)
    e = env.err("sheet_flange", edges=[env.r(plain.edges.item(0))], height=1)
    assert e.code == INVALID_PARAMS and e.message == "edges must belong to a sheet metal body"


def test_flange_rule_lookup_library_then_design(env):
    lib, des = rules(env, lib=["Steel"], design=["Mine"])
    env.comp.activeSheetMetalRule = None
    b = sheet(env, edges=1)
    e0 = env.r(b.edges.item(0))
    env.call("sheet_flange", edges=[e0], height=1, rule="Steel")
    assert env.comp.activeSheetMetalRule is lib.item(0)
    env.call("sheet_flange", edges=[e0], height=1, rule="Mine")
    assert env.comp.activeSheetMetalRule is des.item(0)
    e = env.err("sheet_flange", edges=[e0], height=1, rule="Nope")
    assert e.code == NOT_FOUND and len(env.coll("flangeFeatures").created) == 2


def test_flange_rule_applies_to_base_flange(env):
    lib, _ = rules(env, lib=["Steel"])
    env.comp.activeSheetMetalRule = None
    sk, _ = env.new_profile()
    env.call("sheet_flange", profile=env.profile(sk), rule="Steel")
    assert env.comp.activeSheetMetalRule is lib.item(0)


def test_flange_unsupported_members(env):
    b = sheet(env, edges=1)
    e0 = env.r(b.edges.item(0))
    sk, _ = env.new_profile()
    ff = env.comp.features.flangeFeatures
    ff.hide("createEdgeFlangeInput")
    e = env.err("sheet_flange", edges=[e0], height=1)
    assert e.code == UNSUPPORTED and "FlangeFeatures.createEdgeFlangeInput is not available" in e.message
    ff.hide("createBaseFlangeInput")
    e = env.err("sheet_flange", profile=env.profile(sk))
    assert e.code == UNSUPPORTED and "createBaseFlangeInput" in e.message
    env.comp.features.hide("flangeFeatures")
    assert env.err("sheet_flange", edges=[e0], height=1).code == UNSUPPORTED


def test_flange_unsupported_rule_property_and_enum(env, monkeypatch):
    rules(env, lib=["Steel"])
    b = sheet(env, edges=1)
    e0 = env.r(b.edges.item(0))
    e = env.err("sheet_flange", edges=[e0], height=1, rule="Steel")  # comp has no activeSheetMetalRule
    assert e.code == UNSUPPORTED and "Component.activeSheetMetalRule" in e.message
    monkeypatch.delattr(fusion.BendPositionTypes, "TangentBendPositionType")
    e = env.err("sheet_flange", edges=[e0], height=1, bend_position="tangent")
    assert e.code == UNSUPPORTED and "BendPositionTypes.TangentBendPositionType" in e.message
    assert not env.coll("flangeFeatures").created


# --- bend ------------------------------------------------------------------------------------


def lines(env, n=2):
    sk = env.comp.add_sketch("L")
    return [sk.add_line() for _ in range(n)]


def test_bend_sequence(env):
    b = sheet(env, faces=1)
    face = b.faces.item(0)
    ls = lines(env)
    env.call("sheet_bend", stationary_face=env.r(face), lines=[env.r(x) for x in ls], angle=30,
             line_position="end", bend_relief=False, name="Fold")
    inp = env.last("foldFeatures")
    assert inp.args == (face,)
    assert inp.bendLines.calls("add") == [(ln, "30 deg", fusion.FoldBendLinePositionTypes.EndFoldBendLinePositionType,
                                           False) for ln in ls]


def test_bend_defaults(env):
    b = sheet(env, faces=1)
    ln = lines(env, 1)[0]
    env.call("sheet_bend", stationary_face=env.r(b.faces.item(0)), lines=[env.r(ln)])
    assert env.last("foldFeatures").bendLines.calls("add") == [
        (ln, "90 deg", fusion.FoldBendLinePositionTypes.CenterFoldBendLinePositionType, True)]


def test_bend_validation(env):
    b = sheet(env, faces=1)
    f = env.r(b.faces.item(0))
    circle = env.comp.add_sketch("C").add_circle()
    e = env.err("sheet_bend", stationary_face=f, lines=[env.r(circle)])
    assert e.code == INVALID_PARAMS and "lines accepts SketchLine" in e.message
    plain = sheet(env, faces=1, is_sheet=False)
    e = env.err("sheet_bend", stationary_face=env.r(plain.faces.item(0)), lines=[env.r(lines(env, 1)[0])])
    assert e.message == "stationary_face must belong to a sheet metal body"
    assert not env.coll("foldFeatures").created


def test_bend_unsupported_members(env, monkeypatch):
    b = sheet(env, faces=1)
    f, ln = env.r(b.faces.item(0)), env.r(lines(env, 1)[0])
    monkeypatch.delattr(fusion.FoldBendLinePositionTypes, "StartFoldBendLinePositionType")
    e = env.err("sheet_bend", stationary_face=f, lines=[ln], line_position="start")
    assert e.code == UNSUPPORTED and "FoldBendLinePositionTypes.StartFoldBendLinePositionType" in e.message
    env.comp.features.hide("foldFeatures")
    e = env.err("sheet_bend", stationary_face=f, lines=[ln])
    assert e.code == UNSUPPORTED and "features.foldFeatures" in e.message


# --- unfold ----------------------------------------------------------------------------------


def test_unfold_all_and_selected(env):
    b = sheet(env, faces=3)
    fs = [b.faces.item(i) for i in range(3)]
    env.call("sheet_unfold", stationary_face=env.r(fs[0]), name="U")
    inp = env.last("unfoldFeatures")
    assert inp.args == (fs[0],) and inp.sets("isUnfoldAllBends") == [True] and inp.sets("bendFaces") == []
    env.call("sheet_unfold", stationary_face=env.r(fs[0]), bend_faces=[env.r(fs[1]), env.r(fs[2])])
    inp = env.last("unfoldFeatures")
    assert inp.sets("bendFaces") == [fs[1:]] and inp.sets("isUnfoldAllBends") == []


def test_unfold_unsupported_and_non_sheet(env):
    b = sheet(env, faces=1)
    f = env.r(b.faces.item(0))
    env.comp.features.unfoldFeatures.hide("createInput")
    e = env.err("sheet_unfold", stationary_face=f)
    assert e.code == UNSUPPORTED and "UnfoldFeatures.createInput is not available" in e.message
    env.comp.features.hide("unfoldFeatures")
    assert env.err("sheet_unfold", stationary_face=f).code == UNSUPPORTED
    plain = sheet(env, faces=1, is_sheet=False)
    assert env.err("sheet_unfold", stationary_face=env.r(plain.faces.item(0))).code == INVALID_PARAMS


# --- flat pattern ----------------------------------------------------------------------------


def test_flat_pattern_result(env):
    b = sheet(env, faces=1)
    face = b.faces.item(0)
    fb = env.comp.add_body("Flat", bbox=((1, 2, 3), (11, 5, 3.5)))
    fp = env.feature("FlatPattern")
    fp.flatBody = fb
    made = []
    env.comp.createFlatPattern = lambda f: made.append(f) or fp
    out = env.call("sheet_flat_pattern", stationary_face=env.r(face))
    assert made == [face]
    assert out == {"flat_pattern": env.ctx.ref(fp), "flat_body": env.ctx.ref(fb), "size": [100.0, 30.0, 5.0]}


def test_flat_pattern_existing_rejected_and_unsupported(env):
    b = sheet(env, faces=1)
    f = env.r(b.faces.item(0))
    e = env.err("sheet_flat_pattern", stationary_face=f)
    assert e.code == UNSUPPORTED and "Component.createFlatPattern is not available" in e.message
    env.comp.createFlatPattern = lambda face: pytest.fail("must not be called")
    env.comp.flatPattern = object()
    e = env.err("sheet_flat_pattern", stationary_face=f)
    assert e.code == INVALID_PARAMS and e.message == "component already has a flat pattern"
