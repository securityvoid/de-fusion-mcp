# requirements.md — docs/specs/sketch/
> De-Fusion MCP — `sketch` tool: sketch listing and description, creation, drawing, text, constraints, dimensions, editing, mirror, pattern, projection, replay and deletion

## Overview
The `sketch` tool reads and builds Fusion sketches: listing sketches, describing one sketch in full
(curves, points, constraints, dimensions, profiles), creating sketches on planes or planar faces,
drawing lines, rectangles, circles, arcs, splines, polygons, ellipses, slots, points and text,
adding and deleting geometric constraints and dimensions, offset, trim, extend, fillet, mirror,
pattern, projection, construction toggling, replay of a sketch description and deletion. All actions are `@action("sketch", ...)` functions in
`De-Fusion/defusion/tools/sketch.py`; schema, validation, Ref resolution, units, dispatch, undo
grouping, change summary, results, errors, paging, help and profiles come from the core framework
(`docs/specs/core/design.md`, cited as "core §").

## Operator ask
2026-11-10: "comprehensive Fusion MCP server ... union of the four servers"

## Dependencies
- **DEP-SK-001**: Core set `docs/specs/core/` (action framework, Ref types, units, results, errors,
  paging, undo grouping, fake `adsk` package, live check harness). Point inputs use core `Point2`
  (core § 5.4), which core provides; no sketch-local point type.
- **DEP-SK-002**: Live scenarios build fixtures with `model.extrude` and
  `model.construction_plane` (`docs/specs/model/design.md`).

## Functional Requirements

### Read
- **REQ-SK-001**: The sketch tool shall provide `list` (read), returning the sketches of one
  component or of every component in the design, paged, each with its Ref, plane Ref, component
  name, profile count, curve count and fully-constrained flag.
  - _Acceptance_: In a design with sketches in the root and in one child component, `list` with no
    params returns `total` equal to both counts summed; `list` with `component` returns only that
    component's sketches; each item has `ref`, `plane`, `component`, `profiles`, `curves`,
    `fully_constrained`, `visible`.
- **REQ-SK-002**: The sketch tool shall provide `info` (read) describing one sketch: its frame in
  model space, points, curves (type, key points by point index, flags), constraints, dimensions,
  texts and profiles (index, area, loop count), as one document or one paged section.
  - _Acceptance_: For a sketch holding a 20 x 10 mm two-point rectangle, `info` returns 4 curves of
    type `line`, exactly 1 profile with `area` 200 (mm²) and `loops` 1, and the
    rectangle's automatic horizontal/vertical constraints; `info` with `section: "curves"`,
    `limit: 2` returns `{"curves": {"items": [2 items], "total": 4, "offset": 0, "next_offset": 2}}`; a result over the size budget returns `RESULT_TOO_LARGE` whose hint names `section`.

### Sketch lifecycle
- **REQ-SK-003**: The sketch tool shall provide `create` (write) making a sketch on a `Plane`
  (origin plane name, construction plane Ref or planar face Ref) in the target component, with an
  optional name.
  - _Acceptance_: `create` with `plane: "XZ"` and `name: "S1"` returns a Ref whose `name` is `S1`;
    a following `list` shows it with `plane` naming the XZ origin plane; `create` on a cylindrical
    face returns `INVALID_PARAMS`.
- **REQ-SK-019**: The sketch tool shall provide `delete` (destructive) removing a sketch.
  - _Acceptance_: `delete` without `confirm` returns `CONFIRM_REQUIRED` and the sketch remains;
    with `confirm: true` it returns the deleted Ref and `list` no longer contains it.

### Drawing
- **REQ-SK-004**: The sketch tool shall provide `rectangle` (write) in modes `two_point`, `center`
  and `three_point`.
  - _Acceptance_: Each mode creates 4 lines and the sketch gains one closed profile; `two_point`
    with `[0,0]`,`[20,10]` gives a profile of area 200 mm²; `three_point` with `[0,0]`,`[10,10]`,
    `[-5,5]` gives a rectangle rotated 45 deg with profile area 100 mm² (±0.01).
- **REQ-SK-005**: The sketch tool shall provide `circle` (write) in modes `center_radius` (radius
  or diameter), `two_point` and `three_point`.
  - _Acceptance_: `center_radius` with `diameter: 20` and `radius: "10 mm"` returns a circle whose
    `info` radius is 10; giving both `radius` and `diameter` returns `INVALID_PARAMS`; `two_point`
    `[0,0]`,`[20,0]` gives center `[10,0]`; `three_point` through `[10,0]`,`[0,10]`,`[-10,0]` gives
    center `[0,0]` radius 10.
