# requirements.md — docs/specs/inspect/
> De-Fusion MCP — `inspect` tool: design summary, entity info, topology listing, geometry, bounds, measurement, mass properties, interference, mesh comparison, selection and section analysis

## Overview
The `inspect` tool answers questions about the active design without changing it (except
`section_analysis`, which adds a section analysis to the browser). All actions are
`@action("inspect", ...)` functions in `De-Fusion/defusion/tools/inspect.py`; schema, validation,
Ref resolution, units, dispatch, results, errors, paging, help and profiles come from the core
framework. Section references (§) without a prefix point to `docs/specs/inspect/design.md`; "core
§" points to `docs/specs/core/design.md`.

## Operator ask
2026-11-10: "comprehensive Fusion MCP server ... union of the four servers"

## Dependencies
- **DEP-INS-001**: Core set `docs/specs/core/` (action framework, Ref types, units, results,
  errors, paging, live check harness).
- **DEP-INS-002**: Core type `Entity` (satisfied by core § 5.4: any entity incl. `MeshBody`, no
  class check). Used by `object_info`, `geometry`,
  `bounds`, `measure_*`, `physical_properties`, `interference` and `compare_meshes`.
- **DEP-INS-003**: Live scenarios build geometry with `sketch.create`, `sketch.rectangle`,
  `model.extrude`, `document.export` and `document.import_mesh` (`docs/specs/sketch/`,
  `docs/specs/model/`, `docs/specs/document/`).

## Functional Requirements
- **REQ-INS-001**: The inspect tool shall provide `summary` (read): active document name, saved
  and modified state, version, design type, design intent, default length unit, active component,
  counts of components, occurrences, bodies, mesh bodies, sketches, features and user parameters,
  and timeline length and marker (§ 2.1).
  - _Acceptance_: On a new design with one extruded box, `summary` returns `design.type`
    `"parametric"`, `design.bodies` 1, `design.sketches` 1, `design.features` 1,
    `design.timeline` 2 and `document.saved` false; with no document open it returns `NO_DOCUMENT`.
- **REQ-INS-002**: The inspect tool shall provide `object_info` (read) taking any Ref and returning
  its Ref, Fusion type, type-specific properties, bounding box (when the object has one) and parent
  (§ 2.2).
  - _Acceptance_: For a body Ref the result has `type` `"BRepBody"`, `properties.volume`, a `bbox`
    and `parent` naming the root component; for a user parameter Ref it has `properties.expression`
    and `bbox` null.
- **REQ-INS-003**: The inspect tool shall provide `list_bodies` (read, paged), listing BRep and
  mesh bodies of one component or the whole design including occurrence proxies, each with Ref,
  kind, occurrence path, solid/visible flags, volume, area, face and edge counts (§ 2.3).
  - _Acceptance_: With two boxes in the root, `list_bodies` returns `total` 2 and each item's
    `ref.token` resolves back to the same body through `object_info`; `limit` 1 returns
    `next_offset` 1.
- **REQ-INS-004**: The inspect tool shall provide `list_faces` (read, paged) for one body: index,
  token, surface type, area, centroid, and outward normal for planar faces or radius and axis for
  cylindrical, conical, spherical and toroidal faces (§ 2.4).
  - _Acceptance_: A box returns 6 faces, all `"plane"`, unit normals along ±X/±Y/±Z, and areas
    matching the box dimensions; `{"body": <ref>, "index": i}` resolves to the face with the same
    token.
- **REQ-INS-005**: The inspect tool shall provide `list_edges` (read, paged) for one body: index,
  token, curve type, length, start and end points, and center/radius for arcs and circles (§ 2.5).
  - _Acceptance_: A box returns 12 `"line"` edges whose lengths sum to 4 × (x + y + z).
- **REQ-INS-006**: The inspect tool shall provide `geometry` (read): deep detail of one body, face,
  edge or vertex — full surface or curve parameters, adjacency indices, and per-body topology
  counts and surface-type histogram (§ 2.6).
  - _Acceptance_: For a box face, `geometry` returns `surface.type` `"plane"`, `edges` with 4
    indices and `adjacent_faces` with 4 indices; for a sketch Ref it returns `INVALID_PARAMS`.
