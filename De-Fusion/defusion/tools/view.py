"""Capture the viewport as PNG images and read or set the camera."""
import contextlib
import math
import os
import tempfile
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

import adsk.core

from ..actions import action
from ..context import Ctx
from ..errors import FUSION_ERROR, INVALID_PARAMS, NO_DOCUMENT, UNSUPPORTED, ActionError
from ..results import Image
from ..types import Angle, Length

StandardView = Literal["front", "back", "top", "bottom", "left", "right",
                       "iso_top_right", "iso_top_left", "iso_bottom_right", "iso_bottom_left"]
Projection = Literal["orthographic", "perspective", "perspective_ortho_faces"]
_VIEWS = {"front": "FrontViewOrientation", "back": "BackViewOrientation",
          "top": "TopViewOrientation", "bottom": "BottomViewOrientation",
          "left": "LeftViewOrientation", "right": "RightViewOrientation",
          "iso_top_right": "IsoTopRightViewOrientation", "iso_top_left": "IsoTopLeftViewOrientation",
          "iso_bottom_right": "IsoBottomRightViewOrientation",
          "iso_bottom_left": "IsoBottomLeftViewOrientation"}      # adsk.core.ViewOrientations.<v>
_PROJ = {"orthographic": "OrthographicCameraType", "perspective": "PerspectiveCameraType",
         "perspective_ortho_faces": "PerspectiveWithOrthoFacesCameraType"}  # adsk.core.CameraTypes.<v>
MAX_SIDE = 8192
MAX_PIXELS = 16_777_216
_LEN_PER_CM = {"mm": 10.0, "cm": 1.0, "m": 0.01, "in": 1 / 2.54, "ft": 1 / 30.48, "um": 10000.0}


# --- helpers ---------------------------------------------------------------------------------


def _viewport(ctx):
    if ctx.document is None:
        raise ActionError(NO_DOCUMENT, "no active document")
    vp = ctx.app.activeViewport
    if vp is None:
        raise ActionError(NO_DOCUMENT, "no active viewport")
    return vp


def _unit_names(ctx):
    one = ctx.length_out(1.0)
    length = next(n for n, per in _LEN_PER_CM.items() if math.isclose(one, per, rel_tol=1e-9))
    return length, "deg" if math.isclose(ctx.angle_out(math.pi), 180.0, rel_tol=1e-9) else "rad"


def _enum_name(table, enum, value, default=None):
    for name, member in table.items():
        if getattr(enum, member) == value:
            return name
    return default


def _camera_dict(ctx, vp):
    cam = vp.camera
    projection = _enum_name(_PROJ, adsk.core.CameraTypes, cam.cameraType)
    if projection is None:
        raise ActionError(FUSION_ERROR, "unknown camera type %r" % (cam.cameraType,))
    orientation = _enum_name(_VIEWS, adsk.core.ViewOrientations, cam.viewOrientation, "arbitrary")
    extents = fov = None
    if projection == "orthographic":
        ok, w, h = cam.getExtents()
        if not ok:
            raise ActionError(FUSION_ERROR, "Fusion did not return the camera extents")
        extents = {"width": ctx.length_out(w), "height": ctx.length_out(h)}
    else:
        fov = ctx.angle_out(cam.perspectiveAngle)
    length_unit, angle_unit = _unit_names(ctx)
    return {
        "eye": [ctx.length_out(c) for c in (cam.eye.x, cam.eye.y, cam.eye.z)],
        "target": [ctx.length_out(c) for c in (cam.target.x, cam.target.y, cam.target.z)],
        "up": list(_unit((cam.upVector.x, cam.upVector.y, cam.upVector.z))),
        "projection": projection,
        "orientation": orientation,
        "extents": extents,
        "fov": fov,
        "viewport": {"width": vp.width, "height": vp.height},
        "length_unit": length_unit,
        "angle_unit": angle_unit,
    }


def _apply_view(vp, view, fit, projection=None):
    cam = vp.camera
    cam.isSmoothTransition = False
    if projection is not None:
        cam.cameraType = getattr(adsk.core.CameraTypes, _PROJ[projection])
    if view is not None:
        cam.viewOrientation = getattr(adsk.core.ViewOrientations, _VIEWS[view])
    vp.camera = cam
    if fit:
        vp.fit()
    vp.refresh()


