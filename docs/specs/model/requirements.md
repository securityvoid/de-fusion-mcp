# requirements.md — docs/specs/model/
> De-Fusion MCP — `model` tool: features, primitives, surfaces, sheet metal, construction geometry, parameters, appearance and design settings

## Overview
The `model` tool provides 63 actions: solid features (extrude through rename, including coil and
threads), primitives (cylinder, sphere, torus), surfaces, sheet metal (`sheet_*`), construction
geometry, parameters, materials and appearances, design settings, and destructive actions.
Framework behaviour (schema, validation, Refs, units, dispatch, undo, change summary, errors,
paging, help, profiles) is the core set's (`docs/specs/core/design.md` § 5); § references below
point to `docs/specs/model/design.md`.

## Operator ask
2026-11-10: "comprehensive Fusion MCP server ... union of the four servers"

## Dependencies
- **DEP-MDL-001**: The core set (`docs/specs/core/`): `@action`, types of core design § 5.4
  (including `Entity`, satisfied by core § 5.4), `Ctx` helpers (`text_command`,
  `attr_set`, `require_parametric`, `component`, `page`, unit outputs), `tests/fakes/adsk` and
  `scripts/live_check.py` with `LIVE_SCENARIOS` (core design § 10).
- **DEP-MDL-002**: The sketch set (`docs/specs/sketch/`) actions `create`, `circle`, `rectangle` and
  `line`, used only by live scenarios to build input geometry.

## Functional Requirements
- **REQ-MDL-001**: The model tool shall provide `extrude` (write) of a sketch profile with extents
  `distance`, `to_object`, `symmetric` and `two_sided`, taper angles, any `Operation` and optional
  participant bodies (§ 4.1).
  - _Acceptance_: live `extrude.*` scenarios each return a feature result with one or more bodies;
    `two_sided` without `distance_two` returns `INVALID_PARAMS`; participant bodies with `new_body`
    returns `INVALID_PARAMS`.
- **REQ-MDL-002**: The model tool shall provide `revolve` (write) about an `Axis` with `angle`,
  `symmetric` and `two_sided` extents (§ 4.2).
  - _Acceptance_: live `revolve` of a rectangle 360° about a sketch line returns one body.
- **REQ-MDL-003**: The model tool shall provide `sweep` (write) along a path of sketch curves or
  edges with optional guide rail, twist angle and orientation (§ 4.3).
  - _Acceptance_: live `sweep` and `sweep.guide_rail_twist` return bodies; a face in `path` returns
    `INVALID_PARAMS`.
- **REQ-MDL-004**: The model tool shall provide `loft` (write) through two or more profiles with
  optional rails or a centerline, solid/closed options (§ 4.4).
  - _Acceptance_: live `loft`, `loft.rails`, `loft.centerline` return bodies; one section or rails
    plus centerline return `INVALID_PARAMS`.
- **REQ-MDL-005**: The model tool shall provide `fillet` (write), constant radius on edges with
  tangent-chain control (§ 4.5).
  - _Acceptance_: live `fillet` on a box edge returns a feature result whose `bodies` holds the
    box; radius 0 returns `INVALID_PARAMS`.
- **REQ-MDL-006**: The model tool shall provide `chamfer` (write) of types equal distance, two
  distances and distance-angle (§ 4.6).
  - _Acceptance_: three live chamfer scenarios succeed; `two_distances` without `distance_two`
    returns `INVALID_PARAMS`.
- **REQ-MDL-007**: The model tool shall provide `shell` (write) by removed faces or whole bodies,
  inside/outside/both (§ 4.7).
  - _Acceptance_: live `shell` removing a box's top face returns a feature result with the bbox
    unchanged; giving both `faces` and `bodies` returns `INVALID_PARAMS`.
- **REQ-MDL-008**: The model tool shall provide `mirror` (write) of bodies or features across a
  plane (§ 4.8).
  - _Acceptance_: live `mirror.bodies` adds a body; `mirror.features` succeeds; a mixed list
    returns `INVALID_PARAMS`.
- **REQ-MDL-009**: The model tool shall provide `hole` (write) of types simple, counterbore and
  countersink, positioned at a point on a planar face or at sketch points, with depth or through
  all (§ 4.9).
  - _Acceptance_: four live hole scenarios succeed; both `depth` and `through_all` returns
    `INVALID_PARAMS`.
