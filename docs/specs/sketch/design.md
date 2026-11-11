# design.md — docs/specs/sketch/
> De-Fusion MCP — `sketch` tool

## 1. Overview
One module, `De-Fusion/defusion/tools/sketch.py`, registers 28 actions with `@action("sketch", ...)`
(core § 5.1). The framework does schema, validation, Ref resolution, units, main-thread dispatch,
undo grouping, change summary, errors, paging and help; this document specifies only what each
action does with the Fusion API and what it returns. Tool summary sentence (core § 5.2):
`Create, read and edit Fusion sketches.`

| Action | Access | Timeout | Section |
|---|---|---|---|
| `list` | read | 30 | § 3.1 |
| `info` | read | 60 | § 3.2 |
| `create` | write | 30 | § 4.1 |
| `delete` | destructive | 30 | § 4.2 |
| `rectangle`, `circle`, `line`, `arc`, `spline`, `polygon`, `ellipse`, `slot`, `point`, `text` | write | 30 | § 5 |
| `constraint` | write | 30 | § 6.1 |
| `auto_constrain` | write | 120 | § 6.2 |
| `dimension` | write | 30 | § 6.3 |
| `delete_constraint`, `delete_dimension` | destructive | 30 | § 6.4, § 6.5 |
| `offset`, `trim`, `extend`, `set_construction`, `fillet` | write | 30 | § 7 |
| `project`, `mirror` | write | 60 | § 7.4, § 7.7 |
| `pattern` | write | 120 | § 7.8 |
| `replay` | write | 180 | § 8 |

All write actions work in parametric and direct design mode; every action calls
`ctx.require_design()` first.

## 2. Shared definitions (module level in `sketch.py`)
### 2.1 Point input
Point parameters use core `Point2` (core § 5.4): `[x, y]` sketch space or `[x, y, z]` model space,
converted with the action's `sketch` param and projected onto the sketch plane. Values arrive in
the action as sketch-space `adsk.core.Point3D` (z = 0, cm) and are passed to Fusion as is.

### 2.2 Point output
`xy(ctx, p3d) -> [ctx.length_out(p3d.x), ctx.length_out(p3d.y)]` for a sketch-space `Point3D`
(`SketchPoint.geometry` is sketch space; `worldGeometry` is model space). Values are not rounded.

### 2.3 Membership check
`check_owned(ctx, sketch, entities, param)`: every `SketchCurve`/`SketchPoint` must have
`entity.parentSketch.entityToken == sketch.entityToken`; otherwise
`ctx.fail("INVALID_PARAMS", f"{param}: {entity token} is not in sketch {sketch.name}")`.
Every action that takes both a `sketch` and curves/points runs it before any change.

### 2.4 Type names
`kind(obj) = obj.objectType.split("::")[-1]` (e.g. `adsk::fusion::SketchLine` → `SketchLine`).

| Fusion class | `type` |
|---|---|
| `SketchLine` | `line` |
| `SketchCircle` | `circle` |
| `SketchArc` | `arc` |
| `SketchFittedSpline` | `fitted_spline` |
| `SketchControlPointSpline` | `control_point_spline` |
| `SketchEllipse` | `ellipse` |
| `SketchEllipticalArc` | `elliptical_arc` |
| `SketchConicCurve` | `conic` |
| `SketchFixedSpline` | `fixed_spline` |
| anything else | the class name |

### 2.5 Curve summary (drawing and editing results)
```json
{"ref": <ctx.ref(curve)>, "type": "line",
 "points": {"start": <ref>, "end": <ref>}}
```
`points` keys by type: line `start,end`; circle and ellipse `center`; arc `center,start,end`; fitted spline
`fit` (list of Refs); control-point spline `control` (list); others `{}`. Point Refs come from
`startSketchPoint`, `endSketchPoint`, `centerSketchPoint`, `fitPoints`, `controlPoints`.

Drawing/editing result shape:
```json
{"sketch": <ref>, "curves": [<curve summary>...], "profiles": <sketch.profiles.count>}
```

### 2.6 Common parameters
Every drawing action (§ 5) takes `sketch: Sketch` first; all but `point` and `text` take
`construction: bool = False` last; when
`construction` is true each created curve gets `isConstruction = True`. Multi-curve creation is
wrapped in `sketch.isComputeDeferred = True` … `finally: sketch.isComputeDeferred = False`.

### 2.7 Fully constrained
`fully_constrained(sketch)`: `sketch.isFullyConstrained` when the attribute exists, else
`all(c.isFullyConstrained for c in sketch.sketchCurves if not c.isReference)`.

### 2.8 Reflection and owned curves
- `reflect(p, a, u) -> Point3D`: reflection of sketch-space point `p` across the infinite line
  through `a` with unit direction `u`: `f = a + ((p - a)·u)·u`; result `2f - p` (z = 0).
- Owned curves: Fusion creates some curves together with another object. `owned_curves(sketch)`
  returns `{curve token: {"kind", "index", "role"}}` from:
  - each `SketchEllipse` `e` at curve index `i`: `getattr(e, "majorAxisLine", None)` →
    `{"kind": "ellipse", "index": i, "role": "major"}`, `minorAxisLine` → role `"minor"` (each
    only when not None);
  - each `sketch.sketchTexts` item at text index `t` whose `definition` is a
    `MultiLineTextDefinition`: `definition.rectangleLines[k]` → `{"kind": "text", "index": t,
    "role": k}`.
  Missing attributes (older builds) yield no entries. Owned curves are recreated by their owner,
  never drawn directly (§ 8.2).

### 2.9 Index lookups
`constraint_type(c)` and `dimension_type(d)` return the § 3.2 `type` strings (class tables of
§ 3.2); shared by `info`, `delete_constraint` and `delete_dimension`.