- **REQ-SK-006**: The sketch tool shall provide `line` (write) drawing a connected polyline through
  a list of two or more points, optionally closed.
  - _Acceptance_: 4 points with `closed: true` create 4 lines that share end points (each point
    index appears in exactly two lines in `info`) and one profile; 2 points create one line; 1 point
    returns `INVALID_PARAMS`.
- **REQ-SK-007**: The sketch tool shall provide `arc` (write) in modes `three_point` (start, a point
  on the arc, end) and `center_start_sweep` (center, start point, signed sweep angle).
  - _Acceptance_: `center_start_sweep` with center `[0,0]`, start `[10,0]`, sweep `90` gives an arc
    whose end point is `[0,10]` (±1e-6 mm); `three_point` `[10,0]`,`[0,10]`,`[-10,0]` gives radius
    10 and sweep 180.
- **REQ-SK-008**: The sketch tool shall provide `spline` (write) in modes `fit` (fit points) and
  `control` (control points, degree 3 or 5), each with a `closed` option.
  - _Acceptance_: `fit` with 4 points creates one `fitted_spline` with 4 fit points; `closed: true`
    yields `closed: true` in `info` and one profile; `control` with 4 points and degree 3 creates a
    `control_point_spline` with 4 control points.
- **REQ-SK-009**: The sketch tool shall provide `polygon` (write) drawing a regular polygon given
  center, side count (3–64), radius, inscribed or circumscribed mode and rotation.
  - _Acceptance_: `sides: 6`, `radius: 10`, `mode: "inscribed"` creates 6 lines with every vertex
    10 mm from the center; `mode: "circumscribed"` puts every edge midpoint 10 mm from the center;
    `sides: 2` returns `INVALID_PARAMS`.
- **REQ-SK-023**: The sketch tool shall provide `text` (write) adding sketch text with content,
  height, optional font and angle, in mode `box` (position and optional diagonal corner, horizontal
  and vertical alignment), `along_path` or `fit_on_path` (a sketch curve, above or below), where
  `fit_on_path` returns `UNSUPPORTED` on a build without the API.
  - _Acceptance_: `box` text `"AB"` with `height: 5` at `[0, 0]` appears in `info` `texts` with
    `content` `AB`, `height` 5 and `mode` `box`; `along_path` on a line reports `path` = that line's
    curve index; `height: 0` returns `INVALID_PARAMS`; `fit_on_path` succeeds or returns
    `UNSUPPORTED`.
- **REQ-SK-024**: The sketch tool shall provide `ellipse` (write) from a center, the end of the
  major axis and a point on the ellipse.
  - _Acceptance_: center `[0,0]`, major point `[20,0]`, point `[0,10]` gives an `info` curve of type
    `ellipse` with `major_radius` 20, `minor_radius` 10 and one profile of area 628.32 mm² (±0.01);
    a point on the major axis line returns `INVALID_PARAMS`.
- **REQ-SK-025**: The sketch tool shall provide `slot` (write) in modes `center_to_center` and
  `overall`, given two points and a width, built from two lines and two tangent arcs.
  - _Acceptance_: `center_to_center` `[0,0]`,`[30,0]` width 10 creates 2 lines and 2 arcs and one
    profile of area 378.54 mm² (±0.01); `overall` with the same points gives area 278.54 mm²
    (±0.01); width 0 returns `INVALID_PARAMS`.
- **REQ-SK-026**: The sketch tool shall provide `point` (write) adding one or more standalone sketch
  points.
  - _Acceptance_: `points: [[5, 5], [10, -3]]` returns 2 point Refs and `info` `points` gains 2
    items at those coordinates.

### Constraints and dimensions
- **REQ-SK-010**: The sketch tool shall provide `constraint` (write) adding one geometric constraint
  of type `coincident`, `horizontal`, `vertical`, `parallel`, `perpendicular`, `tangent`, `equal`,
  `concentric`, `midpoint`, `collinear`, `symmetry`, `fix`, `smooth` or `curvature`.
  - _Acceptance_: `horizontal` on a sloped line makes its start and end y equal in `info`;
    `fix` sets `fixed: true` on the given curve; wrong entity counts or kinds for a type return
    `INVALID_PARAMS` naming the expected inputs; `curvature` on a Fusion build without the API
    returns `UNSUPPORTED`.
