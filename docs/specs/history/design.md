# design.md — docs/specs/history/
> De-Fusion MCP — `history` tool: timeline, snapshots, guarded undo, recipes

## 1. Overview
One module, `De-Fusion/defusion/tools/history.py`, registers 16 actions with
`@action("history", ...)`. Framework behaviour (schema, validation, Ref resolution, units, dispatch,
undo grouping, change summary, results, paging, errors, help, profiles) is core § 5 and is not
repeated here. Lengths and angles in results are in the call's output units (core § 8).

| Action | Access | Timeout | Section |
|---|---|---|---|
| `list` | read | 30 | § 3.1 |
| `snapshot_list` | read | 30 | § 3.2 |
| `dump_recipe` | read | 300 | § 3.3, § 7 |
| `set_marker` | write | 60 | § 4.1 |
| `reorder` | write | 60 | § 4.2 |
| `set_suppressed` | write | 60 | § 4.3 |
| `rename` | write | 30 | § 4.4 |
| `group_add` | write | 30 | § 4.5 |
| `snapshot_add` | write | 30 | § 4.6 |
| `snapshot_rename` | write | 30 | § 4.7 |
| `undo` | write (`undo_group=False`) | 60 | § 4.8 |
| `apply_recipe_step` | write | 180 | § 8 |
| `group_delete` | destructive | 60 | § 5.1 |
| `delete_after_marker` | destructive | 60 | § 5.2 |
| `snapshot_revert_pending` | destructive | 30 | § 5.3 |
| `snapshot_delete` | destructive | 30 | § 5.4 |

## 2. Shared helpers (module-private)
### 2.1 Item addressing
Parameters that name a timeline item have the annotation `ItemArg = int | TimelineItem`:
- `int` — timeline index as reported by `list`; resolved by `_item(tl, i)` = `tl.item(i)`;
  `i < 0` or `i >= tl.count` → `INVALID_PARAMS` (`"timeline index <i> out of range 0..<count-1>"`).
- `TimelineItem` Ref — resolved by core § 5.4 before the function runs (token →
  `entity.timelineObject`, `{"index": n}` → timeline item `n`), so the `entity` Ref returned by
  `list` addresses its item.

`ItemArg` and the snapshot address `int | str` are unions, allowed by core § 5.1; core § 5.4
resolves the matching Ref branch.

### 2.2 Item JSON
`_item_json(ctx, obj) -> dict` for a `TimelineObject`:
```json
{"index": 3, "name": "Extrude1", "type": "ExtrudeFeature",
 "entity": {"type": "ExtrudeFeature", "name": "Extrude1", "token": "...", "component": "Root"},
 "is_group": false, "suppressed": false, "rolled_back": false,
 "health": "healthy", "message": null, "group": null}
```
- `type`: `obj.entity.objectType.split("::")[-1]`; `"TimelineGroup"` when `obj.isGroup`; `null`
  when `obj.entity` is `None` or raises.
- `entity`: `ctx.ref(obj.entity)`; `null` for groups or when unavailable.
- `health`: `obj.healthState` mapped from `adsk.fusion.FeatureHealthStates`:
  `HealthyFeatureHealthState` → `healthy`, `WarningFeatureHealthState` → `warning`,
  `ErrorFeatureHealthState` → `error`, `UnknownFeatureHealthState` → `unknown`.
- `message`: `obj.errorOrWarningMessage` or `null` when empty.
- `group`: `obj.parentGroup.index` or `null`.
Every property read is individually guarded; a raising read yields `null` for that key only.

### 2.3 Expanded groups
Fusion raises on `rollTo`, `reorder`, `isSuppressed` and `name` writes for an item inside an expanded
timeline group. `_with_collapsed(obj, fn)` runs `fn()`; on an exception, when
`obj.parentGroup` is not `None` and `obj.parentGroup.isCollapsed` is false, it sets
`obj.parentGroup.isCollapsed = True` and runs `fn()` once more; a second failure raises
`FUSION_ERROR` with the Fusion message and hint `"item is inside timeline group <name>"`.
Collapsing changes only the UI state of the group.

### 2.4 Design type and feature detection
- `_timeline(ctx)`: `design = ctx.require_parametric()`; returns `design.timeline`. Used by every
  action except `undo` and `apply_recipe_step`.
- `_snapshots(ctx)`: `ctx.require_parametric()`; `hasattr(design, "snapshots")` else
  `UNSUPPORTED`; returns `design.snapshots`.
- `group_add` and `group_delete` check `hasattr(tl, "timelineGroups")`; snapshot actions check the
  `Snapshots`/`Snapshot` members they call (`add`, `revertPendingSnapshot`, `hasPendingSnapshot`,
  `deleteMe`); a missing member → `UNSUPPORTED` naming it.
