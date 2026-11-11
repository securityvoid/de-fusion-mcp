# requirements.md — docs/specs/view/
> De-Fusion MCP — `view` tool: viewport capture, multi-view sheet, camera read and control

## Overview
The `view` tool returns images of the active viewport and reads or moves the camera. All actions
are `@action("view", ...)` functions in `De-Fusion/defusion/tools/view.py`; schema, validation,
units, dispatch, results (including `Image`), errors, help and profiles come from the core
framework. Section references (§) without a prefix point to `docs/specs/view/design.md`; "core §"
points to `docs/specs/core/design.md`.

## Operator ask
2026-11-10: "comprehensive Fusion MCP server ... union of the four servers"

## Dependencies
- **DEP-VIEW-001**: Core set `docs/specs/core/` (action framework, `Image` results, units, errors,
  live check harness).
- **DEP-VIEW-002**: Live scenarios build a box with `sketch.create`, `sketch.rectangle` and
  `model.extrude` (`docs/specs/sketch/design.md`, `docs/specs/model/design.md`).

## Functional Requirements
- **REQ-VIEW-001**: The view tool shall provide `capture` (read): a PNG of the active viewport
  returned as an `Image` (core § 5.5) with metadata, at a requested width and height (each 1–8192,
  default the viewport size, product at most 16,777,216 pixels), with optional standard view and
  fit applied temporarily, background `viewport` or `transparent`, and antialiasing on or off
  (§ 2.1); total image bytes are bounded per core § 5.5.
  - _Acceptance_: `capture` with width 640, height 480 returns image content whose PNG IHDR is
    640 × 480; width 8193 or 8192 × 4096 returns `INVALID_PARAMS`; `background "#FF0000"` returns
    `INVALID_PARAMS` ("background must be 'viewport' or 'transparent'"); `transparent` gives at least one pixel
    with alpha 0 and one with alpha 255 when a body is visible.
- **REQ-VIEW-002**: `capture` and `view_sheet` shall restore the user's camera after any temporary
  view or fit, including when the capture fails, and report a restore failure as an error (§ 2.2).
  - _Acceptance_: `get_camera` before and after `capture` with `view "top"`, `fit` true returns
    the same eye, target, up and extents within 1e-6; unit test: with `saveAsImageFile` raising,
    the fake viewport's camera equals the original afterwards.
- **REQ-VIEW-003**: `capture` and `view_sheet` shall write Fusion's image to a private temporary
  directory, read it, and delete the directory on success and on every failure path (§ 2.3).
  - _Acceptance_: Unit tests: after success, after `saveAsImageFile` returning false and after it
    raising, no `defusion-capture-*` directory remains under `tempfile.gettempdir()`.
- **REQ-VIEW-004**: The view tool shall provide `view_sheet` (read): four separate PNG images
  (core § 5.5) — iso, front, top, right — each orthographic and fitted, with tile size 64–2048
  (default 512), the same background and antialias options as `capture`, and tile order in the
  metadata; the camera is restored afterwards (§ 2.4).
  - _Acceptance_: `view_sheet` with tile 256 returns four ImageContent items, each a 256 × 256 PNG, and
    metadata `tiles` with 4 entries in the order iso, front, top, right.
- **REQ-VIEW-005**: The view tool shall provide `get_camera` (read): eye, target, up, projection,
  standard orientation, orthographic extents or perspective field of view, and viewport pixel
  size, as a snapshot that `set_camera` accepts back unchanged (§ 2.5).
  - _Acceptance_: The result has `eye`, `target`, `up`, `projection`, `orientation`,
    `viewport.width`, `viewport.height`, `length_unit`, `angle_unit`, and `extents` when
    orthographic or `fov` when perspective; with no document open it returns `NO_DOCUMENT`.
- **REQ-VIEW-006**: The view tool shall provide `set_camera` (write): standard view, projection,
  fit, orbit (yaw, pitch, roll), pan (screen x, y), zoom factor, or restoration of a full
  `get_camera` snapshot; snapshot is exclusive with the other controls; on any failure the original
  camera is restored; the result is the new camera (§ 2.6).
  - _Acceptance_: Orthographic + fit, then `zoom` 2 halves both extents; `orbit_yaw` 90 keeps the
    eye–target distance and target; `pan_x` 10 mm moves the target 10 mm along screen right;
    restoring an earlier snapshot reproduces it within 1e-6; `camera` with `zoom` returns
    `INVALID_PARAMS`; no control returns `INVALID_PARAMS`.
- **REQ-VIEW-008**: `view.py` shall expose `LIVE_SCENARIOS` in the core § 10 format, each tagged
  with its `"view.<action>"`, covering every action, run by
  `python scripts/live_check.py --only view` (§ 3).
  - _Acceptance_: `python scripts/live_check.py --only view` prints one `PASS` line per scenario
    and exits 0 against the running add-in.
