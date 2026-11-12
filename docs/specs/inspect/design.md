# design.md — docs/specs/inspect/
> De-Fusion MCP — `inspect` tool

## 1. Module and shared helpers
File `De-Fusion/defusion/tools/inspect.py`. Every action is `@action("inspect", access=...)`; the
framework (core § 5) supplies validation, Ref resolution, dispatch, undo grouping (only
`section_analysis` is `write`), result shaping and paging. Every action except `summary` and
`get_selection` calls `ctx.require_design()` first (`NO_DESIGN`).

Types: core § 5.4 types, including `Entity` (any entity incl. `MeshBody`, no class check;
DEP-INS-002). Action-local literals are
defined at the top of `inspect.py`:
```python
Accuracy = Literal["low", "medium", "high", "very_high"]
_ACCURACY = {"low": "LowCalculationAccuracy", "medium": "MediumCalculationAccuracy",
             "high": "HighCalculationAccuracy", "very_high": "VeryHighCalculationAccuracy"}
```
(`getattr(adsk.fusion.CalculationAccuracy, _ACCURACY[a])`).

Output conventions (REQ-INS-015): points are `[x, y, z]` via `ctx.length_out` per coordinate (sketch-space curve points in § 2.2.1 are `[x, y]`);
directions are unit `[x, y, z]` floats (unitless); areas `ctx.area_out`, volumes `ctx.volume_out`,
masses `ctx.mass_out`, angles `ctx.angle_out`. Density (kg/cm³ from Fusion) is output as
`density / ctx.length_out(1.0) ** 3` (kg per output-unit³); moments (kg·cm²) as
`m * ctx.length_out(1.0) ** 2`. Every entity is `ctx.ref(obj)`.

Private helpers (unit-tested directly):
- `_pt(ctx, p)` → `[x, y, z]` from a `Point3D` in cm.
- `_vec(v)` → `[x, y, z]` of a normalised `Vector3D` copy (`v.copy(); normalize()`).
- `_bbox(ctx, bb)` → `{"min", "max", "size", "center"}` from a `BoundingBox3D`
  (`minPoint`/`maxPoint`); `None` input → `None`.
- `_bodies(design, component=None)` → list of `(body, kind, occurrence_path)`: with `component`,
  its `bRepBodies` then `meshBodies` (path `""`); without, `rootComponent.bRepBodies`,
  `rootComponent.meshBodies`, then for each `occ` in `rootComponent.allOccurrences` the proxies
  `occ.bRepBodies` and `occ.meshBodies` with path `occ.fullPathName`. `kind` is `"brep"` or
  `"mesh"`. Proxies give world-space geometry and their own tokens.
- `_surface(ctx, face)` → surface dict (§ 2.4/2.6); `_curve(ctx, edge)` → curve dict (§ 2.5/2.6).
- `_index_of(collection, entity)` → index whose `entityToken` equals `entity.entityToken`, else
  `None` (one token map per call, built lazily).
- Public: `profile_index(profile)` (imported by the history set, DEP-HIS-007) and
  `profile_loops(ctx, profile)` (§ 2.2).

## 2. Actions
### 2.1 summary — read (REQ-INS-001)
```python
@action("inspect", access="read")
def summary(ctx: Ctx) -> dict
```
`ctx.document` is `None` → `NO_DOCUMENT`. Fusion calls: `doc.name`, `doc.isSaved`,
`doc.isModified`, `doc.dataFile` (`None` or raises for unsaved documents: catch → `None`),
`dataFile.versionNumber`, `dataFile.id`; `design.designType`
(`DesignTypes.ParametricDesignType` → `"parametric"`, `DirectDesignType` → `"direct"`);
`design.designIntent` when `hasattr(design, "designIntent")`, mapped from
`DesignIntentTypes.PartDesignIntentType|AssemblyDesignIntentType|HybridDesignIntentType` to
`"part"|"assembly"|"hybrid"`, else `None`; `design.unitsManager.defaultLengthUnits`;
`design.activeComponent`; `design.allComponents` (count; per component sum `bRepBodies.count`,
`meshBodies.count`, `sketches.count`, `features.count`); `rootComponent.allOccurrences.count`;
`design.userParameters.count`; `design.timeline.count` and `markerPosition` (parametric only,
else `None`).
```json
{"document": {"name": "Untitled", "saved": false, "modified": true, "version": null, "file_id": null},
 "design": {"type": "parametric", "intent": "part", "units": "mm", "active_component": {Ref},
   "components": 1, "occurrences": 0, "bodies": 1, "mesh_bodies": 0, "sketches": 1,
   "features": 1, "parameters": 0, "timeline": 2, "timeline_marker": 2}}
```
Active product is not a `Design` (e.g. a drawing) → `"design": null`.

