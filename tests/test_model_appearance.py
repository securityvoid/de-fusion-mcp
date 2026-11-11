"""Materials and appearances (design § 11): lookup order, copying into the design, color appearances."""
import pytest
from model_fakes import Appearance, ColorProp, Prop, env, env_direct, library, material  # noqa: F401

from adsk import core

from defusion.errors import INVALID_PARAMS, NOT_FOUND, UNSUPPORTED


def libs(env, *ls):
    env.app.materialLibraries._items.extend(ls)


def color_base(name="Base"):
    return Appearance(name, [Prop(id="roughness", name="r", value=0.5), Prop(id="from", name="f", value=name),
                             ColorProp(id="opaque_albedo", name="c", value=None)])


def color_lib(*extra):
    return library("Fusion Appearance Library", appearances=[Appearance("Plain", [Prop(id="x", name="x", value=1)]),
                                                             *extra])


# --- material_list ---------------------------------------------------------------------------


def test_material_list_library_all_and_named(env):
    libs(env, library("A", [material("Steel"), material("Brass")]), library("B", [material("Steel 2")]))
    out = env.call("material_list")
    assert out["items"] == [{"name": "Steel", "id": "mat-Steel", "library": "A"},
                            {"name": "Brass", "id": "mat-Brass", "library": "A"},
                            {"name": "Steel 2", "id": "mat-Steel 2", "library": "B"}]
    assert [i["name"] for i in env.call("material_list", library="B")["items"]] == ["Steel 2"]
    e = env.err("material_list", library="Nope")
    assert e.code == NOT_FOUND and e.hints == ["A", "B"]


def test_material_list_design_query_and_paging(env):
    env.design.materials._items.extend([material("Oak"), material("Pine"), material("Soap")])
    libs(env, library("A", [material("Library only")]))
    out = env.call("material_list", source="design")
    assert [i["library"] for i in out["items"]] == ["design"] * 3
    assert [i["name"] for i in env.call("material_list", source="design", query="O")["items"]] == ["Oak", "Soap"]
    page = env.call("material_list", source="design", offset=1, limit=1)
    assert [i["name"] for i in page["items"]] == ["Pine"] and page["next_offset"] == 2 and page["total"] == 3


# --- material_assign -------------------------------------------------------------------------


def test_material_assign_prefers_design_then_library(env):
    mine, lib_steel = material("Steel"), material("Steel")
    libs(env, library("A", [lib_steel]), library("B", [material("Brass")]))
    b1, b2 = env.body("B1"), env.body("B2")
    out = env.call("material_assign", bodies=[env.r(b1)], material="Steel")
    assert b1.material is lib_steel and out == {"bodies": [env.ctx.ref(b1)], "material": "Steel"}
    env.design.materials._items.append(mine)
    env.call("material_assign", bodies=[env.r(b1), env.r(b2)], material="Steel")
    assert b1.material is mine and b2.material is mine
    env.call("material_assign", bodies=[env.r(b2)], material="Brass", library="B")
    assert b2.material.name == "Brass"


def test_material_assign_named_library_and_not_found(env):
    libs(env, library("A", [material("Steel")]), library("B", []))
    b = env.body("B")
    e = env.err("material_assign", bodies=[env.r(b)], material="Steel", library="B")
    assert e.code == NOT_FOUND and not hasattr(b, "material")
    assert env.err("material_assign", bodies=[env.r(b)], material="Steel", library="Z").code == NOT_FOUND


def test_lookup_skips_a_library_whose_item_by_name_raises(env):
    """Real Fusion raises RuntimeError from ``itemByName`` on libraries that hold none of that kind."""
    bad = library("Empty", appearances=[])

    def boom(name):
        raise RuntimeError("3 : invalid name")

    bad.appearances.itemByName = boom
    libs(env, bad, library("A", appearances=[Appearance("Red")]))
    b = env.body("B")
    assert env.call("appearance_set", targets=[env.r(b)], appearance="Red")["appearance"] == "Red"
    assert env.err("appearance_set", targets=[env.r(b)], appearance="Absent").code == NOT_FOUND


# --- appearance_set --------------------------------------------------------------------------


def test_appearance_from_library_is_copied_once(env):
    src = Appearance("Red paint", [Prop(id="k", name="k", value=1)])
    libs(env, library("A", appearances=[src]))
    b1, b2 = env.body("B1"), env.body("B2")
    out = env.call("appearance_set", targets=[env.r(b1)], appearance="Red paint")
    assert out == {"targets": [env.ctx.ref(b1)], "appearance": "Red paint"}
    copy = env.design.appearances.itemByName("Red paint")
    assert copy is not src and b1.appearance is copy and env.design.appearances.count == 1
    env.call("appearance_set", targets=[env.r(b2)], appearance="Red paint")
    assert b2.appearance is copy and env.design.appearances.count == 1


