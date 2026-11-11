# design.md — docs/specs/model/
> De-Fusion MCP — `model` tool: features, primitives, surfaces, sheet metal, construction geometry,
> parameters, appearance and design settings

## 1. Overview
The `model` tool holds every solid/surface modeling action and the design-level settings. Each
action is an `@action("model", ...)` function (core design § 5.1). Schema, validation, Ref
resolution, units, main-thread dispatch, undo grouping, change summary, errors, paging and help are
the framework's (core design § 5); this document specifies only the action bodies.

Conventions used below:
- Types are those of core design § 5.4, including `Entity` (any entity, no class filter); actions
  that take `Entity` check the class themselves.
- "vi(x)" means `x.value_input()` (a `ValueInput.createByString(x.expr)`, core design § 8), so
  user expressions and parameter references survive in the timeline.
- "coll(xs)" means an `adsk.core.ObjectCollection` holding `xs` in order.
- `Operation` is the core enum (`new_body`, `join`, `cut`, `intersect`, `new_component`) mapped to
  `adsk.fusion.FeatureOperations`.
- Every write action that creates a feature returns the **feature result** (§ 3.2) unless stated.
- Unless stated, timeout is 30 s and the action works in both parametric and direct designs.
- All actions call `ctx.require_design()` first (→ `NO_DESIGN`).

## 2. Module layout
```text
De-Fusion/defusion/tools/
  model.py              # imports the model_* modules (registers their actions); LIVE_SCENARIOS
  model_common.py       # shared helpers (§ 3)
  model_features.py     # § 4 solid features, thread_query/list_threads, rename
  model_coil.py         # § 5 coil
  model_primitives.py   # § 6
  model_surfaces.py     # § 7
  model_sheet.py        # § 8
  model_construct.py    # § 9
  model_params.py       # § 10
  model_appearance.py   # § 11
  model_design.py       # § 12, § 13
  model_live.py         # § 14 live scenarios, collected into model.LIVE_SCENARIOS
tests/
  model_fakes.py        # model-specific fake adsk objects layered over tests/fakes/adsk
  test_model_*.py
```
`model.py` imports every `model_*.py` module so that `import defusion.tools.model` registers all
63 actions under tool `model`, and sets `LIVE_SCENARIOS = model_live.SCENARIOS`.

### 2.1 Access classes and timeouts (all 63 actions)
- read (10): `thread_query`, `list_threads`, `parameter_list`, `material_list`, `units_get`,
  `config_inspect`, `find_attributes`, `find_by_token`, `visibility_get`, `selection_sets_list`.
- destructive (4): `parameter_delete`, `remove_body`, `delete_entities`, `set_design_type`
  (switching to direct discards the timeline). The framework adds `confirm`; without
  `confirm: true` the call returns `CONFIRM_REQUIRED` (core design § 5.3).
- write: every other action (49).
- timeouts: `coil`, `thread`, `sheet_flange`, `sheet_bend`, `sheet_unfold`, `sheet_flat_pattern` 60 s;
  `compute_all` 300 s; all others 30 s.

### 2.2 Core types used
`Length`, `Angle`, `Expr`, `Point3`, `Body`, `Face`, `Edge`, `Sketch`, `SketchCurve`,
`SketchPoint`, `Feature`, `Component`, `Parameter`, `Plane`, `Axis`, `Profile`, `Operation`, and
`Entity`, all defined (with their Ref resolution and errors) in core design § 5.4 (DEP-MDL-001).

## 3. Shared helpers (`model_common.py`)
### 3.1 Functions
| Helper | Behaviour |
|---|---|
| `coll(items)` | `ObjectCollection.create()` + `add` each item. |
| `owner(entity) -> Component` | `entity.parentComponent` if present; for `Profile` `profile.parentSketch.parentComponent`; for `BRepFace`/`BRepEdge`/`BRepVertex` `entity.body.parentComponent`; for `SketchCurve`/`SketchPoint` `entity.parentSketch.parentComponent`; last resort `entity.parent` (live: construction planes and axes have no `parentComponent`). |
| `same_owner(ctx, entities, param) -> Component` | `owner` of each; more than one distinct component → `INVALID_PARAMS` "all <param> must belong to one component". |
| `kind(entity) -> str` | `entity.objectType` (e.g. `adsk::fusion::BRepBody`) last segment: `BRepBody`, `BRepFace`, `BRepEdge`, `BRepVertex`, `SketchLine`, ..., or `"Feature"` when the object has `timelineObject` and `healthState` and the segment ends with `Feature` (live: construction planes and axes also have both members). |
| `expect(ctx, items, allowed, param)` | each `kind(item)` must be in `allowed` (a tuple of kind names; `"SketchCurve"` matches every `Sketch*` curve kind) else `INVALID_PARAMS` "<param> accepts <allowed>; got <kind>". |
| `uniform(ctx, items, groups, param) -> str` | all items must fall in one group of `groups` (`{"bodies": ("BRepBody",), "features": ("Feature",), "faces": ("BRepFace",)}`); returns the group name; mixed → `INVALID_PARAMS`. |
| `require_api(ctx, obj, attr, what)` | `hasattr(obj, attr)` else `ctx.fail("UNSUPPORTED", f"{what} is not available in this Fusion build", hints=[...])`. |
| `participants(ctx, inp, bodies)` | when `bodies` is not `None`: `require_api(inp, "participantBodies", ...)`, `inp.participantBodies = list(bodies)`. |
| `positive(ctx, value, param)` | `value.cm` (or `.value` for `Expr`, `.rad` for `Angle`) `> 0` else `INVALID_PARAMS` "<param> must be > 0". |
| `origin_plane(comp, plane)` | when `plane` is one of the active component's origin planes and `comp` differs, returns `comp`'s plane of the same role (`xYConstructionPlane`/`xZConstructionPlane`/`yZConstructionPlane`); else `plane`. |
| `activated(ctx, comp)` | context manager: if `comp` is not `design.activeComponent`, remembers `design.activeOccurrence` (None = root), activates `comp` (`design.activateRootComponent()` for the root, else `design.rootComponent.allOccurrencesByComponent(comp).item(0).activate()`; no occurrence → `INVALID_PARAMS` "component has no occurrence"), and restores the previous activation on exit (also on exceptions). |
| `finish(ctx, feat, name, **extra) -> dict` | `feat.name = name` when `name`; returns the feature result (§ 3.2) merged with `extra`. |
| `cells_hint(ctx, cells)` | list of `"cell <i>: center [x, y, z]"` strings from each `cell.cellBody.boundingBox` center in output length units. |

### 3.2 Feature result
```json
{"feature": <ctx.ref(feat)>, "bodies": [<ctx.ref(b)> for b in feat.bodies]}
```
Actions add keys (e.g. `"sketch"`) where stated.

## 4. Solid features (`model_features.py`)
Sketch-based features are created in `owner(profile)`; edge/face/body-based features in
`same_owner(...)` of their inputs. `participant_bodies` applies only to `join`/`cut`/`intersect`;
given with `new_body`/`new_component` → `INVALID_PARAMS`.

### 4.1 extrude
```python
@action("model", access="write")
def extrude(ctx, profile: Profile, extent: Literal["distance", "to_object", "symmetric", "two_sided"] = "distance",
            distance: Length | None = None, distance_two: Length | None = None,
            to_entity: Entity | None = None, to_offset: Length = 0, flip: bool = False,
            full_length: bool = True, taper_angle: Angle = 0, taper_angle_two: Angle = 0,
            operation: Operation = "new_body", participant_bodies: list[Body] | None = None,
            name: str | None = None) -> dict
```
- Required: `distance` for `distance`, `symmetric`, `two_sided`; `distance_two` for `two_sided`;
  `to_entity` (`BRepFace`, `BRepBody`, `BRepVertex`, `ConstructionPlane`, `ConstructionPoint`) for
  `to_object`. Missing → `INVALID_PARAMS`. Distances must be > 0 (direction comes from `flip`).
