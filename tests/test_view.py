"""view tool tests (fake adsk)."""
import pathlib
import sys
import types as pytypes
import typing

import pytest

from defusion import actions

_TOOLS_DIR = pathlib.Path(__file__).resolve().parent.parent / "De-Fusion" / "defusion" / "tools"


@pytest.fixture(scope="module")
def view():
    """defusion.tools.view imported without the sibling tool modules, in an isolated registry."""
    pkg = pytypes.ModuleType("defusion.tools")
    pkg.__path__ = [str(_TOOLS_DIR)]
    saved = {k: sys.modules.get(k) for k in ("defusion.tools", "defusion.tools.view")}
    sys.modules["defusion.tools"] = pkg
    sys.modules.pop("defusion.tools.view", None)
    with actions.isolated_registry() as reg:
        import importlib

        mod = importlib.import_module("defusion.tools.view")
        mod.REGISTRY = reg
        yield mod
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


# --- camera -----------------------------------------------------------------------------------

import math

from adsk import core, viewfakes

from defusion import units
from defusion.context import Ctx
from defusion.errors import ActionError
from defusion.types import Value


@pytest.fixture
def env(view, fake_app, monkeypatch):
    viewfakes.install(monkeypatch)
    vp = viewfakes.Viewport()
    fake_app.activeViewport = vp
    fake_app.activeDocument = core.Document("Doc")

    class E:
        pass

    e = E()
    e.vp, e.app, e.view = vp, fake_app, view
    e.ctx = lambda length="mm", angle="deg": Ctx.for_app(
        fake_app, frozenset(), [], units.OutputUnits(length, angle))
    return e


def L(n):  # a resolved Length in mm
    return Value("%s mm" % n, n / 10.0, "length")


def A(deg):
    return Value("%s deg" % deg, math.radians(deg), "angle")


def vec(p):
    return (p.x, p.y, p.z)


def approx(a, b, tol=1e-9):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def set_vp(vp, eye=(10, 0, 0), target=(0, 0, 0), up=(0, 0, 1), ctype=0, extents=(120.0, 80.0)):
    cam = vp._camera
    cam.eye, cam.target = core.Point3D(*eye), core.Point3D(*target)
    cam.upVector = core.Vector3D(*up)
    cam.cameraType, cam.extents = ctype, extents


def code(fn, *a, **k):
    with pytest.raises(ActionError) as ei:
        fn(*a, **k)
    return ei.value.code


def test_get_camera_orthographic(env):
    env.vp._camera.viewOrientation = core.ViewOrientations.IsoTopRightViewOrientation
    set_vp(env.vp, eye=(10, 20, 30), target=(1, 2, 3), extents=(12.0, 8.0))
    r = env.view.get_camera(env.ctx())
    assert r == {"eye": [100.0, 200.0, 300.0], "target": [10.0, 20.0, 30.0], "up": [0.0, 0.0, 1.0],
                 "projection": "orthographic", "orientation": "iso_top_right",
                 "extents": {"width": 120.0, "height": 80.0}, "fov": None,
                 "viewport": {"width": 1600, "height": 1000}, "length_unit": "mm", "angle_unit": "deg"}


def test_get_camera_perspective_and_arbitrary(env):
    set_vp(env.vp, ctype=1)
    env.vp._camera.perspectiveAngle = math.pi / 2
    r = env.view.get_camera(env.ctx())
    assert r["projection"] == "perspective" and r["orientation"] == "arbitrary"
    assert r["extents"] is None and r["fov"] == pytest.approx(90.0)
    r = env.view.get_camera(env.ctx(angle="rad"))
    assert r["fov"] == pytest.approx(math.pi / 2) and r["angle_unit"] == "rad"


def test_get_camera_inches_and_extents_failure(env):
    set_vp(env.vp, eye=(2.54, 0, 0))
    r = env.view.get_camera(env.ctx(length="in"))
    assert r["eye"][0] == pytest.approx(1.0) and r["length_unit"] == "in"
    assert r["extents"]["width"] == pytest.approx(120.0 / 2.54)
    env.vp._camera.extents_ok = False
    assert code(env.view.get_camera, env.ctx()) == "FUSION_ERROR"


