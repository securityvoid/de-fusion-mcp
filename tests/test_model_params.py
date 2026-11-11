"""Parameter actions (design § 10): filtering, item shape, validate-then-apply, delete reporting."""
import pytest
from model_fakes import env, env_direct  # noqa: F401

from defusion.errors import CONFIRM_REQUIRED, INVALID_PARAMS


def user(env, name, cm, expression=None, unit="mm", comment=""):
    p = env.design.add_parameter(name, cm, user=True)
    p.expression, p.unit, p.comment = expression or "%g" % cm, unit, comment
    return p


def model(env, name, cm, unit="mm", created_by=None, role="Height"):
    p = env.design.add_parameter(name, cm, user=False)
    p.unit, p.createdBy, p.role = unit, created_by, role
    return p


def names(out):
    return [i["name"] for i in out["items"]]


def test_item_shape_and_units(env):
    f = env.feature("Ext")
    user(env, "w", 2.0, "20 mm", "mm", "width")
    user(env, "a", 0.5, "28.6479 deg", "deg")
    user(env, "n", 3.0, "3", "")
    m = model(env, "d1", 1.5, created_by=f)
    m.isFavorite = True
    out = env.call("parameter_list")
    items = {i["name"]: i for i in out["items"]}
    assert items["w"] == {"name": "w", "kind": "user", "expression": "20 mm", "unit": "mm", "value": 20.0,
                          "comment": "width", "created_by": None, "role": None, "is_favorite": False}
    assert items["a"]["value"] == pytest.approx(28.6478897, rel=1e-6)  # radians out as degrees
    assert items["n"]["value"] == 3.0 and items["n"]["unit"] == ""
    assert items["d1"] == {"name": "d1", "kind": "model", "expression": "1.5", "unit": "mm", "value": 15.0,
                           "comment": "", "created_by": env.ctx.ref(f), "role": "Height", "is_favorite": True}
    assert (out["total"], out["offset"], out["next_offset"]) == (4, 0, None)


def test_kind_filters(env):
    user(env, "u1", 1.0)
    model(env, "m1", 1.0)
    assert names(env.call("parameter_list")) == ["u1", "m1"]
    assert names(env.call("parameter_list", kind="user")) == ["u1"]
    assert names(env.call("parameter_list", kind="model")) == ["m1"]


def test_name_pattern_and_created_by(env):
    f1, f2 = env.feature("A"), env.feature("B")
    user(env, "len_a", 1.0)
    user(env, "wid", 1.0)
    model(env, "len_b", 1.0, created_by=f1)
    model(env, "d2", 1.0, created_by=f2)
    model(env, "orphan", 1.0)
    assert names(env.call("parameter_list", name="len_*")) == ["len_a", "len_b"]
    assert names(env.call("parameter_list", name="LEN_*")) == []
    assert names(env.call("parameter_list", created_by=env.r(f1))) == ["len_b"]
    assert names(env.call("parameter_list", created_by=env.r(f2), name="d?")) == ["d2"]


def test_paging(env):
    for i in range(5):
        user(env, "p%d" % i, 1.0)
    out = env.call("parameter_list", offset=1, limit=2)
    assert names(out) == ["p1", "p2"] and out["next_offset"] == 3 and out["total"] == 5
    assert env.err("parameter_list", limit=0).code == INVALID_PARAMS


# --- create ----------------------------------------------------------------------------------


def test_create_defaults_to_length_unit(env):
    out = env.call("parameter_create", name="w", expression="20 mm", comment="c")
    p = env.design.userParameters.itemByName("w")
    assert (p.expression, p.unit, p.comment) == ("20 mm", "mm", "c")
    assert out["parameter"] == {"name": "w", "kind": "user", "expression": "20 mm", "unit": "mm", "value": 20.0,
                                "comment": "c", "created_by": None, "role": None, "is_favorite": False}


def test_create_unit_options(env):
    env.call("parameter_create", name="count", expression="3", unit="")
    assert env.design.userParameters.itemByName("count").unit == ""
    env.call("parameter_create", name="ang", expression="45 deg", unit="deg")
    out = env.call("parameter_list", name="ang")
    assert out["items"][0]["value"] == pytest.approx(45.0)