- API: `ext = comp.features.extrudeFeatures`; `inp = ext.createInput(profile, op)`;
  direction `ExtentDirections.NegativeExtentDirection` when `flip` else `PositiveExtentDirection`.
  - distance: `inp.setOneSideExtent(DistanceExtentDefinition.create(vi(distance)), direction, vi(taper_angle))`.
  - to_object: `inp.setOneSideExtent(ToEntityExtentDefinition.create(to_entity, False, vi(to_offset)), direction, vi(taper_angle))`.
  - symmetric: `inp.setSymmetricExtent(vi(distance), full_length, vi(taper_angle))` (`full_length`
    true = `distance` is the total length).
  - two_sided: `inp.setTwoSidesExtent(DistanceExtentDefinition.create(vi(distance)), DistanceExtentDefinition.create(vi(distance_two)), vi(taper_angle), vi(taper_angle_two))`.
  - `participants(...)`; `feat = ext.add(inp)`.
- Result: feature result.

### 4.2 revolve
```python
def revolve(ctx, profile: Profile, axis: Axis, extent: Literal["angle", "symmetric", "two_sided"] = "angle",
            angle: Angle = "360 deg", angle_two: Angle | None = None, operation: Operation = "new_body",
            participant_bodies: list[Body] | None = None, name: str | None = None) -> dict
```
API: `revolveFeatures.createInput(profile, axis, op)`; angle → `inp.setAngleExtent(False, vi(angle))`;
symmetric → `setAngleExtent(True, vi(angle))`; two_sided → `setTwoSideAngleExtent(vi(angle), vi(angle_two))`
(`angle_two` required). `positive(angle)`. Access write.

### 4.3 sweep
```python
def sweep(ctx, profile: Profile, path: list[Entity], chain: bool = True,
          guide_rail: list[Entity] | None = None, twist_angle: Angle = 0,
          orientation: Literal["perpendicular", "parallel"] = "perpendicular",
          operation: Operation = "new_body", participant_bodies: list[Body] | None = None,
          name: str | None = None) -> dict
```
`expect(path, ("SketchCurve", "BRepEdge"))` (same for `guide_rail`). API:
`p = comp.features.createPath(coll(path), chain)`; `inp = sweepFeatures.createInput(profile, p, op)`;
`inp.orientation = SweepOrientationTypes.PerpendicularOrientationType | ParallelOrientationType`;
when `guide_rail`: `inp.guideRail = comp.features.createPath(coll(guide_rail), chain)`;
when `twist_angle` ≠ 0: `inp.twistAngle = vi(twist_angle)`; participants; `add`.

### 4.4 loft
```python
def loft(ctx, sections: list[Profile], rails: list[Entity] | None = None, centerline: Entity | None = None,
         solid: bool = True, closed: bool = False, operation: Operation = "new_body",
         participant_bodies: list[Body] | None = None, name: str | None = None) -> dict
```
At least 2 sections; `rails` and `centerline` together → `INVALID_PARAMS`. Rails/centerline:
`SketchCurve` or `BRepEdge`. API: `inp = loftFeatures.createInput(op)`; for each section
`inp.loftSections.add(section)`; each rail `inp.centerLineOrRails.addRail(r)`; centerline
`inp.centerLineOrRails.addCenterLine(c)`; `inp.isSolid = solid`; `inp.isClosed = closed`;
participants; `add`. Component: `owner(sections[0])`.

### 4.5 fillet
```python
def fillet(ctx, edges: list[Edge], radius: Length, tangent_chain: bool = True, name: str | None = None) -> dict
```
`positive(radius)`. API: `inp = filletFeatures.createInput()`; if `hasattr(inp, "edgeSetInputs")`:
`inp.edgeSetInputs.addConstantRadiusEdgeSet(coll(edges), vi(radius), tangent_chain)`, else
`inp.addConstantRadiusEdgeSet(coll(edges), vi(radius), tangent_chain)`; `add`.

### 4.6 chamfer
```python
def chamfer(ctx, edges: list[Edge], chamfer_type: Literal["equal_distance", "two_distances", "distance_angle"] = "equal_distance",
            distance: Length = "1 mm", distance_two: Length | None = None, angle: Angle | None = None,
            flip: bool = False, tangent_chain: bool = True, name: str | None = None) -> dict
```
`two_distances` needs `distance_two`; `distance_angle` needs `angle` (0 < angle < 90 deg).
API (preferred): `inp = chamferFeatures.createInput2()`; `s = inp.chamferEdgeSets`;
`s.addEqualDistanceChamferEdgeSet(coll(edges), vi(distance), tangent_chain)` /
`s.addTwoDistancesChamferEdgeSet(coll(edges), vi(distance), vi(distance_two), flip, tangent_chain)` /
`s.addDistanceAndAngleChamferEdgeSet(coll(edges), vi(distance), vi(angle), flip, tangent_chain)`.
Fallback when `createInput2` is absent: `inp = chamferFeatures.createInput(coll(edges), tangent_chain)`
then `setToEqualDistance(vi(d))` / `setToTwoDistances(vi(d), vi(d2))` / `setToDistanceAndAngle(vi(d), vi(a))`
(`flip` ignored on fallback). `add`.

### 4.7 shell
```python
def shell(ctx, thickness: Length, faces: list[Face] | None = None, bodies: list[Body] | None = None,
          direction: Literal["inside", "outside", "both"] = "inside", tangent_chain: bool = True,
          name: str | None = None) -> dict
```
Exactly one of `faces` (faces removed) or `bodies` (hollow, closed) → else `INVALID_PARAMS`.
API: `inp = shellFeatures.createInput(coll(faces or bodies), tangent_chain)`;
inside → `inp.insideThickness = vi(thickness)`; outside → `inp.outsideThickness = vi(thickness)`;
both → both; `add`.

### 4.8 mirror
```python
def mirror(ctx, entities: list[Entity], plane: Plane, combine: bool = False, name: str | None = None) -> dict
```
`uniform(entities, {"bodies", "features"})`; features → `ctx.require_parametric()`; `combine` only
with bodies. API: `inp = mirrorFeatures.createInput(coll(entities), plane)`; bodies:
`inp.isCombine = combine`; `add`.

### 4.9 hole
```python
def hole(ctx, hole_type: Literal["simple", "counterbore", "countersink"] = "simple", diameter: Length = "5 mm",
         depth: Length | None = None, through_all: bool = False,
         face: Face | None = None, point: Point3 | None = None, sketch_points: list[SketchPoint] | None = None,
         counterbore_diameter: Length | None = None, counterbore_depth: Length | None = None,
         countersink_diameter: Length | None = None, countersink_angle: Angle = "90 deg",
         tip_angle: Angle = "118 deg", participant_bodies: list[Body] | None = None,
         name: str | None = None) -> dict
```
Position: either (`face` planar + `point` model coordinates on it) or `sketch_points` — exactly
one. Extent: exactly one of `depth` or `through_all: true`. Counterbore needs both counterbore
values (> diameter / > 0); countersink needs `countersink_diameter` > `diameter`.
API: `hf = comp.features.holeFeatures`;
`inp = hf.createSimpleInput(vi(diameter))` / `hf.createCounterboreInput(vi(diameter), vi(cbd), vi(cbdepth))` /
`hf.createCountersinkInput(vi(diameter), vi(csd), vi(csa))`;
position `inp.setPositionByPoint(face, point)` or `inp.setPositionBySketchPoints(coll(sketch_points))`;
extent `inp.setDistanceExtent(vi(depth))` or `inp.setAllExtent(ExtentDirections.PositiveExtentDirection)`;
`inp.tipAngle = vi(tip_angle)`; participants; `add`. Component: `owner(face)` or `owner(sketch_points[0])`.
Gotcha: `setPositionByPoint` needs a planar face; a non-planar face → `INVALID_PARAMS` (checked
with `face.geometry.surfaceType == SurfaceTypes.PlaneSurfaceType`).