- Fusion calls that return `False` instead of raising → `FUSION_ERROR`
  `"<Class>.<method> returned false"`.

### 2.5 Snapshot addressing
`_snapshot(snaps, ref: int | str)`: `int` → `snaps.item(i)` (`INVALID_PARAMS` when out of range);
`str` → the snapshots whose `name == ref`; none → `NOT_FOUND`, more than one → `AMBIGUOUS` with
each candidate's `{"index", "name"}` in `error.candidates`.

`_snapshot_json(i, s)` = `{"index": i, "name": s.name, "timeline_index": s.timelineObject.index}`
(`timeline_index` `null` when the read raises).

## 3. Read actions
### 3.1 list
```python
@action("history", access="read", name="list")
def list_items(ctx: Ctx, offset: int = 0, limit: int = 100) -> dict
```
(The function is `list_items` so the module keeps the builtin `list`.)
1. `tl = _timeline(ctx)`.
2. `objs = [tl.item(i) for i in range(tl.count)]`; then for each `g` in `tl.timelineGroups` (when
   present) whose `index` is not among `objs`' indices, insert `g` so `objs` stays sorted by index.
3. Return `{**ctx.page(objs, offset, limit, build=lambda o: _item_json(ctx, o)), "marker":
   tl.markerPosition, "count": tl.count}`.

### 3.2 snapshot_list
```python
@action("history", access="read")
def snapshot_list(ctx: Ctx, offset: int = 0, limit: int = 100) -> dict
```
`snaps = _snapshots(ctx)`; returns `{**ctx.page(range(snaps.count), offset, limit, build=lambda i:
_snapshot_json(i, snaps.item(i))), "pending": snaps.hasPendingSnapshot}`.

### 3.3 dump_recipe
```python
@action("history", access="read", timeout=300)
def dump_recipe(ctx: Ctx, offset: int = 0, limit: int = 20) -> dict
```
Pages by timeline index (default `limit` 20 rather than 100: each item recomputes the design twice
and a sketch description is large). Algorithm in § 7.1. Result:
`{"items": [<steps>], "total": <tl.count>, "offset": o, "next_offset": o+limit or null,
"marker": <restored marker>}`. `total`/`next_offset` count timeline items; `items` holds steps
(a sketch yields 2 steps, a group or coil base sketch 0; the `offset == 0` page also starts with
the § 7.6 parameter steps).

## 4. Write actions
All return `{"item": _item_json(...)}` unless stated, re-reading the item after the change.