### 2.2 object_info — read (REQ-INS-002)
```python
def object_info(ctx: Ctx, entity: Entity, offset: int = 0, limit: int = 25) -> dict
```
Result `{"ref", "type": obj.objectType.split("::")[-1], "properties": {...}, "bbox", "parent"}`.
`bbox` = `_bbox(obj.boundingBox)` when the attribute exists, else `None`. `properties` by type
(attribute reads wrapped so one failing read yields `None` for that key):

| Type | properties | parent |
|---|---|---|
| `BRepBody` | `is_solid`, `is_visible`, `volume`, `area`, `faces`, `edges`, `vertices` (counts), `material` (`material.name`), `appearance` (`appearance.name`), `occurrence_path` | `parentComponent` |
| `MeshBody` | `is_visible`, `nodes` (`mesh.nodeCount`), `triangles` (`mesh.triangleCount`) | `parentComponent` |
| `BRepFace` | `surface` (`_surface`), `area`, `index` in `body.faces` | `body` |
| `BRepEdge` | `curve` (`_curve`), `length`, `index` in `body.edges` | `body` |
| `BRepVertex` | `point`, `index` in `body.vertices` | `body` |
| `Component` | `bodies`, `mesh_bodies`, `sketches`, `features`, `occurrences` (counts), `part_number`, `description`, `material` | `None` for root; else first `design.rootComponent.allOccurrencesByComponent(c)` item's `assemblyContext` component or root |
| `Occurrence` | `full_path`, `is_visible`, `is_grounded`, `is_light_bulb_on`, `transform` (`transform2.asArray()` with translation items 3/7/11 through `ctx.length_out`) | `assemblyContext` or root |
| `Sketch` | `profiles`, `curves` (`sketchCurves.count`), `points`, `is_fully_constrained`, `is_visible`, `reference_plane` (Ref) | `parentComponent` |
| `ExtrudeFeature`, `RevolveFeature` | the `Feature subclasses` keys below plus `profiles` (and for revolve `axis`), § 2.2.1 | `parentComponent` |
| `Feature` subclasses | `health` (`healthState` name), `is_suppressed`, `timeline_index` (`timelineObject.index`, `None` in direct mode) | `parentComponent` |
| `UserParameter`/`ModelParameter` | `expression`, `value` (`value` converted by `unit`: length → `length_out`, angle → `angle_out`, else raw), `unit`, `comment` | `None` |
| `ConstructionPlane` / `ConstructionAxis` / `ConstructionPoint` | `is_visible`, `definition` (`definition.objectType` short name) | `parent` |
| `Joint` / `AsBuiltJoint` | `motion` (`jointMotion.jointType` name), `occurrence_one`, `occurrence_two` (Refs), `is_suppressed` | `parentComponent` |
| any other | `name` when present | `None` |

#### 2.2.1 Extrude and revolve inputs (REQ-INS-017)
`profiles`: `feature.profile` is a `Profile` or an `ObjectCollection`; each profile `p` gives
`{"sketch": p.parentSketch.name, "index": profile_index(p)}`. No caps: nothing is dropped or rounded.
`properties["profile_curves"]` is `ctx.page(seq, offset, limit, build=...)` (core § 5.5; `RESULT_TOO_LARGE` with a
smaller-limit hint when a page is too big), where `seq` is the cheap flat sequence of curve handles
(profile position, loop, curve) over all profiles in order; `build` makes only the requested slice's
records, each `{"profile": <position in profiles>, "loop": <loop index>, ...curve}` from
`profile_loops(ctx, p)`. `profile_index` runs once per profile, for `profiles` only. `object_info` takes
optional `offset: int = 0, limit: int = 25` (a curve record is up to about 700 bytes) like the `list_*` actions; for other entity types they are ignored (no error) and the result is not paged.
- `profile_index(profile)` → position in `profile.parentSketch.profiles` of the profile whose
  `areaProperties()` area and centroid match within 1e-9 cm² / 1e-7 cm, else `None`.
