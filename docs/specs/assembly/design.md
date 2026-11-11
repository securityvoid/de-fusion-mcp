# design.md — docs/specs/assembly/
> De-Fusion MCP — `assembly` tool: components, occurrences, joints, as-built joints, rigid groups and contact sets

## 1. Overview
One module, `De-Fusion/defusion/tools/assembly.py`, registers eight actions on tool `assembly`
(tool summary sentence: "Components, occurrences, joints, rigid groups and contact sets of the
active design."). Everything not written here (schema, validation, Ref resolution, units,
main-thread dispatch, undo grouping, change summary, envelope, errors, paging, help, profiles) is
core behaviour (core § 5). Every action calls `ctx.require_design()` first (`NO_DESIGN` when there
is no active design; core § 5.9). Lengths in results go through `ctx.length_out`, angles through
`ctx.angle_out` (core § 8). Entities in results are `ctx.ref(obj)` (core § 5.4).

| Action | Access | REQ |
|---|---|---|
| `list_components` | read | REQ-ASM-001 |
| `list_occurrences` | read | REQ-ASM-002 |
| `contact_get` | read | REQ-ASM-003 |
| `create_component` | write | REQ-ASM-004 |
| `joint` | write | REQ-ASM-005, REQ-ASM-006, REQ-ASM-007 |
| `as_built_joint` | write | REQ-ASM-005, REQ-ASM-007, REQ-ASM-008 |
| `rigid_group` | write | REQ-ASM-009 |
| `contact_set` | write | REQ-ASM-010 |

All timeouts are the core default (30 s). Joints, as-built joints, rigid groups and contact sets
are always added to `design.rootComponent` collections, using assembly-context proxies (§ 3.5), so
they work for occurrences at any depth.

## 2. Module-level definitions
```python
JointType = Literal["rigid", "revolute", "slider", "cylindrical", "pin_slot", "planar", "ball"]
AxisName = Literal["x", "y", "z"]
KeyPoint = Literal["center", "start", "middle", "end"]
```
Mappings (module constants):

| Name | `adsk` value |
|---|---|
| `rigid` … `ball` | `adsk.fusion.JointTypes.RigidJointType`, `RevoluteJointType`, `SliderJointType`, `CylindricalJointType`, `PinSlotJointType`, `PlanarJointType`, `BallJointType` |
| `x` / `y` / `z` | `adsk.fusion.JointDirections.XAxisJointDirection` / `YAxisJointDirection` / `ZAxisJointDirection` |
| `center` / `start` / `middle` / `end` | `adsk.fusion.JointKeyPointTypes.CenterKeyPoint` / `StartKeyPoint` / `MiddleKeyPoint` / `EndKeyPoint` |

The reverse joint-type map turns `joint.jointMotion.jointType` into the string reported as `type`.

Pydantic models (Ref-typed fields are resolved by the core resolver, core § 5.4):
```python
class JointGeo(BaseModel, extra="forbid"):
    face: Face | None = None
    edge: Edge | None = None
    sketch_curve: SketchCurve | None = None
    vertex: Vertex | None = None
    sketch_point: SketchPoint | None = None
    origin_of: Occurrence | None = None     # origin point of this occurrence's component
    root_origin: bool = False               # origin point of the root component
    joint_origin: JointOrigin | None = None # an existing joint origin, used as is
    keypoint: KeyPoint = "center"           # used for face, edge, sketch_curve only
    occurrence: Occurrence | None = None    # assembly context for the entity (see § 3.5)

class JointLimitsIn(BaseModel, extra="forbid"):
    rotation_min: Angle | None = None
    rotation_max: Angle | None = None
    rotation_rest: Angle | None = None
    slide_min: Length | None = None
    slide_max: Length | None = None
    slide_rest: Length | None = None

class ContactSetCreate(BaseModel, extra="forbid"):
    name: str
    occurrences: list[Occurrence] = []
    bodies: list[Body] = []
```