### 4.10 rect_pattern, circ_pattern
```python
def rect_pattern(ctx, entities: list[Entity], direction_one: Axis, quantity_one: Expr, distance_one: Length,
                 direction_two: Axis | None = None, quantity_two: Expr = 1, distance_two: Length | None = None,
                 distance_type: Literal["spacing", "extent"] = "spacing", symmetric: bool = False,
                 name: str | None = None) -> dict
def circ_pattern(ctx, entities: list[Entity], axis: Axis, quantity: Expr, total_angle: Angle = "360 deg",
                 symmetric: bool = False, name: str | None = None) -> dict
```
`uniform(entities, {"bodies", "features", "faces"})`; features → `require_parametric`. Quantities
are integers ≥ 2 (`quantity_two` ≥ 1). Evaluated from the resolved value, `quantity_one * quantity_two`
(rect) and `quantity` (circ) above 500 → `INVALID_PARAMS` "pattern is capped at 500 instances; split it".
`distance_two` required when `direction_two` is given.
API rect: `rectangularPatternFeatures.createInput(coll(entities), direction_one, vi(quantity_one), vi(distance_one), PatternDistanceType.SpacingPatternDistanceType | ExtentPatternDistanceType)`;
`inp.setDirectionTwo(direction_two, vi(quantity_two), vi(distance_two))` when given;
`inp.isSymmetricInDirectionOne = symmetric` (and `...DirectionTwo`). Circ:
`circularPatternFeatures.createInput(coll(entities), axis)`; `inp.quantity = vi(quantity)`;
`inp.totalAngle = vi(total_angle)`; `inp.isSymmetric = symmetric`. `add`. Result adds
`"pattern_count"` = `feat.patternElements.count` when that attribute exists.

### 4.11 thread_query, list_threads, thread
```python
@action("model", access="read")
def thread_query(ctx, thread_type: str | None = None, size: str | None = None, designation: str | None = None,
                 internal: bool = False, offset: int = 0, limit: int = 100) -> dict
@action("model", access="read")
def list_threads(ctx, offset: int = 0, limit: int = 100) -> dict
@action("model", access="write", timeout=60)
def thread(ctx, faces: list[Face], thread_type: str = "ISO Metric profile", designation: str | None = None,
           thread_class: str | None = None, internal: bool | None = None, modeled: bool = False,
           full_length: bool = True, length: Length | None = None, offset: Length = 0,
           right_handed: bool = True, name: str | None = None) -> dict
```
`q = design.rootComponent.features.threadFeatures.threadDataQuery`.
- thread_query levels (first applicable): no `thread_type` → `q.allThreadTypes` (level `types`);
  `thread_type` only → `q.allSizes(thread_type)` (`sizes`); + `size` → `q.allDesignations(thread_type, size)`
  (`designations`); + `designation` → `q.allClasses(internal, thread_type, designation)` (`classes`).
  Unknown `thread_type` (not in `allThreadTypes`) → `INVALID_PARAMS` with the first 20 types as hints.
  Result `{"level": <level>, **ctx.page(strings, offset, limit)}`.
- list_threads: every `threadFeatures` item of every `design.allComponents` component:
  `{"feature": ref, "thread_type", "designation", "thread_class", "internal", "modeled",
  "full_length", "right_handed", "face_count"}` from `feat.threadInfo` (`threadType`,
  `threadDesignation`, `threadClass`, `isInternal`, `isRightHanded` when present) and
  `feat.isModeled`, `feat.isFullLength`, `feat.faces.count` (live: `ThreadFeature` has no `inputEntities`, and `inputCylindricalFaces` fails unless the timeline is rolled back); paged.
- thread: faces must be cylindrical (`SurfaceTypes.CylinderSurfaceType`) else `INVALID_PARAMS`.
  `internal` default: for the first face, `True` when the face normal at its centroid points toward
  the cylinder axis (`face.evaluator.getNormalAtPoint(face.pointOnFace)` dotted with the vector from
  `pointOnFace` to the axis) else `False`. `designation` default: `q.recommendThreadData(2*radius_cm, internal, thread_type)`
  → `(ok, designation, cls)`; `ok` false → `INVALID_PARAMS` "no standard thread fits diameter <d>".
  `thread_class` default: recommended class, else `q.allClasses(internal, thread_type, designation)[0]`.
  API: `info = threadFeatures.createThreadInfo(internal, thread_type, designation, thread_class)`;
  `info.isRightHanded = right_handed` when the attribute exists, else a left-hand request →
  `UNSUPPORTED`; `inp = threadFeatures.createInput(coll(faces), info)`; `inp.isModeled = modeled`;
  `inp.isFullLength = full_length`; when not full length `inp.threadLength = vi(length)` (required)
  and `inp.threadOffset = vi(offset)`; `add`. Result adds `"designation"`, `"thread_class"`,
  `"internal"`.

### 4.12 draft
```python
def draft(ctx, faces: list[Face], pull_plane: Plane, angle: Angle, angle_two: Angle | None = None,
          flip: bool = False, tangent_chain: bool = True, name: str | None = None) -> dict
```
API: `inp = draftFeatures.createInput(list(faces), pull_plane, tangent_chain)`;
`angle_two` None → `inp.setSingleAngle(False, vi(angle))`; else `inp.setTwoAngles(vi(angle), vi(angle_two))`;
`inp.isDirectionFlipped = flip`; `add`.

### 4.13 split_body, split_face
```python
def split_body(ctx, bodies: list[Body], tool: Entity, extend_tool: bool = True, name: str | None = None) -> dict
def split_face(ctx, faces: list[Face], tool: Entity, extend_tool: bool = True, name: str | None = None) -> dict
```
`tool` kinds: `BRepBody`, `BRepFace`, `ConstructionPlane`, `SketchCurve` (split_face also). API:
`splitBodyFeatures.createInput(coll(bodies), tool, extend_tool)` /
`splitFaceFeatures.createInput(coll(faces), tool, extend_tool)`; `add`.

### 4.14 offset_faces
```python
def offset_faces(ctx, faces: list[Face], distance: Length, name: str | None = None) -> dict
```
`distance` may be negative, not zero. `require_api(features.offsetFacesFeatures, "createInput")`.
API: `offsetFacesFeatures.createInput(list(faces), vi(distance))` (live: the faces are a plain list, an `ObjectCollection` raises `TypeError`); `add`.

### 4.15 scale
```python
def scale(ctx, entities: list[Entity], factor: Expr = 1, factors: list[Expr] | None = None,
          base_point: Entity | None = None, name: str | None = None) -> dict
```
Entity kinds: `BRepBody` and `Sketch` only. `factors` (3
values, non-uniform) only with bodies. All factors > 0. `base_point` kinds `BRepVertex`,
`SketchPoint`, `ConstructionPoint`; default `comp.originConstructionPoint`. API:
`inp = scaleFeatures.createInput(coll(entities), base_point, vi(factor))`;
`factors` → `inp.setToNonUniform(vi(fx), vi(fy), vi(fz))`; `add`.

### 4.16 move
```python
def move(ctx, bodies: list[Body], translate: Point3 | None = None, rotate_axis: Axis | None = None,
         angle: Angle | None = None, name: str | None = None) -> dict
```
At least one of `translate` or (`rotate_axis` + `angle`). Matrix `m = Matrix3D.create()`;
rotation: axis origin and direction from the axis geometry (`ConstructionAxis.geometry`
`InfiniteLine3D.origin/direction`; `BRepEdge.geometry` `Line3D` start→end; `SketchLine.worldGeometry`),
`m.setToRotation(angle.rad, direction, origin)`; translation: `t = Matrix3D.create()`,
`t.translation = Vector3D.create(p.x, p.y, p.z)`, `m.transformBy(t)` (rotation first, then
translation). API: if `hasattr(moveFeatures, "createInput2")`: `inp = moveFeatures.createInput2(coll(bodies))`,
`inp.defineAsFreeMove(m)`; else `inp = moveFeatures.createInput(coll(bodies), m)`; `add`.