- `profile_loops(ctx, profile)` → `[[curve, ...], ...]`, one list per `profile.profileLoops`
  item in Fusion order, curves in `loop.profileCurves` order. Lengths in cm are converted to
  `ctx.length_out`. Each `pc` is read through
  `pc.geometry` (the curve as trimmed in the profile, in sketch space, not `pc.sketchEntity`, which
  is the whole untrimmed sketch curve) and `pc.geometryType`; points are `[x, y]` (z dropped):

| `geometryType` (`adsk.core.Curve3DTypes`) | curve |
|---|---|
| `Line3DCurveType` | `{"type": "line", "start": [x, y], "end": [x, y]}` |
| `Arc3DCurveType` | `{"type": "arc", "center": [x, y], "radius": r, "start": [x, y], "end": [x, y], "ccw": g.normal.z > 0}` (`ccw`: travelling from `start` to `end` is counter-clockwise in sketch space) |
| `Circle3DCurveType` | `{"type": "circle", "center": [x, y], "radius": r}` |
| `Ellipse3DCurveType`, `EllipticalArc3DCurveType`, `NurbsCurve3DCurveType` | `{"type": "ellipse"\|"elliptical_arc"\|"spline", "points": [[x, y], ...]}`: 16 points at equal parameter steps over `getParameterExtents` (`g.evaluator.getPointsAtParameters`), first and last equal to the curve ends. A documented simplification: a sampled polyline, not the exact curve |
| any other | `{"type": "other", "points": [start, end]}` from `pc.geometry.evaluator` ends, or, when no evaluator, raise |

A raising read of any curve raises out of `profile_loops`; `object_info` then returns
`profile_curves: None` and `profiles` stays (the per-key guard of § 2.2), never a partial list.

`axis` (revolve only), `a = feature.axis`; `{"kind", "name", "ref", "origin", "direction"}` with
`origin` via `_pt` and `direction` a unit vector via `_vec`, both in model space (the feature's
component space):

| `a` | `kind` | `name` | `ref` | `origin`, `direction` |
|---|---|---|---|---|
| `ConstructionAxis` equal (`entityToken`) to the component's `x/y/zConstructionAxis` | `origin` | `"X"\|"Y"\|"Z"` | `None` | `a.geometry.origin`, `a.geometry.direction` (`InfiniteLine3D`) |
| other `ConstructionAxis` | `construction_axis` | `a.name` | `ctx.ref(a)` | same |
| `BRepEdge` | `edge` | `None` | `ctx.ref(a)` | `a.geometry` (`Line3D`) `startPoint`; `endPoint - startPoint` |
| `SketchLine` | `sketch_line` | `None` | `{"sketch": {"name": s.name}, "line": n}`, `n` = `_index_of(s.sketchCurves.sketchLines, a)` | `s = a.parentSketch`; `g = a.geometry` (`Line3D`, sketch space); `s.sketchToModelSpace(g.startPoint)` and `(g.endPoint)`; origin = first, direction = second − first |

An axis of another type, or a failing read, gives `axis: None`. The edge Line3D, `InfiniteLine3D` and `sketchToModelSpace`
facts are verified by the `object_info` live scenario (§ 3.2).

### 2.3 list_bodies — read (REQ-INS-003)
```python
def list_bodies(ctx: Ctx, component: Component | None = None, offset: int = 0, limit: int = 100) -> dict
```
Items from `_bodies(design, component)`, each:
`{"ref", "kind": "brep"|"mesh", "occurrence_path", "is_solid" (brep) | null, "is_visible",
"volume" (brep: `body.volume`) | null, "area" (brep: `body.area`) | null, "faces", "edges"
(brep counts) | null}`. Returns `ctx.page(items, offset, limit)`. Items are built only for the
requested page slice (counts first, then rows for `offset..offset+limit`).