- **REQ-SK-011**: The sketch tool shall provide `auto_constrain` (write) running Fusion's
  AutoConstrain on a sketch with result option `thorough`, `fast` or `adjust`, where the Fusion
  build supports it.
  - _Acceptance_: On a build with `Sketch.autoConstrain`, a sketch with an unconstrained polyline
    returns counts of added constraints and dimensions with `constraints_added` > 0; on a build
    without it the call returns `UNSUPPORTED`; on a licence-restricted account Fusion refuses and the call returns `FUSION_ERROR`.
- **REQ-SK-012**: The sketch tool shall provide `dimension` (write) adding a `distance`,
  `horizontal`, `vertical`, `radial`, `diameter`, `angular` or `offset` dimension, with an
  optional Length or Angle value expression, and a `driven` option.
  - _Acceptance_: `radial` with `value: "7 mm"` on a radius-10 circle changes its `info` radius to
    7 and returns the dimension's parameter name; `angular` with `angle: 30` between two lines
    makes their angle 30 deg; `driven: true` with a value returns `INVALID_PARAMS`; a driven
    dimension reports the current value and `driving: false`.
- **REQ-SK-028**: The sketch tool shall provide `delete_constraint` (destructive) deleting
  geometric constraints by their `info` index.
  - _Acceptance_: Without `confirm` it returns `CONFIRM_REQUIRED` and nothing changes; with
    `confirm: true` deleting a rectangle's horizontal constraint returns its type and `info`
    lists one constraint fewer; an out-of-range index returns `INVALID_PARAMS`.
- **REQ-SK-029**: The sketch tool shall provide `delete_dimension` (destructive) deleting sketch
  dimensions by their `info` index.
  - _Acceptance_: Without `confirm` it returns `CONFIRM_REQUIRED`; with `confirm: true` deleting a
    radial dimension returns its type and parameter name, `info` lists one dimension fewer and the
    circle keeps its radius.

### Editing and projection
- **REQ-SK-013**: The sketch tool shall provide `offset` (write) offsetting a connected chain of
  sketch curves by a distance toward a side point.
  - _Acceptance_: Offsetting the 4 lines of a 20 x 10 rectangle by 2 mm toward an outside point
    creates 4 new lines and a 24 x 14 outer loop (profile areas 200 and 136).
- **REQ-SK-014**: The sketch tool shall provide `trim` (write) removing the segment of a curve
  nearest a given point, bounded by intersections.
  - _Acceptance_: Two crossing lines; trimming one at a point beyond the intersection leaves that
    line ending at the intersection point; the result lists the remaining curve Refs.
- **REQ-SK-015**: The sketch tool shall provide `extend` (write) extending a curve's end nearest a
  given point to the next intersecting curve.
  - _Acceptance_: A line ending 5 mm short of a perpendicular line, extended at its near end, ends
    on that line in `info`.
- **REQ-SK-016**: The sketch tool shall provide `project` (write) bringing edges, faces, bodies,
  sketch curves and sketch points into the sketch in mode `project` (linked or unlinked),
  `cut_edges` (body/sketch-plane cut edges) or `intersect` (intersection with the sketch plane).
  - _Acceptance_: Projecting the top face of a 10 mm box onto an XY sketch creates 4 curves with
    `reference: true`; `cut_edges` of the box on a mid-height XY offset plane creates 4 curves;
    `intersect` gives the same 4 curves; `linked: false` on a build without `Sketch.project2`
    returns `UNSUPPORTED`.
- **REQ-SK-017**: The sketch tool shall provide `set_construction` (write) setting or clearing
  `isConstruction` on a list of sketch curves.
  - _Acceptance_: After `set_construction` with `value: true` on a rectangle's 4 lines, `info`
    shows each with `construction: true` and the sketch has 0 profiles; `value: false` restores 1
    profile.
- **REQ-SK-027**: The sketch tool shall provide `fillet` (write) adding a tangent arc of a given
  radius between two sketch curves, trimming them, with optional points selecting the ends.
  - _Acceptance_: A polyline `[0,20]`,`[0,0]`,`[20,0]` filleted at its corner with radius 5 gives an
    arc of radius 5 whose ends are `[0,5]` and `[5,0]` (±1e-6 mm); radius 0 returns
    `INVALID_PARAMS`.