### 4.17 combine
```python
def combine(ctx, target: Body, tools: list[Body], operation: Literal["join", "cut", "intersect"] = "join",
            keep_tools: bool = False, name: str | None = None) -> dict
```
`target` in `tools` → `INVALID_PARAMS`. API: `inp = combineFeatures.createInput(target, coll(tools))`;
`inp.operation = FeatureOperations.<Join|Cut|Intersect>FeatureOperation`; `inp.isKeepToolBodies = keep_tools`;
`inp.isNewComponent = False`; `add`.

### 4.18 rename
```python
@action("model", access="write")
def rename(ctx, target: Entity, name: str) -> dict
```
`name` non-empty after strip. No `name` attribute → `INVALID_PARAMS`. `Occurrence` →
`INVALID_PARAMS` hint "rename its component instead". `previous = target.name`; `target.name = name`;
a raised exception → `INVALID_PARAMS` "<kind> name is read-only" when the message mentions
read-only, else `FUSION_ERROR`. Result `{"target": ctx.ref(target), "previous": previous, "name": target.name}`
(Fusion may suffix a duplicate name; the returned `name` is what Fusion stored).

## 5. Coil (`model_coil.py`)
### 5.1 Why text commands
`adsk.fusion.CoilFeatures` exposes only `item`, `itemByName` and `count`. `CoilFeatureInput` is
documented, but no collection method returns one, and a live probe (prior project, Commands.List
on the Coil command) found no API creation route. Coils are therefore created by driving the
Coil command with text commands. There is no sweep fallback. If a later Fusion build adds
`CoilFeatures.createInput`, this action still uses the text-command path (one code path, one set of
live scenarios); switching is a future spec change.

### 5.2 Signature
```python
CoilType = Literal["revolution_height", "revolution_pitch", "height_pitch", "spiral"]
CoilSection = Literal["circular", "square", "triangular_external", "triangular_internal"]
CoilSectionPosition = Literal["inside", "on_center", "outside"]

@action("model", access="write", timeout=60)
def coil(ctx, diameter: Length, plane: Plane = "XY", center: list[Length] = [0, 0],
         coil_type: CoilType = "revolution_height", revolutions: Expr | None = None,
         height: Length | None = None, pitch: Length | None = None, taper_angle: Angle = 0,
         section: CoilSection = "circular", section_position: CoilSectionPosition = "on_center",
         section_size: Length = "1 mm", clockwise: bool = False,
         operation: Literal["new_body", "join", "cut", "intersect"] = "new_body",
         participant_bodies: list[Body] | None = None, component: Component | None = None,
         name: str | None = None) -> dict
```
`center` is `[x, y]` in the base sketch's coordinates (the sketch on `plane`); the coil axis is the
plane normal through that point. Gotcha: for the `XZ` origin plane, sketch +Y is model −Z.

### 5.3 Validation (all before any Fusion change)
1. `ctx.require_parametric()` → `WRONG_DESIGN_TYPE` in direct mode.
2. `len(center) == 2` else `INVALID_PARAMS`.
3. Size inputs per type (required = must be given; others must be `None`, else `INVALID_PARAMS`
   "<param> is not used by coil_type <t>"):

   | coil_type | required |
   |---|---|
   | `revolution_height` | `revolutions`, `height` |
   | `revolution_pitch` | `revolutions`, `pitch` |
   | `height_pitch` | `height`, `pitch` |
   | `spiral` | `revolutions`, `pitch` |
4. `positive` for `diameter`, `section_size` and every given size input.
5. `section_size.cm <= pitch.cm` when `pitch` is given, else `INVALID_PARAMS`
   "section_size must not exceed pitch".
6. `spiral` with `taper_angle` ≠ 0 → `INVALID_PARAMS` "spiral coils have no taper";
   otherwise `-90 deg < taper_angle < 90 deg`.
7. `participant_bodies` with `new_body` → `INVALID_PARAMS`. With join/cut/intersect, when
   `hasattr(adsk.fusion.CoilFeature, "participantBodies")` is false → `UNSUPPORTED`.

### 5.4 Creation sequence
`comp = ctx.component(component)`; `pl = origin_plane(comp, plane)`;
`before = comp.features.coilFeatures.count`. Inside `activated(ctx, comp)`:
1. `sk = comp.sketches.add(pl)`; `sk.name = f"{name or 'Coil'} base"`;
   `circle = sk.sketchCurves.sketchCircles.addByCenterRadius(Point3D.create(cx.cm, cy.cm, 0), diameter.cm / 2)`.