Shared helpers (private, in the module):
- `_occ_out(ctx, occ) -> dict` — the occurrence item of § 3.2.
- `_geometry(ctx, g: JointGeo) -> (JointGeometry, Occurrence | None)` — § 3.5.
- `_set_motion(inp, type, axis, second_axis)` — § 3.6 motion table, used by `joint` and
  `as_built_joint`.
- `_apply_limits(ctx, joint, type, limits) -> dict | None` — § 3.7.
- `_joint_out(ctx, joint) -> dict` — the joint result of § 3.6.
- `_analysis(design) -> str` — the `analysis` value of § 3.3, used by `contact_get` and
  `contact_set`.

## 3. Actions
### 3.1 list_components
```python
@action("assembly", access="read")
def list_components(ctx: Ctx, offset: int = 0, limit: int = 100) -> dict:
```
- API: `design.allComponents` (iterate `count`/`item(i)`), `design.rootComponent`,
  `rootComponent.allOccurrencesByComponent(c).count`, `c.bRepBodies.count`, `c.sketches.count`,
  `c.joints.count`, `c.asBuiltJoints.count`, `c.partNumber`.
- Order: root first, then `allComponents` order.
- Result: `ctx.page(components, offset, limit, build=item)`, item:
  `{"component": <ref>, "part_number": str, "is_root": bool, "bodies": int, "sketches": int,
  "occurrences": int, "joints": int, "as_built_joints": int}`. Root's `occurrences` is 0.

### 3.2 list_occurrences
```python
@action("assembly", access="read")
def list_occurrences(ctx: Ctx, component: Component | None = None, recursive: bool = True,
                     offset: int = 0, limit: int = 100) -> dict:
```
- API: `comp.allOccurrences` when `recursive` else `comp.occurrences` (`comp` = `component` or
  `design.rootComponent`); per occurrence `fullPathName`, `component`, `isGrounded`,
  `isLightBulbOn`, `isVisible`, `transform2.getAsCoordinateSystem()` → `(origin: Point3D,
  xAxis, yAxis, zAxis: Vector3D)`.
- `path` = `fullPathName`; `depth` = `path.count("+") + 1`; `parent` = `path` up to the last
  `+`, or `null` at depth 1. When `component` is not root, `depth` and `parent` are still taken
  from the full path (absolute in the design).
- Result: `ctx.page(occurrences, offset, limit, build=item)`, item:
  ```json
  {"occurrence": <ref>, "path": "A:1+B:1", "parent": "A:1", "depth": 2,
   "component": <ref>, "grounded": false, "light_bulb": true, "visible": true,
   "transform": {"origin": [x, y, z], "x_axis": [..], "y_axis": [..], "z_axis": [..]}}
  ```
  `origin` values via `ctx.length_out`; axes are unit vectors, unconverted.
- Gotcha: occurrences from `allOccurrences` are proxies, so `transform2` is the world transform.
  `transform` (deprecated) is not used.

### 3.3 contact_get
```python
@action("assembly", access="read")
def contact_get(ctx: Ctx) -> dict:
```
- API: `design.isContactAnalysisEnabled`, `design.isContactSetAnalysis`; when
  `hasattr(design, "contactSets")`: iterate `design.contactSets` (`count`/`item(i)`), per set
  `name`, `isSuppressed`, and members from the first existing attribute of
  `occurrencesAndBodies`, `entities` (each an iterable of `Occurrence`/`BRepBody`).
- `analysis`: `"off"` when contact analysis is disabled; `"contact_sets"` when enabled and
  `isContactSetAnalysis`; else `"all"`.
- Result: `{"analysis": "off"|"all"|"contact_sets", "contact_sets": [{"contact_set": <ref>,
  "name": str, "suppressed": bool, "members": [<ref>...] | null}] | null}`.
  `contact_sets` is `null` when the API lacks `contactSets`; `members` is `null` when neither
  member attribute exists.