- **REQ-MDL-010**: The model tool shall provide `rect_pattern` and `circ_pattern` (write) of bodies,
  features or faces, capped at 500 instances (§ 4.10).
  - _Acceptance_: live `rect_pattern` 3×2 of a body yields 6 bodies in total; `circ_pattern.faces`
    succeeds; quantity 1 in direction one returns `INVALID_PARAMS`; a resolved total
    (`quantity_one * quantity_two`, or `circ_pattern` `quantity`) of 501 or more returns
    `INVALID_PARAMS` and 500 succeeds.
- **REQ-MDL-011**: The model tool shall provide `thread_query` and `list_threads` (read) and
  `thread` (write, modeled or cosmetic, designation defaulted from `ThreadDataQuery`) (§ 4.11).
  - _Acceptance_: `thread_query` with no params returns level `types` containing
    `ISO Metric profile`; live `thread.modeled` on a 10 mm cylinder without designation returns an
    `M10x1.5` designation; `list_threads` lists it.
- **REQ-MDL-012**: The model tool shall provide `coil` (write, timeout 60) creating a coil through
  the Coil text command on a base sketch circle in the target component, for every `coil_type`,
  `section` and `section_position`, with taper, rotation direction, operation and participant
  bodies, keeping the base sketch (§ 5.2, § 5.4, § 5.5).
  - _Acceptance_: live scenarios `coil.revolution_height`, `coil.revolution_pitch`,
    `coil.height_pitch`, `coil.spiral` and the four `coil.section_*` scenarios each add one body and
    return `feature`, `sketch` and non-empty `parameters`; `coil.join_participant` joins into the
    given body (body count unchanged).
- **REQ-MDL-013**: The model tool shall validate `coil` inputs before any change: parametric design
  only, required/forbidden size inputs per `coil_type`, positive values, `section_size <= pitch`
  where pitch applies, no taper for `spiral` (§ 5.3).
  - _Acceptance_: unit tests cover each rule; live `coil.invalid_section_size` returns
    `INVALID_PARAMS` with `changes` absent or unchanged; `coil.direct_rejected` returns
    `WRONG_DESIGN_TYPE`.
- **REQ-MDL-014**: The model tool shall, when coil creation fails at any step, terminate the active
  command, delete the base sketch and return an error (§ 5.4).
  - _Acceptance_: unit test with a fake that fails `CommitCmd` asserts `terminateActiveCommand` and
    `sketch.deleteMe` were called and the error code is `TEXT_COMMAND_FAILED`; a fake that commits
    without a new coil yields `FUSION_ERROR` "coil not created".
- **REQ-MDL-015**: The model tool shall store the full validated coil request as JSON in attribute
  group `defusion`, name `request`, on the CoilFeature, in the format of § 5.6, and document coil
  editing through `parameter_modify` on parameters created by the coil (§ 5.7).
  - _Acceptance_: unit test asserts `ctx.attr_set` receives the exact expected `req` dict for a fixed input;
    live `coil.request_attribute` reads it back with `find_attributes` and replays `params` through
    `model.coil` successfully; live `coil.edit_parameter` changes the coil via `parameter_modify`.
- **REQ-MDL-016**: The model tool shall provide `draft` (write) of faces from a pull plane with one
  or two angles (§ 4.12).
  - _Acceptance_: live `draft` on two box side faces succeeds.
- **REQ-MDL-017**: The model tool shall provide `split_body` and `split_face` (write) with a body,
  face, plane or sketch-curve tool (§ 4.13).
  - _Acceptance_: live `split_body` of a box by the XY plane through its middle yields two bodies;
    `split_face` succeeds.
- **REQ-MDL-018**: The model tool shall provide `offset_faces` (write) (§ 4.14).
  - _Acceptance_: live `offset_faces` +2 mm on a box top face grows its bbox height by 2 mm.
- **REQ-MDL-019**: The model tool shall provide `scale` (write), uniform or non-uniform, about a
  base point (§ 4.15).
  - _Acceptance_: live `scale` factor 2 doubles bbox size; non-uniform on a sketch returns
    `INVALID_PARAMS`.
- **REQ-MDL-020**: The model tool shall provide `move` (write) translating and/or rotating bodies as
  a Move feature (§ 4.16).
  - _Acceptance_: live `move.translate` by [10, 0, 0] shifts bbox x by 10 mm; `move.rotate` 90°
    about Z succeeds; neither input returns `INVALID_PARAMS`.