### 2.4 list_faces — read (REQ-INS-004)
```python
def list_faces(ctx: Ctx, body: Body, offset: int = 0, limit: int = 100) -> dict
```
For `i` in the page slice of `body.faces`: `{"index": i, "ref", "surface_type", "area",
"centroid", ...surface extras}`. `surface_type` from `face.geometry.surfaceType`
(`SurfaceTypes.PlaneSurfaceType` → `"plane"`, `CylinderSurfaceType` → `"cylinder"`,
`ConeSurfaceType` → `"cone"`, `SphereSurfaceType` → `"sphere"`, `TorusSurfaceType` → `"torus"`,
`EllipticalCylinderSurfaceType` → `"elliptical_cylinder"`, `EllipticalConeSurfaceType` →
`"elliptical_cone"`, `NurbsSurfaceType` → `"nurbs"`). `area` = `face.area`; `centroid` =
`face.centroid`. Extras: plane → `"normal"` from `face.evaluator.getNormalAtPoint(face.pointOnFace)`
(returns `(ok, Vector3D)`; outward, accounts for `isParamReversed`, unlike `Plane.normal`);
cylinder → `"radius"`, `"axis"` (`geometry.axis`), `"origin"`; cone → `"radius"`, `"axis"`,
`"half_angle"`; sphere → `"radius"`, `"center"`; torus → `"major_radius"`, `"minor_radius"`,
`"axis"`, `"center"`.

### 2.5 list_edges — read (REQ-INS-005)
```python
def list_edges(ctx: Ctx, body: Body, offset: int = 0, limit: int = 100) -> dict
```
For each edge in the page slice: `{"index", "ref", "curve_type", "length", "start", "end"}` plus
`"center"`, `"radius"` for arcs/circles. `curve_type` from `edge.geometry.curveType`
(`Curve3DTypes.Line3DCurveType` → `"line"`, `Arc3DCurveType` → `"arc"`, `Circle3DCurveType` →
`"circle"`, `Ellipse3DCurveType` → `"ellipse"`, `EllipticalArc3DCurveType` → `"elliptical_arc"`,
`NurbsCurve3DCurveType` → `"nurbs"`, other → `"other"`). `start`/`end` from
`edge.startVertex.geometry`/`edge.endVertex.geometry`; a closed edge without vertices (`None`)
gives `null`.

### 2.6 geometry — read (REQ-INS-006)
```python
def geometry(ctx: Ctx, entity: Entity) -> dict
```
Accepts `BRepBody`, `BRepFace`, `BRepEdge`, `BRepVertex`; anything else → `INVALID_PARAMS`
("geometry takes a body, face, edge or vertex"). Results:
- face: `{"ref", "index", "surface": _surface(...) + full parameters, "area", "centroid",
  "perimeter" (sum of `edge.length` over `face.edges`), "loops" (`face.loops.count`),
  "edges": [indices in body.edges], "adjacent_faces": [indices of the other face of each edge,
  deduplicated]}`. Full parameters add: plane `origin`, `u_direction`; cylinder/cone `origin`;
  nurbs `degree_u`, `degree_v`, `control_points` (`controlPointCountU × V`) from
  `geometry.getData()` (`NurbsSurface`).
- edge: `{"ref", "index", "curve": _curve(...) + full parameters, "length", "start", "end",
  "faces": [indices of edge.faces], "vertices": [indices]}`. Full parameters: line `direction`;
  arc `start_angle`/`end_angle`/`normal`; circle `normal`; ellipse `major_radius`, `minor_radius`;
  nurbs `degree`, `control_points`.
- vertex: `{"ref", "index", "point", "edges": [indices]}`.
- body: `{"ref", "kind", "is_solid", "volume", "area", "lumps" (`lumps.count`), "shells"
  (`shells.count`), "faces", "edges", "vertices" (counts), "surface_types": {"plane": n, ...},
  "bbox"}`.