def test_camera_no_viewport_or_document(env):
    env.app.activeViewport = None
    assert code(env.view.get_camera, env.ctx()) == "NO_DOCUMENT"
    env.app.activeViewport = env.vp
    env.app.activeDocument = None
    assert code(env.view.get_camera, env.ctx()) == "NO_DOCUMENT"


def test_set_camera_validation(env):
    v, c = env.view, env.ctx()
    snap = dict(eye=[10, 0, 0], target=[0, 0, 0], up=[0, 0, 1], projection="orthographic",
                extents={"width": 10, "height": 5})
    S = v.CameraSnapshot
    bad = [
        dict(),
        dict(camera=S(**snap), zoom=2.0),
        dict(camera=S(**snap), fit=True),
        dict(zoom=0.0),
        dict(zoom=-1.0),
        dict(camera=S(**{**snap, "extents": None})),
        dict(camera=S(**{**snap, "projection": "perspective", "extents": None})),
        dict(camera=S(**{**snap, "target": [10, 0, 0]})),
        dict(camera=S(**{**snap, "up": [-1, 0, 0]})),
        dict(camera=S(**{**snap, "up": [0, 0, 0]})),
        dict(camera=S(**{**snap, "extents": {"width": 10}})),
    ]
    for kw in bad:
        assert code(v.set_camera, c, **kw) == "INVALID_PARAMS", kw
    assert env.vp.camera_sets == []
    with pytest.raises(Exception):
        S(**{**snap, "eye": [1, 2]})


def test_set_camera_vector_length_via_pipeline(env, dispatcher):
    from defusion import actions

    snap = {"eye": [1, 2], "target": [0, 0, 0], "up": [0, 0, 1], "projection": "orthographic",
            "extents": {"width": 1, "height": 1}}
    r = actions.call("view", {"action": "set_camera", "params": {"camera": snap}},
                     frozenset({"view.set_camera"}), ["all"])
    assert r.is_error and r.structured_content["error"]["code"] == "INVALID_PARAMS"


def test_orbit_yaw_pitch_roll(env):
    v, vp = env.view, env.vp
    set_vp(vp, eye=(10, 0, 0))
    r = v.set_camera(env.ctx("cm"), orbit_yaw=A(90))
    assert approx(r["eye"], (0, 10, 0)) and approx(r["target"], (0, 0, 0))
    assert approx(vec(vp._camera.upVector), (0, 0, 1))
    set_vp(vp, eye=(10, 0, 0))
    v.set_camera(env.ctx("cm"), orbit_pitch=A(30))
    cam = vp._camera
    d = vec(cam.eye)
    assert math.isclose(math.sqrt(sum(x * x for x in d)), 10.0)
    assert approx(d, (10 * math.cos(math.radians(30)), 0, 10 * math.sin(math.radians(30))), 1e-9) or \
        approx(d, (10 * math.cos(math.radians(30)), 0, -10 * math.sin(math.radians(30))), 1e-9)
    up = vec(cam.upVector)
    assert math.isclose(sum(x * x for x in up), 1.0)
    assert abs(sum(a * b for a, b in zip(d, up))) < 1e-9
    set_vp(vp, eye=(10, 0, 0))
    v.set_camera(env.ctx("cm"), orbit_roll=A(90))
    assert approx(vec(vp._camera.eye), (10, 0, 0)) and approx(vec(vp._camera.upVector), (0, 1, 0))


def test_pitch_direction_is_right_hand_about_screen_right(env):
    # looking from +X toward the origin with up Z, screen right is +Y; +pitch about +Y moves
    # (10,0,0) to (0,0,-10) by the right-hand rule.
    set_vp(env.vp, eye=(10, 0, 0))
    env.view.set_camera(env.ctx("cm"), orbit_pitch=A(90))
    assert approx(vec(env.vp._camera.eye), (0, 0, -10))


