# design.md — docs/specs/view/
> De-Fusion MCP — `view` tool

## 1. Module and shared definitions
File `De-Fusion/defusion/tools/view.py`. Every action is `@action("view", ...)`; the framework
(core § 5) supplies validation, dispatch, result shaping (`Image`, core § 5.5) and errors. Every
action first gets `vp = ctx.app.activeViewport`; `ctx.document` `None` or `vp` `None` →
`NO_DOCUMENT`. No action needs a `Design` (works in any workspace with a viewport).

Literals and maps at the top of `view.py`:
```python
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
```
Standard views follow Fusion's own definition of each orientation (the ViewCube and the
document's Y-up/Z-up setting decide what "front" means).

Background parameter: `"viewport"` or `"transparent"`; anything else (including `"#RRGGBB"`) →
`INVALID_PARAMS` ("background must be 'viewport' or 'transparent'").

Helpers:
- `_unit_names(ctx)` → `(length_unit, angle_unit)`: the `_LEN_PER_CM` key whose value equals
  `ctx.length_out(1.0)` (relative 1e-9), and `"deg"` if `ctx.angle_out(math.pi) ≈ 180` else
  `"rad"`.
- `_camera_dict(ctx, vp)` → the § 2.5 result.
- `_apply_view(vp, view, fit, projection=None)`: `cam = vp.camera`; `cam.isSmoothTransition =
  False`; `projection` → `cam.cameraType = getattr(adsk.core.CameraTypes, _PROJ[projection])`;
  `view` → `cam.viewOrientation = getattr(adsk.core.ViewOrientations, _VIEWS[view])`;
  `vp.camera = cam`; `fit` → `vp.fit()`; `vp.refresh()`.
- `_restore(vp, original)`: `original.isSmoothTransition = False`; `vp.camera = original`;
  `vp.refresh()`.

## 2. Design
### 2.1 capture — read, timeout 120 (REQ-VIEW-001)
```python
@action("view", access="read", timeout=120)
def capture(ctx: Ctx, width: int | None = None, height: int | None = None,
            view: StandardView | None = None, fit: bool = False,
            background: str = "viewport", antialias: bool = True) -> Image
```
1. Validate `background`. `w = width or vp.width`, `h = height or vp.height`; either outside
   1..`MAX_SIDE` or `w * h > MAX_PIXELS` → `INVALID_PARAMS` naming the limit.
2. `original = vp.camera` (Fusion returns a copy). If `view` or `fit`: `_apply_view(vp, view,
   fit)`.
3. `vp.refresh()`; `png = _save_png(ctx, vp, w, h, background, antialias)`;
   `cam = _camera_dict(ctx, vp)` (the camera used for the shot).
4. `finally`: when step 2 changed the camera, `_restore` (§ 2.2).
5. Return `Image(png, {"width": w, "height": h, "view": view, "fit": fit, "background":
   background, "antialias": antialias, "camera": cam})`.