def _restore(vp, original):
    original.isSmoothTransition = False
    vp.camera = original
    vp.refresh()


@contextlib.contextmanager
def _restoring(vp, original, active=True, on_success=True):
    """Restore ``original`` on exit (§ 2.2); a failing restore becomes FUSION_ERROR."""
    try:
        yield
    except Exception as exc:
        if active:
            try:
                _restore(vp, original)
            except Exception as rexc:
                raise ActionError(
                    FUSION_ERROR, "camera restore failed: %s (after error: %s)" % (rexc, exc)
                ) from exc
        raise
    else:
        if active and on_success:
            try:
                _restore(vp, original)
            except Exception as rexc:
                raise ActionError(FUSION_ERROR, "camera restore failed: %s" % rexc) from rexc


# --- vector math (plain tuples) ----------------------------------------------------------------


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _mul(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    n = _norm(a)
    return (a[0] / n, a[1] / n, a[2] / n)


def _rotate(v, k, theta):
    """Rodrigues: rotate ``v`` about the unit axis ``k`` by ``theta`` (right-hand rule)."""
    c, s = math.cos(theta), math.sin(theta)
    return _add(_add(_mul(v, c), _mul(_cross(k, v), s)), _mul(k, _dot(k, v) * (1 - c)))


def _xyz(p):
    return (p.x, p.y, p.z)


# --- get_camera / set_camera -------------------------------------------------------------------


@action("view", access="read")
def get_camera(ctx: Ctx) -> dict:
    """Read the active viewport camera as a self-describing snapshot that set_camera accepts.

    Returns eye, target, up, projection, orientation, extents (orthographic) or fov
    (perspective), the viewport pixel size and the length and angle units used."""
    return _camera_dict(ctx, _viewport(ctx))


class CameraSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")  # accepts get_camera output unchanged
    eye: list[float] = Field(min_length=3, max_length=3)
    target: list[float] = Field(min_length=3, max_length=3)
    up: list[float] = Field(min_length=3, max_length=3)
    projection: Projection
    extents: dict[str, float] | None = None  # {"width", "height"}; required for orthographic
    fov: float | None = None  # required for perspective kinds
    length_unit: Literal["mm", "cm", "m", "in", "ft", "um"] = "mm"
    angle_unit: Literal["deg", "rad"] = "deg"


def _invalid(msg):
    return ActionError(INVALID_PARAMS, msg)


def _check_snapshot(s):
    if s.projection == "orthographic":
        if not s.extents or not {"width", "height"} <= set(s.extents):
            raise _invalid("camera.extents with width and height is required for an orthographic camera")
    elif s.fov is None:
        raise _invalid("camera.fov is required for a perspective camera")
    eye, target, up = tuple(s.eye), tuple(s.target), tuple(s.up)
    d = _sub(target, eye)
    if _norm(d) == 0:
        raise _invalid("camera.eye and camera.target must differ")
    if _norm(_cross(up, d)) <= 1e-9 * _norm(up) * _norm(d):
        raise _invalid("camera.up must not be parallel to the view direction")


def _apply_snapshot(vp, s):
    scale = 1 / _LEN_PER_CM[s.length_unit]
    cam = vp.camera
    cam.isSmoothTransition = False
    cam.cameraType = getattr(adsk.core.CameraTypes, _PROJ[s.projection])
    cam.eye = adsk.core.Point3D.create(*(v * scale for v in s.eye))
    cam.target = adsk.core.Point3D.create(*(v * scale for v in s.target))
    cam.upVector = adsk.core.Vector3D.create(*s.up)
    if s.projection == "orthographic":
        if not cam.setExtents(s.extents["width"] * scale, s.extents["height"] * scale):
            raise ActionError(FUSION_ERROR, "Fusion rejected the camera extents")
    else:
        cam.perspectiveAngle = s.fov * (math.pi / 180 if s.angle_unit == "deg" else 1.0)
    cam.isFitView = False
    vp.camera = cam


def _apply_controls(vp, orbit_yaw, orbit_pitch, orbit_roll, pan_x, pan_y, zoom):
    cam = vp.camera
    cam.isSmoothTransition = False
    target = _xyz(cam.target)
    d = _sub(_xyz(cam.eye), target)
    up = _unit(_xyz(cam.upVector))
    right = _unit(_cross(_unit(_mul(d, -1)), up))
    screen_up = _cross(right, _unit(_mul(d, -1)))
    if orbit_yaw is not None:
        d = _rotate(d, up, orbit_yaw.rad)
    if orbit_pitch is not None:
        axis = _unit(_cross(_unit(_mul(d, -1)), up))
        d = _rotate(d, axis, orbit_pitch.rad)
        up = _rotate(up, axis, orbit_pitch.rad)
    if orbit_roll is not None:
        up = _rotate(up, _unit(_mul(d, -1)), orbit_roll.rad)
    if pan_x is not None or pan_y is not None:
        delta = _add(
            _mul(right, pan_x.cm if pan_x is not None else 0.0),
            _mul(screen_up, pan_y.cm if pan_y is not None else 0.0),
        )
        target = _add(target, delta)
    if zoom is not None:
        if cam.cameraType == adsk.core.CameraTypes.OrthographicCameraType:
            ok, w, h = cam.getExtents()
            if not ok or not cam.setExtents(w / zoom, h / zoom):
                raise ActionError(FUSION_ERROR, "Fusion rejected the camera extents")
        else:
            d = _mul(d, 1 / zoom)
    eye = _add(target, d)
    cam.eye = adsk.core.Point3D.create(*eye)
    cam.target = adsk.core.Point3D.create(*target)
    cam.upVector = adsk.core.Vector3D.create(*up)
    cam.isFitView = False
    vp.camera = cam


@action("view", access="write", undo_group=False)
def set_camera(ctx: Ctx, camera: CameraSnapshot | None = None,
               view: StandardView | None = None, projection: Projection | None = None,
               fit: bool = False, orbit_yaw: Angle | None = None, orbit_pitch: Angle | None = None,
               orbit_roll: Angle | None = None, pan_x: Length | None = None,
               pan_y: Length | None = None, zoom: float | None = None) -> dict:
    """Set the viewport camera from a snapshot, or from a standard view plus orbit, pan and zoom.

    Pass either `camera` (a get_camera result) or any of view, projection, fit, orbit_yaw,
    orbit_pitch, orbit_roll, pan_x, pan_y and zoom, applied in that order. Positive orbit angles
    follow the right-hand rule; zoom above 1 moves closer. Camera changes are not undoable. A
    snapshot restores exactly only on a viewport of the same pixel size."""
    vp = _viewport(ctx)
    adjust = [orbit_yaw, orbit_pitch, orbit_roll, pan_x, pan_y, zoom]
    others = [view, projection, *adjust]
    if camera is not None:
        if fit or any(o is not None for o in others):
            raise _invalid("camera cannot be combined with any other control")
    elif not fit and all(o is None for o in others):
        raise _invalid("give camera, or at least one of view, projection, fit, orbit, pan or zoom")
    if zoom is not None and zoom <= 0:
        raise _invalid("zoom must be greater than 0")
    if camera is not None:
        _check_snapshot(camera)
    original = vp.camera
    with _restoring(vp, original, on_success=False):
        if camera is not None:
            _apply_snapshot(vp, camera)
        else:
            if view is not None or projection is not None or fit:
                _apply_view(vp, view, fit, projection)
            if any(a is not None for a in adjust):
                _apply_controls(vp, *adjust)
        vp.refresh()
        return _camera_dict(ctx, vp)


# --- capture ---------------------------------------------------------------------------------

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _check_background(background):
    if background not in ("viewport", "transparent"):
        raise _invalid("background must be 'viewport' or 'transparent'")


def _save_png(ctx, vp, w, h, background, antialias):
    """The viewport rendered at ``w`` x ``h`` as PNG bytes, via a private temporary directory."""
    plain = background == "viewport" and antialias
    if not plain and not (hasattr(adsk.core, "SaveImageFileOptions")
                          and hasattr(vp, "saveAsImageFileWithOptions")):
        raise ActionError(
            UNSUPPORTED,
            "this Fusion build cannot capture with transparent background or without antialiasing",
        )
    with tempfile.TemporaryDirectory(prefix="defusion-capture-", ignore_cleanup_errors=True) as d:
        path = os.path.join(d, "capture.png")
        if plain:
            ok = vp.saveAsImageFile(path, w, h)
        else:
            o = adsk.core.SaveImageFileOptions.create(path)
            o.width = w
            o.height = h
            o.isBackgroundTransparent = background != "viewport"
            o.isAntiAliased = antialias
            ok = vp.saveAsImageFileWithOptions(o)
        if not ok or not os.path.isfile(path):
            raise ActionError(FUSION_ERROR, "Fusion did not write the viewport image")
        with open(path, "rb") as f:
            png = f.read()
    if not png.startswith(_PNG_SIGNATURE):
        raise ActionError(FUSION_ERROR, "Fusion did not write the viewport image")
    return png


@action("view", access="read", timeout=120)
def capture(ctx: Ctx, width: int | None = None, height: int | None = None,
            view: StandardView | None = None, fit: bool = False,
            background: str = "viewport", antialias: bool = True) -> Image:
    """Capture the active viewport as a PNG image.

    Defaults to the viewport's pixel size and current camera. `view` and `fit` change the camera
    only for the shot; the user's camera is restored afterwards. `background` is 'viewport' or
    'transparent'. Sides are limited to 8192 pixels and the area to 16777216 pixels."""
    vp = _viewport(ctx)
    _check_background(background)
    w = vp.width if width is None else width
    h = vp.height if height is None else height
    if not (1 <= w <= MAX_SIDE and 1 <= h <= MAX_SIDE):
        raise _invalid("width and height must each be 1 to %d pixels" % MAX_SIDE)
    if w * h > MAX_PIXELS:
        raise _invalid("width x height must not exceed %d pixels" % MAX_PIXELS)
    original = vp.camera
    with _restoring(vp, original, active=view is not None or fit):
        if view is not None or fit:
            _apply_view(vp, view, fit)
        vp.refresh()
        png = _save_png(ctx, vp, w, h, background, antialias)
        cam = _camera_dict(ctx, vp)
    return Image(png, {"width": w, "height": h, "view": view, "fit": fit,
                       "background": background, "antialias": antialias, "camera": cam})


_SHEET = (("iso", "iso_top_right"), ("front", "front"), ("top", "top"), ("right", "right"))


@action("view", access="read", timeout=120)
def view_sheet(ctx: Ctx, tile: int = 512, background: str = "viewport",
               antialias: bool = True) -> list[Image]:
    """Capture four orthographic tiles (iso, front, top, right), each fitted to the model, as four PNG images.

    `tile` is the pixel size of each square tile (64 to 2048). The user's camera is restored
    afterwards. `background` is 'viewport' or 'transparent'."""
    vp = _viewport(ctx)
    _check_background(background)
    if isinstance(tile, bool) or not 64 <= tile <= 2048:
        raise _invalid("tile must be 64 to 2048 pixels")
    meta = {"tile": tile, "background": background, "tiles": [name for name, _ in _SHEET]}
    original = vp.camera
    with _restoring(vp, original):
        pngs = []
        for _, std_view in _SHEET:
            _apply_view(vp, std_view, True, "orthographic")
            pngs.append(_save_png(ctx, vp, tile, tile, background, antialias))
    return [Image(png, dict(meta)) for png in pngs]


# --- live scenarios (design § 3.2) -------------------------------------------------------------

from .live_common import _MM, _lc  # noqa: E402


async def _call(session, act, params, units=_MM):
    return await _lc().call(session, "view", act, params, units=units)


async def _do(session, act, params, units=_MM):
    env = await _call(session, act, params, units)
    assert env["ok"], "view.%s: %s" % (act, env.get("error"))
    return env["result"]


async def _invalid_call(session, act, params):
    env = await _call(session, act, params)
    assert not env["ok"] and env["error"]["code"] == "INVALID_PARAMS", env


async def _box(session):
    return await _lc().box(session, 20, 10, 5)


async def _pngs(session, act, params):
    """The PNG byte strings of a view call (the harness ``call`` keeps only the envelope)."""
    import base64

    res = await session.call_tool("view", {"action": act, "params": params, "units": _MM})
    assert not res.is_error, res.structured_content
    pngs = [base64.b64decode(c.data) for c in res.content if c.type == "image"]
    assert pngs and all(p.startswith(_PNG_SIGNATURE) for p in pngs), res.structured_content
    return pngs


def _ihdr(png):
    import struct

    width, height = struct.unpack(">II", png[16:24])
    return width, height, png[25]  # byte 25 is the colour type


def _same_camera(a, b):
    for key in ("eye", "target", "up"):
        assert all(abs(x - y) < 1e-6 for x, y in zip(a[key], b[key])), (key, a[key], b[key])
    for key in ("width", "height"):
        if a["extents"]:
            assert abs(a["extents"][key] - b["extents"][key]) < 1e-6, (a["extents"], b["extents"])


async def _capture_size(session):
    await _box(session)
    (png,) = await _pngs(session, "capture", {"width": 640, "height": 480})
    assert _ihdr(png)[:2] == (640, 480)
    await _invalid_call(session, "capture", {"width": 8193})
    await _invalid_call(session, "capture", {"width": 8192, "height": 4096})


async def _capture_backgrounds(session):
    await _box(session)
    (png,) = await _pngs(session, "capture", {"width": 256, "height": 256, "background": "transparent"})
    assert _ihdr(png)[2] == 6, _ihdr(png)
    await _invalid_call(session, "capture", {"background": "#FF0000"})
    await _pngs(session, "capture", {"width": 256, "height": 256, "antialias": False})


async def _capture_restores_camera(session):
    await _box(session)
    before = await _do(session, "get_camera", {})
    await _pngs(session, "capture", {"view": "top", "fit": True, "width": 256, "height": 256})
    _same_camera(before, await _do(session, "get_camera", {}))


async def _view_sheet(session):
    await _box(session)
    before = await _do(session, "get_camera", {})
    pngs = await _pngs(session, "view_sheet", {"tile": 256})
    assert len(pngs) == 4 and [_ihdr(p)[:2] for p in pngs] == [(256, 256)] * 4
    _same_camera(before, await _do(session, "get_camera", {}))


async def _get_camera(session):
    await _box(session)
    await _do(session, "set_camera", {"view": "iso_top_right", "projection": "orthographic", "fit": True})
    cam = await _do(session, "get_camera", {})
    for key in ("eye", "target", "up", "projection", "orientation", "extents", "viewport",
                "length_unit", "angle_unit"):
        assert key in cam, (key, cam)
    assert cam["projection"] == "orthographic" and cam["extents"], cam
    inches = await _do(session, "get_camera", {}, units={"length": "in", "angle": "deg"})
    assert inches["length_unit"] == "in", inches


def _dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


async def _set_camera_controls(session):
    await _box(session)
    base = await _do(session, "set_camera", {"view": "iso_top_right", "projection": "orthographic", "fit": True})
    zoomed = await _do(session, "set_camera", {"zoom": 2})
    assert abs(zoomed["extents"]["width"] - base["extents"]["width"] / 2) < 1e-3 * base["extents"]["width"], (
        base["extents"], zoomed["extents"])
    base = await _do(session, "set_camera", {"view": "iso_top_right", "projection": "orthographic", "fit": True})
    turned = await _do(session, "set_camera", {"orbit_yaw": 90})
    assert _dist(turned["target"], base["target"]) < 1e-6, (base["target"], turned["target"])
    assert abs(_dist(turned["eye"], turned["target"]) - _dist(base["eye"], base["target"])) < 1e-6
    base = await _do(session, "set_camera", {"view": "iso_top_right", "projection": "orthographic", "fit": True})
    panned = await _do(session, "set_camera", {"pan_x": 10})
    right = _cross(_sub(base["target"], base["eye"]), base["up"])
    right = _unit(right)
    moved = _sub(panned["target"], base["target"])
    assert _dist(moved, _mul(right, 10)) < 1e-6, (moved, right)


async def _set_camera_snapshot(session):
    await _box(session)
    snap = await _do(session, "set_camera", {"view": "front", "fit": True})
    await _do(session, "set_camera", {"view": "top", "fit": True})
    _same_camera(snap, await _do(session, "set_camera", {"camera": snap}))
    await _invalid_call(session, "set_camera", {"camera": snap, "zoom": 2})


LIVE_SCENARIOS = [
    ("capture_size", "view.capture", _capture_size),
    ("capture_backgrounds", "view.capture", _capture_backgrounds),
    ("capture_restores_camera", "view.capture", _capture_restores_camera),
    ("view_sheet", "view.view_sheet", _view_sheet),
    ("get_camera", "view.get_camera", _get_camera),
    ("set_camera_controls", "view.set_camera", _set_camera_controls),
    ("set_camera_snapshot", "view.set_camera", _set_camera_snapshot),
]
