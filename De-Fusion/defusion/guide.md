# De-Fusion modelling guide

You drive Autodesk Fusion through a small set of De-Fusion tools. Build models in small, checked
steps: one action, then look at the result.

## 1. Calling De-Fusion

Each tool (`sketch`, `model`, `assembly`, `history`, `inspect`, `view`, `document`, `server`, ...) takes
an `action` and a `params` object. Call `action: "help"` with no params to list a tool's actions. Call
it with `{"action": "extrude"}` to get that action's full help and input schema before using it for the
first time. Never guess parameter names.

```json
{"action": "help", "params": {"action": "extrude"}}
```

- Destructive actions (for example `sketch.delete`, `model.remove_body`) need `"confirm": true` in
  `params`. Without it you get `CONFIRM_REQUIRED` and nothing happens.
- The server's profile decides which actions you can see. An action outside it returns `NOT_ALLOWED`.
- A success result is `{"ok": true, "result": ..., "units": ..., "changes": ...}`. `changes` appears on
  write calls and summarises new bodies, timeline growth and unhealthy features. Read it every time.
- A failure is `{"ok": false, "error": {"code", "message", "hints"}}`. Read the `hints`; they
  usually name the fix. Common codes: `INVALID_PARAMS`, `NOT_FOUND`, `AMBIGUOUS`, `NOT_ALLOWED`,
  `CONFIRM_REQUIRED`, `WRONG_DESIGN_TYPE`, `FUSION_ERROR`.
- A call that runs longer than the timeout returns `{"status": "running", "job_id": ...}`. Poll
  `server.job_status` with that id until the state is `done` or `failed`.
- `server.status` reports versions, the active profile and the queue.

## 2. Units

Bare numbers are millimetres and degrees unless the design says otherwise. You can also pass unit
expressions such as `"0.5 in"` or `"90 deg"`, and parameter expressions such as `"wall*2"`.
Results come back in the call's output units, shown in `units`. Pass `"units": {"length": "in"}` at
the top level of a call to change that for one call. `inspect.summary` or `model.units_get` shows the
design defaults.

## 3. Components first

Make one component per physical part, before its bodies exist.

- Call `assembly.create_component` with a `name`, then work inside it (pass `component` to
  `sketch.create`).
- A component is the part; an occurrence is one placed instance of it. Copies of a part are
  occurrences of one component, so edits propagate.
- Why: joints (`assembly.joint`), per-part export and bills of materials all work on components.
  Bodies piled into the root component cannot be jointed or exported separately.
- `assembly.list_components` and `assembly.list_occurrences` show what exists.

## 4. Parameters

Drive every dimension you may want to change from a user parameter.

- Create with `model.parameter_create` (`name`, `expression`, optional `unit`). Use clear names such as
  `wall` or `bolt_d`.
- Use the name inside any length or angle: `"distance": "wall*2"`, `"value": "bolt_d/2"`.
- Change values with `model.parameter_modify`; dependent features rebuild. List them with
  `model.parameter_list`.

## 5. Sketch to feature

1. Pick a plane (`"XY"`, `"XZ"`, `"YZ"`, a construction plane or a planar face Ref).
2. `sketch.create` with `plane`; keep the returned sketch Ref.
3. Draw: `sketch.line`, `sketch.rectangle`, `sketch.circle`, `sketch.arc`, `sketch.slot` and friends.
4. Fix the shape: `sketch.constraint` for geometry rules, `sketch.dimension` for sizes.
5. Check with `sketch.info` (the `profiles` section).
6. Build the feature: `model.extrude`, `model.revolve`, `model.sweep`, `model.loft`.
7. Verify with `inspect.list_bodies` and `inspect.bounds`.

```json
{"action": "rectangle", "params": {"sketch": {"name": "Sketch1"}, "mode": "two_point",
 "p1": [0, 0], "p2": [20, 10]}}
```

## 6. Profiles

A closed loop in a sketch is a profile. Profiles are addressed as `{"sketch": <Ref>, "index": n}`,
where `n` is the position in `sketch.info`. `index` may be a list to use several profiles in one call.

- Always list the profiles with `sketch.info` before extruding; do not assume the order.
- A loop inside another loop is a hole; the outer profile and the inner one are separate indices.
- Profiles change when the sketch changes. After any edit, list them again.

```json
{"action": "extrude", "params": {"profile": {"sketch": {"name": "Sketch1"}, "index": 0}, "distance": "wall*2"}}
```

## 7. Refs

Every entity in a result is a Ref: `{"type", "name", "token", "component"}`. Feed it straight back as
an input. Accepted input forms are `{"token": ...}`, `{"name": ..., "component": ...}`, a bare name,
and `{"body": <Ref>, "index": n}` for a face, edge or vertex.

- Prefer tokens; names can repeat. A name that matches several entities returns `AMBIGUOUS` with the
  candidates in `error.candidates`; choose one and retry with its token.
- Face and edge indices come from `inspect.list_faces` and `inspect.list_edges`.
- Timeline edits (rolling back, reordering, deleting) can change tokens and indices. Read Refs again
  after them.

## 8. Timeline and rollback

A parametric design records a timeline; a direct design does not (`WRONG_DESIGN_TYPE` from actions that
need one). `history.list` shows the items, their health and the marker.

- To edit an earlier feature, `history.set_marker` to move before it, change it, then move the marker to
  `"end"`.
- `history.set_suppressed` switches a feature off and on without deleting it.
- After changes, `model.compute_all` recomputes and lists unhealthy items. Fix warnings before
  building on top of them.

## 9. Undo

Each write call is one undo step, so a failed call leaves no half-built work. If the envelope shows
`"undo_grouped": false` the grouping failed for that call, and several undo steps may be involved.
Use `history.undo` to step back one operation; it refuses an undo that would drop timeline items.

## 10. Coils and threads

- Use `model.coil` when the helix is the part: springs, spiral grooves, threaded bodies you must print
  in one piece. It needs a parametric design.
- Use `model.thread` on a cylindrical face for standard threads. `modeled: true` cuts real geometry,
  which prints; the default cosmetic thread is only a drawing annotation.
- For 3D printing prefer modeled threads, at a coarse pitch, and add clearance for internal threads.
- `model.thread_query` lists valid types, sizes, designations and classes, one level per call.
- Read each action's `help` for the parameters; they are numerous.

## 11. Verifying work

Check after every feature, not only at the end.

- `inspect.summary` gives counts and the timeline; `inspect.bounds` gives the size;
  `inspect.measure_distance` checks gaps; `inspect.physical_properties` gives volume and mass.
- `view.capture` returns a PNG of the viewport and `view.view_sheet` returns four tiles (iso, front,
  top, right). Use a sheet to catch shape mistakes a table of numbers hides.
- Read the `changes` summary in each write result: the body count and timeline length should move
  exactly as you expect.

## 12. Escape hatch

When no action does what you need, use `script.execute_python`. It runs in Fusion's main thread with
`adsk`, `app`, `ui`, `design` and `root` preloaded, and is destructive, so pass `"confirm": true`.

- Look up exact API names and signatures with `script.api_docs` before writing code.
- Pass a `session_id` to keep variables between calls.
- Save a snippet worth reusing with `script.save_script`, then fetch it with `script.load_script`.
- The script tool may be outside your profile; if it is, say so rather than working around it.