### 2.7 bounds — read (REQ-INS-007)
```python
def bounds(ctx: Ctx, entity: Entity | None = None, precise: bool = False) -> dict
```
`entity` `None` → `design.rootComponent` (whole design). Without `precise`: `_bbox(entity.boundingBox)`
(object lacking `boundingBox` → `INVALID_PARAMS`). `BRepBody.boundingBox` can be loose for curved
surfaces. With `precise`: `app.measureManager.getOrientedBoundingBox(entity, Vector3D(1,0,0),
Vector3D(0,1,0))` (`OrientedBoundingBox3D`: `centerPoint`, `length`, `width`, `height`) gives a
tight world-axis box; `min = center - size/2`. `measureManager` or `getOrientedBoundingBox`
missing → `UNSUPPORTED`. Result `{"ref" | null, "precise", "min", "max", "size", "center"}`.

### 2.8 measure_distance — read (REQ-INS-008)
```python
def measure_distance(ctx: Ctx, a: Entity, b: Entity) -> dict
```
`r = app.measureManager.measureMinimumDistance(a, b)` (`MeasureResults`: `value` cm,
`positionOne`, `positionTwo`). Accepts BRep entities, sketch entities, construction geometry and
occurrences. Result `{"distance", "point_a", "point_b", "delta": point_b - point_a}`. Gotcha:
pass proxy Refs from `list_bodies` for bodies inside occurrences; a native body Ref measures in
component space.

### 2.9 measure_angle — read (REQ-INS-009)
```python
def measure_angle(ctx: Ctx, a: Entity, b: Entity, c: Entity | None = None) -> dict
```
`app.measureManager.measureAngle(a, b)` or `measureAngle(a, b, c)` (three points/vertices: angle
at `b`). `MeasureResults.value` is radians. Result `{"angle", "points": [positionOne,
positionTwo, positionThree when present]}`. Fusion raising (e.g. two parallel cylinders) →
`FUSION_ERROR` with Fusion's message.

### 2.10 physical_properties — read, timeout 120 (REQ-INS-010)
```python
def physical_properties(ctx: Ctx, entities: list[Entity] | None = None,
                        accuracy: Accuracy = "very_high") -> dict
```
`entities` `None` → `[design.rootComponent]` (whole design including occurrences). Each entity
must be `BRepBody`, `Component` or `Occurrence`, else `INVALID_PARAMS` naming its type.
Per entity `p = e.getPhysicalProperties(level)`; when the build lacks `getPhysicalProperties`,
`p = e.physicalProperties` and that item's `accuracy` is `"low"`. Per item:
`{"ref", "accuracy", "mass" (p.mass kg), "volume", "area", "density", "center_of_mass"
(p.centerOfMass), "moments": {"xx","yy","zz","xy","yz","xz"} (p.getXYZMomentsOfInertia() →
(ok, xx, yy, zz, xy, yz, xz), about the world origin), "principal_moments": [i1,i2,i3]
(p.getPrincipalMomentsOfInertia()), "principal_axes": [[...],[...],[...]] (p.getPrincipalAxes()
→ (ok, x, y, z))}`.
Result `{"items": [...], "total": {...}}` where `total` (only when more than one item) has `mass`,
`volume`, `area` sums, `density` = mass/volume (null when volume 0), `center_of_mass` mass-weighted
mean, and `moments` component sums (valid because all are about the world origin). Principal
values are per item only. Gotcha: overlapping entities (a component and one of its bodies) are
double-counted; the caller chooses the entities.

### 2.11 interference — read, timeout 300 (REQ-INS-011)
```python
def interference(ctx: Ctx, entities: list[Entity] | None = None,
                 include_coincident_faces: bool = False, offset: int = 0, limit: int = 100) -> dict
```
`entities` `None` → every visible BRep body from `_bodies(design)` (kind `"brep"`). Each entity
must be `BRepBody` or `Occurrence` (else `INVALID_PARAMS`); fewer than 2 → `INVALID_PARAMS`; more
than 200 → `INVALID_PARAMS` with the count and the hint to pass `entities`.
Calls: `coll = adsk.core.ObjectCollection.create()`, `coll.add(e)` each;
`inp = design.createInterferenceInput(coll)`; `inp.areCoincidentFacesIncluded =
include_coincident_faces`; `res = design.analyzeInterference(inp)` (`InterferenceResults`). Per
result `r`: `{"a": ref(r.entityOne), "b": ref(r.entityTwo), "volume": r.interferenceBody.volume,
"bbox": _bbox(r.interferenceBody.boundingBox)}` (`interferenceBody` is a transient body, not
added to the design; `createCorrespondingBodies` is never called). Result
`ctx.page(pairs, offset, limit)` plus `"checked": len(entities)`.