`_save_png(ctx, vp, w, h, background, antialias) -> bytes`:
- Inside the § 2.3 temp directory, `path = <dir>/capture.png`.
- `background == "viewport" and antialias` → `ok = vp.saveAsImageFile(path, w, h)`.
- Otherwise options are needed: `hasattr(adsk.core, "SaveImageFileOptions")` and
  `hasattr(vp, "saveAsImageFileWithOptions")`, else `UNSUPPORTED` ("this Fusion build cannot
  capture with transparent background or without antialiasing"). `o =
  adsk.core.SaveImageFileOptions.create(path)`; `o.width = w`; `o.height = h`;
  `o.isBackgroundTransparent = background != "viewport"`; `o.isAntiAliased = antialias`;
  `ok = vp.saveAsImageFileWithOptions(o)`.
- `not ok` or the file is missing → `FUSION_ERROR` ("Fusion did not write the viewport image").
  Bytes not starting with `b"\x89PNG\r\n\x1a\n"` → `FUSION_ERROR`.

### 2.2 Camera restore (REQ-VIEW-002)
`capture` and `view_sheet` take `original = vp.camera` before any change and call `_restore`
in a `finally` block. If `_restore` raises, the action raises `ActionError("FUSION_ERROR",
"camera restore failed: <e>")`, chained to the original error when the capture also failed (the
message then contains both texts), even if the image was captured. `set_camera` uses the same
restore on failure (§ 2.6).

### 2.3 Temporary files (REQ-VIEW-003)
`saveAsImageFile*` writes to a path Fusion chooses to create; a pre-created file (`mkstemp`) can
be rejected or locked on Windows, so each capture uses a fresh directory:
```python
with tempfile.TemporaryDirectory(prefix="defusion-capture-", ignore_cleanup_errors=True) as d:
    path = os.path.join(d, "capture.png")
    ... save, then read bytes with open(path, "rb") ...
```
The context manager deletes the directory and file on return and on every exception. Bytes are
fully read inside the `with` block. `view_sheet` uses one directory per tile via the same helper.
No path is ever returned to the client.

### 2.4 view_sheet — read, timeout 120 (REQ-VIEW-004)
```python
@action("view", access="read", timeout=120)
def view_sheet(ctx: Ctx, tile: int = 512, background: str = "viewport",
               antialias: bool = True) -> Image
```
`background` is validated as in § 1. `tile` outside 64..2048 → `INVALID_PARAMS`. Tiles in order: `("iso", "iso_top_right")`, `("front", "front")`, `("top", "top")`,
`("right", "right")`. `original = vp.camera`; for each tile `_apply_view(vp, view, fit=True,
projection="orthographic")`, `_save_png(ctx, vp, tile, tile, background, antialias)`. `finally`
`_restore`. Return the core § 5.5 multi-image result: four ImageContent tiles in that order (the
total counts against the core § 5.5 image byte limit) and meta `{"tile": tile, "background":
background, "tiles": ["iso", "front", "top", "right"]}`.

### 2.5 get_camera — read (REQ-VIEW-005)
```python
@action("view", access="read")
def get_camera(ctx: Ctx) -> dict
```
`cam = vp.camera`. Projection from `cam.cameraType` (reverse `_PROJ`). Orientation from
`cam.viewOrientation` (reverse `_VIEWS`; `ArbitraryViewOrientation` or unknown → `"arbitrary"`).
Orthographic: `ok, w, h = cam.getExtents()` (`ok` false → `FUSION_ERROR`). Perspective kinds:
`cam.perspectiveAngle` (radians).
```json
{"eye": [x, y, z], "target": [x, y, z], "up": [0.0, 0.0, 1.0],
 "projection": "orthographic", "orientation": "iso_top_right",
 "extents": {"width": 120.0, "height": 80.0}, "fov": null,
 "viewport": {"width": 1600, "height": 1000},
 "length_unit": "mm", "angle_unit": "deg"}
```
`eye`, `target`, `extents` through `ctx.length_out`; `up` unit vector; `fov` through
`ctx.angle_out` (null for orthographic; `extents` null for perspective kinds); unit names from
`_unit_names`. The snapshot is self-describing so `set_camera` can restore it whatever the
caller's `units`.

### 2.6 set_camera — write, `undo_group=False` (REQ-VIEW-006)
```python
class CameraSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")   # accepts get_camera output unchanged
    eye: list[float]; target: list[float]; up: list[float]   # length 3 each
    projection: Projection
    extents: dict[str, float] | None = None     # {"width", "height"}; required for orthographic
    fov: float | None = None                    # required for perspective kinds
    length_unit: Literal["mm", "cm", "m", "in", "ft", "um"] = "mm"
    angle_unit: Literal["deg", "rad"] = "deg"

@action("view", access="write", undo_group=False)
def set_camera(ctx: Ctx, camera: CameraSnapshot | None = None,
               view: StandardView | None = None, projection: Projection | None = None,
               fit: bool = False, orbit_yaw: Angle | None = None, orbit_pitch: Angle | None = None,
               orbit_roll: Angle | None = None, pan_x: Length | None = None,
               pan_y: Length | None = None, zoom: float | None = None) -> dict
```
`undo_group=False`: camera changes are not part of Fusion's undo history, so a transaction would
only add an empty undo entry. Validation (`INVALID_PARAMS`): no control given; `camera` with any
other control; `zoom <= 0`; snapshot vectors not length 3; orthographic snapshot without
`extents`; perspective snapshot without `fov`; `eye == target`; `up` parallel to `target - eye`.

`original = vp.camera`; then inside `try`:
- Snapshot: `s = 1 / _LEN_PER_CM[length_unit]` (cm per unit), `a = math.pi/180` for `deg` else 1.
  `cam = vp.camera`; `isSmoothTransition = False`; `cameraType`; `eye =
  Point3D.create(*(v*s))`; `target` likewise; `upVector = Vector3D.create(*up)`; orthographic →
  `cam.setExtents(w*s, h*s)` (false → `FUSION_ERROR`); perspective → `cam.perspectiveAngle =
  fov*a`; `cam.isFitView = False`; `vp.camera = cam`.
- Otherwise in this order: `projection`/`view`/`fit` via `_apply_view` (only when one is given);
  then, if any of orbit/pan/zoom is given, `cam = vp.camera`, `isSmoothTransition = False`, and:
  - vectors in cm: `d = eye - target`, `fwd = unit(-d)`, `up = unit(upVector)`,
    `right = unit(fwd × up)`, `screen_up = right × fwd`.
  - orbit: yaw rotates `d` about `up` by `orbit_yaw.rad`; pitch rotates `d` and `up` about
    `right` (recomputed after yaw) by `orbit_pitch.rad`; roll rotates `up` about `fwd`
    (recomputed) by `orbit_roll.rad`. Rodrigues: `v cosθ + (k × v) sinθ + k (k·v)(1 − cosθ)`.
    Positive angles follow the right-hand rule. `eye = target + d`.
  - pan: `delta = right * pan_x.cm + screen_up * pan_y.cm`; `eye += delta`; `target += delta`
    (the model appears to move left/down for positive values).
  - zoom (>1 closer): orthographic → `ok, w, h = cam.getExtents()`, `cam.setExtents(w/zoom,
    h/zoom)`; perspective kinds → `eye = target + d/zoom`.
  - `cam.isFitView = False`; `vp.camera = cam`.
- `vp.refresh()`; return `_camera_dict(ctx, vp)`.
On any exception: `_restore(vp, original)` and re-raise (restore failure handled as § 2.2).
Gotcha: Fusion adjusts extents to the viewport aspect ratio; a snapshot restores exactly only on a
viewport of the same pixel size.

## 3. Testability
### 3.1 Unit (`tests/test_view.py`, fake adsk)
Fakes added additively to `tests/fakes/adsk/core.py`: `Viewport` (`camera` getter returns a copy,
setter stores a copy and records it, `refresh`, `fit` sets fake extents, `width`, `height`,
`saveAsImageFile` and `saveAsImageFileWithOptions` writing a fixed minimal PNG,
configurable to return false or raise), `Camera` (`eye`, `target`, `upVector`, `cameraType`,
`viewOrientation`, `perspectiveAngle`, `getExtents`, `setExtents`, `isSmoothTransition`,
`isFitView`), `SaveImageFileOptions`, `ViewOrientations`, `CameraTypes`, `Application.activeViewport`.
Tests:
- `capture`: limits (8193, 8192 × 4096, 0) → `INVALID_PARAMS`; default size = viewport size;
  `saveAsImageFile` used for viewport+antialias, options path otherwise; options class missing →
  `UNSUPPORTED`; `background "#FF0000"` → `INVALID_PARAMS` with the § 1 message; camera
  restored after success, after false and after raise; restore raising → `FUSION_ERROR`; no
  `defusion-capture-*` directory left in `tempfile.gettempdir()` after each case.
- `view_sheet`: 4 orientation sets recorded in order, four ImageContent items in order iso, front,
  top, right, camera restored, `tile` 63 → `INVALID_PARAMS`.
- `get_camera`: ortho and perspective shapes; `in` units scale eye by 1/2.54; no viewport →
  `NO_DOCUMENT`.
- `set_camera`: each validation error; orbit yaw 90 maps eye (10,0,0) about target 0, up Z, to
  (0,10,0); pitch and roll preserve |d| and orthonormality; pan; zoom ortho and perspective;
  snapshot round-trip with `length_unit "in"`; failure mid-way restores the original camera.

### 3.2 Live (`LIVE_SCENARIOS` in `view.py`, `python scripts/live_check.py --only view`)
`LIVE_SCENARIOS` is a list of `(name, "view.<action>", async fn(session))` (core § 10); each
scenario below lists its tag. Actions are called with `live_check.call(session, tool, action,
params)`. Setup helper `_box(session)` is `live_check.box(session, 20, 10, 5)` (core § 10).
Scenarios (PNG size read from the IHDR chunk with `struct`):
- `capture_size` (`view.capture`): 640 × 480 → IHDR 640 × 480; 8193 → `INVALID_PARAMS`; 8192 × 4096 →
  `INVALID_PARAMS`.
- `capture_backgrounds` (`view.capture`): `transparent` → valid PNG with IHDR colour type 6; `"#FF0000"` →
  `INVALID_PARAMS`; `antialias` false → valid PNG.
- `capture_restores_camera` (`view.capture`): `get_camera`, `capture` with `view "top"`, `fit` true, `get_camera`
  → equal within 1e-6.
- `view_sheet` (`view.view_sheet`): tile 256 → four 256 × 256 images in order; camera unchanged afterwards.
- `get_camera` (`view.get_camera`): orthographic result has `eye`, `target`, `up`,
  `projection`, `orientation`, `extents`, `viewport`, `length_unit`, `angle_unit`; with
  `units: {"length": "in"}` `length_unit` is `"in"`.
- `set_camera_controls` (`view.set_camera`): orthographic + fit; zoom 2 halves extents; `orbit_yaw` 90 keeps target
  and distance; `pan_x` 10 moves target 10 mm along the prior screen right.
- `set_camera_snapshot` (`view.set_camera`): snapshot, change view, restore snapshot → equal within 1e-6; `camera`
  plus `zoom` → `INVALID_PARAMS`.
