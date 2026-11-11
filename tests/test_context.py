import math

import pytest
from adsk import core, fusion

from defusion.context import Ctx
from defusion.units import OutputUnits
from helpers import raises_action


def make(app, allowed=("server.status",), items=("readonly",), out=None):
    return Ctx.for_app(app, allowed, list(items), out or OutputUnits.for_call(None))


def test_fields(fake_app, fake_design):
    c = make(fake_app, ("server.status", "model.extrude"), ("all", "-script"))
    assert c.app is fake_app and c.ui is fake_app.userInterface
    assert c.document is fake_app.activeDocument and c.design is fake_design
    assert c.allowed == frozenset({"server.status", "model.extrude"})
    assert isinstance(c.allowed, frozenset)
    assert c.profile_items == ["all", "-script"]


def test_units_reflect_per_call_output(fake_app):
    c = make(fake_app, out=OutputUnits.for_call({"length": "in"}))
    assert c.units == {"length": "in", "angle": "deg"}
    assert c.length_out(2.54) == pytest.approx(1.0)


def test_design_taken_from_document_not_active_product(fake_app, fake_design):
    import adsk

    cam = adsk.cam.CAM()
    fake_app.activeProduct = cam  # Manufacture workspace active
    assert make(fake_app).design is fake_design


def test_no_document_means_no_design(fake_app):
    c = make(fake_app)
    assert c.document is None and c.design is None
    assert raises_action(c.require_design).code == "NO_DESIGN"
    assert raises_action(c.component).code == "NO_DESIGN"


def test_require_design_and_parametric(fake_app, fake_design):
    c = make(fake_app)
    assert c.require_design() is fake_design
    assert c.require_parametric() is fake_design
    direct = fusion.Design(parametric=False)
    fake_app.activeDocument = core.Document("D", {"DesignProductType": direct})
    c2 = make(fake_app)
    assert c2.require_design() is direct
    assert raises_action(c2.require_parametric).code == "WRONG_DESIGN_TYPE"


def test_component(fake_app, fake_design):
    c = make(fake_app)
    sub = fake_design.rootComponent.add_occurrence("O").component
    assert c.component() is fake_design.activeComponent
    assert c.component(sub) is sub


def test_ref_and_refs(fake_app, fake_design):
    b = fake_design.rootComponent.add_body("B")
    c = make(fake_app)
    assert c.ref(b) == {"type": "BRepBody", "name": "B", "token": b.entityToken, "component": "Root"}
    assert c.refs([b, b]) == [c.ref(b)] * 2


def test_unit_outputs(fake_app):
    c = make(fake_app, out=OutputUnits("in", "rad"))
    assert c.length_out(2.54) == pytest.approx(1.0)
    assert c.area_out(2.54 ** 2) == pytest.approx(1.0)
    assert c.volume_out(2.54 ** 3) == pytest.approx(1.0)
    assert c.angle_out(math.pi) == pytest.approx(math.pi)
    assert c.mass_out(3.0) == 3.0


def test_page_paths(fake_app):
    c = make(fake_app)
    seq = list(range(250))
    p1 = c.page(seq, 0, 100)
    assert (p1["total"], p1["offset"], p1["next_offset"], len(p1["items"])) == (250, 0, 100, 100)
    assert c.page(seq, 100, 100)["next_offset"] == 200
    last = c.page(seq, 200, 100)
    assert last["next_offset"] is None and last["items"] == list(range(200, 250))
    assert c.page(seq, 250, 10) == {"items": [], "total": 250, "offset": 250, "next_offset": None}
    assert c.page([1, 2], 0, 2)["next_offset"] is None


@pytest.mark.parametrize("offset,limit", [(0, 0), (0, 501), (-1, 10), (0, -3), (True, 5), (0, True)])
def test_page_bounds(fake_app, offset, limit):
    assert raises_action(make(fake_app).page, [1], offset, limit).code == "INVALID_PARAMS"


def test_page_limit_edges_ok(fake_app):
    c = make(fake_app)
    assert c.page([1], 0, 1)["items"] == [1]
    assert c.page([1], 0, 500)["items"] == [1]


def test_page_builds_only_the_slice(fake_app):
    built = []
    out = make(fake_app).page(list(range(250)), 100, 3, build=lambda i: built.append(i) or i * 2)
    assert built == [100, 101, 102] and out["items"] == [200, 202, 204]


def test_text_command(fake_app):
    c = make(fake_app)
    fake_app.text_command_results["Ok"] = "fine"
    assert c.text_command("Ok") == "fine"
    fake_app.text_command_errors["Bad"] = RuntimeError("nope")
    e = raises_action(c.text_command, "Bad cmd")
    assert e.code == "TEXT_COMMAND_FAILED" and "Bad cmd" in e.message and "nope" in e.message
    assert e.extra["command"] == "Bad cmd"


def test_attr_round_trip(fake_app, fake_design):
    c = make(fake_app)
    b = fake_design.rootComponent.add_body("B")
    assert c.attr_get(b, "k") is None
    c.attr_set(b, "k", {"a": 1})
    assert c.attr_get(b, "k") == {"a": 1}
    assert b.attributes.itemByName("defusion", "k").value == '{"a": 1}'
    c.attr_set(b, "k", [1, 2])  # overwrite
    assert c.attr_get(b, "k") == [1, 2]
    assert b.attributes.count == 1


def test_fail(fake_app):
    e = raises_action(make(fake_app).fail, "NOT_FOUND", "m", ["h"])
    assert (e.code, e.message, e.hints) == ("NOT_FOUND", "m", ["h"])


def test_offline_ctx():
    c = Ctx.offline(frozenset({"server.status"}), ["all"], OutputUnits("cm", "rad"))
    assert (c.app, c.ui, c.document, c.design) == (None, None, None, None)
    assert c.units == {"length": "cm", "angle": "rad"} and c.allowed == {"server.status"}
    assert c.length_out(1) == 1
    assert raises_action(c.text_command, "x").code == "UNSUPPORTED"
    assert raises_action(c.require_design).code == "NO_DESIGN"