- **REQ-SK-030**: The sketch tool shall provide `mirror` (write) creating mirrored copies of sketch
  curves across a sketch line, with symmetry constraints by default.
  - _Acceptance_: Line `[10,0]`-`[20,10]` mirrored across line `[0,-10]`-`[0,10]` creates a line
    `[-10,0]`-`[-20,10]` and 2 `symmetry` constraints in `info`; a mirror `line` that is an arc
    returns `INVALID_PARAMS`.
- **REQ-SK-031**: The sketch tool shall provide `pattern` (write) copying sketch curves in a
  rectangular pattern (two directions, counts, spacings, direction angle) or a circular pattern
  (center, count, total angle).
  - _Acceptance_: `rectangular` of a radius-2 circle at `[0,0]` with `count_x: 3, spacing_x: 10,
    count_y: 2, spacing_y: 10` yields 6 circles centered on the 3 x 2 grid; `circular` of a circle
    at `[10,0]` about `[0,0]` with `count: 4` on an XZ sketch adds circles centered at `[0,10]`,
    `[-10,0]`, `[0,-10]` (±1e-6 mm); 501 instances returns `INVALID_PARAMS`.

### Replay
- **REQ-SK-018**: The sketch tool shall provide `replay` (write) building a sketch's geometry,
  constraints and dimensions from a description in the shape `info` emits, into an existing sketch
  or a new sketch on a given plane, and shall expose `describe` (the `info` body) as an importable
  function for the history tool.
  - _Acceptance_: For a sketch with lines, a circle, an arc, a fitted spline, coincident,
    tangent, horizontal and equal constraints and radial and distance dimensions, `info` →
    `replay` onto a new sketch → `info` yields equal curve types, point coordinates (±1e-6 mm),
    constraint types, dimension types and values, and profile count and areas; curves of types
    `replay` cannot build are listed in `skipped`, not created.
- **REQ-SK-032**: `info` shall describe ellipses, sketch texts and standalone points, and `replay`
  shall recreate them (texts in `box`, `along_path` and `fit_on_path` modes; curves Fusion creates
  with an ellipse or box text are recreated by their owner, not drawn twice).
  - _Acceptance_: A sketch with an ellipse, a box text at angle 30 deg, an along-path text on a
    line and a standalone point, after `info` → `replay` onto a new sketch → `info`, has equal
    ellipse center and radii, equal text content, height, angle, mode and box corners (±1e-6 mm),
    the same `path` curve type, the point at the same coordinates, the same curve count, and no
    `skipped` item.

### Cross-cutting
- **REQ-SK-020**: Every point input of the sketch tool shall be a core `Point2` (core § 5.4:
  `[x, y]` sketch space or `[x, y, z]` model space projected onto the sketch plane); every point output shall be `[x, y]` in sketch space in the
  call's output length unit, and `info` shall give the sketch frame in model space.
  - _Acceptance_: On a sketch whose plane is an XY construction plane offset 5 mm, a circle with
    center `[3, 4, 5]` and one with center `[3, 4]` report the same `info` center `[3, 4]`;
    `info.sketch.origin` is `[0, 0, 5]`; with `units: {"length": "in"}` coordinates are in inches.
- **REQ-SK-021**: Every drawing and editing action shall take the target `sketch` Ref, reject
  curves or points from a different sketch, accept `construction` where it creates curves, and
  return the created or remaining curves as curve summaries plus the sketch's profile count
  (`point` returns point Refs, `text` the text, `pattern` counts).
  - _Acceptance_: `line` with `construction: true` returns curves whose `info` shows
    `construction: true`; `trim` given a curve of another sketch returns `INVALID_PARAMS`
    naming the curve; each drawing result has `sketch`, `curves` (each with `ref`, `type`,
    `points`) and `profiles`.
- **REQ-SK-022**: `De-Fusion/defusion/tools/sketch.py` shall define `LIVE_SCENARIOS` in the core
  § 10 format with one scenario per action, named by the action and tagged `"sketch.<action>"`, that exercises the action against the running add-in.
  - _Acceptance_: `python scripts/live_check.py --only sketch` prints one `PASS sketch.<action>`
    line for each of the 28 actions and exits 0; an `UNSUPPORTED` outcome of `auto_constrain`,
    `curvature` or `text` `fit_on_path` counts as a pass. On a licence-restricted account Fusion
    refuses `auto_constrain` with `FUSION_ERROR`; that action is then excluded (REQ-SK-022 is met by
    the other 27 and TASK-SK-010's verify).