- **REQ-INS-007**: The inspect tool shall provide `bounds` (read): axis-aligned bounding box of a
  body, component, occurrence or the whole design, with min, max, size and center, and an optional
  tight box (`precise`) (§ 2.7).
  - _Acceptance_: A 20 × 10 × 5 mm box returns `size` [20, 10, 5] (± 1e-6 mm with `precise`
    true); no entity returns the design box.
- **REQ-INS-008**: The inspect tool shall provide `measure_distance` (read): minimum distance
  between any two entities with the closest point on each and the delta vector (§ 2.8).
  - _Acceptance_: Two boxes with facing faces 30 mm apart return `distance` 30 and points whose
    X differ by 30.
- **REQ-INS-009**: The inspect tool shall provide `measure_angle` (read): angle between two
  entities, or at the middle of three points/vertices (§ 2.9).
  - _Acceptance_: Two adjacent box faces return 90 (deg); with `units {"angle": "rad"}` π/2.
- **REQ-INS-010**: The inspect tool shall provide `physical_properties` (read): mass, volume, area,
  density, center of mass, moments of inertia about the world origin and principal moments, per
  entity and combined, with `accuracy` (`low|medium|high|very_high`, default `very_high`) (§ 2.10).
  - _Acceptance_: A 20 × 10 × 5 mm box returns `volume` 1000 and `area` 700 (mm units) and `mass`
    equal to `volume × density` within 1e-9 relative; the result echoes `accuracy`.
- **REQ-INS-011**: The inspect tool shall provide `interference` (read, paged) between bodies or
  occurrences, returning each interfering pair and the interference volume, without adding bodies
  to the design; with no `entities` and more than 200 visible bodies it shall return
  `INVALID_PARAMS` with the count and a hint to pass `entities` (§ 2.11).
  - _Acceptance_: Two 10 mm cubes overlapping by 5 mm in X return one pair with `volume` 500;
    `summary.design.bodies` is unchanged afterwards; one entity returns `INVALID_PARAMS`; unit test:
    201 fake bodies and no `entities` returns `INVALID_PARAMS`, 200 succeeds.
- **REQ-INS-012**: The inspect tool shall provide `compare_meshes` (read): deviation statistics
  (min, max, mean, RMS, standard deviation, median, 95th percentile, max location, fraction within
  tolerance, optional reverse direction and Hausdorff distance) between two mesh bodies or a mesh
  and a BRep body, feature-detecting the mesh accessors (§ 2.12).
  - _Acceptance_: A box BRep compared with its own STL import returns `max` < 0.001 mm; the mesh
    of a 20 × 10 × 6 mm box compared with a 20 × 10 × 5 mm box returns `max` 1 ± 0.001 mm; a body
    without mesh accessors returns `UNSUPPORTED`.
- **REQ-INS-013**: The inspect tool shall provide `get_selection` (read, paged): the user's current
  UI selection as Refs with the pick point (§ 2.13).
  - _Acceptance_: Unit test: a fake selection of a face and an edge returns two items whose `ref`
    tokens match; live: with nothing selected the result has `total` 0.
- **REQ-INS-014**: The inspect tool shall provide `section_analysis` (write): create a section
  analysis on a plane at an offset, optionally flipped and named; on a Fusion build without the
  section-analysis API it returns `UNSUPPORTED` (§ 2.14).
  - _Acceptance_: Live: `section_analysis` on `"XY"` with offset 2 mm returns `analysis.name`, and
    a second call returns a different index; unit test: a fake design without `analyses` returns
    `UNSUPPORTED`.
- **REQ-INS-015**: Every inspect list action shall take `offset`/`limit` and return `ctx.page`
  (core § 5.5); every length, area, volume, angle, mass, density and moment in a result shall be in
  the call's output units (core § 8); every entity in a result shall be a `ctx.ref` (core § 5.4)
  (§ 1).
  - _Acceptance_: Unit tests: `list_faces` with `units {"length": "in"}` returns areas scaled by
    1 / 6.4516 relative to cm²; each list action returns the four page keys.
- **REQ-INS-016**: `inspect.py` shall expose `LIVE_SCENARIOS` covering every action, run by
  `python scripts/live_check.py --only inspect` (§ 3).
  - _Acceptance_: `python scripts/live_check.py --only inspect` prints one `PASS` line per
    scenario and exits 0 against the running add-in.