### 2.12 compare_meshes — read, timeout 300 (REQ-INS-012)
```python
def compare_meshes(ctx: Ctx, test: Entity, reference: Entity, samples: int = 2000,
                   tolerance: Length | None = None, symmetric: bool = False) -> dict
```
`samples` outside 1..20000 → `INVALID_PARAMS`. `test` and `reference` are each `MeshBody` or
`BRepBody` (else `INVALID_PARAMS`); at least one must be a `MeshBody` (else `INVALID_PARAMS`,
hint: use `measure_distance` for two BRep bodies).

Triangle extraction `_triangles(body) -> (nodes: list[tuple], tris: list[tuple[int,int,int]])`
in cm, world space, with feature detection:
- `MeshBody`: `body.displayMesh` (`TriangleMesh`) when `hasattr(body, "displayMesh")`, else
  `body.mesh` (`PolygonMesh`) when it has `triangleNodeIndices`; neither → `UNSUPPORTED`
  ("this Fusion build exposes no mesh data for MeshBody"). Nodes from `nodeCoordinatesAsDouble`
  (flat xyz), triangles from `nodeIndices` (`TriangleMesh`) or `triangleNodeIndices`
  (`PolygonMesh`).
- `BRepBody`: `calc = body.meshManager.createMeshCalculator()`;
  `calc.setQuality(adsk.fusion.TriangleMeshQualityOptions.VeryHighQualityTriangleMesh)`;
  `m = calc.calculate()`; same fields. Missing `meshManager` → `UNSUPPORTED`.
- World space: when `body.assemblyContext` is not `None`, compare the node AABB with
  `body.boundingBox`; if they differ by more than 1e-4 cm on any axis, transform every node by
  `body.assemblyContext.transform2` (row-major `asArray()` 4×4 applied in Python). This detects
  whether the build already returned world coordinates for proxies.

Sampling `_sample_points(nodes, tris, samples)`: all nodes followed by every triangle centroid;
when more than `samples`, take every `k`-th with `k = ceil(len / samples)` (deterministic).

Distance `_deviation(points, nodes, tris) -> list[float]` (pure Python, unit-tested): uniform grid
over the reference AABB (expanded by 1e-6 cm) with cell size `s = max(diag / cbrt(len(tris)), 1e-6)`;
each triangle is registered in every cell its AABB overlaps. For each point, search cubic shells
`k = 0, 1, 2, ...` of cells around the point's cell (clamped to the grid; a point outside the grid
starts at the nearest cell), computing exact point–triangle distance (Ericson, *Real-Time
Collision Detection* § 5.1.5, closest point on triangle) for triangles not yet tested; stop when
the best distance ≤ `k * s` or all cells are visited. Unsigned distances.

Stats `_stats(d, tol)` (pure Python): `{"min", "max", "mean", "rms", "std" (population), "median",
"p95" (nearest-rank), "count"}` plus `"within_tolerance"` = fraction `d <= tol` (null when no
tolerance). Lengths go through `ctx.length_out`.

Result:
```json
{"test": {Ref}, "reference": {Ref}, "samples": 2000,
 "forward": {"min": 0.0, "max": 0.0, "mean": 0.0, "rms": 0.0, "std": 0.0, "median": 0.0,
             "p95": 0.0, "count": 2000, "within_tolerance": null, "max_point": [x, y, z]},
 "reverse": {...} | null, "hausdorff": 0.0 | null}
```
`forward` = test samples → reference surface; `symmetric` adds `reverse` (reference samples →
test surface) and `hausdorff` = max of both maxima. Reference triangle count above 5,000,000 →
`INVALID_PARAMS` ("reference mesh too large").