def test_pan(env):
    set_vp(env.vp, eye=(10, 0, 0))
    env.view.set_camera(env.ctx("cm"), pan_x=L(20), pan_y=L(30))
    cam = env.vp._camera
    # screen right = +Y, screen up = +Z; eye and target both shift by the same delta
    assert approx(vec(cam.target), (0, 2, 3)) and approx(vec(cam.eye), (10, 2, 3))


def test_zoom_orthographic_and_perspective(env):
    set_vp(env.vp)
    r = env.view.set_camera(env.ctx(), zoom=2.0)
    assert r["extents"] == {"width": 600.0, "height": 400.0} and r["eye"] == [100.0, 0.0, 0.0]
    set_vp(env.vp, ctype=1)
    r = env.view.set_camera(env.ctx("cm"), zoom=2.0)
    assert approx(r["eye"], (5, 0, 0)) and r["target"] == [0.0, 0.0, 0.0]


def test_view_projection_fit(env):
    r = env.view.set_camera(env.ctx(), view="top", projection="perspective", fit=True)
    assert env.vp.fits == 1 and r["projection"] == "perspective" and r["orientation"] == "top"
    assert env.vp.camera_sets[-1].isSmoothTransition is False and env.vp.refreshes >= 1


def test_snapshot_round_trip_inches(env):
    v = env.view
    set_vp(env.vp, eye=(2.54, 5.08, 7.62), target=(2.54, 0, 0), up=(0, 1, 0), extents=(25.4, 12.7))
    snap = v.get_camera(env.ctx(length="in", angle="rad"))
    assert snap["length_unit"] == "in" and snap["eye"] == pytest.approx([1.0, 2.0, 3.0]) and snap["target"][0] == pytest.approx(1.0)
    before = env.vp._camera.copy()
    v.set_camera(env.ctx(), view="top", projection="perspective")
    assert env.vp._camera.cameraType == 1
    out = v.set_camera(env.ctx(length="in"), camera=v.CameraSnapshot(**snap))
    cam = env.vp._camera
    assert approx(vec(cam.eye), vec(before.eye), 1e-9) and approx(vec(cam.target), vec(before.target), 1e-9)
    assert approx(vec(cam.upVector), vec(before.upVector)) and cam.cameraType == 0
    assert approx(cam.extents, before.extents, 1e-9) and out["length_unit"] == "in"


def test_snapshot_perspective_degrees(env):
    snap = dict(eye=[0, 0, 100], target=[0, 0, 0], up=[0, 1, 0], projection="perspective", fov=60)
    env.view.set_camera(env.ctx(), camera=env.view.CameraSnapshot(**snap, extra_key=1))
    assert env.vp._camera.perspectiveAngle == pytest.approx(math.pi / 3)
    assert approx(vec(env.vp._camera.eye), (0, 0, 10))


def test_set_camera_failure_restores_original(env):
    set_vp(env.vp, eye=(7, 7, 7))
    orig = vec(env.vp._camera.eye)
    env.vp._camera.extents_ok = False  # zoom on an orthographic camera fails after the view change
    assert code(env.view.set_camera, env.ctx(), view="top", zoom=2.0) == "FUSION_ERROR"
    assert vec(env.vp._camera.eye) == orig
    assert env.vp.camera_sets[-1].isSmoothTransition is False


def test_set_camera_restore_failure(env):
    env.vp.set_exc, env.vp.set_exc_after = RuntimeError("boom"), 1  # first set ok, the restore fails
    env.vp._camera.extents_ok = False
    with pytest.raises(ActionError) as ei:
        env.view.set_camera(env.ctx(), view="top", zoom=2.0)
    assert ei.value.code == "FUSION_ERROR" and "restore failed" in ei.value.message
    assert "boom" in ei.value.message and ei.value.__cause__ is not None


# --- capture ----------------------------------------------------------------------------------

import glob
import os
import tempfile

from defusion.results import Image


@pytest.fixture
def tmpdirs():
    pattern = os.path.join(tempfile.gettempdir(), "defusion-capture-*")
    before = set(glob.glob(pattern))
    yield lambda: set(glob.glob(pattern)) - before
    assert set(glob.glob(pattern)) - before == set()


