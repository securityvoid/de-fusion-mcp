import base64
import json

import adsk
import pytest
from adsk import core, fusion
from mcp.types import ImageContent, TextContent

from defusion import results
from defusion.errors import ActionError
from defusion.results import Image
from defusion.units import OutputUnits

UNITS = {"length": "mm", "angle": "deg"}


def text_of(r):
    (t,) = [c for c in r.content if isinstance(c, TextContent)]
    return json.loads(t.text)


def test_ok_envelope_shape():
    assert results.ok_envelope("model", "extrude", {"a": 1}, UNITS) == {
        "ok": True, "tool": "model", "action": "extrude", "result": {"a": 1}, "units": UNITS}
    e = results.ok_envelope("model", "extrude", {}, UNITS, changes={"bodies": [0, 1]})
    assert e["changes"] == {"bodies": [0, 1]} and "undo_grouped" not in e
    assert results.ok_envelope("m", "a", 1, UNITS, changes={}, undo_grouped=False)["undo_grouped"] is False
    assert results.ok_envelope("m", "a", 1, UNITS, changes={}, undo_grouped=True).get("undo_grouped") is None
    assert "changes" not in results.ok_envelope("m", "a", 1, UNITS)


def test_error_envelope_shape():
    e = results.error_envelope("m", "a", ActionError("NOT_FOUND", "gone", ["h"], candidates=[1]))
    assert e == {"ok": False, "tool": "m", "action": "a",
                 "error": {"code": "NOT_FOUND", "message": "gone", "hints": ["h"], "candidates": [1]}}
    f = results.error_envelope("m", "a", RuntimeError("x"))
    assert f["error"]["code"] == "FUSION_ERROR" and "RuntimeError" in f["error"]["message"]


def test_success_call_result():
    r = results.shape("model", "extrude", {"a": 1}, UNITS, changes={})
    assert r.is_error is False and len(r.content) == 1
    assert r.structured_content == text_of(r)
    assert r.structured_content["result"] == {"a": 1}


def test_error_call_result():
    r = results.error_result("m", "a", ActionError("NOT_FOUND", "x"))
    assert r.is_error is True and r.structured_content == text_of(r)
    assert r.structured_content["error"]["code"] == "NOT_FOUND"


def test_single_image_then_text():
    r = results.shape("view", "shot", Image(b"\x89PNG", {"w": 1}), UNITS)
    assert [type(c) for c in r.content] == [ImageContent, TextContent]
    assert r.content[0].mime_type == "image/png"
    assert base64.b64decode(r.content[0].data) == b"\x89PNG"
    assert text_of(r)["result"] == {"w": 1} and r.structured_content == text_of(r)


def test_image_list_in_order():
    r = results.shape("view", "shot", [Image(b"a", {"i": 0}), Image(b"bb", {"i": 1})], UNITS)
    assert [type(c) for c in r.content] == [ImageContent, ImageContent, TextContent]
    assert [base64.b64decode(c.data) for c in r.content[:2]] == [b"a", b"bb"]
    assert text_of(r)["result"] == [{"i": 0}, {"i": 1}]


def sized(n):  # compact JSON of {"s": "x"*k} is 8 + k bytes
    return {"s": "x" * (n - 8)}


def test_result_size_boundary():
    assert len(results.dumps(sized(65536)).encode()) == 65536
    r = results.shape("m", "a", sized(65536), UNITS)
    assert r.is_error is False
    r = results.shape("m", "a", sized(65537), UNITS)
    assert r.is_error is True
    err = r.structured_content["error"]
    assert err["code"] == "RESULT_TOO_LARGE" and "limit" in err["hints"][0]


def test_size_counts_utf8_bytes_not_chars():
    # 'é' is 2 bytes: 8 + 2*k bytes with k chars
    assert results.shape("m", "a", {"s": "é" * 32764}, UNITS).is_error is False   # 65536 bytes
    assert results.shape("m", "a", {"s": "é" * 32765}, UNITS).is_error is True    # 65538 bytes


