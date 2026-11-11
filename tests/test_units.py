import math

import pytest
from adsk import fusion

from defusion import units
from defusion.errors import ActionError
from defusion.units import OutputUnits


@pytest.fixture(autouse=True)
def _defaults():
    yield
    units.set_input_defaults("mm", "deg")


def test_to_expr():
    assert units.to_expr(5, "mm") == "5 mm"
    assert units.to_expr(5.0, "mm") == "5 mm"
    assert units.to_expr(1.5, "in") == "1.5 in"
    assert units.to_expr("d1*2", "mm") == "d1*2"
    with pytest.raises(ActionError) as e:
        units.to_expr(True, "mm")
    assert e.value.code == "INVALID_PARAMS"


def test_no_design_tables():
    assert units.evaluate_length("2 ft", None, "p") == pytest.approx(60.96)
    assert units.evaluate_length("5", None, "p") == pytest.approx(0.5)
    assert units.evaluate_length("1 in", None, "p") == pytest.approx(2.54)
    assert units.evaluate_angle("90 deg", None, "p") == pytest.approx(math.pi / 2)
    assert units.evaluate_angle("1 rad", None, "p") == pytest.approx(1.0)
    assert units.evaluate_expr("3", None, "p") == 3.0


def test_default_input_units_are_the_configured_ones():
    units.set_input_defaults("in", "rad")
    assert units.evaluate_length("5", None, "p") == pytest.approx(12.7)
    assert units.evaluate_angle("2", None, "p") == pytest.approx(2.0)


@pytest.mark.parametrize("expr", ["3 furlong", "d1*2", ""])
def test_no_design_rejects_and_names_parameter(expr):
    with pytest.raises(ActionError) as e:
        units.evaluate_length(expr, None, "width")
    assert e.value.code == "INVALID_PARAMS" and "width" in e.value.message


def test_no_design_angle_unit_rejected():
    with pytest.raises(ActionError) as e:
        units.evaluate_angle("5 mm", None, "tilt")
    assert "tilt" in e.value.message


def test_design_path_uses_units_manager(fake_design):
    fake_design.add_parameter("d1", 2.0)
    assert units.evaluate_length("d1*2", fake_design, "p") == pytest.approx(4.0)
    assert units.evaluate_angle("90 deg", fake_design, "p") == pytest.approx(math.pi / 2)
    assert units.evaluate_expr("3*2", fake_design, "p") == 6.0
    with pytest.raises(ActionError) as e:
        units.evaluate_length("3 furlong", fake_design, "depth")
    assert e.value.code == "INVALID_PARAMS" and "depth" in e.value.message


def test_input_ignores_output_units():
    # per-call output units never change input interpretation
    out = OutputUnits.for_call({"length": "in"})
    assert out.length_unit == "in"
    assert units.evaluate_length(units.to_expr(5, "mm"), None, "p") == pytest.approx(0.5)


def test_output_conversions():
    o = OutputUnits("in", "rad")
    assert o.length(2.54) == pytest.approx(1.0)
    assert o.area(2.54 ** 2) == pytest.approx(1.0)
    assert o.volume(2.54 ** 3) == pytest.approx(1.0)
    assert o.angle(1.0) == pytest.approx(1.0)
    assert o.mass(2.5) == 2.5
    d = OutputUnits("mm", "deg")
    assert d.length(1.0) == pytest.approx(10.0)
    assert d.area(1.0) == pytest.approx(100.0)
    assert d.volume(1.0) == pytest.approx(1000.0)
    assert d.angle(math.pi) == pytest.approx(180.0)
    assert d.as_dict() == {"length": "mm", "angle": "deg"}


def test_output_validation():
    for kw in ({"length": "furlong"}, {"angle": "grad"}):
        with pytest.raises(ActionError) as e:
            OutputUnits(**kw)
        assert e.value.code == "INVALID_PARAMS"


def test_for_call_merges_and_validates():
    units.set_input_defaults("cm", "rad")
    o = OutputUnits.for_call({"angle": "deg"})
    assert o.as_dict() == {"length": "cm", "angle": "deg"}
    assert OutputUnits.for_call(None).as_dict() == {"length": "cm", "angle": "rad"}
    for bad in ({"length": "furlong"}, {"bogus": "x"}, "mm"):
        with pytest.raises(ActionError) as e:
            OutputUnits.for_call(bad)
        assert e.value.code == "INVALID_PARAMS"