def test_capture_limits(env, tmpdirs):
    c = env.ctx()
    for kw in (dict(width=8193), dict(width=8192, height=4096), dict(width=0), dict(height=0),
               dict(width=-5)):
        assert code(env.view.capture, c, **kw) == "INVALID_PARAMS", kw
    assert env.vp.saves == []
    img = env.view.capture(c, width=8192, height=2048)
    assert (img.meta["width"], img.meta["height"]) == (8192, 2048)


def test_capture_default_size_and_result(env, tmpdirs):
    img = env.view.capture(env.ctx())
    assert isinstance(img, Image) and img.png == env.vp.png
    assert (img.meta["width"], img.meta["height"]) == (1600, 1000)
    assert env.vp.saves[0][0] == "plain" and env.vp.saves[0][2:4] == (1600, 1000)
    assert img.meta["camera"]["projection"] == "orthographic"
    assert {"view", "fit", "background", "antialias"} <= set(img.meta)
    assert env.vp.camera_sets == []  # nothing changed, nothing restored
    assert not os.path.exists(env.vp.saves[0][1])


def test_capture_options_path(env, tmpdirs):
    env.view.capture(env.ctx(), width=64, height=32, background="transparent")
    env.view.capture(env.ctx(), width=64, height=32, antialias=False)
    a, b = env.vp.saves
    assert a[0] == b[0] == "options" and a[2:6] == (64, 32, True, True) and b[4:6] == (False, False)


def test_capture_unsupported_options(env, monkeypatch, tmpdirs):
    viewfakes.install(monkeypatch, with_options=False)
    env.app.activeViewport = env.vp
    assert code(env.view.capture, env.ctx(), background="transparent") == "UNSUPPORTED"
    assert code(env.view.capture, env.ctx(), antialias=False) == "UNSUPPORTED"
    assert env.view.capture(env.ctx()).png  # plain path needs no options class
    assert tmpdirs() == set()


def test_capture_bad_background(env):
    with pytest.raises(ActionError) as ei:
        env.view.capture(env.ctx(), background="#FF0000")
    assert ei.value.code == "INVALID_PARAMS"
    assert ei.value.message == "background must be 'viewport' or 'transparent'"


def test_capture_view_fit_restores_camera(env, tmpdirs):
    set_vp(env.vp, eye=(7, 8, 9))
    env.vp._camera.viewOrientation = core.ViewOrientations.ArbitraryViewOrientation
    img = env.view.capture(env.ctx(), view="top", fit=True)
    assert env.vp.saves[0][-1] == core.ViewOrientations.TopViewOrientation and env.vp.fits == 1
    assert img.meta["camera"]["orientation"] == "top" and img.meta["view"] == "top"
    cam = env.vp._camera
    assert vec(cam.eye) == (7, 8, 9) and cam.viewOrientation == core.ViewOrientations.ArbitraryViewOrientation
    assert env.vp.camera_sets[-1].isSmoothTransition is False and env.vp.camera_sets[0].isSmoothTransition is False


def test_capture_restores_after_failures(env, tmpdirs):
    set_vp(env.vp, eye=(7, 8, 9))
    env.vp.save_result = False
    assert code(env.view.capture, env.ctx(), view="top") == "FUSION_ERROR"
    assert vec(env.vp._camera.eye) == (7, 8, 9)
    env.vp.save_result, env.vp.save_exc = True, RuntimeError("save blew up")
    with pytest.raises(RuntimeError):
        env.view.capture(env.ctx(), view="top")
    assert vec(env.vp._camera.eye) == (7, 8, 9)


def test_capture_missing_or_bad_file(env, tmpdirs):
    env.vp.write_file = False
    assert code(env.view.capture, env.ctx()) == "FUSION_ERROR"
    env.vp.write_file, env.vp.png = True, b"GIF89a-not-a-png"
    assert code(env.view.capture, env.ctx()) == "FUSION_ERROR"


