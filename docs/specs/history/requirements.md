# requirements.md — docs/specs/history/
> De-Fusion MCP — `history` tool: timeline listing, marker, reorder, suppress, rename, groups, position snapshots, guarded undo, and timeline recipes (dump and replay)

## Overview
The `history` tool reads and edits the parametric timeline of the active design, manages assembly
position snapshots, undoes the last operation with a guard against losing the timeline, and turns a
timeline into a replayable recipe whose steps are ordinary De-Fusion action calls. All actions are
`@action("history", ...)` functions in `De-Fusion/defusion/tools/history.py`; schema, validation,
Ref resolution, units, dispatch, undo grouping, change summary, results, errors, paging, help and
profiles come from the core framework (`docs/specs/core/design.md`, cited as "core §"). Design
details are in `docs/specs/history/design.md` (cited as "design §").

## Operator ask
2026-11-10: "comprehensive Fusion MCP server ... union of the four servers"

## Dependencies
- **DEP-HIS-001**: The core set (`docs/specs/core/`) provides the action framework, `Ctx`, paging,
  errors and the live check harness. Satisfied by core: `TimelineItem` resolution and
  `int | TimelineItem` unions (core § 5.1, § 5.4), `ctx.allowed`, `ctx.units` and
  `ctx.attr_get` returning the parsed value (core § 5.9), `actions.call_inline` (core § 5.11) and
  the `LIVE_SCENARIOS` tuple format (core § 10).
- **DEP-HIS-002**: The sketch set (`docs/specs/sketch/`) provides the importable describe function
  behind `sketch.info` and the `sketch.create` / `sketch.replay` actions (REQ-SK-003, REQ-SK-018).
- **DEP-HIS-003**: The model set (`docs/specs/model/`) provides the `model.*` feature actions that
  recipe steps call (including `model.parameter_create`), and `model.coil`, which stores its request
  on the coil feature in attribute group `defusion`, key `request` (model design § 5.6).
- **DEP-HIS-004**: The document, inspect, assembly and script sets provide `document.create`,
  `document.close`, `inspect.list_bodies`, `inspect.physical_properties`, `inspect.bounds`,
  `assembly.create_component` and `script.execute_python`, used by the live scenarios.