@pytest.mark.parametrize("name", ["1a", "a b", "", "a-b"])
def test_create_rejects_bad_names(env, name):
    e = env.err("parameter_create", name=name, expression="1 mm")
    assert e.code == INVALID_PARAMS and "must match" in e.message
    assert env.design.userParameters.count == 0


def test_create_rejects_duplicates_including_model_parameters(env):
    model(env, "d1", 1.0)
    user(env, "w", 1.0)
    for n in ("d1", "w"):
        e = env.err("parameter_create", name=n, expression="1 mm")
        assert e.code == INVALID_PARAMS and e.message == "parameter %r already exists" % n


def test_create_rejects_invalid_expression(env):
    e = env.err("parameter_create", name="x", expression="3 furlong")
    assert e.code == INVALID_PARAMS and "is not valid for unit 'mm'" in e.message
    e = env.err("parameter_create", name="y", expression="3 mm", unit="")
    assert e.code == INVALID_PARAMS


# --- modify ----------------------------------------------------------------------------------


def test_modify_without_batch_api_sets_in_order(env):
    a, b = user(env, "a", 1.0), user(env, "b", 1.0)
    out = env.call("parameter_modify", edits=[{"name": "b", "expression": "5 mm"}, {"name": "a", "expression": "2 mm"}])
    assert (a.expression, b.expression) == ("2 mm", "5 mm")
    assert [p["name"] for p in out["parameters"]] == ["b", "a"]


def test_modify_uses_batch_api_when_present(env):
    a, b = user(env, "a", 1.0), user(env, "b", 1.0)
    calls = []
    env.design.modifyParameters = lambda ps, vs: calls.append((ps, [v.value for v in vs]))
    env.call("parameter_modify", edits=[{"name": "a", "expression": "2 mm"}, {"name": "b", "expression": "a*2"}])
    assert calls == [([a, b], ["2 mm", "a*2"])]
    assert (a.expression, b.expression) == ("1", "1")  # only the batch call changes things


def test_modify_validates_all_before_changing_anything(env):
    a = user(env, "a", 1.0, "1 mm")
    user(env, "b", 1.0, "1 mm")
    e = env.err("parameter_modify", edits=[
        {"name": "a", "expression": "2 mm"}, {"name": "ghost", "expression": "1 mm"},
        {"name": "b", "expression": "bad unit"}, {"name": "a", "expression": "3 mm"}])
    assert e.code == INVALID_PARAMS
    assert e.hints == ["ghost: no such parameter", "b: invalid expression 'bad unit'", "a: listed more than once"]
    assert e.message == "bad edits: " + "; ".join(e.hints)
    assert a.expression == "1 mm"


def test_modify_edit_shape_is_strict(env):
    user(env, "a", 1.0)
    assert env.err("parameter_modify", edits=[{"name": "a"}]).code == INVALID_PARAMS
    assert env.err("parameter_modify", edits=[{"name": "a", "expression": "1", "x": 1}]).code == INVALID_PARAMS


# --- delete ----------------------------------------------------------------------------------


def test_delete_requires_confirm(env):
    user(env, "a", 1.0)
    assert env.err("parameter_delete", names=["a"]).code == CONFIRM_REQUIRED


def test_delete_reports_deleted_and_failed(env):
    a, b = user(env, "a", 1.0), user(env, "b", 1.0)
    b.delete_ok = False
    out = env.call("parameter_delete", names=["a", "b", "a"], confirm=True)
    assert out == {"deleted": ["a"], "failed": [{"name": "b", "reason": "in use by other parameters or features"}]}


def test_delete_rejects_model_and_unknown_before_deleting(env):
    a = user(env, "a", 1.0)
    model(env, "m", 1.0)
    calls = []
    a.deleteMe = lambda: calls.append(1) or True
    e = env.err("parameter_delete", names=["a", "m", "zzz"], confirm=True)
    assert e.code == INVALID_PARAMS and e.message == "not user parameters: m, zzz" and calls == []