- Not paged: contact sets per design are few; the core size budget (core § 5.5) still applies.

### 3.4 create_component
```python
@action("assembly", access="write")
def create_component(ctx: Ctx, name: str | None = None, parent: Occurrence | None = None,
                     bodies: list[Body] | None = None) -> dict:
```
- API: `parent_comp = parent.component if parent else design.rootComponent`;
  `occ = parent_comp.occurrences.addNewComponent(adsk.core.Matrix3D.create())`; when `parent` is
  given, `occ = occ.createForAssemblyContext(parent)`; `occ.component.name = name` when given;
  per body `body.moveToComponent(occ)` (returns the moved `BRepBody`).
- `bodies == []` → `INVALID_PARAMS` ("omit bodies for an empty component").
- `bodies` given and `not hasattr(bodies[0], "moveToComponent")` → `UNSUPPORTED` (checked before
  the component is created).
- Result: `{"component": <ref>, "occurrence": <ref>, "bodies": [<ref of moved body>...]}`
  (`bodies` is `[]` for an empty component).
- Gotcha: moving bodies changes their tokens; the result returns the new refs.

### 3.5 Joint geometry resolution (`_geometry`)
1. Exactly one of `face`, `edge`, `sketch_curve`, `vertex`, `sketch_point`, `joint_origin`,
   `origin_of`, `root_origin=True` must be set; otherwise `INVALID_PARAMS` ("set exactly one geometry field").
2. Origins need no proxy step:
   - `origin_of`: `occ.component.originConstructionPoint.createForAssemblyContext(occ)`.
   - `root_origin`: `design.rootComponent.originConstructionPoint`.
3. Other entities: owning component = `face.body.parentComponent`, `edge.body.parentComponent`,
   `vertex.body.parentComponent`, `sketch_curve.parentSketch.parentComponent`,
   `sketch_point.parentSketch.parentComponent`, `joint_origin.parentComponent`. Context
   occurrence:
   - `entity.assemblyContext` not `None` → use the entity as is; context = that occurrence.
   - else `g.occurrence` given → it must satisfy `g.occurrence.component == owner` else
     `INVALID_PARAMS`; entity = `entity.createForAssemblyContext(g.occurrence)`.
   - else owner is the root component → native entity, context `None`.
   - else `occs = design.rootComponent.allOccurrencesByComponent(owner)`: one → proxy in it;
     none → `NOT_FOUND` ("component has no occurrence"); more → `AMBIGUOUS` with
     `candidates = ctx.refs(occs)`.
4. Build:
   | Field | Call |
   |---|---|
   | `face`, planar (`face.geometry.surfaceType == adsk.core.SurfaceTypes.PlaneSurfaceType`) | `JointGeometry.createByPlanarFace(face, None, kp)` |
   | `face`, non-planar | `JointGeometry.createByNonPlanarFace(face, kp)` |
   | `edge`, `sketch_curve` | `JointGeometry.createByCurve(curve, kp)` |
   | `vertex`, `sketch_point`, origins | `JointGeometry.createByPoint(point)` |
   | `joint_origin` | none: the (proxied) `JointOrigin` itself is the geometry (`Joints.createInput` accepts a `JointOrigin` in place of a `JointGeometry`); `keypoint` is ignored |
5. Returns `(geometry, context_occurrence)` where `geometry` is a `JointGeometry` or a
   `JointOrigin`; for origins the context is `origin_of` or `None`.
- Gotcha: an invalid keypoint for the entity (e.g. `start` on a planar face) makes Fusion raise;
  it surfaces as `FUSION_ERROR` with the hint "valid keypoints depend on the entity: faces use
  center; open curves use start/middle/end; circles use center".