- **DEP-HIS-005**: Core `Axis` (core § 5.4, REQ-CORE-057) accepts `{"sketch": <Ref>, "line": n}` for a
  `SketchLine` (`n` = index in that sketch's `sketchCurves.sketchLines`), so every `Axis` parameter
  (`model.revolve.axis`, `circ_pattern.axis`, `rect_pattern.direction_*`, `move.rotate_axis`,
  `construction_plane.axis`) takes it. Owned by core (TASK-CORE-032).
- **DEP-HIS-006**: Core `Profile` (core § 5.4, REQ-CORE-058) accepts a list of `{"sketch", "index"}`
  entries (profiles from several sketches, resolved to one `ObjectCollection`). Owned by core
  (TASK-CORE-032).
- **DEP-HIS-007**: The inspect set provides the importable `profile_index(profile)` (REQ-INS-017;
  inspect design § 2.2), which `dump_recipe` uses for profile recovery.

## Functional Requirements

### Reading the timeline
- **REQ-HIS-001**: The history tool shall provide `list` (read, paged) returning, for each timeline
  item, its index, name, entity type, entity Ref, suppressed and rolled-back flags, health state and
  message, group membership and whether it is a group, plus the marker position (design § 3.1).
  - _Acceptance_: In a parametric design with a sketch and an extrude, `list` returns 2 items with
    indices 0 and 1, types `Sketch` and `ExtrudeFeature`, `health` `healthy`, `group` null, and
    `marker` 2; `limit: 1` returns 1 item with `next_offset` 1.
- **REQ-HIS-002**: The history tool shall provide `snapshot_list` (read, paged) returning each
  position snapshot's index, name and timeline index, and whether uncaptured position changes are
  pending (design § 3.2).
  - _Acceptance_: In an assembly with one moved occurrence and one captured snapshot,
    `snapshot_list` returns 1 item with a name and a timeline index, and `pending` false.

### Editing the timeline
- **REQ-HIS-003**: The history tool shall provide `set_marker` (write) moving the timeline marker to
  `"begin"`, `"end"`, `"next"`, `"prev"`, an integer marker position, or before a given timeline
  item, and returning the new marker position (design § 4.1).
  - _Acceptance_: With 3 items, `"begin"` returns `marker` 0, `"next"` then returns 1, `"end"`
    returns 3, `2` returns 2, a Ref to item 1 returns 1; position `4` returns `INVALID_PARAMS`.
- **REQ-HIS-004**: The history tool shall provide `reorder` (write) moving an item to before another
  item or to the end, and with `dry_run: true` only reporting whether the move is possible
  (design § 4.2).
  - _Acceptance_: Two independent sketches swap places with `reorder`; `dry_run: true` returns
    `can_reorder` without changing `list`; moving an extrude before its own sketch returns
    `can_reorder: false` with `dry_run` and `INVALID_PARAMS` without it.
- **REQ-HIS-005**: The history tool shall provide `set_suppressed` (write) suppressing or
  unsuppressing a timeline item (design § 4.3).
  - _Acceptance_: Suppressing an extrude that made the only body changes `changes.bodies` to
    `[1, 0]` and `list` reports `suppressed: true`; unsuppressing restores 1 body.
- **REQ-HIS-006**: The history tool shall provide `rename` (write) renaming a timeline item
  (design § 4.4).
  - _Acceptance_: Renaming item 1 to `Base` makes `list` report name `Base` for index 1.
- **REQ-HIS-007**: The history tool shall provide `group_add` (write) grouping a contiguous range of
  timeline items, with an optional group name (design § 4.5).
  - _Acceptance_: `group_add` over items 0–1 with name `G` returns a group item named `G`; `list`
    shows items 0 and 1 with `group` equal to the group's index; `start` after `end` returns
    `INVALID_PARAMS`.
- **REQ-HIS-008**: The history tool shall provide `group_delete` (destructive) removing a timeline
  group, keeping its contents unless `delete_contents` is true (design § 5.1).
  - _Acceptance_: Without `confirm` it returns `CONFIRM_REQUIRED`; with `confirm` and default
    options the group disappears and its 2 items remain; with `delete_contents: true` the items are
    removed too; a non-group item returns `INVALID_PARAMS`.
- **REQ-HIS-009**: The history tool shall provide `delete_after_marker` (destructive) deleting every
  timeline item after the marker and returning the deleted names (design § 5.2).
  - _Acceptance_: With 3 items and the marker at 1, it deletes 2 items, returns their names, and
    `list` total becomes 1.

### Position snapshots
- **REQ-HIS-010**: The history tool shall provide `snapshot_add` (write) capturing pending occurrence
  position changes as a snapshot with an optional name (design § 4.6).
  - _Acceptance_: After an occurrence move, `snapshot_add` with name `Pose A` returns a snapshot
    named `Pose A` and `snapshot_list` reports `pending` false; with nothing pending it returns
    `INVALID_PARAMS`.
- **REQ-HIS-011**: The history tool shall provide `snapshot_rename` (write) renaming a snapshot
  addressed by index or exact name (design § 4.7).
  - _Acceptance_: Renaming `Pose A` to `Pose B` makes `snapshot_list` report `Pose B`; an unknown
    name returns `NOT_FOUND`.
- **REQ-HIS-012**: The history tool shall provide `snapshot_revert_pending` (destructive) discarding
  uncaptured occurrence position changes (design § 5.3).
  - _Acceptance_: After an occurrence move, it returns `reverted: true` and the occurrence's
    transform equals its value before the move; with nothing pending it returns `reverted: false`.
- **REQ-HIS-013**: The history tool shall provide `snapshot_delete` (destructive) deleting a snapshot
  addressed by index or exact name (design § 5.4).
  - _Acceptance_: With `confirm`, deleting `Pose B` lowers `snapshot_list` total by 1.

### Undo
- **REQ-HIS-014**: The history tool shall provide `undo` (write) undoing the last operation (one whole
  De-Fusion call when that call was undo-grouped); when the undo would switch the design from
  parametric to direct, it shall redo and return `WRONG_DESIGN_TYPE` (design § 4.8).
  - _Acceptance_: After a `model.extrude` call, `undo` returns `undone: true` and `changes.bodies`
    `[1, 0]`; a unit test whose fake undo flips the design type asserts one redo and
    `WRONG_DESIGN_TYPE`.

### Recipes
- **REQ-HIS-015**: A recipe step shall be one De-Fusion action call
  `{"tool": ..., "action": ..., "params": {...}}` using the live action's parameter names, plus the
  replay metadata keys `source`, `bodies`, `suppressed`, `stale` and `current` only; a non-replayable
  item shall be `{"kind": "unknown", "type", "name", "parameters", "reason", "source"}`
  (design § 6).
  - _Acceptance_: A unit test validates every step that `dump_recipe` emits from the fake timeline
    (one per supported feature type) against the registered action's pydantic model with no errors.
- **REQ-HIS-016**: The history tool shall provide `dump_recipe` (read, paged by timeline index)
  mapping each sketch to a `sketch.create` step and a `sketch.replay` step and each supported
  feature (extrude, revolve, fillet, chamfer, shell, rectangular and circular pattern, mirror,
  combine, hole, thread, construction plane, construction axis, move) to its `model.*` step, with
  entity references expressed by name and values as the feature's `ModelParameter` expressions
  (fixed values with units where none exists or the expression references a model parameter),
  preceded on the first page by one `model.parameter_create` step per user parameter, and shall
  restore the marker afterwards (design § 6, § 7).
  - _Acceptance_: For sketch → extrude → fillet, `dump_recipe` returns 4 steps in order
    (`sketch.create`, `sketch.replay`, `model.extrude`, `model.fillet`), the extrude's profile is
    `{"sketch": {"name": "<sketch>"}, "index": 0}`, the fillet's edges are
    `{"body": {"name": "<body>"}, "index": n}`, and `list` reports the same marker before and after.
    A unit test with user parameter `wall` and an extrude whose distance expression is `wall / 2`
    returns a first step `model.parameter_create` for `wall` and the extrude `distance` `"wall / 2"`.
    A marker restore that fails or does not take effect returns `FUSION_ERROR` naming both positions.
    A 21-item timeline with large sketches at the default `limit` returns a page under 64 KiB with
    `next_offset` set.
- **REQ-HIS-017**: `dump_recipe` shall emit a coil carrying a stored `defusion.request/1` attribute
  as a `model.coil` step whose params are the request's `params` verbatim (Refs reduced to names),
  skip the coil's base sketch, and add `"stale": true` and the current expressions when the coil's
  model parameter values differ from the request (design § 7.4).
  - _Acceptance_: Unit tests: a fake coil whose parameters match its request yields a `model.coil`
    step whose params equal the request's `params` and no `stale` key, and its preceding base
    sketch yields no step; changing its diameter parameter yields `stale: true`.
- **REQ-HIS-018**: `dump_recipe` shall emit every other item (unsupported feature type or option,
  coil without a stored request, or any read failure) as an `unknown` step holding the entity type,
  name, raw model parameters and the reason, never failing the dump (design § 7.5).
  - _Acceptance_: A unit test with a fake `LoftFeature` and a fake extrude whose extent read raises
    returns 2 `unknown` steps with `parameters` and `reason`, and the call succeeds.
- **REQ-HIS-019**: The history tool shall provide `apply_recipe_step` (write) running one step through
  core `actions.call_inline` (core § 5.11: profile check against `ctx.allowed`, validation, Ref
  resolution, units, inside the outer call's undo group), refusing
  `unknown` steps and any step whose tool is not `sketch` or `model` or whose action is destructive
  (design § 8).
  - _Acceptance_: A `model.extrude` step creates the extrude; with a profile that excludes
    `model.extrude` it returns `NOT_ALLOWED`; a step with a misspelt param returns `INVALID_PARAMS`
    with `error.schema`; an `unknown` step, a `document.export` step and a `cam.post_process` step each return
    `INVALID_PARAMS` and run nothing.
- **REQ-HIS-020**: `apply_recipe_step` shall give the newly created timeline entity the step's
  `source.name`, the newly created bodies the step's `bodies` names in order, and suppress the new
  item when `suppressed` is true, so later steps resolve their name references (design § 8.3).
  - _Acceptance_: Applying a dumped `model.extrude` step whose source name is `Base` and bodies
    `["Plate"]` yields a feature named `Base` and a body named `Plate`.

- **REQ-HIS-023**: `dump_recipe` shall express a `SketchLine` axis (revolve axis, pattern
  direction, rotation or construction-plane axis) as `{"sketch": {"name": s}, "line": n}` instead
  of leaving the item unmapped, such that the replayed step uses the same line (design § 7.7;
  DEP-HIS-005).
  - _Acceptance_: A unit test with a revolve whose `axis` is the 2nd `SketchLine` of `Sketch1` yields
    a `model.revolve` step with `axis` `{"sketch": {"name": "Sketch1"}, "line": 1}`, valid against the
    registered `model.revolve` model; a `SketchLine` in a sketch in another component's occurrence
    context yields an `unknown` step `"unnamed reference: SketchLine"`. Live `recipe_axis_line`
    replays a revolve about a sketch line into a new document with equal body count, volume and
    bounding box.
- **REQ-HIS-024**: `dump_recipe` shall express an extrude or revolve `profile` as follows (design
  § 7.7): all profiles matched in one sketch keep the `{"sketch", "index"}` form (index list for
  several); profiles from several sketches become a list of `{"sketch", "index"}` entries, one per
  sketch in first-seen order; a profile that cannot be matched by area and centroid leaves the item
  unmapped (`unknown`, reason `"unnamed reference: Profile"`; `inspect.object_info` on the feature returns its
  loop curves, REQ-INS-017) (DEP-HIS-006, DEP-HIS-007).
  - _Acceptance_: Unit tests: an extrude over one profile in `Sketch1` and one in `Sketch2` yields
    `profile` `[{"sketch": {"name": "Sketch1"}, "index": 0}, {"sketch": {"name": "Sketch2"},
    "index": 1}]`, valid against the registered model; a fake profile with no area/centroid match
    yields an `unknown` step `"unnamed reference: Profile"`. Live `recipe_profiles` replays a two-sketch extrude
    into a new document with equal body count, volume and bounding box.

### Cross-cutting
- **REQ-HIS-021**: Every history action except `undo` and `apply_recipe_step` shall require a
  parametric design and return `WRONG_DESIGN_TYPE` in a direct design; every action shall return
  `UNSUPPORTED` when the Fusion API member it needs is absent (design § 2.4).
  - _Acceptance_: Unit tests call each of the 14 parametric actions on a fake direct design and get
    `WRONG_DESIGN_TYPE`; `group_add` on a fake timeline without `timelineGroups` returns
    `UNSUPPORTED`.
- **REQ-HIS-022**: The history module shall ship live scenarios covering every action, including a
  recipe round trip of a small part with a coil, one coil-only round trip per `coil_type`, and
  the sketch-line axis and multi-sketch profile round trips, each replayed into a new document with
  equal body count, volume and bounding box (design § 9.2).
  - _Acceptance_: `python scripts/live_check.py --profile full --only history` prints only `PASS`
    lines and exits 0.