### 2.13 get_selection — read (REQ-INS-013)
```python
def get_selection(ctx: Ctx, offset: int = 0, limit: int = 100) -> dict
```
`sels = ctx.ui.activeSelections`; for `i` in `range(sels.count)`: `s = sels.item(i)` →
`{"ref": ctx.ref(s.entity), "point": _pt(s.point) or null}`. Returns `ctx.page`. Works with any
active product; no design required. Does not change the selection.

### 2.14 section_analysis — write (REQ-INS-014)
```python
@action("inspect", access="write")
def section_analysis(ctx: Ctx, plane: Plane, offset: Length = 0, flip: bool = False,
                     name: str | None = None) -> dict
```
API presence is uncertain across builds, so it is feature-detected:
`analyses = getattr(design, "analyses", None)`; `sa = getattr(analyses, "sectionAnalyses", None)`;
either missing → `UNSUPPORTED` ("section analysis API not available in this Fusion build").
Calls: `inp = sa.createInput(plane, adsk.core.ValueInput.createByString(offset.expr))`; when
`flip`: `inp.isFlipped = True` if `hasattr(inp, "isFlipped")`, else `UNSUPPORTED` naming `flip`;
`a = sa.add(inp)`; `name` given → `a.name = name`. Result
`{"analysis": {"name": a.name, "index": sa.count - 1, "plane": ctx.ref(plane) | "XY"|"XZ"|"YZ",
"offset": ctx.length_out(offset.cm), "flipped": flip}}`. The live scenario (§ 3.2) proves the
API on this machine's build.

## 3. Testability
### 3.1 Unit (`tests/test_inspect.py`, fake adsk)
Fakes are added additively (no change to existing behaviour) to `tests/fakes/adsk/core.py`
(`MeasureManager`, `Selections`, `OrientedBoundingBox3D`) and `tests/fakes/adsk/fusion.py` (the rest): `MeasureManager`
(`measureMinimumDistance`, `measureAngle`, `getOrientedBoundingBox`), `BRepFace.evaluator`,
`centroid`, surface geometries (`Plane`, `Cylinder`, `Sphere`), edge curves (`Line3D`, `Circle3D`),
`PhysicalProperties` with `getXYZMomentsOfInertia`, `getPrincipalMomentsOfInertia`,
`getPrincipalAxes`, `createInterferenceInput`/`analyzeInterference`, `MeshBody` with
`displayMesh`, `Selections`, `Analyses.sectionAnalyses`, and (REQ-INS-017) `Profile.parentSketch`/`profileLoops`, `ProfileLoop.profileCurves`,
`ProfileCurve.geometry`/`geometryType`, `Sketch.sketchCurves.sketchLines`/`sketchToModelSpace`, `SketchLine.geometry`, `RevolveFeature.axis`
(`tests/fakes/adsk/fusion.py`), and `Line3D`, `Arc3D`, `Circle3D`, `InfiniteLine3D` with `evaluator.getPointsAtParameters`
(`tests/fakes/adsk/core.py`). Tests:
- `summary`: parametric and direct fakes; no document → `NO_DOCUMENT`; missing `designIntent` →
  `null`.
- `object_info` per row of the § 2.2 table; failing attribute read → `None` for that key.
- `object_info` extrude/revolve (§ 2.2.1): rectangle profile (4 lines, `index`, `in` scaling), circle,
  arc (`ccw` both ways), ellipse (`points` ends equal curve ends), profile collection of two,
  unmatched profile → `index` `None`, raising `profileLoops` → `profile_curves` `None` (`profiles` stays), raising `areaProperties` → `profiles` `None`; revolve axis
  for each of origin, construction axis, edge and `SketchLine` (offset-plane sketch: `sketchToModelSpace`
  applied), unsupported axis type → `None`. `profile_index` and `profile_loops` directly.
- `list_bodies`/`list_faces`/`list_edges`: page keys, slicing, unit conversion with `in`.
- `geometry`: face adjacency on a fake box; sketch → `INVALID_PARAMS`.
- `bounds`: default, `precise`, missing `getOrientedBoundingBox` → `UNSUPPORTED`.
- `physical_properties`: single item, two-item totals (mass-weighted COM, summed moments),
  fallback to `physicalProperties` reports `"low"`, wrong type → `INVALID_PARAMS`.
