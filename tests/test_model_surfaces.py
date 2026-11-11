"""Surface actions (design § 7): API call sequence, validation, trim cell selection."""
import pytest
from model_fakes import Geo, env, env_direct  # noqa: F401

from adsk import core, fusion

from defusion.errors import INVALID_PARAMS, UNSUPPORTED

FO = fusion.FeatureOperations


def edges(env, n=2, comp=None):
    b = env.body("B", comp=comp, edges=n)
    return [b.edges.item(i) for i in range(n)]


def surf(env, name="S", comp=None):
    return env.body(name, comp=comp, solid=False)


# --- patch -----------------------------------------------------------------------------------


def test_patch_edges_and_curves(env):
    es = edges(env)
    out = env.call("surface_patch", boundary=[env.r(e) for e in es], chain=False, name="P")
    assert env.comp.features.created_paths == [("Path", es, False)]
    inp = env.last("patchFeatures")
    assert inp.args == (("Path", es, False), FO.NewBodyFeatureOperation) and out["feature"]["name"] == "P"
    ln = env.comp.add_sketch("S").add_line()
    env.call("surface_patch", boundary=[env.r(ln)])
    assert env.comp.features.created_paths[-1] == ("Path", [ln], True)


def test_patch_rejects_other_kinds_and_mixed_components(env):
    b = env.body("B", faces=1)
    e = env.err("surface_patch", boundary=[env.r(b.faces.item(0))])
    assert e.code == INVALID_PARAMS and "boundary accepts" in e.message
    sub = env.comp.add_occurrence("O").component
    e = env.err("surface_patch", boundary=[env.r(edges(env, 1)[0]), env.r(edges(env, 1, sub)[0])])
    assert e.message == "all boundary must belong to one component"


# --- stitch ----------------------------------------------------------------------------------


def test_stitch_defaults_and_options(env):
    a, b = surf(env, "A"), surf(env, "B")
    env.call("surface_stitch", bodies=[env.r(a), env.r(b)])
    inp = env.last("stitchFeatures")
    assert inp.args == ([a, b], "0.01 mm", FO.NewBodyFeatureOperation)
    env.call("surface_stitch", bodies=[env.r(a)], tolerance=0.5, operation="new_component")
    assert env.last("stitchFeatures").args == ([a], "0.5 mm", FO.NewComponentFeatureOperation)


def test_stitch_rejects_solids_and_join(env):
    s = env.body("Solid")
    e = env.err("surface_stitch", bodies=[env.r(s)])
    assert e.code == INVALID_PARAMS and e.message == "bodies must be surface bodies"
    assert env.err("surface_stitch", bodies=[env.r(surf(env))], operation="join").code == INVALID_PARAMS


# --- thicken ---------------------------------------------------------------------------------


def test_thicken_arguments(env):
    b = env.body("B", faces=2)
    fs = [b.faces.item(0), b.faces.item(1)]
    env.call("surface_thicken", faces=[env.r(f) for f in fs], thickness=2, symmetric=True, operation="join",
             chain=False)
    assert env.last("thickenFeatures").args == (fs, "2 mm", True, FO.JoinFeatureOperation, False)
    env.call("surface_thicken", faces=[env.r(fs[0])], thickness=-1)
    assert env.last("thickenFeatures").args == ([fs[0]], "-1 mm", False, FO.NewBodyFeatureOperation, True)


def test_thicken_rejects_zero(env):
    b = env.body("B", faces=1)
    e = env.err("surface_thicken", faces=[env.r(b.faces.item(0))], thickness=0)
    assert e.code == INVALID_PARAMS and e.message == "thickness must not be 0"
    assert not env.coll("thickenFeatures").created


# --- ruled -----------------------------------------------------------------------------------