def test_appearance_design_version_wins_and_library_can_be_named(env):
    mine = Appearance("Same")
    env.design.appearances._items.append(mine)
    libs(env, library("A", appearances=[Appearance("Same")]), library("B", appearances=[Appearance("Other")]))
    b = env.body("B")
    env.call("appearance_set", targets=[env.r(b)], appearance="Same")
    assert b.appearance is mine and env.design.appearances.count == 1
    env.call("appearance_set", targets=[env.r(b)], appearance="Other", library="B")
    assert b.appearance.name == "Other"
    assert env.err("appearance_set", targets=[env.r(b)], appearance="Absent", library="A").code == NOT_FOUND


def test_appearance_targets_faces_occurrences_and_components(env):
    env.design.appearances._items.append(Appearance("P"))
    app = env.design.appearances.item(0)
    body = env.body("Face owner", faces=1)
    face = body.faces.item(0)
    occ = env.comp.add_occurrence("O")
    sub = env.comp.add_occurrence("O2").component
    inner = [env.body("I1", comp=sub), env.body("I2", comp=sub)]
    out = env.call("appearance_set", targets=[env.r(face), env.r(occ), env.r(sub)], appearance="P")
    assert face.appearance is app and occ.appearance is app
    assert all(b.appearance is app for b in inner) and not hasattr(sub, "appearance")
    assert out["targets"] == [env.ctx.ref(face), env.ctx.ref(occ), env.ctx.ref(sub)]


def test_appearance_target_kind_checked_before_copy(env):
    libs(env, library("A", appearances=[Appearance("P")]))
    b = env.body("B", edges=1)
    e = env.err("appearance_set", targets=[env.r(b.edges.item(0))], appearance="P")
    assert e.code == INVALID_PARAMS and "targets accepts" in e.message
    assert env.design.appearances.count == 0


# --- color_set -------------------------------------------------------------------------------


def test_color_creates_design_appearance_from_library_base(env):
    libs(env, color_lib(color_base("Paint")))
    b = env.body("B")
    out = env.call("color_set", targets=[env.r(b)], color="#ff8000")
    app = env.design.appearances.itemByName("De-Fusion color FF8000FF")
    assert out == {"targets": [env.ctx.ref(b)], "appearance": "De-Fusion color FF8000FF", "color": "#FF8000FF"}
    assert b.appearance is app
    assert app.appearanceProperties.itemById("opaque_albedo").value == ("Color", 255, 128, 0, 255)
    assert app.appearanceProperties.itemById("roughness").value == 0.5  # copied from the base


def test_color_with_alpha_and_reuse(env):
    libs(env, color_lib(color_base()))
    b1, b2 = env.body("B1"), env.body("B2")
    env.call("color_set", targets=[env.r(b1)], color="#0a0B0c80")
    out = env.call("color_set", targets=[env.r(b2)], color="#0A0B0C80")
    app = env.design.appearances.itemByName("De-Fusion color 0A0B0C80")
    assert b1.appearance is app and b2.appearance is app and env.design.appearances.count == 1
    assert out["color"] == "#0A0B0C80"
    assert app.appearanceProperties.itemById("opaque_albedo").value == ("Color", 10, 11, 12, 128)


def test_color_reuse_does_not_need_the_library(env):
    libs(env, color_lib(color_base()))
    env.call("color_set", targets=[env.r(env.body("B1"))], color="#112233")
    env.app.materialLibraries._items.clear()
    b = env.body("B2")
    env.call("color_set", targets=[env.r(b)], color="#112233")
    assert b.appearance.name == "De-Fusion color 112233FF"


@pytest.mark.parametrize("bad", ["red", "#12345", "#1234567", "112233", "#GG0000", "#1122334"])
def test_color_format_rejected(env, bad):
    libs(env, color_lib(color_base()))
    e = env.err("color_set", targets=[env.r(env.body("B"))], color=bad)
    assert e.code == INVALID_PARAMS and e.message == "color must be #RRGGBB or #RRGGBBAA"
    assert env.design.appearances.count == 0


def test_color_unsupported_without_a_colorable_base(env):
    b = env.body("B")
    t = [env.r(b)]
    assert env.err("color_set", targets=t, color="#000000").code == UNSUPPORTED  # no library at all
    libs(env, color_lib())  # only the "Plain" appearance without an albedo color property
    e = env.err("color_set", targets=t, color="#000000")
    assert e.code == UNSUPPORTED and "Fusion Appearance Library" in e.message
    assert env.design.appearances.count == 0


def test_color_picks_first_colorable_appearance_by_index(env):
    libs(env, color_lib(color_base("First"), color_base("Second")))
    env.call("color_set", targets=[env.r(env.body("B"))], color="#000000")
    app = env.design.appearances.itemByName("De-Fusion color 000000FF")
    assert app.appearanceProperties.itemById("from").value == "First"


def test_color_target_kind_checked(env):
    libs(env, color_lib(color_base()))
    b = env.body("B", edges=1)
    assert env.err("color_set", targets=[env.r(b.edges.item(0))], color="#000000").code == INVALID_PARAMS
    assert env.design.appearances.count == 0