- `interference`: one entity → `INVALID_PARAMS`; 201 fake bodies → `INVALID_PARAMS`, 200 succeed; pairs paged; `createCorrespondingBodies` never
  called.
- `_deviation`: point 1 cm above a unit square (two triangles) → 1.0; point beyond an edge →
  Euclidean distance to the edge; random points against brute force over 200 triangles agree to
  1e-12. `_stats` against hand-computed values. `_sample_points` stride. Proxy transform applied
  only when the AABB mismatches.
- `compare_meshes`: no mesh accessors → `UNSUPPORTED`; two BRep → `INVALID_PARAMS`.
- `get_selection`: two fake selections.
- `section_analysis`: no `analyses` → `UNSUPPORTED`; flip without `isFlipped` → `UNSUPPORTED`.

### 3.2 Live (`LIVE_SCENARIOS` in `inspect.py`, `python scripts/live_check.py --only inspect`)
`LIVE_SCENARIOS` is a list of `(name, "inspect.<name>", async fn(session))` (core § 10): every
scenario name is the action it tests, so `--only inspect.<action>` selects it. Actions are called
through `live_check.call(session, tool, action, params)`. Boxes come from `live_check.box(session, w, d, h)` (core § 10); a placed box is moved with `model.move(translate=...)`. Scenarios (each starts in the harness's scratch design):
- `summary`: one box → counts per REQ-INS-001.
- `object_info`: body and face Refs → types `BRepBody`, `BRepFace`. Then (REQ-INS-017): `sketch.create` on `XY` with
  `sketch.rectangle {"mode": "two_point", "p1": [0, 0], "p2": [20, 10]}`, `model.extrude` → `profiles`
  has 1 entry, `index` 0, one loop of 4 `line` curves whose corners are (0,0), (20,0), (20,10), (0,10) mm;
  a circle sketch → one `circle` curve, `radius` as drawn; an `XY` sketch with a line `[[0, 0], [0, 20]]` then a
  rectangle `p1 [10, 0]`, `p2 [20, 20]` revolved (`model.revolve`) about `{"sketch": <ref>, "line": 0}` →
  `axis.kind` `"sketch_line"`, `direction` [0, 1, 0], `origin` [0, 0, 0]; the same on a sketch on a construction plane
  offset 5 mm in Z → `origin` z 5 mm (settles `ProfileCurve.geometry` sketch-space coordinates, `Line3D` /
  `sketchToModelSpace` and the `{"sketch", "line"}` index); revolve about `"Y"` → `kind` `"origin"`, `name` `"Y"`; an
  ellipse sketch extrude → `type` `"ellipse"` with first point equal to last.
- `list_bodies`: two boxes, `limit` 1 → `next_offset` 1; token round-trip.
- `list_faces`: 20 × 10 × 5 mm box → 6 plane faces with ±axis normals.
- `list_edges`: 20 × 10 × 5 mm box → 12 line edges summing to 140 mm.
- `geometry`: face → 4 edges, 4 adjacent faces.
- `bounds`: box → size [20, 10, 5]; `precise` same within 1e-6.
- `measure_distance`: boxes 30 mm apart → distance 30.
- `measure_angle`: adjacent faces of a box → angle 90.
- `physical_properties`: volume 1000, area 700, mass = volume × density.
- `interference`: two 10 mm cubes overlapping 5 mm in X → one pair, volume 500; body count
  unchanged.
- `compare_meshes`: export the 20 × 10 × 5 box and a 20 × 10 × 6 box each with
  `document.export(paths=[<temp dir>/<name>.stl], format="stl", body=<ref>)` and insert
  both with `document.import_mesh(paths=[...], unit="mm")` (mesh Refs from
  `results[0].result.meshes`, read only when envelope `ok` and `results[0].ok` are both true, else the scenario fails; document design § 2.7); mesh vs own BRep → `forward.max` < 0.001; 6 mm mesh vs
  5 mm mesh → `forward.max` 1 ± 0.001.
- `get_selection`: `ui` selection empty → `total` 0.
- `section_analysis`: `"XY"`, offset 2 → `analysis.name` present; a second call → index + 1.