- **REQ-MDL-021**: The model tool shall provide `combine` (write) join/cut/intersect with keep-tools
  (§ 4.17).
  - _Acceptance_: live `combine` join of two overlapping boxes reduces body count by one; with
    `keep_tools` it does not.
- **REQ-MDL-022**: The model tool shall provide `rename` (write) for any named entity (§ 4.18).
  - _Acceptance_: live `rename` of a body, a feature and a sketch returns `previous` and the new
    `name`; an occurrence returns `INVALID_PARAMS`.
- **REQ-MDL-023**: The model tool shall provide `cylinder`, `sphere` and `torus` (write) built from
  a sketch plus extrude or revolve features (§ 6).
  - _Acceptance_: live `cylinder` d 20 h 10 has bbox 20×20×10 mm; `sphere` d 20 has bbox
    20×20×20; `torus` 40/10 on `XY` has bbox x×y×z 50×10×50; torus minor ≥ major returns
    `INVALID_PARAMS`.
- **REQ-MDL-024**: The model tool shall provide `surface_patch`, `surface_stitch`,
  `surface_thicken` and `surface_ruled` (write) (§ 7).
  - _Acceptance_: the four live surface scenarios succeed; stitching a solid body returns
    `INVALID_PARAMS`.
- **REQ-MDL-025**: The model tool shall provide `surface_trim` (write) removing selected cells,
  reporting cells in hints on a bad index (§ 7).
  - _Acceptance_: live `surface_trim` succeeds; index 99 returns `INVALID_PARAMS` with one hint per
    cell.
- **REQ-MDL-026**: The model tool shall provide `sheet_flange` (base and edge), `sheet_bend` and
  `sheet_unfold` (write, timeout 60), feature-detecting the partial sheet metal API and returning
  `UNSUPPORTED` naming the missing member (§ 8).
  - _Acceptance_: unit tests remove each member and get `UNSUPPORTED`; live `sheet_chain` passes;
    `sheet_flange.not_sheet_metal` returns `INVALID_PARAMS`.
- **REQ-MDL-027**: The model tool shall provide `sheet_flat_pattern` (write) (§ 8).
  - _Acceptance_: live `sheet_chain` returns a flat body ref and size; a second call returns
    `INVALID_PARAMS`.
- **REQ-MDL-028**: The model tool shall provide `construction_plane` (write, parametric) by offset,
  angle, midplane, three points, tangent and position along a path (§ 9).
  - _Acceptance_: live `construction_plane` creates one plane per method; a missing input returns
    `INVALID_PARAMS` naming it.
- **REQ-MDL-029**: The model tool shall provide `construction_axis` (write, parametric) by two
  planes, two points, edge, circular face and normal to face at point (§ 9).
  - _Acceptance_: live `construction_axis` creates one axis per method.
- **REQ-MDL-030**: The model tool shall provide `ucs` (write) through the user coordinate system API
  when present, else `UNSUPPORTED` (§ 9).
  - _Acceptance_: live `ucs` returns a UCS ref or `UNSUPPORTED`; unit test covers both.
- **REQ-MDL-031**: The model tool shall provide `parameter_list` (read) of user/model parameters,
  filterable by name pattern and by creating feature, paged (§ 10.1).
  - _Acceptance_: `parameter_list(created_by=<coil>)` returns exactly the coil's model parameters;
    `kind: "user"` returns only user parameters.
- **REQ-MDL-032**: The model tool shall provide `parameter_create` and batch `parameter_modify`
  (write); a batch with any invalid edit changes nothing (§ 10.2, § 10.3).
  - _Acceptance_: live `parameters` creates `w = 20 mm`, modifies `[w→30 mm, bad→1]` and gets
    `INVALID_PARAMS` with `w` still 20 mm, then modifies `[w→30 mm]` successfully.
- **REQ-MDL-033**: The model tool shall provide `parameter_delete` (destructive) for user
  parameters, reporting in-use failures (§ 10.4).
  - _Acceptance_: without `confirm` → `CONFIRM_REQUIRED`; deleting a referenced parameter lands in
    `failed`; deleting a model parameter returns `INVALID_PARAMS`.