def test_capture_restore_failure(env, tmpdirs):
    env.vp.set_exc, env.vp.set_exc_after = RuntimeError("no restore"), 1
    with pytest.raises(ActionError) as ei:
        env.view.capture(env.ctx(), view="top")
    assert ei.value.code == "FUSION_ERROR" and "restore failed: no restore" in ei.value.message
    env.vp.set_exc, env.vp.set_exc_after = RuntimeError("no restore"), 2
    env.vp.save_exc = RuntimeError("save failed")
    with pytest.raises(ActionError) as ei:
        env.view.capture(env.ctx(), view="top")
    assert "no restore" in ei.value.message and "save failed" in ei.value.message
    assert isinstance(ei.value.__cause__, RuntimeError)


def test_capture_result_through_pipeline(env, dispatcher):
    from defusion import actions

    r = actions.call("view", {"action": "capture", "params": {"width": 64, "height": 64}},
                     frozenset({"view.capture"}), ["all"])
    assert not r.is_error and r.content[0].type == "image" and r.content[-1].type == "text"


# --- view_sheet -------------------------------------------------------------------------------


def test_view_sheet_tiles_in_order(env, tmpdirs):
    set_vp(env.vp, eye=(7, 8, 9), ctype=1)
    imgs = env.view.view_sheet(env.ctx(), tile=256)
    V = core.ViewOrientations
    assert [s[-1] for s in env.vp.saves] == [
        V.IsoTopRightViewOrientation, V.FrontViewOrientation, V.TopViewOrientation, V.RightViewOrientation]
    assert all(s[0] == "plain" and s[2:4] == (256, 256) for s in env.vp.saves)
    assert env.vp.fits == 4
    assert all(c.cameraType == 0 for c in env.vp.camera_sets[:-1] if c.viewOrientation != V.ArbitraryViewOrientation)
    assert len(imgs) == 4 and all(isinstance(i, Image) and i.png == env.vp.png for i in imgs)
    assert imgs[0].meta == {"tile": 256, "background": "viewport", "tiles": ["iso", "front", "top", "right"]}
    cam = env.vp._camera
    assert vec(cam.eye) == (7, 8, 9) and cam.cameraType == 1
    assert env.vp.camera_sets[-1].isSmoothTransition is False


def test_view_sheet_limits_and_background(env, tmpdirs):
    for t in (63, 2049, 0):
        assert code(env.view.view_sheet, env.ctx(), tile=t) == "INVALID_PARAMS"
    assert code(env.view.view_sheet, env.ctx(), background="#FFF") == "INVALID_PARAMS"
    assert env.vp.saves == []
    env.view.view_sheet(env.ctx(), tile=64)
    env.view.view_sheet(env.ctx(), tile=2048, background="transparent", antialias=False)
    assert env.vp.saves[-1][0] == "options" and env.vp.saves[-1][4:6] == (True, False)


def test_view_sheet_restores_on_failure_and_cleans_up(env, tmpdirs):
    set_vp(env.vp, eye=(7, 8, 9))
    env.vp.save_result = False
    assert code(env.view.view_sheet, env.ctx()) == "FUSION_ERROR"
    assert vec(env.vp._camera.eye) == (7, 8, 9) and len(env.vp.saves) == 1


def test_view_sheet_restore_failure(env, tmpdirs):
    env.vp.set_exc, env.vp.set_exc_after = RuntimeError("no restore"), 4
    with pytest.raises(ActionError) as ei:
        env.view.view_sheet(env.ctx())
    assert ei.value.code == "FUSION_ERROR" and "restore failed: no restore" in ei.value.message


def test_view_sheet_through_pipeline_returns_four_images(env, dispatcher):
    from defusion import actions

    r = actions.call("view", {"action": "view_sheet", "params": {"tile": 64}},
                     frozenset({"view.view_sheet"}), ["all"])
    assert not r.is_error, r.structured_content
    assert [c.type for c in r.content] == ["image"] * 4 + ["text"]
    assert len(r.structured_content["result"]) == 4
