# requirements.md — docs/specs/assembly/
> De-Fusion MCP — `assembly` tool: components, occurrences, joints, as-built joints, rigid groups and contact sets

## Overview
The `assembly` tool reads and builds the assembly structure of the active design: listing
components and the occurrence tree with world transforms, creating components (empty or from
existing bodies), creating joints and as-built joints of every Fusion joint type, creating rigid
groups, and reading and changing contact analysis and contact sets. All actions are
`@action("assembly", ...)` functions in `De-Fusion/defusion/tools/assembly.py`; schema,
validation, Ref resolution, units, dispatch, undo grouping, change summary, results, errors,
paging, help and profiles come from the core framework. Section references (core §) point to
`docs/specs/core/design.md`; (§) alone points to this set's design.md.

## Operator ask
2026-11-10: "comprehensive Fusion MCP server ... union of the four servers"

## Dependencies
- **DEP-ASM-001**: Core set `docs/specs/core/` (action framework, Ref types, units, results,
  errors, paging, undo grouping, fake `adsk` package, live check harness).
- **DEP-ASM-002**: The `sketch` and `model` tool sets (`docs/specs/sketch/`, `docs/specs/model/`)
  provide `sketch.create`, `sketch.rectangle` and `model.extrude`, which the live scenarios use to
  build fixture boxes; the `script` set provides `script.execute_python`, which the `joint` live
  scenario uses to add a joint origin.
  Only the live verification task (TASK-ASM-007) waits on them.

## Functional Requirements
- **REQ-ASM-001**: The assembly tool shall provide `list_components` (read), returning every
  component of the active design, paged, each with its Ref, part number, whether it is the root,
  and counts of bodies, sketches, occurrences, joints and as-built joints (§ 3.1).
  - _Acceptance_: In a design with a root and two new components, `list_components` returns
    `total` 3, exactly one item has `is_root: true`, and each new component reports
    `occurrences` 1; with `limit` 1 the result has one item and `next_offset` 1.
- **REQ-ASM-002**: The assembly tool shall provide `list_occurrences` (read), returning the
  occurrence tree under a component (default root), recursive by default, paged, each with its Ref,
  full path, parent path, depth, component Ref, grounded/visible flags and world transform with
  the origin in the call's output length unit (§ 3.2).
  - _Acceptance_: With an occurrence nested inside another, `list_occurrences` returns both with
    depths 1 and 2 and the child's `parent` equal to the parent's `path`; `recursive: false`
    returns only depth 1; an occurrence moved 25.4 mm along X reports `origin` `[1, 0, 0]` when
    called with `units {"length": "in"}`.
- **REQ-ASM-003**: The assembly tool shall provide `contact_get` (read), returning the design's
  contact analysis mode and every contact set with its name, suppressed state and member Refs;
  on a Fusion build without the `contactSets` API it shall return `contact_sets: null` (§ 3.3).
  - _Acceptance_: In a new design `contact_get` returns `analysis` `"off"` and `contact_sets` `[]`
    (or `null` when unsupported); after `contact_set` creates a set, `contact_get` lists it by name
    with its two members.
- **REQ-ASM-004**: The assembly tool shall provide `create_component` (write), creating a new
  component with one occurrence under a parent occurrence (default root), named as given, and,
  when bodies are given, moving those bodies into it (§ 3.4).
  - _Acceptance_: `create_component {"name": "Bracket"}` returns component and occurrence Refs and
    `list_components` then shows `Bracket`; with `bodies` of two root bodies, both bodies are in
    `Bracket` afterwards and the root has two fewer bodies; an empty `bodies` list returns
    `INVALID_PARAMS`.
- **REQ-ASM-005**: Joint geometry inputs (`JointGeo`, § 3.5) shall accept exactly one of a face,
  edge, sketch curve, vertex, sketch point, joint origin (`JointOrigin`), occurrence origin or root
  origin, with a key point and
  an optional assembly-context occurrence, and shall resolve native entities to assembly-context
  proxies by the rule in § 3.5.
  - _Acceptance_: A `JointGeo` with two entity fields returns `INVALID_PARAMS`; a face of a
    component placed twice without `occurrence` returns `AMBIGUOUS` with both occurrence Refs in
    `candidates`; the same face with `occurrence` succeeds; a rigid `joint` with `geometry_one
    {"joint_origin": {"name": "JO1"}}` and `geometry_two {"root_origin": true}` succeeds;
    `as_built_joint` with `geometry.joint_origin` succeeds or returns `UNSUPPORTED`.
- **REQ-ASM-006**: The assembly tool shall provide `joint` (write), creating a joint of type
  `rigid`, `revolute`, `slider`, `cylindrical`, `pin_slot`, `planar` or `ball` between two
  `JointGeo` inputs, with optional offset, angle, flip, motion axes and name (§ 3.6).
  - _Acceptance_: For each of the seven types, `joint` between the origins of two new components
    returns a joint Ref whose reported `type` equals the requested type; `offset` `"10 mm"` moves
    the second occurrence's origin 10 mm along the joint Z; both geometries in the same occurrence
    returns `INVALID_PARAMS`.
- **REQ-ASM-007**: `joint` and `as_built_joint` shall accept optional motion limits (rotation
  min/max/rest as `Angle`, slide min/max/rest as `Length`) for the joint types that have them, and
  reject limits for other types (§ 3.7).
  - _Acceptance_: A revolute joint with `limits {"rotation_min": -45, "rotation_max": 90}` reads
    back minimum -45 and maximum 90 deg with both enabled; `slide_min` on a revolute joint returns
    `INVALID_PARAMS`.
- **REQ-ASM-008**: The assembly tool shall provide `as_built_joint` (write), creating an as-built
  joint of any of the seven types between two occurrences in their current positions, with
  geometry required for every type except `rigid`, optional limits and name (§ 3.8).
  - _Acceptance_: `as_built_joint` rigid between two occurrences returns a Ref and neither
    occurrence's transform changes; `revolute` without `geometry` returns `INVALID_PARAMS`.
- **REQ-ASM-009**: The assembly tool shall provide `rigid_group` (write), creating a rigid group
  from two or more occurrences, with an include-children flag and name (§ 3.9).
  - _Acceptance_: `rigid_group` with two occurrences returns a Ref with `name` set; one occurrence
    returns `INVALID_PARAMS`.
- **REQ-ASM-010**: The assembly tool shall provide `contact_set` (write), setting the contact
  analysis mode (`off`, `all`, `contact_sets`), creating a named contact set from occurrences
  and/or bodies, and enabling or suppressing an existing contact set by name; on a Fusion build
  without the `contactSets` API, set creation and enabling shall return `UNSUPPORTED` (§ 3.10).
  - _Acceptance_: `contact_set {"analysis": "contact_sets", "create": {"name": "C1",
    "occurrences": [A, B]}}` returns the set Ref and `contact_get` shows analysis `contact_sets`
    and set `C1`; `{"set": "C1", "enabled": false}` makes `contact_get` report `suppressed: true`;
    an unknown set name returns `NOT_FOUND`; a call with no field set returns `INVALID_PARAMS`.
- **REQ-ASM-011**: The assembly tool module shall carry unit tests against the fake `adsk`
  package for every action and error path above, and `LIVE_SCENARIOS` covering every action
  against running Fusion (§ 4).
  - _Acceptance_: `pytest -q tests/test_assembly.py` passes; `python scripts/live_check.py --profile full --only
    assembly` prints PASS for every scenario in § 4.2 and exits 0.