## 3. Read actions
### 3.1 `list`
```python
@action("sketch", access="read", name="list")
def list_sketches(ctx, component: Component | None = None, offset: int = 0, limit: int = 100) -> dict
```
The Python function is `list_sketches` (a function named `list` would shadow the builtin used in
the module's annotations); the action name is `list`. Components: `[component]` if given else `design.allComponents`. For each, each
`component.sketches` item in order:
```json
{"ref": <ref>, "component": "<component name>", "plane": <ctx.ref(sketch.referencePlane) or null>,
 "profiles": n, "curves": n, "fully_constrained": bool, "visible": bool}
```
`curves` = `sketch.sketchCurves.count`; `visible` = `sketch.isVisible`. `referencePlane` is a
`ConstructionPlane` or `BRepFace` (null when the plane's source was deleted). Returns
`ctx.page(items, offset, limit)`.

### 3.2 `info`
```python
@action("sketch", access="read", timeout=60)
def info(ctx, sketch: Sketch,
         section: Literal["all", "curves", "points", "constraints", "dimensions", "texts",
                          "profiles"] = "all",
         offset: int = 0, limit: int = 100) -> dict
```
Implemented by the module function `describe(ctx, sketch) -> dict` (also imported by the history
tool), which returns the full description below. `section="all"` returns it whole (`offset`/`limit`
ignored; a too-large result gets core `RESULT_TOO_LARGE`, whose hint names `section` and `limit`).
Any other section returns `{"sketch": ..., "units": ..., "<section>": ctx.page(list, offset, limit)}`.

Description (all coordinates sketch space, § 2.2; lengths and angles in output units):
```json
{"sketch": {"ref": <ref>, "component": "name", "plane": <ref|null>,
            "origin": [x,y,z], "x_direction": [x,y,z], "y_direction": [x,y,z],
            "fully_constrained": bool, "is_parametric": bool},
 "units": {"length": "mm", "angle": "deg"},
 "points": [{"index": 0, "ref": <ref>, "xy": [x,y], "fixed": false,
             "origin": true, "reference": false}],
 "curves": [{"index": 0, "ref": <ref>, "type": "line", "construction": false, "fixed": false,
             "reference": false, "fully_constrained": false, "start": 1, "end": 2}],
 "constraints": [{"index": 0, "type": "coincident", "points": [3], "curves": [0]}],
 "dimensions": [{"index": 0, "type": "radial", "points": [], "curves": [4], "value": 10.0,
                 "expression": "10 mm", "parameter": "d1", "driving": true, "text_point": [x,y]}],
 "texts": [{"index": 0, "ref": <ref>, "content": "AB", "height": 5.0, "font": "Arial",
            "angle": 0.0, "mode": "box", "box": [[x,y],[x,y]], "horizontal_align": "left",
            "vertical_align": "bottom", "path": null, "above": null}],
 "profiles": [{"index": 0, "area": 200.0, "loops": 1}]}
```
- `sketch.origin` = `ctx.length_out` of `sketch.origin` (model space), `x_direction`/`y_direction`
  = `sketch.xDirection`/`sketch.yDirection` (unit vectors, not converted). `is_parametric` =
  `sketch.isParametric`.
- `units` = the call's output units.
- **points**: `sketch.sketchPoints` in order; `index` = position. `origin` = point's token equals
  `sketch.originPoint.entityToken`; `fixed` = `isFixed`; `reference` = `isReference`.
  A dict `pidx: token → index` is built first and used for every point reference below.
- **points** include standalone points (created by `point`); they have no curve and replay
  recreates them with `sketchPoints.add` (§ 8.2 step 5).
- **curves**: `sketch.sketchCurves` in order; flags from `isConstruction`, `isFixed`,
  `isReference`, `isFullyConstrained`. A curve in `owned_curves(sketch)` (§ 2.8) also gets
  `"owner": {"kind", "index", "role"}`. A dict `cidx: token → index` is built for curve references
  below. Type-specific keys:
  - line: `start`, `end` (point indices of `startSketchPoint`, `endSketchPoint`).
  - circle: `center` (`centerSketchPoint`), `radius` (`ctx.length_out(radius)`).
  - arc: `center`, `start`, `end`, `radius`, `sweep` = `ctx.angle_out(g.endAngle - g.startAngle)`
    from `g = arc.geometry` (`Arc3D` in sketch space; Fusion arcs run counter-clockwise about the
    sketch normal, so `sweep` is positive).
  - fitted_spline: `fit_points` (indices of `fitPoints`), `closed` (`isClosed`).
  - control_point_spline: `control_points` (indices of `controlPoints`), `degree`
    (`int(degree)`), `closed` (`isClosed`).
  - ellipse: `center` (`centerSketchPoint`), `major_radius` / `minor_radius`
    (`ctx.length_out` of `majorAxisRadius` / `minorAxisRadius`), `major_axis` = `[x, y]` of the
    unit vector `geometry.majorAxis` (sketch space; not converted).
  - every other type: `"replayable": false` and, when present, `center` and `start`/`end` indices.
- **constraints**: `sketch.geometricConstraints` in order. Mapping by class (§ 2.4) to `type` and
  the entity attributes read; each entity goes to `points` (SketchPoint) or `curves` (SketchCurve)
  by its class, preserving attribute order:

  | Class | `type` | Entity attributes |
  |---|---|---|
  | `CoincidentConstraint` | coincident | `point`, `entity` |
  | `HorizontalConstraint` / `HorizontalPointsConstraint` | horizontal | `line` / `pointOne`, `pointTwo` |
  | `VerticalConstraint` / `VerticalPointsConstraint` | vertical | `line` / `pointOne`, `pointTwo` |
  | `ParallelConstraint` | parallel | `lineOne`, `lineTwo` |
  | `PerpendicularConstraint` | perpendicular | `lineOne`, `lineTwo` |
  | `CollinearConstraint` | collinear | `lineOne`, `lineTwo` |
  | `TangentConstraint` | tangent | `curveOne`, `curveTwo` |
  | `EqualConstraint` | equal | `curveOne`, `curveTwo` |
  | `ConcentricConstraint` | concentric | `entityOne`, `entityTwo` |
  | `MidPointConstraint` | midpoint | `point`, `midPointCurve` |
  | `SymmetryConstraint` | symmetry | `entityOne`, `entityTwo`; plus `"symmetry_line": <curve index>` from `symmetryLine` |
  | `SmoothConstraint` | smooth | `curveOne`, `curveTwo` |
  | `CurvatureConstraint` | curvature | `curveOne`, `curveTwo` |
  | anything else | class name | none; `"replayable": false` |

  An entity whose token is not in the sketch's points/curves (e.g. a face used by a surface
  constraint) makes the item `"replayable": false`. A constraint with any entity that is an owned
  curve (§ 2.8) or the start/end point of one gets `"owned": true` (Fusion re-adds these with the
  owner; replay does not).
- **dimensions**: `sketch.sketchDimensions` in order; common keys `value`, `expression`,
  `parameter` (from `dim.parameter.value/expression/name`; `value` via `ctx.length_out` or
  `ctx.angle_out` by type), `driving` (`isDriving`), `text_point` (`xy(textPosition)`):

  | Class | `type` | Entities |
  |---|---|---|
  | `SketchLinearDimension` | `distance` / `horizontal` / `vertical` from `orientation` = `AlignedDimensionOrientation` / `HorizontalDimensionOrientation` / `VerticalDimensionOrientation` | `entityOne`, `entityTwo` |
  | `SketchRadialDimension` | radial | `entity` |
  | `SketchDiameterDimension` | diameter | `entity` |
  | `SketchAngularDimension` | angular | `lineOne`, `lineTwo` |
  | `SketchOffsetDimension` | offset | `line`, `entityTwo` |
  | anything else | class name | `"replayable": false` |

  Driven dimensions have `parameter: null` when `dim.parameter` is None; `value` then comes from
  `dim.value` when present, else null.
- **texts**: `sketch.sketchTexts` in order (`[]` when the attribute is missing); `content` =
  `text`, `height` = `ctx.length_out(height)`, `font` = `fontName`, `angle` =
  `ctx.angle_out(text_angle(t))` (Fusion raises on reading `SketchText.angle`; the helper derives it from the first box line). `mode` from `kind(t.definition)`: `MultiLineTextDefinition` → `box`,
  `AlongPathTextDefinition` → `along_path`, `FitOnPathTextDefinition` → `fit_on_path`; no
  `definition` attribute or another class → `"replayable": false` (other keys still filled).
  - box: `horizontal_align` / `vertical_align` from `definition.horizontalAlignment` /
    `verticalAlignment` (reverse of the § 5.10 maps); `box` = the un-rotated box: take the
    distinct start/end points of the 4 `rectangleLines`, centre `c` = their mean, rotate each by
    `-angle` about `c`, `box = [xy(min corner), xy(max corner)]` of the rotated points. `path`,
    `above` null.
  - along_path: `path` = `cidx` of `definition.path` (not a curve of this sketch →
    `"replayable": false`), `above` = `isAbovePath`, `horizontal_align` as box; `box`,
    `vertical_align` null.
  - fit_on_path: `path`, `above` as along_path; alignments and `box` null.
- **profiles**: `sketch.profiles` in order; `area` = `ctx.area_out(p.areaProperties().area)`,
  `loops` = `p.profileLoops.count`.

Gotchas: profile order and curve tokens can change after any edit; callers re-read `info`.
Projected curves are `reference: true`. Text bold/italic style and flips are not described.

## 4. Sketch lifecycle
### 4.1 `create`
```python
@action("sketch", access="write")
def create(ctx, plane: Plane, component: Component | None = None, name: str | None = None) -> dict
```
- Target `comp = ctx.component(component)`; origin-plane names resolve against it (core § 5.4).
- A `BRepFace` plane must have `geometry.surfaceType == SurfaceTypes.PlaneSurfaceType`, else
  `INVALID_PARAMS` "plane: face is not planar".
- `sk = comp.sketches.add(plane)`. When the face belongs to a body in another occurrence, Fusion
  needs the occurrence: pass `comp.sketches.add(plane, plane.assemblyContext)` when
  `plane.assemblyContext` is not None and `comp` is the root.
- `name` → `sk.name = name`.
- Result `{"sketch": <ref>, "plane": <ref of sk.referencePlane>, "origin": [x,y,z],
  "x_direction": [...], "y_direction": [...]}` (as in § 3.2 `sketch`).
- Shared by `replay` (§ 8) via module function `create_sketch(ctx, plane, component, name)`.

### 4.2 `delete`
```python
@action("sketch", access="destructive")
def delete(ctx, sketch: Sketch) -> dict
```
`r = ctx.ref(sketch)`; `sketch.deleteMe()`; a `False` return → `FUSION_ERROR` "deleteMe returned
false". Result `{"deleted": r}`. Features consuming its profiles become unhealthy; that is visible in
the core change summary and the history tool, not reported here.

## 5. Drawing actions
All return the § 2.5 result except `point` (§ 5.9) and `text` (§ 5.10). `lines = sketch.sketchCurves.sketchLines`,
`circles = sketch.sketchCurves.sketchCircles`, `arcs = sketch.sketchCurves.sketchArcs`.

### 5.1 `rectangle`
```python
def rectangle(ctx, sketch: Sketch, mode: Literal["two_point", "center", "three_point"],
              p1: Point2, p2: Point2, p3: Point2 | None = None,
              construction: bool = False) -> dict
```
- `two_point`: `lines.addTwoPointRectangle(P1, P2)` (opposite corners).
- `center`: `lines.addCenterPointRectangle(P1, P2)` (`p1` center, `p2` a corner).
- `three_point`: `lines.addThreePointRectangle(P1, P2, P3)` (`p1`,`p2` first edge, `p3` sets the
  height); `p3` required, else `INVALID_PARAMS`. `p3` given in another mode → `INVALID_PARAMS`.
- Each returns a `SketchLineList` of 4 lines; Fusion adds its own horizontal/vertical or
  perpendicular/parallel constraints.
- Coincident `p1`/`p2` (zero width or height) → `INVALID_PARAMS` before calling Fusion.

### 5.2 `circle`
```python
def circle(ctx, sketch: Sketch, mode: Literal["center_radius", "two_point", "three_point"],
           center: Point2 | None = None, radius: Length | None = None,
           diameter: Length | None = None, p1: Point2 | None = None, p2: Point2 | None = None,
           p3: Point2 | None = None, construction: bool = False) -> dict
```
- `center_radius`: requires `center` and exactly one of `radius`/`diameter` (>0);
  `circles.addByCenterRadius(C, r_cm)` with `r_cm = radius.cm` or `diameter.cm / 2`.
- `two_point`: `p1`, `p2` are diameter ends; `circles.addByTwoPoints(P1, P2)`.
- `three_point`: `circles.addByThreePoints(P1, P2, P3)`; collinear points → Fusion raises →
  `FUSION_ERROR`.
- Parameters not used by the mode must be None, else `INVALID_PARAMS` naming them.
- A numeric radius does not create a dimension; use `dimension` to make it parametric.

### 5.3 `line`
```python
def line(ctx, sketch: Sketch, points: Annotated[list[Point2], Field(min_length=2)],
         closed: bool = False, construction: bool = False) -> dict
```
- First segment `lines.addByTwoPoints(P0, P1)`; each next segment starts at the previous line's
  `endSketchPoint` (shared point, no coincident constraint needed): `addByTwoPoints(prev.endSketchPoint, Pi)`.
- `closed` (requires ≥ 3 points, else `INVALID_PARAMS`): final segment
  `addByTwoPoints(last.endSketchPoint, first.startSketchPoint)`.
- Consecutive equal points → `INVALID_PARAMS` "points[i] equals points[i-1]".

### 5.4 `arc`
```python
def arc(ctx, sketch: Sketch, mode: Literal["three_point", "center_start_sweep"],
        start: Point2, point: Point2 | None = None, end: Point2 | None = None,
        center: Point2 | None = None, sweep: Angle | None = None,
        construction: bool = False) -> dict
```
- `three_point`: requires `start`, `point` (any point on the arc), `end`;
  `arcs.addByThreePoints(S, M, E)`.
- `center_start_sweep`: requires `center`, `start`, `sweep` (non-zero, |sweep| < 360 deg);
  `arcs.addByCenterStartSweep(C, S, sweep.rad)`. Positive sweep is counter-clockwise about the
  sketch normal; Fusion stores negative sweeps as an arc from the other end, so `info` always
  reports a positive sweep.
- Unused parameters for the mode must be None, else `INVALID_PARAMS`.
- Fillet arcs are made by `fillet` (§ 7.6).

### 5.5 `spline`
```python
def spline(ctx, sketch: Sketch, mode: Literal["fit", "control"],
           points: Annotated[list[Point2], Field(min_length=2)], closed: bool = False,
           degree: Literal[3, 5] = 3, construction: bool = False) -> dict
```
- Points go into an `ObjectCollection` of `Point3D`.
- `fit`: `sketch.sketchCurves.sketchFittedSplines.add(coll)`; `closed` → `spline.isClosed = True`
  (needs ≥ 3 points).
- `control`: needs `len(points) >= degree + 1`, else `INVALID_PARAMS`.
  `sketch.sketchCurves.sketchControlPointSplines.add(coll, degree)` (Fusion's `SplineDegrees`
  values equal the degree). `closed` → `spline.isClosed = True`; if that assignment raises,
  `UNSUPPORTED` "closed control-point splines are not supported by this Fusion build" (the undo
  group rolls back the spline).
- When `sketchControlPointSplines` is missing (`hasattr`), `control` → `UNSUPPORTED`.

### 5.6 `polygon`
```python
def polygon(ctx, sketch: Sketch, center: Point2, sides: Annotated[int, Field(ge=3, le=64)],
            radius: Length, mode: Literal["inscribed", "circumscribed"] = "inscribed",
            rotation: Angle = 0, construction: bool = False) -> dict
```
Computed in Python, no polygon API: vertex radius `R = radius.cm` (inscribed) or
`radius.cm / cos(pi/sides)` (circumscribed); first vertex angle `a0 = rotation.rad` (inscribed) or
`rotation.rad + pi/sides` (circumscribed, so an edge midpoint lies at `rotation`); vertex
`k` = center + R·(cos(a0 + 2πk/sides), sin(a0 + 2πk/sides)). Drawn with the § 5.3 closed-polyline
logic, then `geometricConstraints.addEqual(line0, line_k)` for k = 1..sides-1. `radius <= 0` →
`INVALID_PARAMS`.

### 5.7 `ellipse`
```python
def ellipse(ctx, sketch: Sketch, center: Point2, major_point: Point2, point: Point2,
            construction: bool = False) -> dict
```
- `major_point` is the end of the major axis; `point` is any point on the ellipse.
- `major_point == center`, or `point` on the line through `center` and `major_point` (distance
  < 1e-7 cm) → `INVALID_PARAMS` before calling Fusion.
- `sketch.sketchCurves.sketchEllipses.add(C, M, P)`. Fusion raising (e.g. `point` farther from
  the centre than `major_point` along the minor direction) → `FUSION_ERROR`.
- Shared with replay as `add_ellipse(sketch, C, M, P)`.

### 5.8 `slot`
```python
def slot(ctx, sketch: Sketch, mode: Literal["center_to_center", "overall"],
         p1: Point2, p2: Point2, width: Length, construction: bool = False) -> dict
```
The Fusion API has no documented sketch-slot method, so the slot is built from 2 lines and 2 arcs.
- `width <= 0` or `p1 == p2` → `INVALID_PARAMS`. `r = width.cm / 2`, `d` = unit vector `p1 → p2`,
  `n = (-d.y, d.x)`.
- Arc centres: `center_to_center`: `A = p1`, `B = p2`. `overall` (`p1`, `p2` are the slot's outer
  ends): `A = p1 + d·r`, `B = p2 - d·r`; `|p2 - p1| <= width` → `INVALID_PARAMS`.
- Curves (compute deferred, § 2.6): `L1 = lines.addByTwoPoints(A + n·r, B + n·r)`;
  `L2 = lines.addByTwoPoints(B - n·r, A - n·r)`;
  `arcB = arcs.addByThreePoints(L1.endSketchPoint, B + d·r, L2.startSketchPoint)`;
  `arcA = arcs.addByThreePoints(L2.endSketchPoint, A - d·r, L1.startSketchPoint)`.
- Constraints: `gc.addTangent` for (L1, arcB), (L2, arcB), (L2, arcA), (L1, arcA);
  `gc.addEqual(arcA, arcB)`.
- Result: § 2.5 with `[L1, arcB, L2, arcA]`.

### 5.9 `point`
```python
def point(ctx, sketch: Sketch, points: Annotated[list[Point2], Field(min_length=1)]) -> dict
```
`sketch.sketchPoints.add(P)` per point, in order. Result `{"sketch": <ref>, "points": [{"ref":
<ref>, "xy": [x, y]}...], "profiles": n}`.

### 5.10 `text`
```python
def text(ctx, sketch: Sketch, content: Annotated[str, Field(min_length=1)], height: Length,
         mode: Literal["box", "along_path", "fit_on_path"] = "box",
         position: Point2 | None = None, box_corner: Point2 | None = None,
         path: SketchCurve | None = None, above: bool = True,
         horizontal_align: Literal["left", "center", "right"] = "left",
         vertical_align: Literal["top", "middle", "bottom"] = "bottom",
         font: str | None = None, angle: Angle = 0) -> dict
```
- `height <= 0` → `INVALID_PARAMS`. `content` may contain `\n` (multi-line, box mode).
- `texts = sketch.sketchTexts`; `hasattr(texts, "createInput2")` else `UNSUPPORTED`
  "sketch text needs SketchTexts.createInput2". `inp = texts.createInput2(content, height.cm)`.
- Alignment maps: `left`/`center`/`right` → `adsk.core.HorizontalAlignments.LeftHorizontalAlignment`
  / `CenterHorizontalAlignment` / `RightHorizontalAlignment`; `top`/`middle`/`bottom` →
  `adsk.core.VerticalAlignments.TopVerticalAlignment` / `MiddleVerticalAlignment` /
  `BottomVerticalAlignment`.
- `box`: `position` (first box corner) required; `path` → `INVALID_PARAMS`. `box_corner` (diagonal
  corner) defaults to `position + (W, H)` with `W = max(len(line)) · height.cm`,
  `H = lines · 1.5 · height.cm` over `content.split("\n")`. Zero box width or height →
  `INVALID_PARAMS`. `inp.setAsMultiLine(P, Q, h_align, v_align, 0)`; `inp.angle = angle.rad`.
- `along_path`: `path` required (membership § 2.3); `position`, `box_corner` or a non-zero `angle`
  → `INVALID_PARAMS`. `inp.setAsAlongPath(path, above, h_align, 0)`.
- `fit_on_path`: as `along_path`; `hasattr(inp, "setAsFitOnPath")` else `UNSUPPORTED`
  "fit-on-path text is not supported by this Fusion build"; `inp.setAsFitOnPath(path, above)`.
  Alignments are ignored.
- `font` → `inp.fontName = font`. `t = texts.add(inp)`.
- Result `{"sketch": <ref>, "text": {"ref": <ref>, "content": t.text, "height": <out>,
  "font": t.fontName, "angle": <out>, "mode": mode}, "profiles": n}`.
- Shared with replay as `add_text(ctx, sketch, spec) -> SketchText` (spec = the action's
  parameters in internal units).
- Gotchas: Fusion rotates a box text by `angle` about the box centre (§ 3.2 `box` relies on it;
  the `replay` live scenario's angled text proves it); an unknown font name raises in Fusion →
  `FUSION_ERROR`.

## 6. Constraints and dimensions
### 6.1 `constraint`
```python
def constraint(ctx, sketch: Sketch,
               type: Literal["coincident", "horizontal", "vertical", "parallel", "perpendicular",
                             "tangent", "equal", "concentric", "midpoint", "collinear",
                             "symmetry", "fix", "smooth", "curvature"],
               curves: list[SketchCurve] = [], points: list[SketchPoint] = [],
               symmetry_line: SketchCurve | None = None) -> dict
```
`gc = sketch.geometricConstraints`. Accepted inputs (anything else → `INVALID_PARAMS`
"`<type>` needs <accepted forms>"):

| type | Inputs | Fusion call |
|---|---|---|
| coincident | points[2] or points[1]+curves[1] | `gc.addCoincident(point, entity)` |
| horizontal | curves[1] line, or points[2] | `gc.addHorizontal(line)` / `gc.addHorizontalPoints(p1, p2)` |
| vertical | curves[1] line, or points[2] | `gc.addVertical(line)` / `gc.addVerticalPoints(p1, p2)` |
| parallel | curves[2] lines | `gc.addParallel(l1, l2)` |
| perpendicular | curves[2] lines | `gc.addPerpendicular(l1, l2)` |
| collinear | curves[2] lines | `gc.addCollinear(l1, l2)` |
| tangent | curves[2] | `gc.addTangent(c1, c2)` |
| equal | curves[2] | `gc.addEqual(c1, c2)` |
| concentric | curves[2] circles/arcs/ellipses | `gc.addConcentric(c1, c2)` |
| midpoint | points[1]+curves[1] | `gc.addMidPoint(point, curve)` |
| symmetry | (points[2] or curves[2]) + `symmetry_line` (a line) | `gc.addSymmetry(e1, e2, symmetry_line)` |
| smooth | curves[2], at least one a spline | `gc.addSmooth(c1, c2)` |
| curvature | curves[2] | `gc.addCurvature(c1, c2)`; missing attribute → `UNSUPPORTED` |
| fix | any curves and/or points (≥1) | `e.isFixed = True` per entity |

"line" means `kind == "SketchLine"`. Fusion raising (e.g. over-constrained) → `FUSION_ERROR` with
its message. Result: `{"sketch": <ref>, "constraint": {"type": t, "ref": <ref or null>},
"fully_constrained": bool}`; for `fix`, `"constraint": {"type": "fix", "fixed": n}`.

### 6.2 `auto_constrain`
```python
@action("sketch", access="write", timeout=120)
def auto_constrain(ctx, sketch: Sketch,
                   result: Literal["thorough", "fast", "adjust"] = "thorough") -> dict
```
Feature detection: `hasattr(sketch, "createAutoConstrainInput") and hasattr(sketch, "autoConstrain")`;
missing → `UNSUPPORTED` "AutoConstrain needs a Fusion build from 2026 or later".
`inp = sketch.createAutoConstrainInput()`; `inp.resultOption =
AutoConstrainResultTypes.Option1AutoConstrainResultType` (thorough) / `Option2…` (fast) /
`Option3…` (adjust: may move geometry within tolerance); `r = sketch.autoConstrain(inp)`.
A licence-restricted account makes `autoConstrain` raise ("AutoConstrain is not available since user is restricted") → `FUSION_ERROR`.
`r is None` (option 3 when the sketch is not eligible) → `FUSION_ERROR` "sketch not eligible for
adjust; use thorough or fast". Result `{"sketch": <ref>, "constraints_added":
len(r.addedConstraints), "dimensions_added": len(r.addedDimensions), "moved":
len(r.movedGeometry), "fully_constrained": r.isFullyConstrained}`.

### 6.3 `dimension`
```python
def dimension(ctx, sketch: Sketch,
              type: Literal["distance", "horizontal", "vertical", "radial", "diameter",
                            "angular", "offset"],
              curves: list[SketchCurve] = [], points: list[SketchPoint] = [],
              value: Length | None = None, angle: Angle | None = None,
              driven: bool = False, text_point: Point2 | None = None) -> dict
```
`sd = sketch.sketchDimensions`; `driving = not driven`; `T = text_point` or the default below.

| type | Inputs | Fusion call | Default text point |
|---|---|---|---|
| distance / horizontal / vertical | points[2], or curves[1] line (uses its start/end points) | `sd.addDistanceDimension(a, b, orientation, T, driving)` with `DimensionOrientations.AlignedDimensionOrientation` / `HorizontalDimensionOrientation` / `VerticalDimensionOrientation` | midpoint of a,b + 5 mm along +Y (horizontal), +X (vertical), the left normal (distance) |
| radial | curves[1] circle/arc | `sd.addRadialDimension(c, T, driving)` | center + 1.5·radius at 45° |
| diameter | curves[1] circle/arc | `sd.addDiameterDimension(c, T, driving)` | center + 1.5·radius at 45° |
| angular | curves[2] lines | `sd.addAngularDimension(l1, l2, T, driving)` | see gotcha |
| offset | curves[2] parallel lines | `sd.addOffsetDimension(l1, l2, T, driving)` | midpoint between the lines' midpoints + 5 mm along l1 |

- `angle` is accepted only for `angular`, `value` only for the others; the wrong one →
  `INVALID_PARAMS`. `driven` with a value → `INVALID_PARAMS` "driven dimensions take no value".
- When a value is given: `dim.parameter.expression = value.expr` (or `angle.expr`), preserving
  units and parameter references.
- Angular gotcha: Fusion measures the angle in the quadrant that contains the text point. Default
  `T = X + 5 mm · unit(u1 + u2)`, where `X` is the intersection of the two infinite lines (parallel
  → `INVALID_PARAMS`) and `ui` the unit vector from `X` along line `i` to its farther end point.
  Callers
  wanting the supplementary angle pass `text_point`.
- Result `{"sketch": <ref>, "dimension": {"type": t, "parameter": name|null, "value": v,
  "expression": e|null, "driving": bool}, "fully_constrained": bool}`; `value` in output units.

### 6.4 `delete_constraint`
```python
@action("sketch", access="destructive")
def delete_constraint(ctx, sketch: Sketch,
                      indices: Annotated[list[int], Field(min_length=1)]) -> dict
```
- `indices` are positions in `sketch.geometricConstraints` as listed by `info` (§ 3.2). Duplicate
  or out-of-range index → `INVALID_PARAMS`. All items are read first; an item with
  `isDeletable == False` (attribute present) → `INVALID_PARAMS` "constraint <i> is not deletable"
  before any deletion.
- `c.deleteMe()` per item in descending index order; a `False` return → `FUSION_ERROR`
  (the undo group rolls back earlier deletions).
- Result `{"sketch": <ref>, "deleted": [{"index": i, "type": constraint_type(c)}...],
  "fully_constrained": bool}`.
- Gotcha: indices shift after any edit; callers read `info` first.

### 6.5 `delete_dimension`
```python
@action("sketch", access="destructive")
def delete_dimension(ctx, sketch: Sketch,
                     indices: Annotated[list[int], Field(min_length=1)]) -> dict
```
As § 6.4 over `sketch.sketchDimensions`; result items `{"index": i, "type": dimension_type(d),
"parameter": <dim.parameter.name or null>}`. Deleting a driving dimension deletes its model
parameter; Fusion raising because other expressions use it → `FUSION_ERROR`.

## 7. Editing and projection
### 7.1 `offset`
```python
def offset(ctx, sketch: Sketch, curves: Annotated[list[SketchCurve], Field(min_length=1)],
           distance: Length, side_point: Point2, construction: bool = False) -> dict
```
`coll = ObjectCollection` of `curves`; `new = sketch.offset(coll, side_point, distance.cm)`. Curves must form one connected chain (Fusion raises otherwise → `FUSION_ERROR`).
`distance <= 0` → `INVALID_PARAMS`. Result: § 2.5 with the new curves.

### 7.2 `trim`
```python
def trim(ctx, sketch: Sketch, curve: SketchCurve, point: Point2) -> dict
```
`res = curve.trim(point, True)` (`createConstraints=True`). `point` selects the segment
to delete (the piece of `curve` nearest to it between intersections). `res` is an
`ObjectCollection` of the curves left; the original curve's Ref may be invalid afterwards. Result:
§ 2.5 with `res` (empty list when the whole curve was removed).

### 7.3 `extend`
```python
def extend(ctx, sketch: Sketch, curve: SketchCurve, point: Point2) -> dict
```
`res = curve.extend(point, True)`; `point` selects the end to extend (nearest end).
Nothing to extend to → Fusion raises → `FUSION_ERROR`. Result: § 2.5 with `res`.

### 7.4 `project`
```python
@action("sketch", access="write", timeout=60)
def project(ctx, sketch: Sketch, mode: Literal["project", "cut_edges", "intersect"] = "project",
            edges: list[Edge] = [], faces: list[Face] = [], bodies: list[Body] = [],
            curves: list[SketchCurve] = [], points: list[SketchPoint] = [],
            linked: bool = True) -> dict
```
At least one entity required, else `INVALID_PARAMS`. `curves`/`points` must not belong to
`sketch` itself (`INVALID_PARAMS`).
- `project`: entities in the order edges, faces, bodies, curves, points.
  - `linked` true: `sketch.project(e)` per entity, collecting each returned `ObjectCollection`.
  - `linked` false: requires `hasattr(sketch, "project2")`, else `UNSUPPORTED`;
    `sketch.project2(ObjectCollection(all entities), False)`.
  - Bodies: `sketch.project(body)` projects the body outline. If Fusion rejects a body, the
    action fails with `FUSION_ERROR` naming the body.
- `cut_edges`: only `bodies` allowed (others → `INVALID_PARAMS`); `sketch.projectCutEdges(body)`
  per body.
- `intersect`: `sketch.intersectWithSketchPlane(ObjectCollection(all entities))`.
Returned items that are `SketchCurve`s become curve summaries; `SketchPoint`s are returned as
Refs. Result `{"sketch": <ref>, "curves": [...], "points": [<ref>...], "profiles": n}`.

### 7.5 `set_construction`
```python
def set_construction(ctx, sketch: Sketch,
                     curves: Annotated[list[SketchCurve], Field(min_length=1)],
                     value: bool = True) -> dict
```
`c.isConstruction = value` per curve. Reference (projected) curves accept it in Fusion; if Fusion
raises, `FUSION_ERROR` with the curve's token. Result: § 2.5 with the given curves.

### 7.6 `fillet`
```python
def fillet(ctx, sketch: Sketch, curve_one: SketchCurve, curve_two: SketchCurve, radius: Length,
           point_one: Point2 | None = None, point_two: Point2 | None = None) -> dict
```
- `radius <= 0`, `curve_one` equal to `curve_two`, or exactly one of `point_one`/`point_two` given
  → `INVALID_PARAMS`.
- Points select the ends to fillet. Both omitted: both curves need `startSketchPoint` and
  `endSketchPoint` (else `INVALID_PARAMS` "point_one and point_two are required for <type>");
  of the 4 start/end pairs, the pair with the smallest distance gives `point_one` (on
  `curve_one`) and `point_two` (on `curve_two`).
- `arc = arcs.addFillet(curve_one, P1, curve_two, P2, radius.cm)`; Fusion trims both curves and
  adds tangent constraints. No radius dimension is added.
- Result: § 2.5 with `[arc]`.

### 7.7 `mirror`
```python
@action("sketch", access="write", timeout=60)
def mirror(ctx, sketch: Sketch, curves: Annotated[list[SketchCurve], Field(min_length=1)],
           line: SketchCurve, constrain: bool = True) -> dict
```
The Fusion API has no documented sketch-mirror method, so mirrored copies are built.
- `line` must be a `SketchLine` in `sketch` and not in `curves`; every curve's § 2.4 type must be
  line, circle, arc, fitted_spline, control_point_spline or ellipse; else `INVALID_PARAMS`
  before any change.
- `a = line.startSketchPoint.geometry`, `u` = unit direction to `endSketchPoint`; `R(p) =
  reflect(p, a, u)` (§ 2.8).
- `nmap: token → SketchPoint`. Before drawing, each key point `p` of the curves lying on the
  line (`|R(p) - p| < 1e-7` cm) maps to itself. `Q(p)` = `nmap[p]` if mapped else `R(p)`.
- Per curve, in order (compute deferred, § 2.6), with the § 8.2 builders: line
  `addByTwoPoints(Q(start), Q(end))`; circle `addByCenterRadius(Q(center), radius)`; arc
  `addByThreePoints(Q(end), R(mid), Q(start))` with `mid` = `arc.geometry` evaluated at half sweep;
  fitted spline `Q` of each fit point; control-point spline `R` of each control point, same degree;
  ellipse `add_ellipse(sketch, Q(center), R(center + major_axis·major_radius),
  R(center + minor_dir·minor_radius))`. Closed flag and `isConstruction` copied. Then every key
  point of the original not yet in `nmap` maps to the new curve's key point nearest `R(p)`.
- `constrain`: per mapped pair `(p, nmap[p])` with `nmap[p] is not p`:
  `gc.addSymmetry(p, nmap[p], line)`; per circle pair also `gc.addEqual(c, c')`. Fusion raising →
  `FUSION_ERROR` (the undo group rolls back).
- Result: § 2.5 with the new curves plus `"constraints": n` added.
- Gotcha: ellipse radii and axis direction are not constrained to the original.

### 7.8 `pattern`
```python
@action("sketch", access="write", timeout=120)
def pattern(ctx, sketch: Sketch, curves: Annotated[list[SketchCurve], Field(min_length=1)],
            type: Literal["rectangular", "circular"],
            count_x: Annotated[int, Field(ge=1)] = 1, spacing_x: Length | None = None,
            count_y: Annotated[int, Field(ge=1)] = 1, spacing_y: Length | None = None,
            direction: Angle = 0, center: Point2 | None = None,
            count: Annotated[int, Field(ge=2)] | None = None, total_angle: Angle = 360) -> dict
```
The Fusion API exposes no sketch-pattern object, so instances are copies: `hasattr(sketch,
"copy")` else `UNSUPPORTED`. `coll = ObjectCollection` of `curves`; per instance `m =
adsk.core.Matrix3D.create()`, set as below, then `sketch.copy(coll, m)` (the matrix is in sketch
space; the live scenario on an XZ sketch proves it).
- `rectangular`: `count_x · count_y >= 2`; `spacing_x` required when `count_x > 1`, `spacing_y`
  when `count_y > 1`; `center`, `count` → `INVALID_PARAMS`. `d1 = (cos direction, sin
  direction)`, `d2 = (-sin direction, cos direction)`; instance `(i, j) != (0, 0)`:
  `m.translation = Vector3D.create(t.x, t.y, 0)` with `t = i·spacing_x·d1 + j·spacing_y·d2`.
- `circular`: `center` and `count` required; spacing/count_x/count_y given → `INVALID_PARAMS`.
  `0 < |total_angle| <= 360 deg` else `INVALID_PARAMS`. `step = total / count` when
  `|total| = 360 deg` (±1e-9 rad), else `total / (count - 1)`; instance `k = 1..count-1`:
  `m.setToRotation(k·step, Vector3D.create(0, 0, 1), C)`.
- More than 500 instances → `INVALID_PARAMS`.
- Result `{"sketch": <ref>, "instances": n (excluding the original), "curves": <created curve
  count>, "profiles": n}` (counts only; callers read `info` for the new curves).
- Gotcha: copies are independent; no pattern relation or constraints link them.

## 8. Replay
```python
@action("sketch", access="write", timeout=180)
def replay(ctx, description: dict[str, Any], sketch: Sketch | None = None,
           plane: Plane | None = None, component: Component | None = None,
           name: str | None = None) -> dict
```
Exactly one of `sketch` / `plane` (else `INVALID_PARAMS`); with `plane`, the target is
`create_sketch(ctx, plane, component, name)` (§ 4.1). Body is the module function
`replay_into(ctx, sketch, description) -> dict`.

### 8.1 Input model
`description` is validated by pydantic model `SketchDescription` (`extra="ignore"`) with fields
`units` (optional `{"length", "angle"}`), `points`, `curves`, `constraints`, `dimensions`, `texts` (lists,
default empty; item models `extra="ignore"` with the § 3.2 keys). This is exactly `info` with
`section="all"`, or the concatenated `items` of paged sections. `sketch` and `profiles` keys are
ignored. Validation failure → `INVALID_PARAMS` with pydantic messages.

Numbers in `description` are in `description.units` (fallback: config default units, core § 8):
a length `v` becomes `ctx`-evaluated expression `f"{v} {units.length}"` → cm via
`design.unitsManager.evaluateExpression(expr, "cm")`; angles likewise to radians.

### 8.2 Algorithm
1. `skipped = []`; `pmap: index → SketchPoint`; the description point with `origin: true` maps to
   `sketch.originPoint`.
2. `sketch.isComputeDeferred = True`.
3. Curves in index order. `P(i)` = `pmap[i]` if present else `Point3D(x, y, 0)` of point `i`.
   The per-type calls are module function `build_curve(sketch, type, pts, data)` (`pts` the key
   points as `Point3D` or `SketchPoint`, `data` radius/sweep/degree/closed), also used by `mirror`
   (§ 7.7).
   - curve with `owner` (§ 2.8): not drawn here; mapped in step 4.
   - line: `lines.addByTwoPoints(P(start), P(end))`.
   - circle: `circles.addByCenterRadius(P(center), r)`.
   - arc: `arcs.addByThreePoints(P(start), M, P(end))`, `M` = center + r·(cos, sin) of
     `startAngle + sweep/2` where `startAngle = atan2(start - center)`. (Three-point form lets both
     ends reuse existing SketchPoints.)
   - fitted_spline: `sketchFittedSplines.add(ObjectCollection(P(i) for fit_points))`;
     `isClosed = closed`.
   - control_point_spline: `sketchControlPointSplines.add(ObjectCollection(Point3D…), degree)`;
     `isClosed = closed`.
   - ellipse: `add_ellipse(sketch, P(center), C + major_axis·major_radius,
     C + (-major_axis.y, major_axis.x)·minor_radius)` (§ 5.7), `C` = point `center`'s coordinates.
     Then each description curve with `owner {"kind": "ellipse", "index": i, "role": r}` maps to
     the new ellipse's `majorAxisLine` (`r = "major"`) or `minorAxisLine` (`"minor"`) when not None.
   - `replayable: false` or unknown type → append `{"kind": "curve", "index": i, "reason":
     "type <t> not replayable"}`; no curve.
   After creation, every point index of the curve not yet in `pmap` maps to the new curve's
   matching SketchPoint (`startSketchPoint`, `endSketchPoint`, `centerSketchPoint`, `fitPoints[k]`,
   `controlPoints[k]`). `isConstruction` is set from `construction`. `cmap[i] = curve`.
4. Texts in index order via `add_text` (§ 5.10) with the description's content, height, font,
   angle, mode, alignments, `above`, `box` (first and second corner) and `path` (`cmap[path]`).
   `replayable: false`, or `path` unmapped → skipped `{"kind": "text", "index": t, "reason":
   "text not replayable" | "entity skipped"}`; `UNSUPPORTED`/Fusion exception → skipped with
   `reason: "fusion: <message>"`. After a box text is created, each description curve with
   `owner {"kind": "text", "index": t, "role": k}` maps to the new text's
   `definition.rectangleLines[k]`. Owned-curve mapping (here and step 3) sets `cmap[j]` and maps
   the curve's `start`/`end` point indices not yet in `pmap` to its start/end SketchPoints. An owned
   curve left unmapped is skipped with `reason: "owner not recreated"`.
5. Points still not in `pmap` (standalone points and points of skipped curves): `pmap[i] =
   sketch.sketchPoints.add(Point3D)`.
6. `sketch.isComputeDeferred = False`.
7. Constraints in order via the § 6.1 table (entities from `pmap`/`cmap`). `owned: true` items are
   not replayed and not reported (Fusion re-added them with the owner). Skipped when
   `replayable: false` or any entity index is unmapped (`reason: "entity skipped"`); a Fusion
   exception is caught and recorded as skipped with `reason: "fusion: <message>"`.
8. Dimensions in order via the § 6.3 calls with `isDriving = driving` and the given `text_point`;
   driving ones then get `parameter.expression = f"{value} {unit}"` (length or angle unit by type).
   Same skip rules as step 7 (dimensions on owned curves are replayed against the mapped curves).
9. `isFixed = True` on every mapped point/curve with `fixed` or `reference` true (last, so fixing
   does not block steps 7–8). Reference curves are recreated as ordinary fixed curves; the link to
   their source geometry is not recreated.

Result:
```json
{"sketch": <ref>, "curves": [<ref or null per description curve>], "points": n,
 "constraints": n, "dimensions": n, "texts": n, "skipped": [{"kind": "curve|text|constraint|dimension",
 "index": i, "reason": "..."}], "profiles": n}
```
Gotcha: dimension expressions that referenced the source sketch's own parameters (`d1`) are
replayed as values, so parameter names in the new sketch differ.

## 9. Errors
Only core codes (core § 5.10) are used. Action-specific causes:

| Code | Cause |
|---|---|
| `INVALID_PARAMS` | mode/parameter mismatch, wrong entity count or kind, entity from another sketch, non-planar face, degenerate geometry detected before calling Fusion, bad `description` |
| `UNSUPPORTED` | `auto_constrain` (build without the API), `curvature`, `project(linked=false)`, `spline(mode="control")` or closed control-point spline, `text` (no `createInput2`) or `text(mode="fit_on_path")`, `pattern` (no `Sketch.copy`) on a build without the API |
| `FUSION_ERROR` | Fusion rejects the operation (over-constraint, `auto_constrain` on a licence-restricted account, collinear 3-point circle, offset of an open non-chain, extend with no target) |

## 10. Testability
### 10.1 Unit (fake adsk)
`tests/fakes/sketch.py` adds sketch fakes used with the core fake package (core § 10), attached to
the core fake `Component.sketches`: `FakeSketch` (`sketchCurves` with `sketchLines`,
`sketchCircles`, `sketchArcs` (with `addFillet`), `sketchFittedSplines`,
`sketchControlPointSplines`, `sketchEllipses`; `sketchPoints` (with `add`), `sketchTexts` (with
`createInput2`, `add` and an input fake with `setAsMultiLine`/`setAsAlongPath`/optional
`setAsFitOnPath`), `originPoint`, `geometricConstraints`, `sketchDimensions`, `profiles`,
`modelToSketchSpace`, `origin`, `xDirection`, `yDirection`, `referencePlane`, `isComputeDeferred`,
`offset`, `project`, `projectCutEdges`, `intersectWithSketchPlane`, `deleteMe`, optional `copy`,
optional `project2`, `createAutoConstrainInput`/`autoConstrain`), curve fakes with exact geometry
for lines, circles, three-point and center-sweep arcs, ellipses (with optional axis lines),
splines (fit/control points only), `trim`/`extend` that record calls, text fakes with a
`definition` of each class (box texts own 4 `rectangleLines`), constraint and dimension fakes with
the § 3.2 attributes, `objectType`, `isDeletable` and `deleteMe`, and a profile fake computing loop
area for closed polylines and circles. Each Fusion call is recorded so tests
assert exact API calls and arguments in `tests/test_sketch.py`.

### 10.2 Live
`LIVE_SCENARIOS` in `sketch.py`: a list of `(name, "sketch.<action>", async fn(session))` (core
§ 10), one per action, `name` = action name and tagged with that action, so
`python scripts/live_check.py --only sketch.<action>` runs it. Scenarios call actions with
`live_check.call(session, tool, action, params)`. Each scenario creates its own sketch in the
scratch design and asserts through `info`:

| Scenario | Checks |
|---|---|
| list | two new sketches in the root; `total` ≥ 2; `component` = root returns both |
| info | REQ-SK-002 acceptance incl. section paging |
| create | XZ origin plane + name; planar face of the box; cylindrical face of the cylinder → `INVALID_PARAMS` |
| delete | `CONFIRM_REQUIRED`, then removal |
| rectangle, circle, line, arc, spline, polygon | REQ-SK-004..009 acceptance values |
| ellipse, slot, point, text | REQ-SK-024, REQ-SK-025, REQ-SK-026, REQ-SK-023 acceptance values; `fit_on_path` passes on success or on `UNSUPPORTED` |
| fillet | REQ-SK-027 acceptance |
| delete_constraint, delete_dimension | `CONFIRM_REQUIRED`, then REQ-SK-028 / REQ-SK-029 acceptance |
| mirror | REQ-SK-030 acceptance |
| pattern | REQ-SK-031 acceptance (circular case on an XZ sketch) |
| constraint | horizontal, fix, coincident, tangent, symmetry; `curvature` passes on success or on `UNSUPPORTED` |
| auto_constrain | `constraints_added > 0`, or `UNSUPPORTED`; excluded from TASK-SK-010 verify when Fusion refuses with `FUSION_ERROR` (licence-restricted account) |
| dimension | radial value 7 mm, angular 30 deg, driven |
| offset, trim, extend, set_construction | REQ-SK-013/014/015/017 acceptance |
| project | top face of the box projected; `cut_edges` and `intersect` on a sketch on the mid-height plane |
| replay | REQ-SK-018 and REQ-SK-032 round trips on new sketches |

For `auto_constrain`, `curvature` and `text` `fit_on_path` an `UNSUPPORTED` outcome counts as PASS (the action raises it
only after its `hasattr` check fails).
Fixtures built with other tools (exact params, model design):
- Box (`create` on a face, `project`): `live_check.box(session, 10, 10, 10)` (core § 10; a 10 mm
  cube; faces from the returned body Ref).
- Cylinder (`create` rejection): `sketch.create` `{"plane": "XY"}`, `sketch.circle`
  `{"sketch": <ref>, "mode": "center_radius", "center": [30, 0], "radius": 5}`, `model.extrude`
  `{"profile": {"sketch": <ref>, "index": 0}, "distance": 10}`.
- Offset plane (REQ-SK-016 `cut_edges`/`intersect`, REQ-SK-020): `model.construction_plane`
  `{"method": "offset", "plane": "XY", "offset": 5}`; its result `plane` Ref is the `plane` of
  `sketch.create`.
The live tasks therefore wait on model TASK-MDL-002 (extrude) and TASK-MDL-009
(construction_plane).