2. `ui.activeSelections.clear()`; `ui.activeSelections.add(circle)`.
3. `ctx.text_command("Commands.Start Coil")`.
4. Send, in this order (values: lengths in cm, angles in rad, `repr(float)`):
   1. `Commands.SetString infoSizeType <v>` — `infoRevolutionAndHeight` / `infoRevolutionAndPitch` /
      `infoHeightAndPitch` / `infoSpiral`.
   2. `Commands.SetString infoSectionType <v>` — `infoCircular` / `infoRectanglar` / `infoTrianglar` /
      `infoTrianglar2` (Fusion's spellings; circular / square / triangular_external / triangular_internal).
   3. `Commands.SetString infoSectionPosition <v>` — `infoInside` / `infoOnCenter` / `infoOutside`.
   4. `Commands.SetDouble SectionSize <cm>` — before the size inputs, because Fusion rebuilds the
      preview on each `SetDouble` and fails when the default section does not fit.
   5. Size inputs for the type, in this order when present: `Commands.SetDouble CoilRevolutions <n>`,
      `Commands.SetDouble CoilHeight <cm>`, `Commands.SetDouble CoilPitch <cm>`.
   6. `Commands.SetDouble CoilTaperAngle <rad>` — not sent for `spiral`.
   7. `Commands.SetString infoBooleanType <v>` — `infoNewBodyType` / `infoJoinType` / `infoCutType` /
      `infoIntersectType`.
   8. `Commands.SetBool CoilFlipRotation 1` — only when `clockwise`.
   The diameter is taken from the selected circle; no diameter command is sent.
5. `ctx.text_command("NuCommands.CommitCmd")`.
6. `comp.features.coilFeatures.count == before + 1` else failure "coil not created".

Failure (any exception in 2–6, including `TEXT_COMMAND_FAILED`): `ui.terminateActiveCommand()`
(exceptions ignored), `sk.deleteMe()` (exceptions ignored), then re-raise the original
`ActionError`, or `ctx.fail("FUSION_ERROR", "coil not created: <text>")` for other exceptions.
The core undo group then aborts (core design § 5.7).

### 5.5 After creation
`coil = comp.features.coilFeatures.item(before)`. The base sketch is kept.
1. `participant_bodies` given: `coil.timelineObject.rollTo(True)`;
   `coil.participantBodies = list(participant_bodies)`; `design.timeline.moveToEnd()`.
2. `coil.name = name` when given.
3. Store the request (§ 5.6): `ctx.attr_set(coil, "request", req)` (`req` is the dict; core `attr_set` serialises it, core design § 5.9).
4. Result:
```json
{"feature": <ref coil>, "bodies": [<ref>...], "sketch": <ref sk>,
 "parameters": [{"name": "d12", "expression": "20 mm", "role": "<p.role>"}]}
```
`parameters` = every `design.allParameters` item that is a `ModelParameter` with
`createdBy == coil` (compared by `entityToken`), in `allParameters` order.

### 5.6 Request attribute format (consumed by the history set)
Attribute group `defusion`, name `request`, on the `CoilFeature`. Value: JSON text of `req` as
written by `ctx.attr_set` (core design § 5.9); readers parse it (`ctx.attr_get`), so key order and
whitespace are not significant:
```json
{"format": "defusion.request/1",
 "tool": "model",
 "action": "coil",
 "params": {
   "plane": "XY" | <ref>,
   "center": ["<expr>", "<expr>"],
   "diameter": "<expr>",
   "coil_type": "revolution_height",
   "revolutions": "<expr>" | null,
   "height": "<expr>" | null,
   "pitch": "<expr>" | null,
   "taper_angle": "<expr>",
   "section": "circular",
   "section_position": "on_center",
   "section_size": "<expr>",
   "clockwise": false,
   "operation": "new_body",
   "participant_bodies": [<ref>, ...] | null,
   "component": <ref> | null,
   "name": "<str>" | null}}
```
- Every key of the coil signature except `ctx` is present (defaults filled).
- `"<expr>"` is the resolved `Value.expr` (bare numbers already carry the default unit, e.g. `"20 mm"`;
  user expressions such as `"d1*2"` are kept as written; `Expr` values keep their text, e.g. `"5"`).
- `plane` is `"XY"`, `"XZ"` or `"YZ"` when `pl` is one of `comp`'s origin planes, else `ctx.ref(plane)`.
  `component` is `ctx.ref(comp)` when the caller passed `component`, else `null`. Refs are
  `ctx.ref()` dicts (type/name/token/component), which are valid Ref inputs.
- `params` is a valid `params` object for `model.coil`; replay = call `model.coil` with it.
- The attribute records the creation request only. Later edits are made with `parameter_modify`
  on the coil's model parameters (§ 10) and are not written back; history reads current values
  with `parameter_list(created_by=<coil ref>)`.

### 5.7 Editing a coil
Find the coil's numeric parameters with `parameter_list(created_by=<coil ref>)` (§ 10.1; the coil
result's `parameters` lists them too) and change them with `parameter_modify`. Non-numeric options
(type, section, position, rotation, operation) cannot be edited by API; delete and recreate.

## 6. Primitives (`model_primitives.py`)
All three create a sketch named `"<name or Kind> sketch"` on `origin_plane(comp, plane)` in
`comp = ctx.component(component)`, then a real extrude/revolve feature in `comp`. `center` is
`[x, y]` in sketch coordinates (length 2 else `INVALID_PARAMS`). Result: feature result +
`"sketch": ref`. Operation/participant rules as § 4.
```python
def cylinder(ctx, diameter: Length, height: Length, plane: Plane = "XY", center: list[Length] = [0, 0],
             operation: Operation = "new_body", participant_bodies: list[Body] | None = None,
             component: Component | None = None, name: str | None = None) -> dict
def sphere(ctx, diameter: Length, plane: Plane = "XY", center: list[Length] = [0, 0],
           operation: Operation = "new_body", participant_bodies: list[Body] | None = None,
           component: Component | None = None, name: str | None = None) -> dict
def torus(ctx, major_diameter: Length, minor_diameter: Length, plane: Plane = "XY", center: list[Length] = [0, 0],
          operation: Operation = "new_body", participant_bodies: list[Body] | None = None,
          component: Component | None = None, name: str | None = None) -> dict
```
- cylinder: `sketchCircles.addByCenterRadius(c, d/2)`; extrude `sk.profiles.item(0)` one side
  `vi(height)` (§ 4.1 distance path). Positive values.
- sphere: arc `sketchArcs.addByCenterStartSweep(c, Point3D(cx + r, cy, 0), math.pi)` and line
  `sketchLines.addByTwoPoints(arc.startSketchPoint, arc.endSketchPoint)`; revolve
  `sk.profiles.item(0)` about the line, `setAngleExtent(False, "360 deg")`.
- torus: requires `minor_diameter < major_diameter`. Axis: construction line
  (`isConstruction = True`) from `(cx, cy - major/2, 0)` to `(cx, cy + major/2, 0)`; circle center
  `(cx + major/2, cy, 0)`, radius `minor/2`; revolve the circle's profile about the axis line 360°.
  The torus axis is the sketch Y direction through `center`.
- Feature names: when `name` is given the feature gets `name` and the body gets `name`.

## 7. Surfaces (`model_surfaces.py`)
```python
def surface_patch(ctx, boundary: list[Entity], chain: bool = True, name: str | None = None) -> dict
def surface_stitch(ctx, bodies: list[Body], tolerance: Length = "0.01 mm",
                   operation: Literal["new_body", "new_component"] = "new_body", name: str | None = None) -> dict
def surface_thicken(ctx, faces: list[Face], thickness: Length, symmetric: bool = False,
                    operation: Operation = "new_body", chain: bool = True, name: str | None = None) -> dict
def surface_ruled(ctx, edges: list[Entity], distance: Length, angle: Angle = 0,
                  ruled_type: Literal["normal", "tangent"] = "normal", chain: bool = True,
                  name: str | None = None) -> dict
def surface_trim(ctx, tool: Entity, remove_cells: list[int], name: str | None = None) -> dict
```
All write. Components: `same_owner` of the inputs (`trim`: `owner(tool)`).
- patch: `boundary` kinds `BRepEdge`, `SketchCurve`; `path = features.createPath(coll(boundary), chain)`;
  `inp = patchFeatures.createInput(path, FeatureOperations.NewBodyFeatureOperation)`; `add`.
- stitch: bodies must be surface bodies (`not body.isSolid`); `inp = stitchFeatures.createInput(coll(bodies), vi(tolerance), op)`; `add`.
- thicken: `thickness` ≠ 0; `inp = thickenFeatures.createInput(coll(faces), vi(thickness), symmetric, op, chain)`; `add`.
- ruled: `edges` are `BRepEdge` only (live: the input is a `Profile`, and only `Component.createBRepEdgeProfile` builds one from open curves);
  `require_api(features.ruledSurfaceFeatures, "createInput")`; `profile = comp.createBRepEdgeProfile(coll(edges), chain)`;
  `inp = ruledSurfaceFeatures.createInput(profile, vi(distance), vi(angle), RuledSurfaceTypes.NormalRuledSurfaceType | TangentRuledSurfaceType)`.
  A `TypeError` from `createInput` (different signature in this build) → `UNSUPPORTED`
  "RuledSurfaceFeatures.createInput signature differs in this Fusion build". `add`.
- trim: `tool` kinds `BRepBody`, `BRepFace`, `ConstructionPlane`, `SketchCurve`;
  `inp = trimFeatures.createInput(tool)`; `cells = inp.bRepCells`; `remove_cells` non-empty, unique,
  each `0 <= i < cells.count` else `INVALID_PARAMS` with `cells_hint(cells)`; for each index
  `cells.item(i).isSelected = True`; `add`.

## 8. Sheet metal (`model_sheet.py`)
All write, timeout 60. Sheet metal classes added in Fusion 2704+ are mostly preview API. Each
action feature-detects its collection and method with `require_api` → `UNSUPPORTED` naming the
missing member. Input bodies must be sheet metal (`body.isSheetMetal`, when that attribute exists)
else `INVALID_PARAMS`.
```python
def sheet_flange(ctx, edges: list[Edge] | None = None, profile: Profile | None = None,
                 height: Length | None = None, angle: Angle = "90 deg",
                 bend_position: Literal["inside", "outside", "adjacent", "tangent"] | None = None,
                 rule: str | None = None, name: str | None = None) -> dict
def sheet_bend(ctx, stationary_face: Face, lines: list[SketchCurve], angle: Angle = "90 deg",
               line_position: Literal["center", "start", "end"] = "center", bend_relief: bool = True,
               name: str | None = None) -> dict
def sheet_unfold(ctx, stationary_face: Face, bend_faces: list[Face] | None = None, name: str | None = None) -> dict
def sheet_flat_pattern(ctx, stationary_face: Face) -> dict
```
- flange: exactly one of `edges` (edge flange; `height` required) or `profile` (base flange: creates
  the first sheet metal body; `height`/`angle`/`bend_position` not allowed) else `INVALID_PARAMS`.
  `rule` (either mode): `r = design.librarySheetMetalRules.itemByName(rule)` or
  `design.designSheetMetalRules.itemByName(rule)` (none → `NOT_FOUND`), then
  `comp.activeSheetMetalRule = r` (`require_api(comp, "activeSheetMetalRule")`). The body check
  above applies to edges only. `ff = comp.features.flangeFeatures`.
  Base: `require_api(ff, "createBaseFlangeInput")`; `inp = ff.createBaseFlangeInput([profile])`; `add`.
  Edge: `require_api(ff, "createEdgeFlangeInput")`;
  `inp = ff.createEdgeFlangeInput(list(edges), vi(height))`; `inp.angle = vi(angle)`;
  `bend_position` → `inp.bendPositionType = getattr(adsk.fusion.BendPositionTypes, "<Inside|Outside|Adjacent|Tangent>BendPositionType")`
  (missing enum member → `UNSUPPORTED`); `add`.
- bend (Fusion "Fold"): `require_api(comp.features, "foldFeatures")`; `inp = foldFeatures.createInput(stationary_face)`;
  `pos = getattr(adsk.fusion.FoldBendLinePositionTypes, "<Center|Start|End>FoldBendLinePositionType")`
  (missing → `UNSUPPORTED`); for each line `inp.bendLines.add(line, vi(angle), pos, bend_relief)`;
  lines must be `SketchLine` else `INVALID_PARAMS`; `add`.
- unfold: `require_api(comp.features.unfoldFeatures, "createInput")`; `inp = unfoldFeatures.createInput(stationary_face)`;
  `bend_faces` None → `inp.isUnfoldAllBends = True`; else `inp.bendFaces = list(bend_faces)`
  (cylindrical faces of bends); `add`. Result adds nothing; refold is out of scope of the approved list.
- flat_pattern: `comp = owner(stationary_face)`; `require_api(comp, "createFlatPattern")`;
  `comp.flatPattern` not `None` → `INVALID_PARAMS` "component already has a flat pattern";
  `fp = comp.createFlatPattern(stationary_face)`. Result
  `{"flat_pattern": ctx.ref(fp), "flat_body": ctx.ref(fp.flatBody), "size": [dx, dy, dz]}` from
  `fp.flatBody.boundingBox` in output length units.
- UNSUPPORTED path summary: missing `flangeFeatures.createEdgeFlangeInput`/`createBaseFlangeInput`,
  `Component.activeSheetMetalRule`, `features.foldFeatures`,
  `unfoldFeatures.createInput`, `Component.createFlatPattern`, or the enum members above.

## 9. Construction (`model_construct.py`)
All write, parametric only (`require_parametric`; direct designs support only by-plane planes).
```python
def construction_plane(ctx, method: Literal["offset", "angle", "midplane", "three_points", "tangent", "along_path"],
                       plane: Plane | None = None, offset: Length = 0, axis: Axis | None = None,
                       angle: Angle = 0, plane_two: Plane | None = None, points: list[Entity] | None = None,
                       face: Face | None = None, path: Entity | None = None, position: Expr = 0.5,
                       component: Component | None = None, name: str | None = None) -> dict
def construction_axis(ctx, method: Literal["two_planes", "two_points", "edge", "circular_face", "normal_to_face"],
                      plane: Plane | None = None, plane_two: Plane | None = None, points: list[Entity] | None = None,
                      edge: Edge | None = None, face: Face | None = None, point: Entity | None = None,
                      component: Component | None = None, name: str | None = None) -> dict
def ucs(ctx, origin: Entity, angle_x: Angle = 0, angle_y: Angle = 0, angle_z: Angle = 0,
        component: Component | None = None, name: str | None = None) -> dict
```
`comp = ctx.component(component)`. Inputs required per method; missing → `INVALID_PARAMS` naming
them. Points kinds: `BRepVertex`, `SketchPoint`, `ConstructionPoint`.

| plane method | needs | call on `inp = comp.constructionPlanes.createInput()` |
|---|---|---|
| offset | plane, offset | `setByOffset(plane, vi(offset))` |
| angle | axis, angle, plane | `setByAngle(axis, vi(angle), plane)` |
| midplane | plane, plane_two | `setByTwoPlanes(plane, plane_two)` |
| three_points | points (3) | `setByThreePoints(p1, p2, p3)` |
| tangent | face (cylindrical/conical), angle, plane | `setByTangent(face, vi(angle), plane)` |
| along_path | path (`BRepEdge`/`SketchCurve`), position 0..1 | `setByDistanceOnPath(path, vi(position))` |

| axis method | needs | call on `inp = comp.constructionAxes.createInput()` |
|---|---|---|
| two_planes | plane, plane_two | `setByTwoPlanes(plane, plane_two)` |
| two_points | points (2) | `setByTwoPoints(p1, p2)` |
| edge | edge (linear) | `setByEdge(edge)` (live: `setByLine` takes an `InfiniteLine3D` and is direct-design only) |
| circular_face | face (cylindrical/conical) | `setByCircularFace(face)` |
| normal_to_face | face, point | `setByNormalToFaceAtPoint(face, point)` |

`add(inp)`; name; result `{"plane"|"axis": ctx.ref(obj)}`.

ucs (preview API, Fusion May 2026+): `require_api(comp, "userCoordinateSystems")`;
`geom = adsk.fusion.UserCoordinateSystemGeometry.createByPoint(origin)`;
`inp = comp.userCoordinateSystems.createInput(geom)`; for each non-zero angle,
`require_api(inp, "angleX"|"angleY"|"angleZ")` then set `vi(angle)`; `ucs = userCoordinateSystems.add(inp)`;
name; result `{"ucs": ctx.ref(ucs)}`. Access write.

## 10. Parameters (`model_params.py`)
### 10.1 parameter_list (read)
```python
def parameter_list(ctx, kind: Literal["all", "user", "model"] = "all", name: str | None = None,
                   created_by: Entity | None = None, offset: int = 0, limit: int = 100) -> dict
```
Source `design.userParameters`, `design.allParameters` filtered to `ModelParameter`, or
`design.allParameters`. `name` is an `fnmatch` pattern. `created_by` keeps model parameters whose
`createdBy.entityToken == created_by.entityToken`. Item:
`{"name", "kind": "user"|"model", "expression", "unit", "value", "comment", "created_by": ref|null, "role": str|null, "is_favorite"}`.
`value`: `ctx.length_out(p.value)` when `unitsManager.isValidExpression("1 " + p.unit, "cm")`;
`ctx.angle_out(p.value)` when valid for `"rad"`; else `p.value`. Paged.

### 10.2 parameter_create (write)
```python
def parameter_create(ctx, name: str, expression: str, unit: str | None = None, comment: str = "") -> dict
```
`unit` None → `design.unitsManager.defaultLengthUnits`; `""` = unitless. `name` must match
`^[A-Za-z_][A-Za-z0-9_]*$` and `design.allParameters.itemByName(name)` must be `None`, else
`INVALID_PARAMS`. `unitsManager.isValidExpression(expression, unit)` else `INVALID_PARAMS`.
`p = design.userParameters.add(name, ValueInput.createByString(expression), unit, comment)`;
result `{"parameter": <item § 10.1>}`.

### 10.3 parameter_modify (write)
```python
class ParamEdit(BaseModel):
    name: str
    expression: str
def parameter_modify(ctx, edits: list[ParamEdit]) -> dict
```
Validate every edit first (parameter exists via `allParameters.itemByName`; duplicate names in
`edits` rejected; `isValidExpression(expr, p.unit)`); any failure → `INVALID_PARAMS` listing every
bad edit, nothing changed. Apply: if `hasattr(design, "modifyParameters")`:
`design.modifyParameters([p...], [ValueInput.createByString(e)...])` (one recompute); else
`p.expression = e` in order. Result `{"parameters": [<item § 10.1> ...]}` in edit order.

### 10.4 parameter_delete (destructive)
```python
def parameter_delete(ctx, names: list[str]) -> dict
```
Each name must be a user parameter (`design.userParameters.itemByName`); a model parameter or
unknown name → `INVALID_PARAMS` before any delete. `ok = p.deleteMe()`; `False` → recorded as
failed "in use by other parameters or features". Result `{"deleted": [names], "failed": [{"name", "reason"}]}`.

## 11. Appearance (`model_appearance.py`)
```python
def material_list(ctx, source: Literal["design", "library"] = "library", library: str | None = None,
                  query: str | None = None, offset: int = 0, limit: int = 100) -> dict      # read
def material_assign(ctx, bodies: list[Body], material: str, library: str | None = None) -> dict   # write
def appearance_set(ctx, targets: list[Entity], appearance: str, library: str | None = None) -> dict  # write
def color_set(ctx, targets: list[Entity], color: str) -> dict                                       # write
```
- material_list: `design` → `design.materials`; `library` → each `app.materialLibraries` item (or
  only the one named `library`, unknown → `NOT_FOUND`) `.materials`. `query` = case-insensitive
  substring of the name. Items `{"name", "id", "library": <lib name or "design">}`. Paged.
- Lookup (material/appearance): `design.materials|appearances.itemByName(n)` first, then the named
  library or every library in order (`.materials|.appearances.itemByName(n)`; live: Fusion raises
  `RuntimeError` "invalid name" from a library that holds none of that kind, which is skipped); none → `NOT_FOUND`.
- material_assign: `body.material = mat` for each body. Result `{"bodies": refs, "material": mat.name}`.
- appearance_set: targets kinds `BRepBody`, `BRepFace`, `Occurrence`, `Component` (component →
  its bodies). A library appearance not yet in the design is copied once:
  `design.appearances.addByCopy(lib_app, lib_app.name)`. Set `target.appearance = app_in_design`.
  Result `{"targets": refs, "appearance": name}`.
- color_set: `color` is `#RRGGBB` or `#RRGGBBAA` (hex, case-insensitive) else `INVALID_PARAMS`.
  Design appearance name `"De-Fusion color RRGGBBAA"` (upper-case, AA defaults FF); reused when it
  exists. Otherwise base = the first appearance of the `"Fusion Appearance Library"` library (by
  index) whose `appearanceProperties.itemById("opaque_albedo")` is a `ColorProperty`; none →
  `UNSUPPORTED`. `a = design.appearances.addByCopy(base, name)`;
  `a.appearanceProperties.itemById("opaque_albedo").value = Color.create(r, g, b, alpha)`; assign
  as appearance_set. Result `{"targets": refs, "appearance": name, "color": "#RRGGBBAA"}`.

## 12. Design settings (`model_design.py`)
| Action | Access | Signature | Behaviour / API | Result |
|---|---|---|---|---|
| `units_get` | read | `()` | `design.unitsManager.defaultLengthUnits`; `design.fusionUnitsManager.distanceDisplayUnits` mapped back to `mm cm m in ft` | `{"default_length_units", "display_length_units", "output_units": {"length", "angle"}}` |
| `units_set` | write | `(length: Literal["mm","cm","m","in","ft"])` | `design.fusionUnitsManager.distanceDisplayUnits = DistanceUnits.<Millimeter|Centimeter|Meter|Inch|Foot>DistanceUnits` | `{"display_length_units"}` |
| `config_inspect` | read | `(offset=0, limit=100)` | `require_api(design, "configurationTopTable")`; `t = design.configurationTopTable` (None → not configured); rows `t.rows.item(i)` `id`, `name`; columns `t.columns.item(i)` `title`, `objectType` last segment; `t.activeRow` | `{"configured": bool, "active_row": {"id","name"}|null, "columns": [...], **page(rows)}` |
| `create_configured_design` | write | `()` | `require_api(design, "createConfiguredDesign")`; already configured (`design.isConfiguredDesign`; `configurationTopTable` itself raises on restricted accounts) → `INVALID_PARAMS`; `t = design.createConfiguredDesign()` (restricted account → `FUSION_ERROR`) | `{"configured": true, "rows": t.rows.count}` |
| `find_attributes` | read | `(group: str = "defusion", attribute: str = "", offset=0, limit=100)` | `design.findAttributes(group, attribute)` (empty string matches any name, Fusion semantics) | page of `{"group", "name", "value", "parent": ref|null}` (`value` truncated to 2000 chars with `"truncated": true`) |
| `find_by_token` | read | `(token: str)` | `design.findEntityByToken(token)` | `{"entities": refs}` (empty list when none) |
| `visibility_get` | read | `(targets: list[Entity] | None = None)` | targets → per entity `isLightBulbOn` and `isVisible` (attributes that exist); none → design flags (§ 12.1) | `{"entities": [{"target", "light_bulb", "visible"}]}` or `{"flags": {...}}` |
| `visibility_set` | write | `(targets: list[Entity] | None = None, visible: bool | None = None, flag: str | None = None, value: bool | None = None)` | exactly one of (`targets`+`visible`) or (`flag`+`value`); per target: set `isLightBulbOn` when present else `isVisible`; neither settable → `INVALID_PARAMS`; flag: § 12.1 | same as get |
| `selection_sets_list` | read | `(offset=0, limit=100)` | `require_api(design, "selectionSets")`; each set `name`, `entities` | page of `{"name", "count", "entities": refs}` |
| `selection_set_create` | write | `(name: str, entities: list[Entity])` | `design.selectionSets.add(list(entities), name)` | `{"name", "count"}` |
| `set_design_type` | destructive | `(design_type: Literal["parametric","direct"])` (+ framework `confirm`) | `design.designType = DesignTypes.ParametricDesignType|DirectDesignType`; no-op when equal. Switching to direct discards the timeline (Fusion behaviour) | `{"design_type", "changed": bool}` |
| `set_design_intent` | write | `(intent: Literal["part","assembly","hybrid"])` | `require_api(design, "designIntent")` and `hasattr(adsk.fusion, "DesignIntentTypes")`; `design.designIntent = DesignIntentTypes.<Part|Assembly|Hybrid>DesignIntentType` | `{"design_intent", "changed": bool}` |
| `activate_root` | write | `()` | `design.activateRootComponent()` | `{"active_component": ref}` |
| `compute_all` | write, timeout 300 | `()` | `design.computeAll()` | `{"timeline_health": [{"item": ref, "health": name}]}` for timeline items whose `healthState` is not `HealthyFeatureHealthState` (parametric only; direct → `[]`) |
| `set_modeling_in_assembly` | write | `(enabled: bool)` | `require_api(design, "isModelingInAssemblyEnabled")`; set it (live: Fusion accepts it only when `designIntent` is assembly, else `RuntimeError`; the scenario sets the intent first) | `{"enabled"}` |

### 12.1 Design visibility flags
`require_api(design, "objectVisibility")`; flags are the attributes of `design.objectVisibility`
whose names start with `is` and whose values are `bool` (discovered with `dir()`), keyed by the
attribute name. `flag` not among them → `INVALID_PARAMS` listing the names.

## 13. Destructive (`model_design.py`)
```python
@action("model", access="destructive")
def remove_body(ctx, body: Body) -> dict
@action("model", access="destructive")
def delete_entities(ctx, targets: list[Entity]) -> dict
```
- remove_body: `r = ctx.ref(body)` first. Parametric: `feat = body.parentComponent.features.removeFeatures.add(body)`,
  result `{"removed": r, "feature": ctx.ref(feat)}`; direct: `body.deleteMe()`, `"feature": null`.
- delete_entities: refs captured first; for each target in order: no `deleteMe` →
  failed "not deletable"; `deleteMe()` returning `False` or raising → failed with the reason;
  a target already invalid (`isValid` false, removed by an earlier delete) → `deleted` without a
  call. Result `{"deleted": [refs], "failed": [{"target": ref, "reason": str}]}`.

## 14. Testability
### 14.1 Unit (no Fusion)
`tests/model_fakes.py` builds on `tests/fakes/adsk` (core) with recording fakes for the feature
collections used here (`createInput*`/`add` record arguments and return objects with settable
attributes), `ThreadDataQuery`, `CoilFeatures` (count grows only when the fake app sees
`NuCommands.CommitCmd` after `Commands.Start Coil`), parameters, appearances and materials.
Members can be removed per test to exercise `UNSUPPORTED`.

| Test file | Covers |
|---|---|
| `tests/test_model_registration.py` | 63 actions registered under `model` with the access classes and timeouts of this design; all `sheet_*` names; no `box`, `delete_all` |
| `tests/test_model_common.py` | § 3 helpers: `expect`, `uniform`, `same_owner`, `activated` restore on exception, `participants` errors |
| `tests/test_model_features.py` | § 4: per action, the API call sequence and required-parameter errors; chamfer/fillet/move fallbacks; thread default designation and left-hand UNSUPPORTED; rename read-only |
| `tests/test_model_coil.py` | § 5: validation table; exact text-command list per coil_type and section; no taper command for spiral; `CoilFlipRotation` only when clockwise; failure → terminate + sketch delete + error; count check; participant roll; `attr_set` called with the exact `req` dict for a fixed input; parameters filter |
| `tests/test_model_primitives.py` | § 6 sketch geometry and feature calls; torus diameter check |
| `tests/test_model_surfaces.py` | § 7 incl. trim cell index errors with hints, ruled TypeError → UNSUPPORTED |
| `tests/test_model_sheet.py` | § 8 UNSUPPORTED for each missing member; existing flat pattern error |
| `tests/test_model_construct.py` | § 9 method table, missing inputs, ucs UNSUPPORTED |
| `tests/test_model_params.py` | § 10 incl. atomic validation and modifyParameters fallback |
| `tests/test_model_appearance.py` | § 11 lookup order, addByCopy once, color naming/reuse, bad hex |
| `tests/test_model_design.py` | § 12, § 13 |

### 14.2 Live
`model_live.SCENARIOS` is the `LIVE_SCENARIOS` list of `(name, "model.<action>", async fn(session))`
(core design § 10); each `fn` calls actions through `live_check.call(session, tool, action, params)`.
`python scripts/live_check.py --only model` runs all; `--only model.<action>` runs those tagged with
that action. Each scenario builds its own geometry in the scratch document with `sketch.create`
(`plane="XY"`), `sketch.rectangle` / `sketch.circle` / `sketch.line` and `model` actions (solids:
`live_check.box`, core design § 10),
asserts on returned results and on `inspect`-free checks (`ok`, `changes.bodies`, refs), and leaves
the document parametric unless it says otherwise.
Names; the tag is `"model.<first name segment>"` unless a tag is given in brackets:

- `extrude.distance`, `extrude.symmetric`, `extrude.two_sided`, `extrude.to_object`, `extrude.taper_cut`
- `revolve`, `sweep`, `sweep.guide_rail_twist`, `loft`, `loft.rails`, `loft.centerline`
- `fillet`, `chamfer.equal_distance`, `chamfer.two_distances`, `chamfer.distance_angle`, `shell`
- `mirror.bodies`, `mirror.features`, `hole.simple`, `hole.counterbore`, `hole.countersink`,
  `hole.sketch_points`, `rect_pattern`, `circ_pattern.faces`
- `thread_query`, `thread.modeled`, `thread.cosmetic`, `list_threads`
- `coil.revolution_height`, `coil.revolution_pitch`, `coil.height_pitch`, `coil.spiral`,
  `coil.section_circular`, `coil.section_square`, `coil.section_triangular_external`,
  `coil.section_triangular_internal`, `coil.join_participant`, `coil.invalid_section_size`,
  `coil.edit_parameter`, `coil.request_attribute`
- `draft`, `split_body`, `split_face`, `offset_faces`, `scale`, `move.translate`, `move.rotate`,
  `combine`, `rename`
- `cylinder`, `sphere`, `torus`
- `surface_patch`, `surface_stitch`, `surface_thicken`, `surface_ruled`, `surface_trim`
- `sheet_chain` [`model.sheet_flange`]: rectangle sketch → `sheet_flange(profile, rule="Steel (mm)")` (base) →
  `sheet_flange(edges)` → `sheet_bend` on a sketch line drawn on the base face → `sheet_unfold`
  (all bends) → `sheet_flat_pattern`. Each step passes on success or on `UNSUPPORTED` naming the
  missing member (later steps are then skipped and reported in the PASS line).
- `sheet_flange.not_sheet_metal`: edge flange on a plain extruded box edge → `INVALID_PARAMS`.
- `construction_plane` (all six methods), `construction_axis` (all five), `ucs` (success or `UNSUPPORTED`)
- `parameters` [`model.parameter_create`] (create, list, modify batch, delete in-use failure,
  delete with `confirm: true`)
- `materials` [`model.material_assign`], `appearance_set`, `color_set`
- `units` [`model.units_set`], `find_attributes`, `find_by_token`, `visibility`
  [`model.visibility_set`], `selection_sets` [`model.selection_set_create`],
  `set_design_intent` (success or `UNSUPPORTED`), `set_modeling_in_assembly` (success or
  `UNSUPPORTED`), `activate_root`, `compute_all`
- `remove_body`, `delete_entities` (with and without `confirm`)
- last, in order: `set_design_type` (`design_type="direct"` without `confirm` →
  `CONFIRM_REQUIRED`; `"direct"` then `"parametric"` with `confirm: true` → `changed: true` each),
  `coil.direct_rejected` (`set_design_type` `"direct"` with `confirm: true`, coil →
  `WRONG_DESIGN_TYPE`, `set_design_type` `"parametric"` with `confirm: true`), `config`
  [`model.create_configured_design`] (`create_configured_design` then `config_inspect`, or both
  `UNSUPPORTED`)

Live observations (Fusion 2706.1.17, restricted licence account):
- `coil` with `operation` join/cut/intersect and `participant_bodies` → `UNSUPPORTED` ("CoilFeature.participantBodies is
  not available"), as § 5.3 item 7; `coil.join_participant` accepts that and checks the join without participants
  (which merges the coil into every body it touches).
- `torus` axis is the sketch Y direction, so on `XY` the ring lies in the XZ plane (bounding box `[D, d, D]`).
- `surface_thicken` result `bodies` lists the source surface body as well as the new solid; check the body-count change.
- `create_configured_design` / `config_inspect` fail with "Cannot create/retrieve configuration top table as the user is
  restricted" (`FUSION_ERROR`) on a restricted account; the `config` scenario cannot pass there (licence, not code).
- `set_design_intent` and `selection_set_create` succeed on the installed build.

Coil scenario checks: `ok`; one new body (`changes.bodies` grows by 1 for `new_body`); the result
`sketch` exists; `find_attributes(group="defusion", attribute="request")` returns the coil as
parent with JSON whose `params.coil_type`/`params.section` match the call; for `edit_parameter`,
`parameter_modify` on one returned parameter changes the bounding box reported in `changes.bbox`.