def test_ruled_arguments(env):
    es = edges(env)
    env.call("surface_ruled", edges=[env.r(e) for e in es], distance=5, angle=10, ruled_type="tangent", chain=False)
    assert env.comp.features.created_paths == [("EdgeProfile", es, False)]
    assert env.last("ruledSurfaceFeatures").args == (
        ("EdgeProfile", es, False), "5 mm", "10 deg", fusion.RuledSurfaceTypes.TangentRuledSurfaceType)
    env.call("surface_ruled", edges=[env.r(es[0])], distance=1)
    assert env.last("ruledSurfaceFeatures").args == (
        ("EdgeProfile", [es[0]], True), "1 mm", "0 deg", fusion.RuledSurfaceTypes.NormalRuledSurfaceType)


def test_ruled_unsupported_when_missing_or_signature_differs(env):
    e0 = env.r(edges(env, 1)[0])
    env.comp.features.ruledSurfaceFeatures.hide("createInput")
    e = env.err("surface_ruled", edges=[e0], distance=1)
    assert e.code == UNSUPPORTED and "RuledSurfaceFeatures.createInput is not available" in e.message
    env.comp.features.hide("ruledSurfaceFeatures")
    assert env.err("surface_ruled", edges=[e0], distance=1).code == UNSUPPORTED


def test_ruled_type_error_means_signature_differs(env):
    e0 = env.r(edges(env, 1)[0])

    def boom(*a):
        raise TypeError("arg count")

    env.comp.features.ruledSurfaceFeatures.createInput = boom
    e = env.err("surface_ruled", edges=[e0], distance=1)
    assert e.code == UNSUPPORTED
    assert e.message == "RuledSurfaceFeatures.createInput signature differs in this Fusion build"


# --- trim ------------------------------------------------------------------------------------


def cells(env, n=3):
    def cell(i):
        pt = lambda v: Geo(x=v, y=0.0, z=0.0)  # noqa: E731
        return Geo(cellBody=Geo(boundingBox=Geo(minPoint=pt(i), maxPoint=pt(i + 2))), isSelected=False)

    cs = core.Collection([cell(i) for i in range(n)])
    coll = env.comp.features.trimFeatures
    orig = coll.createInput

    def create(tool):
        inp = orig(tool)
        inp.__dict__["bRepCells"] = cs
        return inp

    coll.createInput = create
    return cs


def test_trim_selects_cells(env):
    cs = cells(env)
    tool = env.body("T")
    out = env.call("surface_trim", tool=env.r(tool), remove_cells=[0, 2], name="Tr")
    assert [c.isSelected for c in cs._items] == [True, False, True]
    assert env.last("trimFeatures").args == (tool,) and out["feature"]["name"] == "Tr"


def test_trim_accepts_plane_and_curve_tools(env):
    cells(env)
    env.call("surface_trim", tool=env.r(env.comp.xYConstructionPlane), remove_cells=[1])
    env.call("surface_trim", tool=env.r(env.comp.add_sketch("S").add_line()), remove_cells=[1])
    assert len(env.coll("trimFeatures").created) == 2


@pytest.mark.parametrize("bad", [[], [0, 0], [3], [-1]])
def test_trim_cell_index_errors_carry_center_hints(env, bad):
    cs = cells(env)
    e = env.err("surface_trim", tool=env.r(env.body("T")), remove_cells=bad)
    assert e.code == INVALID_PARAMS and "remove_cells" in e.message
    assert e.hints == ["cell 0: center [10.0, 0.0, 0.0]", "cell 1: center [20.0, 0.0, 0.0]",
                       "cell 2: center [30.0, 0.0, 0.0]"]
    assert not any(c.isSelected for c in cs._items) and not env.coll("trimFeatures").added


def test_trim_rejects_wrong_tool_kind(env):
    cells(env)
    b = env.body("B", edges=1)
    e = env.err("surface_trim", tool=env.r(b.edges.item(0)), remove_cells=[0])
    assert e.code == INVALID_PARAMS and "tool accepts" in e.message