### 3.6 joint
```python
@action("assembly", access="write")
def joint(ctx: Ctx, type: JointType, geometry_one: JointGeo, geometry_two: JointGeo,
          offset: Length | None = None, angle: Angle | None = None, flip: bool = False,
          axis: AxisName = "z", second_axis: AxisName | None = None,
          limits: JointLimitsIn | None = None, name: str | None = None) -> dict:
```
- `geometry_one` is the moving side (Fusion's first selection), `geometry_two` the fixed side.
- Both context occurrences equal (including both `None`) → `INVALID_PARAMS` ("geometry_one and
  geometry_two must be in different occurrences").
- `second_axis` given for a type other than `pin_slot`/`ball` → `INVALID_PARAMS`.
- API: `joints = design.rootComponent.joints`; `inp = joints.createInput(g1, g2)`; motion per
  table; `inp.offset = offset.value_input()`; `inp.angle = angle.value_input()`;
  `inp.isFlipped = flip`; `j = joints.add(inp)`; `j.name = name` when given; limits § 3.7.

  | type | Motion call (`a` = `axis`, `b` = `second_axis`, default the first of `x`, `y`, `z` that differs from `a`) |
  |---|---|
  | `rigid` | `inp.setAsRigidJointMotion()` |
  | `revolute` | `inp.setAsRevoluteJointMotion(a)` |
  | `slider` | `inp.setAsSliderJointMotion(a)` |
  | `cylindrical` | `inp.setAsCylindricalJointMotion(a)` |
  | `pin_slot` | `inp.setAsPinSlotJointMotion(a, b)` (rotation axis, slide direction) |
  | `planar` | `inp.setAsPlanarJointMotion(a)` (normal) |
  | `ball` | `inp.setAsBallJointMotion(a, b)` (pitch, yaw) |
- Result (`_joint_out`):
  ```json
  {"joint": <ref>, "type": "revolute", "occurrence_one": <ref|null>, "occurrence_two": <ref|null>,
   "offset": 10.0, "angle": 0.0, "flipped": false, "limits": {...} | null}
  ```
  `offset`/`angle` read from `j.offset.value` (cm) / `j.angle.value` (rad) via `ctx.length_out` /
  `ctx.angle_out`; `occurrence_one/two` from `j.occurrenceOne/Two`; `limits` from § 3.7.

### 3.7 Motion limits (`_apply_limits`)
Applied after the joint exists, on `j.jointMotion`:

| type | rotation_* → | slide_* → |
|---|---|---|
| `revolute` | `rotationLimits` | rejected |
| `slider` | rejected | `slideLimits` |
| `cylindrical`, `pin_slot` | `rotationLimits` | `slideLimits` |
| `rigid`, `planar`, `ball` | rejected | rejected |

- Any rejected field set → `INVALID_PARAMS` naming the field and type (checked before
  `joints.add`, so nothing is created).
- For each given value on a `JointLimits` object `L`: min → `L.isMinimumValueEnabled = True;
  L.minimumValue = v`; max → `isMaximumValueEnabled`/`maximumValue`; rest →
  `isRestValueEnabled`/`restValue`. Values are `.rad` for angles and `.cm` for lengths (JointLimits
  take internal units, not expressions).
- `rotation_min >= rotation_max` or `slide_min >= slide_max` when both given → `INVALID_PARAMS`.
- Reported `limits`: `{"rotation": {"min", "max", "rest"}, "slide": {...}}` with only the limit
  objects that apply to the type, each value `null` when not enabled, else converted with
  `ctx.angle_out` / `ctx.length_out`. `null` for `rigid`, `planar`, `ball`.

### 3.8 as_built_joint
```python
@action("assembly", access="write")
def as_built_joint(ctx: Ctx, type: JointType, occurrence_one: Occurrence,
                   occurrence_two: Occurrence, geometry: JointGeo | None = None,
                   axis: AxisName = "z", second_axis: AxisName | None = None,
                   limits: JointLimitsIn | None = None, name: str | None = None) -> dict:
```
- `occurrence_one == occurrence_two` → `INVALID_PARAMS`. `geometry` is `None` and `type !=
  "rigid"` → `INVALID_PARAMS` ("geometry required for <type>"). `second_axis` rule as § 3.6.
- API: `abj = design.rootComponent.asBuiltJoints`;
  `inp = abj.createInput(occurrence_one, occurrence_two, g)` with `g = _geometry(...)[0]` or `None`.
  With `geometry.joint_origin`, Fusion's documented parameter is a `JointGeometry`; if
  `createInput` raises for the `JointOrigin`, the action returns `UNSUPPORTED` "as-built joints do
  not accept a joint origin on this Fusion build" (the live scenario passes on success or that
  outcome);
  same motion table as § 3.6 on `inp`; `j = abj.add(inp)`; name; limits § 3.7.
- Result: `_joint_out` without `offset`, `angle`, `flipped`.

### 3.9 rigid_group
```python
@action("assembly", access="write")
def rigid_group(ctx: Ctx, occurrences: list[Occurrence], include_children: bool = True,
                name: str | None = None) -> dict:
```
- Fewer than 2 distinct occurrences → `INVALID_PARAMS`.
- API: `coll = adsk.core.ObjectCollection.create()`; add each; `g =
  design.rootComponent.rigidGroups.add(coll, include_children)`; `g.name = name` when given.
- Result: `{"rigid_group": <ref>, "name": g.name, "occurrences": [<ref>...]}` from `g.occurrences`.

### 3.10 contact_set
```python
@action("assembly", access="write")
def contact_set(ctx: Ctx, analysis: Literal["off", "all", "contact_sets"] | None = None,
                create: ContactSetCreate | None = None, set: ContactSet | None = None,
                enabled: bool | None = None) -> dict:
```
- None of `analysis`, `create`, `set` given → `INVALID_PARAMS`. `set` without `enabled`, or
  `enabled` without `set` → `INVALID_PARAMS`. `create` with fewer than 2 members in total →
  `INVALID_PARAMS`.
- Order: `analysis`, then `create`, then `set`/`enabled`.
- `analysis`: `off` → `design.isContactAnalysisEnabled = False`; `all` → enabled `True`,
  `isContactSetAnalysis = False`; `contact_sets` → both `True`.
- `set` is a core `ContactSet` Ref (core § 5.4: name, token or output ref; core returns
  `NOT_FOUND`/`AMBIGUOUS`, and `UNSUPPORTED` when `design.contactSets` is absent).
- `create`: `not hasattr(design, "contactSets")` → `UNSUPPORTED`;
  `members` = a plain Python list of occurrences then bodies (Fusion rejects an `ObjectCollection` here); `cs = design.contactSets.add(members)`;
  `cs.name = create.name`. Set: `set.isSuppressed = not enabled`.
- Result: `{"analysis": <current, as § 3.3>, "created": <ref|null>, "updated": <ref|null>}`.

## 4. Testability
### 4.1 Unit (fake adsk)
`tests/test_assembly.py`, using `tests/fakes/adsk`, with `tests/fakes/adsk/fusion.py` extended with: `JointTypes`, `JointDirections`,
`JointKeyPointTypes`, `JointGeometry` (static `createBy*` recording their arguments), joints /
as-built joints / rigid groups collections recording `createInput`/`add` calls, a `JointLimits`
fake, `Occurrence` with `fullPathName`, `transform2.getAsCoordinateSystem()` and
`createForAssemblyContext`, `Component.allOccurrencesByComponent`, and a `Design` with contact
flags and an optional `contactSets`. Tests (names are `-k` selectors):
- `list_components`: root first, counts, paging.
- `list_occurrences`: depth/parent from paths, non-recursive, origin converted to `in`.
- `contact_get`: modes, sets, `contact_sets: null` without the attribute.
- `create_component`: empty, named, under parent, with bodies, `bodies: []` error, `UNSUPPORTED`.
- `geometry`: each field maps to its `createBy*` call and keypoint; `joint_origin` is returned
  as the (proxied) joint origin with no `createBy*` call; zero/two fields error; proxy
  rule (assemblyContext set, explicit occurrence, wrong occurrence, root, single, none, ambiguous).
- `joint`: each type's motion call and axes; offset/angle/flip; same-occurrence error;
  `second_axis` misuse error; result shape.
- `limits`: table acceptance/rejection, enabled flags and internal values, min >= max error.
- `as_built_joint`: rigid without geometry, non-rigid without geometry error, same occurrence error,
  `createInput` raising for a `joint_origin` → `UNSUPPORTED`.
- `rigid_group`: add call and include_children; fewer than two error.
- `contact_set`: three analysis modes, create, suppress, create `UNSUPPORTED`, empty call
  error.

### 4.2 Live (`LIVE_SCENARIOS` in `assembly.py`, run by `scripts/live_check.py --only assembly[.action]`)
Each row is one `LIVE_SCENARIOS` entry `(name, "assembly.<action>", async fn(session))` (core § 10),
tagged with the action in the first column; calls use `live_check.call(session, tool, action,
params, units=None)`. The `joint` scenario uses `script.execute_python`, so it runs with `--profile full`. Each scenario runs in the harness's scratch design and creates its own
fixtures. A fixture box (DEP-ASM-002) is `live_check.box(session, 20, 20, 10)` (core § 10), which returns the body; `assembly.create_component {"bodies": [body]}` puts it in its
own occurrence when a scenario needs one.

| Scenario (action) | Steps and checks |
|---|---|
| `create_component` | Create `A` and `B` empty; create `C` from two fixture root bodies; `list_components` shows `A`, `B`, `C` with `C` bodies 2. Proves `moveToComponent`. |
| `list_components` | Create `A` and `B`: root first, `total` ≥ 3, `A` and `B` report `occurrences` 1, `limit` 1 returns one item with `next_offset` 1. |
| `list_occurrences` | Create `A` and `P`, then `Q` with `parent` = P's occurrence: depths 1 and 2, `Q.parent == P.path`, `recursive: false` omits `Q`. Then a rigid `joint` with `geometry_one {"origin_of": A}`, `geometry_two {"root_origin": true}`, `offset "25.4 mm"`; `list_occurrences` with `units {"length": "in"}` shows A's `origin` `[0, 0, 1]` (±1e-6). |
| `joint` | For each of the seven types: two new empty components, `joint` origin-to-origin returns the requested `type`; a revolute joint with `limits {"rotation_min": -45, "rotation_max": 90}` reads back -45/90; a revolute joint from a fixture box's planar face (`face`, keypoint `center`) to a second box's straight edge (`edge`, keypoint `middle`) succeeds; a rigid joint whose `geometry_one` is `{"joint_origin": <ref>}` (fixture: `script.execute_python {"confirm": true, "code": "c = design.allComponents.itemByName('A'); ji = c.jointOrigins.createInput(adsk.fusion.JointGeometry.createByPoint(c.originConstructionPoint)); jo = c.jointOrigins.add(ji); jo.name = 'JO1'"}` on a new empty component `A`; the Ref is `{"name": "JO1"}`) and `geometry_two {"root_origin": true}` succeeds. |
| `as_built_joint` | Rigid between two occurrences: transforms unchanged; revolute with `geometry.origin_of`; revolute with `geometry.joint_origin` succeeds or returns `UNSUPPORTED`. |
| `rigid_group` | Two occurrences → Ref with name. |
| `contact_get` | New design: `analysis` `"off"`, `contact_sets` `[]` (or `null` on a build without `contactSets`). |
| `contact_set` | Set analysis `contact_sets`, create `C1` from two box occurrences, then `{"set": "C1", "enabled": false}`; `contact_get` reflects each step (`suppressed: true` last). On a build without `contactSets`, the scenario asserts `UNSUPPORTED` and `contact_sets: null` and passes. |