- **REQ-MDL-034**: The model tool shall provide `material_list` (read) and `material_assign`
  (write) (§ 11).
  - _Acceptance_: `material_list(query="steel")` returns items with `library`; assigning
    `Steel` to a body succeeds.
- **REQ-MDL-035**: The model tool shall provide `appearance_set` (write) from the design or a
  library and `color_set` (write) with an RGBA override appearance (§ 11).
  - _Acceptance_: live `color_set("#FF0000")` twice creates the appearance
    `De-Fusion color FF0000FF` once; `#12` returns `INVALID_PARAMS`.
- **REQ-MDL-036**: The model tool shall provide `units_get` (read) and `units_set` (write) for the
  design display length unit (§ 12).
  - _Acceptance_: `units_set("in")` then `units_get` reports `display_length_units: "in"`.
- **REQ-MDL-037**: The model tool shall provide `config_inspect` (read) and
  `create_configured_design` (write), feature-detected (§ 12); it returns `FUSION_ERROR` on licence-restricted accounts.
  - _Acceptance_: live `config` converts the scratch design and lists one row, or both return
    `UNSUPPORTED`.
- **REQ-MDL-038**: The model tool shall provide `find_attributes` and `find_by_token` (read) (§ 12).
  - _Acceptance_: `find_by_token` with a body ref's token returns that body; `find_attributes`
    finds the coil `request` attribute.
- **REQ-MDL-039**: The model tool shall provide `visibility_get` (read) and `visibility_set`
  (write) for entities and design visibility flags (§ 12, § 12.1).
  - _Acceptance_: hiding a body then `visibility_get` reports `light_bulb: false`; an unknown flag
    returns `INVALID_PARAMS` listing valid flags.
- **REQ-MDL-040**: The model tool shall provide `selection_sets_list` (read) and
  `selection_set_create` (write) (§ 12).
  - _Acceptance_: creating set `S` of two bodies then listing shows `S` with count 2.
- **REQ-MDL-041**: The model tool shall provide `set_design_type` (destructive: switching to direct
  discards the timeline) and `set_design_intent` (write, feature-detected) (§ 12).
  - _Acceptance_: `set_design_type` without `confirm: true` returns `CONFIRM_REQUIRED`; with
    `confirm: true`, switching to direct and back reports `changed: true` each time;
    `set_design_intent` returns a result or `UNSUPPORTED`.
- **REQ-MDL-042**: The model tool shall provide `activate_root`, `compute_all` (timeout 300) and
  `set_modeling_in_assembly` (write, feature-detected) (§ 12).
  - _Acceptance_: live scenarios for each succeed (`set_modeling_in_assembly` may return
    `UNSUPPORTED`); `compute_all` lists unhealthy timeline items only.
- **REQ-MDL-043**: The model tool shall provide `remove_body` and `delete_entities` (destructive)
  (§ 13).
  - _Acceptance_: `remove_body` in parametric mode returns a Remove feature and body count drops by
    one; `delete_entities` of a sketch and a construction plane lists both in `deleted`.
- **REQ-MDL-044**: The model tool shall return the feature result (§ 3.2) from every
  feature-creating action, apply `name` to the created feature, and create the feature in the
  component owning its inputs (or `component` where the action takes one).
  - _Acceptance_: unit tests assert result shape and naming per action; live `cylinder` with
    `component` set to a sub-component creates the body in that component.
- **REQ-MDL-045**: The model tool shall register exactly the 63 actions of design.md with the access
  classes and timeouts stated there, sheet metal actions named `sheet_*`, and no `box` or
  `delete_all` action.
  - _Acceptance_: `pytest -q tests/test_model_registration.py` passes.
- **REQ-MDL-046**: The model tool shall feature-detect every API member marked optional or preview
  in design.md and return `UNSUPPORTED` naming the member instead of `FUSION_ERROR`.
  - _Acceptance_: unit tests delete each detected member from the fakes and assert `UNSUPPORTED`.
- **REQ-MDL-047**: The model tool shall expose `LIVE_SCENARIOS` covering every action, one scenario
  per `coil_type` and per coil section type, run by `scripts/live_check.py --only model`.
  - _Acceptance_: `python scripts/live_check.py --only model` prints PASS for every scenario of
    design § 14.2 and exits 0, except scenario `config`: on a licence-restricted account Fusion
    refuses `create_configured_design` with FUSION_ERROR and that scenario is excluded.