### 4.1 set_marker
```python
@action("history", access="write", timeout=60)
def set_marker(ctx: Ctx,
               position: Literal["begin", "end", "next", "prev"] | int | TimelineItem) -> dict
```
| `position` | Fusion call |
|---|---|
| `"begin"` | `tl.moveToBeginning()` |
| `"end"` | `tl.moveToEnd()` |
| `"next"` | `tl.movetoNextStep()` (lower-case `t` is Fusion's spelling) |
| `"prev"` | `tl.moveToPreviousStep()` |
| `int n` | `0 <= n <= tl.count` else `INVALID_PARAMS`; `tl.markerPosition = n` |
| `TimelineItem` | `_with_collapsed(obj, lambda: obj.rollTo(True))` — marker just before the item |

Result `{"marker": tl.markerPosition, "count": tl.count}`. At the begin/end boundary `"prev"`/`"next"`
returning false leaves the marker unchanged and is not an error.

### 4.2 reorder
```python
@action("history", access="write", timeout=60)
def reorder(ctx: Ctx, item: ItemArg, before: ItemArg | Literal["end"],
            dry_run: bool = False) -> dict
```
`target = -1` for `"end"`, else the resolved `before` item's `index`. `ok =
obj.canReorder(target)`. `dry_run` → `{"can_reorder": ok, "moved": false, "item": ...}` with no
change. Not `dry_run` and `not ok` → `INVALID_PARAMS` `"cannot move <name> before <target>"` with
hint `"a feature cannot move before the items it depends on"`. Else
`old = obj.index`; `_with_collapsed(obj, lambda: obj.reorder(target))`; result `{"can_reorder": true,
"moved": obj.index != old, "item": ...}` (live: `canReorder` can be true for an extrude before its
sketch while `reorder` leaves everything in place, so `moved` is read from the re-read index). `item` equal to `before` → `INVALID_PARAMS`.

### 4.3 set_suppressed
```python
@action("history", access="write", timeout=60)
def set_suppressed(ctx: Ctx, item: ItemArg, suppressed: bool) -> dict
```
Groups are refused (`INVALID_PARAMS`). `_with_collapsed(obj, lambda: setattr(obj, "isSuppressed",
suppressed))`.

### 4.4 rename
```python
@action("history", access="write")
def rename(ctx: Ctx, item: ItemArg, name: str) -> dict
```
`name.strip()` empty → `INVALID_PARAMS`. `_with_collapsed(obj, lambda: setattr(obj, "name", name))`
(`TimelineObject.name` is settable and renames the entity in the browser).

### 4.5 group_add
```python
@action("history", access="write")
def group_add(ctx: Ctx, start: ItemArg, end: ItemArg, name: str | None = None) -> dict
```
`start.index > end.index` → `INVALID_PARAMS`. `g = tl.timelineGroups.add(start.index, end.index)`;
`None` → `FUSION_ERROR` `"range cannot be grouped"`. When `name`, `g.name = name`. Result
`{"group": _item_json(ctx, g), "members": [indices of the items inside g]}` (read with `g.count`/`g.item(i)`: a collapsed group hides its items from `tl.item`).

### 4.6 snapshot_add
```python
@action("history", access="write")
def snapshot_add(ctx: Ctx, name: str | None = None) -> dict
```
`not snaps.hasPendingSnapshot` → `INVALID_PARAMS` `"no pending position changes to capture"`.
`s = snaps.add()`; when `name`, `s.name = name`. Result `{"snapshot": _snapshot_json(i, s)}` with
`i = snaps.count - 1`.

### 4.7 snapshot_rename
```python
@action("history", access="write")
def snapshot_rename(ctx: Ctx, snapshot: int | str, name: str) -> dict
```
`s = _snapshot(snaps, snapshot)`; `s.name = name`; result `{"snapshot": _snapshot_json(...)}`.

### 4.8 undo
```python
@action("history", access="write", timeout=60, undo_group=False)
def undo(ctx: Ctx) -> dict
```
Works in parametric and direct designs.
1. `design = ctx.require_design()`; `before = design.designType`.
2. `ctx.app.executeTextCommand("Transaction.Undo")` (live: `UndoCommand.execute()` and
   `Commands.Start UndoCommand` do nothing from the API; the Transaction text command undoes the last
   committed transaction synchronously).
3. (no event pumping is needed.)
4. `design = ctx.app.activeDocument.products.itemByProductType("DesignProductType")` (re-read as
   core § 5.9 defines `ctx.design`; the old object may be stale); `after = design.designType`.
5. If `before == ParametricDesignType` and `after == DirectDesignType`:
   `ctx.app.executeTextCommand("Transaction.Redo")`; re-read the
   design type and fail `WRONG_DESIGN_TYPE` with message
   `"undo would convert the design to direct modelling and drop the timeline; it was redone"`
   (or `"...; redo did not restore parametric mode"` when the re-read is still direct) and hint
   `"remove the feature with history.set_suppressed or a delete action instead"`.
6. Result `{"undone": true, "design_type": "parametric"|"direct"}`.
Each undo-grouped De-Fusion call (core § 5.7) is one undo step.

## 5. Destructive actions
Each takes the framework's `confirm` (core § 5.3).

### 5.1 group_delete
```python
@action("history", access="destructive", timeout=60)
def group_delete(ctx: Ctx, group: ItemArg, delete_contents: bool = False) -> dict
```
Item not `isGroup` → `INVALID_PARAMS`. `g = adsk.fusion.TimelineGroup.cast(obj)`; record name and
member names; `g.deleteMe(delete_contents)`. Result `{"deleted": <name>, "contents_deleted":
delete_contents, "members": [names]}`.

### 5.2 delete_after_marker
```python
@action("history", access="destructive", timeout=60)
def delete_after_marker(ctx: Ctx) -> dict
```
`names = [tl.item(i).name for i in range(tl.markerPosition, tl.count)]`; `tl.deleteAllAfterMarker()`.
Result `{"deleted": names, "count": tl.count}`. Nothing after the marker → `{"deleted": [], ...}`
without calling Fusion.

### 5.3 snapshot_revert_pending
```python
@action("history", access="destructive")
def snapshot_revert_pending(ctx: Ctx) -> dict
```
`not snaps.hasPendingSnapshot` → `{"reverted": false}`; else `snaps.revertPendingSnapshot()`;
`{"reverted": true}`.

### 5.4 snapshot_delete
```python
@action("history", access="destructive")
def snapshot_delete(ctx: Ctx, snapshot: int | str) -> dict
```
`s = _snapshot(...)`; record name; `s.deleteMe()`; `{"deleted": <name>, "count": snaps.count}`.

## 6. Recipe format
A recipe is the ordered list of steps that `dump_recipe` pages out. A **step** is a De-Fusion action
call plus replay metadata:
```json
{"tool": "model", "action": "extrude",
 "params": {"profile": {"sketch": {"name": "Sketch1"}, "index": 0},
            "distance": "10 mm", "operation": "new_body"},
 "source": {"index": 1, "name": "Extrude1", "type": "ExtrudeFeature"},
 "bodies": ["Body1"]}
```
- `tool`, `action`, `params` — exactly what a client would send to that tool; `params` uses the
  live action's parameter names and accepted value forms (core § 5.4).
- `source` — the dumped timeline item. Replay renames the created entity to `source.name`.
- `bodies` — names of bodies the item created, in component body order (omitted when none).
- `suppressed: true` — present only when the dumped item was suppressed.
- `stale: true` and `current: {...}` — coil only (§ 7.4).
- User parameter steps (§ 7.6) are `model.parameter_create` calls with no metadata keys.
- Unknown item: `{"kind": "unknown", "type": "<class>", "name": "<name>", "parameters":
  {"<model parameter name>": "<expression>"}, "reason": "<why not mapped>", "source": {...}}`.

**References between steps are names**: bodies `{"name": b}`, sketches `{"name": s}`, features
`{"name": f}`, construction planes/axes `{"name": p}`, origin planes `"XY"|"XZ"|"YZ"`, origin axes
`"X"|"Y"|"Z"`, profiles `{"sketch": {"name": s}, "index": i}`, faces/edges
`{"body": {"name": b}, "index": i}`. Because replay restores every dumped name, each reference
resolves in the replay document.

**Values.** Where the feature exposes a `ModelParameter` `p`, the value is `p.expression` (keeps
units and user-parameter references, e.g. `"10 mm"`, `"wall * 2"`), via the § 7.3 helpers. When
`p.dependencyParameters` (attribute present) contains a `ModelParameter` — whose auto-generated
name differs in the replay document — or the attribute is absent, the value is the fixed current
value instead. Fixed values (that fallback and values with no `ModelParameter`, e.g. a hole point
or a move translation) are strings with an explicit unit in the call's output units, formatted
`f"{v:.10g} {unit}"` (`"12.5 mm"`, `"90 deg"`); fixed counts are integers. User parameters are
recreated by the § 7.6 steps at the start of the recipe, so referenced names resolve on replay.

**Binding to the sketch and model sets.** Action names and parameter names in § 7 are those of
`docs/specs/sketch/design.md` and `docs/specs/model/design.md`. Where those sets name an action or
parameter differently, theirs win and this section and the code follow them; the TASK-HIS-008 unit
test validates every emitted step against the registered action's pydantic model, so a mismatch
fails the build.

## 7. dump_recipe
### 7.1 Algorithm
```
tl = _timeline(ctx); saved = tl.markerPosition
page = ctx.page(list(range(tl.count)), offset, limit)
steps = _parameter_steps(design) if offset == 0 else []        # § 7.6
try:
    for i in page["items"]:
        obj = tl.item(i)
        if obj.isGroup or _is_coil_base_sketch(ctx, tl, i): continue   # § 7.4
        try:
            _with_collapsed(obj, lambda: obj.rollTo(True))      # before: inputs are live
            before_tokens = {b.entityToken for b in _all_bodies(design)}
            inputs = _read_inputs(ctx, obj)                      # § 7.2 / 7.3 / 7.4
            _with_collapsed(obj, lambda: obj.rollTo(False))     # after: outputs exist
            new_bodies = [b.name for b in _all_bodies(design) if b.entityToken not in before_tokens]
            steps += _finish(ctx, obj, inputs, new_bodies)      # adds sketch.replay, source, bodies, suppressed
        except Exception as e:
            steps.append(_unknown(ctx, obj, reason=f"{type(e).__name__}: {e}"))
finally:
    restore_error = None
    try: tl.markerPosition = saved
    except Exception as e: restore_error = e
    if restore_error or tl.markerPosition != saved:
        raise FUSION_ERROR(f"marker restore failed: at {tl.markerPosition}, expected {saved}")
page["items"] = steps; page["marker"] = tl.markerPosition
```
- `_all_bodies(design)`: `bRepBodies` of every component in `design.allComponents`, component order
  then body order.
- Inputs are read with the marker before the item because feature inputs (edges, faces, profiles)
  refer to geometry that exists only then; edge and face indices are positions in
  `body.edges`/`body.faces` at that moment, matched by `entityToken`. Replay recreates the same
  geometry in the same order, so the indices match.
- A suppressed item is read the same way and gets `"suppressed": true`.

### 7.2 Sketches
For a `Sketch` entity two steps:
1. `{"tool": "sketch", "action": "create", "params": {"plane": <plane>}}` where `<plane>` from
   `sketch.referencePlane`: the component's `xYConstructionPlane`/`xZConstructionPlane`/
   `yZConstructionPlane` → `"XY"`/`"XZ"`/`"YZ"`; a `ConstructionPlane` → `{"name": p.name}`; a
   `BRepFace` → face ref (§ 6). Read before the item.
2. `{"tool": "sketch", "action": "replay", "params": {"sketch": {"name": <sketch name>},
   "description": describe(ctx, sketch)}}`, `describe` being `defusion.tools.sketch.describe`
   (sketch design § 3.2, the body of `sketch.info` with `section="all"`), called after the item
   (marker after the sketch) with the dump's `ctx`.
The rename to `source.name` happens on the create step (§ 8.3), so the replay step's reference
resolves.

### 7.3 Features
A row applies only when every listed condition holds; otherwise the item is `unknown` with the
failed condition as `reason`. `op` = `feature.operation` mapped back through core's `Operation`
literal mapping (`types.py`). For a `ModelParameter` `p`: `L(p)`, `A(p)` and `N(p)` return
`p.expression`, or under the § 6 fixed-value fallback `ctx.length_out(p.value)` /
`ctx.angle_out(p.value)` formatted per § 6 / `int(round(p.value))` respectively. Plane forms are
those of core `Plane` (§ 6 names), axis forms those of core `Axis`.

| Fusion entity | Conditions | Step | `params` |
|---|---|---|---|
| `ExtrudeFeature` | `extentOne` is `DistanceExtentDefinition`, `extentTwo` is `None`, `taperAngleOne` value 0 | `model.extrude` | `profile` (§ 6 from `feature.profile`; a collection → `index` list), `distance` = `L(extentOne.distance)`, `operation` = op |
| `RevolveFeature` | `extentDefinition` is `AngleExtentDefinition` | `model.revolve` | `profile`, `axis` (origin axis, construction axis name or edge ref from `feature.axis`), `angle` = `A(extentDefinition.angle)`, `extent` = `"symmetric"` when `extentDefinition.isSymmetric` (omitted otherwise), `operation` |
| `FilletFeature` | exactly one edge set, a `ConstantRadiusFilletEdgeSet` | `model.fillet` | `edges` = edge refs of `edgeSet.edges`, `radius` = `L(edgeSet.radius)` |
| `ChamferFeature` | exactly one edge set, an `EqualDistanceChamferEdgeSet` | `model.chamfer` | `edges`, `distance` = `L(edgeSet.distance)` |
| `ShellFeature` | `inputEntities` are faces | `model.shell` | `faces` = face refs, `thickness` = `L(insideThickness)` |
| `RectangularPatternFeature` | `inputEntities` are bodies or features | `model.rect_pattern` | `entities` (name refs), `direction_one` (axis form of `directionOneEntity`), `quantity_one` = `N(quantityOne)`, `distance_one` = `L(distanceOne)`, `distance_type` = `"extent"` when `patternDistanceType` is `ExtentPatternDistanceType` else `"spacing"`, and when `directionTwoEntity` is set `direction_two` (its axis form), `quantity_two` = `N(quantityTwo)`, `distance_two` = `L(distanceTwo)` |
| `CircularPatternFeature` | same | `model.circ_pattern` | `entities`, `axis` (axis form of `feature.axis`), `quantity` = `N(quantity)`, `total_angle` = `A(totalAngle)`, `symmetric` = `isSymmetric` |
| `MirrorFeature` | `inputEntities` are bodies or features | `model.mirror` | `entities`, `plane` (origin plane, construction plane name or face ref of `mirrorPlane`) |
| `CombineFeature` | — | `model.combine` | `target` = `{"name": targetBody.name}`, `tools` = name refs of `toolBodies`, `operation` = op, `keep_tools` = `isKeepToolBodies` |
| `HoleFeature` | `holeType` is `SimpleHoleType`, `extentDefinition` is `DistanceExtentDefinition`, `hasattr(feature, "holePositionDefinition")` and it is a `PointHolePositionDefinition` | `model.hole` | `face` (face ref of `holePositionDefinition.planarEntity`), `point` (`[x,y,z]` fixed lengths of `holePositionDefinition.point`), `diameter` = `L(holeDiameter)`, `depth` = `L(extentDefinition.distance)`, `tip_angle` = `A(tipAngle)` |
| `ThreadFeature` | — | `model.thread` | `faces` = face refs of `inputEntities` (`[inputCylindricalFace]` when `inputEntities` is absent), `thread_type` = `threadInfo.threadType`, `designation` = `threadInfo.threadDesignation`, `thread_class` = `threadInfo.threadClass`, `internal` = `threadInfo.isInternal`, `modeled` = `isModeled`, `full_length` = `isFullLength`, `right_handed` = `threadInfo.isRightHanded` (when present); when not `isFullLength`: `length` = `L(threadLength)`, `offset` = `L(threadOffset)` |
| `ConstructionPlane` | definition `ConstructionPlaneOffsetDefinition` | `model.construction_plane` | `method` = `"offset"`, `plane` (plane form of `planarEntity`), `offset` = `L(offset)` |
| `ConstructionPlane` | definition `ConstructionPlaneAtAngleDefinition` | `model.construction_plane` | `method` = `"angle"`, `axis` (axis form of `linearEntity`), `plane` (`planarEntity`), `angle` = `A(angle)` |
| `ConstructionPlane` | definition `ConstructionPlaneMidplaneDefinition` | `model.construction_plane` | `method` = `"midplane"`, `plane` (`planarEntityOne`), `plane_two` (`planarEntityTwo`) |
| `ConstructionAxis` | definition `ConstructionAxisByLineDefinition`, `linearEntity` is a `BRepEdge` | `model.construction_axis` | `method` = `"edge"`, `edge` (edge ref of `linearEntity`) |
| `ConstructionAxis` | definition `ConstructionAxisTwoPlaneDefinition` | `model.construction_axis` | `method` = `"two_planes"`, `plane` (`planarEntityOne`), `plane_two` (`planarEntityTwo`) |
| `MoveFeature` | `hasattr(feature, "transform")`; the transform decomposes as below | `model.move` | `bodies` = name refs of `inputEntities` bodies; `translate`, `rotate_axis`, `angle` as below |
| `CoilFeature` | § 7.4 | `model.coil` | stored request params |

`MoveFeature` decomposition (`model.move` rotates first, then translates, model design § 4.16):
`a = feature.transform.asArray()`; `R` = the 3×3 rotation part (entries 0–2, 4–6, 8–10), `t` =
entries 3, 7, 11 (cm). `t` non-zero (any `|t_k| > 1e-9`) → `translate` = `[x, y, z]` fixed lengths
of `t`. `R` the identity within 1e-9 → no rotation keys. Otherwise `θ = acos(clamp((trace(R) −
1) / 2, −1, 1))` and the unit axis `u = (R21 − R12, R02 − R20, R10 − R01) / (2 sin θ)` (for `θ`
within 1e-6 of π: the normalised column of `R + I` with the largest norm); `u` within 1e-9 of
±X/±Y/±Z → `rotate_axis` = `"X"`/`"Y"`/`"Z"` and `angle` = fixed angle of `θ` (negated for a
negative axis); any other axis → `unknown` with reason `"rotation axis is not an origin axis"`.

Any entity that has no name-based reference form (sketch curves, vertices, entities in another
component's occurrence context) makes the item `unknown` with reason `"unnamed reference: <type>"`.
Profile index: position in `profile.parentSketch.profiles` of the profile whose
`areaProperties().area` and centroid match within 1e-9 cm² / 1e-7 cm.

### 7.4 Coil
`req = ctx.attr_get(coil, "request")` (core § 5.9 returns the parsed value or `None`; attribute
written by `model.coil`, format model design § 5.6:
`{"format": "defusion.request/1", "tool": "model", "action": "coil", "params": {...}}`). `req` not a
dict, `req.get("format") != "defusion.request/1"`, `tool`/`action` not `model`/`coil`, or `params`
not a dict → `unknown` with reason `"coil has no stored request"`. Else the step is
`{"tool": "model", "action": "coil", "params": P, ...}` where `P` is `req["params"]` verbatim except
that each Ref dict in it (a dict with a `token` key: `plane`, `participant_bodies` items,
`component`) is reduced to `{"name": r["name"]}` plus `"component": r["component"]` when not null,
because tokens do not exist in the replay document (§ 6 name references).

Base sketch: `model.coil` creates its own base sketch named `f"{name or 'Coil'} base"` (model design
§ 5.4), so `_is_coil_base_sketch(ctx, tl, i)` is true when item `i` is a `Sketch`, item `i + 1`
exists and is a `CoilFeature` with a valid stored request, and the sketch's name equals
`f"{req['params']['name'] or 'Coil'} base"`; that sketch yields no steps (replaying it would
duplicate the sketch the coil step creates). It never raises (any exception → false).

Staleness: the coil's model parameters are the set `model.parameter_list(created_by=<coil>)`
returns (model design § 5.5, § 10.1), read directly: every `design.allParameters` item that is a
`ModelParameter` with `p.createdBy.entityToken == coil.entityToken`. Each is matched to a request
key by `p.role.lower()` containing: `diameter` → `diameter`, `revolution` → `revolutions`, `height`
→ `height`, `pitch` → `pitch`, `angle` → `taper_angle`, `section` → `section_size` (model design
§ 5.2 names). For each matched key whose value in `P` is not null, evaluate it with
`design.unitsManager.evaluateExpression(value, unit)` (`unit` `"cm"` for lengths, `"rad"` for
`taper_angle`, `""` for `revolutions`; the stored values carry units or user-parameter
expressions) and compare with `p.value`; a difference above `1e-6 * max(1, |p.value|)`, or an
evaluation that raises, makes the step `"stale": true` with `"current": {<key>: p.expression}` for
every matched key. TASK-HIS-011 runs a live probe that prints the coil's parameter roles and fixes
this table and the code if Fusion's role strings differ.

### 7.5 Unknown
`_unknown(ctx, obj, reason)`: `type` and `name` as § 2.2; `parameters` = `{p.name: p.expression}`
for the entity's model parameters (matched by `createdBy` as § 7.4; `{}` when none or unreadable);
`source` as § 6. Never raises.

### 7.6 User parameters
`_parameter_steps(design)`: one step per `design.userParameters` item `p`,
`{"tool": "model", "action": "parameter_create", "params": {"name": p.name, "expression":
p.expression, "unit": p.unit, "comment": p.comment}}` (model design § 10.2; `p.unit` is `""` for
unitless). Order: `userParameters` order, with each parameter moved after every user parameter in
its `p.dependencyParameters` (stable; repeat passes until no move). Emitted only on the page with
`offset == 0`, before the timeline steps; not counted in `total`.

## 8. apply_recipe_step
```python
@action("history", access="write", timeout=180)
def apply_recipe_step(ctx: Ctx, step: dict[str, Any]) -> dict
```
### 8.1 Checks
1. `step.get("kind") == "unknown"` → `INVALID_PARAMS` `"unknown step <type> <name> cannot be
   replayed"` with its `reason` as hint.
2. Required keys `tool` (str), `action` (str), `params` (object); allowed extra keys `source`,
   `bodies` (list of str), `suppressed` (bool), `stale` (bool), `current` (object); anything else →
   `INVALID_PARAMS` naming the key.
3. `tool` not `sketch` or `model`, or the target action's access is `destructive` →
   `INVALID_PARAMS` `"<tool>.<action> cannot be a recipe step"`.

### 8.2 Running the step
`result = actions.call_inline(ctx, tool, action, params)` (core § 5.11): runs the target action's
validation, Ref and unit resolution and function inside this call, with no new dispatch, undo group
or change summary; `"tool.action"` not in `ctx.allowed` (core § 5.9) → `NOT_ALLOWED`. The outer
`apply_recipe_step` call's undo group and change summary cover the step, and an error aborts the
whole step (core § 5.7). Errors from the inner call propagate unchanged.

### 8.3 Restoring names
Before the call record `n0 = tl.count` (when parametric) and body tokens; after:
1. When `source.name` is given and exactly one timeline item was added, set its entity's `name`
   (`timelineObject.name` when the entity has no settable name) to `source.name`.
2. When `bodies` is given, new bodies (token difference, § 7.1 order) are renamed to `bodies[k]`
   pairwise; a length mismatch adds `"warnings": ["created <n> bodies, recipe lists <m>"]`.
3. When `suppressed` is true, the added item's `isSuppressed = True`.
Result: `{"step": {"tool", "action"}, "result": <inner result>, "renamed": [<Refs>],
"stale": true?, "warnings": [...]?}`. A stale step is still applied as stored (the request is the
source of truth); `stale` is echoed so the caller can follow up with `current`.

## 9. Testability
### 9.1 Unit (fake adsk)
`tests/test_history.py` with `tests/fakes/adsk` extended by a fake `Timeline` (items, marker,
move methods, `timelineGroups`, `deleteAllAfterMarker`), `TimelineObject`/`TimelineGroup`,
`Snapshots`/`Snapshot`, feature fakes for every § 7.3 row plus `CoilFeature` with `ModelParameter`
(`role`, `value`, `expression`, `createdBy`) and attributes, and scripted
`Transaction.Undo`/`Transaction.Redo` text commands (fake `executeTextCommand`) that mutate the fake design. Tests cover each action's
result shape and errors, `_with_collapsed`, direct-design `WRONG_DESIGN_TYPE`, `UNSUPPORTED`
detection of absent API members, the undo guard, marker restore after a raising read, every step validating against the
registered action models, `L`/`A`/`N` emitting `p.expression` (a user-parameter reference kept, a
model-parameter reference falling back to the fixed value), user parameter steps and their
dependency order, the `MoveFeature` decomposition (translation only, origin-axis rotation, other
axis → unknown), coil verbatim params with token-free Refs, coil base sketch skipped, coil stale,
unknown fallbacks, a marker setter that ignores the restore (`FUSION_ERROR` naming both positions),
a 21-item timeline with large sketches whose default `dump_recipe` page is under 64 KiB with
`next_offset` set (not `RESULT_TOO_LARGE`), and `apply_recipe_step` checks, renames, refusal of
`document.export` and `cam.post_process` steps (no call recorded) and profile refusal (`NOT_ALLOWED`
from core `call_inline`).

### 9.2 Live (`LIVE_SCENARIOS` in `history.py`)
`LIVE_SCENARIOS` uses the core § 10 tuple format `(name, "history.<action>", async fn(session))`;
calls go through `scripts/live_check.call(session, tool, action, params)`. Run with
`python scripts/live_check.py --profile full --only history[.<action>]` (`full` because the
snapshot scenarios use `script.execute_python` and the recipe scenarios use destructive
`document.close {"documents": [<id>], "confirm": true}`, document design § 2.7). Each scenario builds its own geometry in the scratch design. Helpers:
- `box`: `live_check.box(session, w, d, h)` (core design § 10); returns the body ref.
- `_pose(session)`: two `box`es, each moved into its own component with
  `assembly.create_component {"bodies": [<body ref>]}`; then `script.execute_python {"code":
  <sets occurrence 2's transform2 to a 10 mm X translation>, "confirm": true}` so a position change
  is pending.

| Scenario | Tag | Steps and checks |
|---|---|---|
| `list` | `history.list` | `box` + second `sketch.create`; 3 items `Sketch`, `ExtrudeFeature`, `Sketch`, marker 3; `limit: 1` → `next_offset` 1 |
| `set_marker` | `history.set_marker` | `box`; all six position forms (REQ-HIS-003 values); out-of-range → `INVALID_PARAMS` |
| `reorder` | `history.reorder` | two `sketch.create` + `box`; dry-run and real swap of the two sketches; the extrude's sketch moved to `"end"` → `can_reorder: false` / `INVALID_PARAMS` (Fusion reports `true` for the extrude before its sketch but leaves it in place) |
| `set_suppressed` | `history.set_suppressed` | `box`; suppress → `changes.bodies` `[1, 0]`; unsuppress → `[0, 1]` |
| `rename` | `history.rename` | `box`; rename item 1 to `Base`; `list` shows it |
| `group_add` | `history.group_add` | `box`; group 0–1 named `G`; `start` > `end` → `INVALID_PARAMS` |
| `group_delete` | `history.group_delete` | `box`, group; no `confirm` → `CONFIRM_REQUIRED`; keep contents; regroup, `delete_contents: true` |
| `delete_after_marker` | `history.delete_after_marker` | `box` + sketch; marker 1; 2 names deleted, total 1 |
| `snapshot_list` | `history.snapshot_list` | `_pose`; `pending` true |
| `snapshot_add` | `history.snapshot_add` | `_pose`; add `Pose A`; `pending` false; add again → `INVALID_PARAMS` |
| `snapshot_rename` | `history.snapshot_rename` | `_pose`, add `Pose A`; rename to `Pose B`; unknown name → `NOT_FOUND` |
| `snapshot_revert_pending` | `history.snapshot_revert_pending` | `_pose`; revert → `reverted: true`, occurrence transform restored; again → `reverted: false` |
| `snapshot_delete` | `history.snapshot_delete` | `_pose`, add `Pose A`; delete with `confirm`; total drops by 1 |
| `undo` | `history.undo` | `box`; `undo` → `undone: true`, `inspect.list_bodies` total 0 (`undo_group=False` calls report no `changes`) |
| `recipe_part` | `history.dump_recipe` | `model.parameter_create {"name": "wall", "expression": "20 mm"}`; `box(40, 20, "wall / 2")`; `model.fillet` on one edge; `model.construction_plane {"method": "offset", "plane": "XY", "offset": "wall / 4"}`; `model.coil {"plane": <plane ref>, "center": [20, 10], "diameter": "30 mm", "coil_type": "revolution_height", "revolutions": 3, "height": "4 mm", "operation": "join"}`. `dump_recipe` (all pages): first step `model.parameter_create` `wall`, the extrude step's `distance` is `"wall / 2"`, no unknown steps, no step for the coil base sketch. Record body count (`inspect.list_bodies {}` total), volume (`inspect.physical_properties {}` `items[0].volume`) and bbox (`inspect.bounds {}`); `document.create {}`; `apply_recipe_step` every step; equal body count, volume within 1e-6 relative, bbox within 1e-4 mm; `document.close {"confirm": true}` |
| `apply_recipe_step` | `history.apply_recipe_step` | `box`; `history.rename` the extrude item to `Base`; `model.rename` the body to `Plate`; dump (3 steps); `document.create {}`; apply the 3 steps → feature `Base`, body `Plate`; a step with a misspelt param → `INVALID_PARAMS` with `error.schema`; an `unknown` step → `INVALID_PARAMS`; `document.close {"confirm": true}` |
| `recipe_coil_revolution_pitch`, `recipe_coil_height_pitch`, `recipe_coil_spiral` | `history.dump_recipe` | one `model.coil` of that `coil_type` (`new_body`), dump, replay into a new document, same comparison as `recipe_part` |
| `coil_probe` | `history.dump_recipe` | one `model.coil`; prints each of its model parameters' `role` (TASK-HIS-011 confirms § 7.4) |