def test_image_total_boundary():
    four_mib = 4 * 1024 * 1024
    assert results.shape("v", "a", Image(b"x" * four_mib, {}), UNITS).is_error is False
    r = results.shape("v", "a", [Image(b"x" * four_mib, {}), Image(b"y", {})], UNITS)
    assert r.is_error is True
    err = r.structured_content["error"]
    assert err["code"] == "RESULT_TOO_LARGE" and str(four_mib + 1) in err["message"]
    assert "width" in err["hints"][0] and "tile" in err["hints"][0]


def test_check_result_raises_for_job_results():
    results.check_result(sized(65536))
    with pytest.raises(ActionError) as e:
        results.check_result(sized(65537))
    assert e.value.code == "RESULT_TOO_LARGE"


def test_page_is_context_page():
    p = results.page(list(range(5)), 0, 2)
    assert p["next_offset"] == 2
    for off, lim in ((-1, 5), (0, 0), (0, 501)):
        with pytest.raises(ActionError):
            results.page([1], off, lim)


# ---- change summary ---------------------------------------------------------------------------------

MM = OutputUnits("mm", "deg")


def test_snapshot_counts_all_components_and_bbox(fake_design):
    root = fake_design.rootComponent
    root.add_body("A", bbox=((0, 0, 0), (1, 1, 1)))
    sub = root.add_occurrence("O").component
    sub.add_body("B", bbox=((-1, 0, 0), (2, 3, 4)))
    sub.add_body("Hidden", bbox=((-50, -50, -50), (50, 50, 50)), visible=False)
    s = results.snapshot(fake_design, MM)
    assert s["bodies"] == 3 and s["timeline"] == 0
    assert s["bbox"] == pytest.approx([-10, 0, 0, 20, 30, 40])
    assert results.snapshot(fake_design, OutputUnits("cm", "deg"))["bbox"] == pytest.approx([-1, 0, 0, 2, 3, 4])


def test_snapshot_empty_design(fake_design):
    assert results.snapshot(fake_design, MM) == {"bodies": 0, "timeline": 0, "bbox": None}


def test_snapshot_direct_mode_timeline_null():
    d = fusion.Design(parametric=False)
    assert results.snapshot(d, MM)["timeline"] is None


def test_diff_adding_body(fake_design):
    before = results.snapshot(fake_design, MM)
    fake_design.rootComponent.add_body("A")
    fake_design.rootComponent.add_sketch("S")
    after = results.snapshot(fake_design, MM)
    d = results.diff(before, after)
    assert d["bodies"] == [0, 1] and d["timeline"] == [0, 1]
    assert d["bbox"] == [None, pytest.approx([0, 0, 0, 10, 10, 10])]


def test_diff_noop_is_empty(fake_design):
    fake_design.rootComponent.add_body("A")
    s = results.snapshot(fake_design, MM)
    assert results.diff(s, results.snapshot(fake_design, MM)) == {}


def test_diff_omits_only_unchanged_keys(fake_design):
    before = results.snapshot(fake_design, MM)
    fake_design.rootComponent.add_sketch("S")
    assert results.diff(before, results.snapshot(fake_design, MM)) == {"timeline": [0, 1]}


def test_bbox_failure_is_null_not_error(fake_design):
    b = fake_design.rootComponent.add_body("A")
    b._bbox_error = True
    s = results.snapshot(fake_design, MM)
    assert s["bbox"] is None and s["bodies"] == 1


def test_over_500_visible_bodies_skips_bbox(fake_design):
    root = fake_design.rootComponent
    for i in range(501):
        root.add_body("B%d" % i)
    core.reset_calls()
    s = results.snapshot(fake_design, MM)
    assert s["bbox"] is None and s["bodies"] == 501
    assert not [n for n, _ in core.CALLS if n == "BRepBody.boundingBox"]


def test_exactly_500_visible_bodies_gets_bbox(fake_design):
    root = fake_design.rootComponent
    for i in range(500):
        root.add_body("B%d" % i)
    assert results.snapshot(fake_design, MM)["bbox"] is not None


def test_hidden_bodies_do_not_count_toward_cap(fake_design):
    root = fake_design.rootComponent
    for i in range(500):
        root.add_body("V%d" % i)
    for i in range(10):
        root.add_body("H%d" % i, visible=False)
    assert results.snapshot(fake_design, MM)["bbox"] is not None
