import math
from types import SimpleNamespace

import pytest
from adsk import core, fusion
from pydantic import TypeAdapter, ValidationError

from defusion import types
from defusion.errors import ActionError
from defusion.types import Angle, Expr, FileName, Length, Operation, Point3, Resolver, Vector3


def resolve(tp, raw, design=None, path="p"):
    spec = TypeAdapter(tp).validate_python(raw)
    return spec.resolve(Resolver(SimpleNamespace(design=design), path=path))


def test_length_number_uses_default_unit():
    v = resolve(Length, 5)
    assert v.expr == "5 mm" and v.cm == pytest.approx(0.5)


def test_length_string_expression():
    assert resolve(Length, "1 in").cm == pytest.approx(2.54)


def test_length_with_design_expression(fake_design):
    fake_design.add_parameter("d1", 2.0)
    v = resolve(Length, "d1*2", fake_design)
    assert v.cm == pytest.approx(4.0) and v.expr == "d1*2"


def test_length_invalid_expression_names_parameter(fake_design):
    with pytest.raises(ActionError) as e:
        resolve(Length, "3 furlong", fake_design, path="distance")
    assert e.value.code == "INVALID_PARAMS" and "distance" in e.value.message


def test_angle():
    v = resolve(Angle, 90)
    assert v.expr == "90 deg" and v.rad == pytest.approx(math.pi / 2)
    assert resolve(Angle, "1 rad").rad == pytest.approx(1.0)


def test_expr_unitless():
    v = resolve(Expr, 3)
    assert v.expr == "3" and v.number == 3.0
    assert resolve(Expr, "7").number == 7.0


def test_value_wrong_kind_property():
    with pytest.raises(AttributeError):
        resolve(Length, 1).rad


def test_value_input_is_created_by_string(fake_app):
    vi = resolve(Length, "1 in").value_input()
    assert isinstance(vi, core.ValueInput) and vi.kind == "string" and vi.value == "1 in"


@pytest.mark.parametrize("bad", [True, None, [1], {}, "", "  ", float("nan")])
def test_scalar_rejects_bad_input(bad):
    with pytest.raises(ValidationError):
        TypeAdapter(Length).validate_python(bad)


def test_schema_shape():
    s = TypeAdapter(Length).json_schema()
    assert {"type": "number"} in s["oneOf"] and {"type": "string"} in s["oneOf"]


def test_point3_list_and_dict(fake_app):
    p = resolve(Point3, [10, 0, "1 in"])
    assert isinstance(p, core.Point3D)
    assert (p.x, p.y, p.z) == (pytest.approx(1.0), 0, pytest.approx(2.54))
    q = resolve(Point3, {"x": 10, "y": 0, "z": "1 in"})
    assert (q.x, q.y, q.z) == (p.x, p.y, p.z)


@pytest.mark.parametrize("bad", [[1, 2], [1, 2, 3, 4], {"x": 1, "y": 2}, "x", [1, 2, True]])
def test_point3_wrong_shape(bad):
    with pytest.raises(ValidationError):
        TypeAdapter(Point3).validate_python(bad)


def test_vector3(fake_app):
    v = resolve(Vector3, [1, 0, 2.5])
    assert isinstance(v, core.Vector3D) and (v.x, v.y, v.z) == (1, 0, 2.5)
    for bad in ([1, 2], ["1", 2, 3], [True, 0, 0]):
        with pytest.raises(ValidationError):
            TypeAdapter(Vector3).validate_python(bad)


@pytest.mark.parametrize("name", ["part", "a b", "x.stl", "a-b_c", "é"])
def test_filename_ok(name):
    assert TypeAdapter(FileName).validate_python(name) == name


@pytest.mark.parametrize(
    "name", ["", "a/b", "a\\b", "..", "a..b", ".hidden", "-x", "a\nb", "a\x00", "x" * 65]
)
def test_filename_rejected(name):
    with pytest.raises(ValidationError):
        TypeAdapter(FileName).validate_python(name)


def test_filename_64_chars_ok():
    assert TypeAdapter(FileName).validate_python("x" * 64)


@pytest.mark.parametrize(
    "lit,member",
    [("new_body", "NewBodyFeatureOperation"), ("join", "JoinFeatureOperation"),
     ("cut", "CutFeatureOperation"), ("intersect", "IntersectFeatureOperation"),
     ("new_component", "NewComponentFeatureOperation")],
)
def test_operation_maps_to_enum(lit, member):
    assert types.adsk_enum(lit) == getattr(fusion.FeatureOperations, member)
    assert TypeAdapter(Operation).validate_python(lit) == lit


def test_unknown_enum_value():
    with pytest.raises(ActionError):
        types.adsk_enum("nope")
