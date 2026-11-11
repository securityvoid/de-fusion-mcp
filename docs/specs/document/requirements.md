# requirements.md — docs/specs/document/
> De-Fusion MCP — `document` tool: open documents, cloud data navigation, data files, save, import and export

## Overview
The `document` tool manages Fusion documents and their cloud data files: listing and switching open
documents, creating, opening, saving and closing them, navigating hubs, projects and folders,
reading and changing data files, and moving geometry in and out of Fusion through files (export,
import, mesh import). All actions are `@action("document", ...)` functions in
`De-Fusion/defusion/tools/document.py`; schema, validation, dispatch, results, errors, paging,
help and profiles come from the core framework.

## Operator ask
2026-11-10: "comprehensive Fusion MCP server ... union of the four servers"

## Dependencies
- **DEP-DOC-001**: Core set `docs/specs/core/` (action framework, dispatcher and jobs, Ref types,
  units, results, errors, profiles, live check harness).

## Functional Requirements
- **REQ-DOC-001**: The document tool shall provide `list_open` (read), returning every open
  document as a document summary, paged.
  - _Acceptance_: With two documents open, `list_open` returns `total` 2, exactly one item has
    `active: true`, and each item has `name`, `id`, `active`, `saved`, `modified`, `visible`,
    `type` and `file`.
- **REQ-DOC-002**: The document tool shall provide `list_hubs` (read) and `list_projects` (read,
  optional `hub` by id or name, default the active hub), each paged.
  - _Acceptance_: `list_hubs` returns at least one hub with `active: true`; `list_projects` with no
    params returns the active hub's projects, each with `id`, `name`, `root_folder_id`; an unknown
    `hub` returns `NOT_FOUND`.
- **REQ-DOC-003**: The document tool shall provide `list_folders` (read, children of a folder,
  files included by default, paged) and `resolve_folder` (read, a cloud folder from a path like
  `"Project/Sub/Folder"` or a folder id).
  - _Acceptance_: `resolve_folder {"folder": "<project>"}` returns the project's root folder;
    `list_folders` on it returns items with `kind` `folder` or `file`, each with `id`, `name` and
    `path`; a path with a missing segment returns `NOT_FOUND` naming that segment.
- **REQ-DOC-004**: The document tool shall provide `datafile_get` (read), returning the properties
  of a cloud data file addressed by id or path.
  - _Acceptance_: For a saved file, `datafile_get` returns `id`, `name`, `extension`,
    `description`, `version_number`, `read_only`, `folder_id`, `project_name` and `path`, and the
    same result is returned whether the file is addressed by its `id` or its `path`.
- **REQ-DOC-005**: The document tool shall provide `list_references` (read), listing the external
  references of a document (direct, or all nested with `nested: true`), paged.
  - _Acceptance_: For a design with one inserted cloud component, `list_references` returns one
    item with `file` (id, name, path), `out_of_date` and `version_number`; for a design with none
    it returns `total` 0.
- **REQ-DOC-006**: The document tool shall provide `export` (read), writing the active design, or
  one component or body of it, to an absolute file path in any of f3d, step, iges, sat, smt, stl,
  3mf, obj, usdz, with mesh refinement and one-file-per-body options for stl, 3mf and obj. It only creates new files:
  an existing target file, including any per-body file, returns `INVALID_PARAMS` before anything is
  written.
  - _Acceptance_: Exporting a scratch design with one body to `<tmp>/a.stl`, `<tmp>/a.step` and
    `<tmp>/a.f3d` each returns `files` naming an existing non-empty file; a relative path, an
    existing target, or an existing `a_<body>.stl` with `one_file_per_body` returns `INVALID_PARAMS`
    and leaves the file unchanged; an `overwrite` param returns `INVALID_PARAMS`; a format whose `create*ExportOptions` method is missing, or that Fusion refuses as restricted, returns `UNSUPPORTED`.
- **REQ-DOC-007**: The document tool shall provide `create` (write), creating a new design
  document, optionally parametric or direct, and making it active.
  - _Acceptance_: `create {"design_type": "direct"}` returns a document summary with
    `active: true`, `saved: false`, and `design_type` `direct`; `list_open` total grows by 1.
- **REQ-DOC-008**: The document tool shall provide `open` (write), opening a cloud data file by id
  or path and making it active; a file already open is activated instead of opened again.
  - _Acceptance_: Opening a saved, closed file returns its document summary with
    `already_open: false`; opening it again returns `already_open: true` and `list_open` total does
    not change.
- **REQ-DOC-009**: The document tool shall provide `activate` (write), making an open document the
  active one.
  - _Acceptance_: With documents A and B open and B active, `activate {"document": "A"}` returns A
    with `active: true` and a following `list_open` shows A active.
- **REQ-DOC-010**: The document tool shall provide `save` (write), saving a new version of an
  already-saved document with a description.
  - _Acceptance_: After a change to a saved document, `save {"description": "x"}` returns
    `modified: false` and the data file's `version_number` grows by 1 once uploaded; a never-saved
    document returns `INVALID_PARAMS` with a hint to use `save_as`.
- **REQ-DOC-011**: The document tool shall provide `save_as` (write), saving a document to a cloud
  folder under a given name (a core `FileName`).
  - _Acceptance_: `save_as {"folder": "<project>", "name": "n"}` returns `saved: true` and a
    `file.path` of `<project>/n`; `datafile_get` on that path succeeds; saving under a name that
    already exists in the folder returns `INVALID_PARAMS`.
- **REQ-DOC-012**: The document tool shall provide `set_name` (write), renaming a document that has
  not been saved yet.
  - _Acceptance_: On an unsaved document, `set_name {"name": "n"}` returns `name` `n`; on a saved
    document it returns `INVALID_PARAMS` with a hint to use `datafile_set`.
- **REQ-DOC-013**: The document tool shall provide `advance_to_latest` (write) and
  `update_all_references` (write), updating a document's referenced components.
  - _Acceptance_: On a design with no references both succeed and return the document summary
    with `references_out_of_date: 0`; on a Fusion build missing the method they return
    `UNSUPPORTED`.
- **REQ-DOC-014**: The document tool shall provide `set_read_only` (write), making a saved
  document read-only or editable.
  - _Acceptance_: `set_read_only {"read_only": true}` on a saved document succeeds and a following
    `datafile_get` reports `read_only: true` within 5 s; `read_only: false` reverses it; an unsaved
    document returns `INVALID_PARAMS`.
- **REQ-DOC-015**: The document tool shall provide `datafile_set` (write, rename a data file; `name` is a
  core `FileName`; Fusion's `DataFile.description` has no setter, so descriptions are set only by `save`/`save_as`) and `datafile_refresh` (write, refresh its cached cloud state).
  - _Acceptance_: `datafile_set {"file": f, "name": "n"}` returns `name` `n` and a
    following `datafile_get` agrees; `datafile_set` without `name` returns
    `INVALID_PARAMS`; `datafile_refresh` returns the data file properties.
- **REQ-DOC-016**: The document tool shall provide `datafile_copy` (write) and `datafile_move`
  (write), copying or moving a data file into another folder.
  - _Acceptance_: Copying a file into its own folder returns a new file with a different `id`;
    moving a file to another folder returns the file with the new `folder_id` and `path`.
- **REQ-DOC-017**: The document tool shall provide `import` (write), importing f3d, step, iges,
  sat or smt into a new document or into a component of the active design, and dxf or svg as
  sketches on a plane of the active design.
  - _Acceptance_: Importing a STEP file exported by `export` with `target` `new_document` creates
    an active document with at least one body; with `target` `active` it adds at least one
    occurrence or body to the target component; a dxf with `target` `new_document` returns
    `INVALID_PARAMS`.
- **REQ-DOC-018**: The document tool shall provide `import_mesh` (write), importing stl, obj or 3mf
  into a component of the active design with a stated file unit.
  - _Acceptance_: Importing an ASCII stl of a 10 mm cube spanning 0..10 with `unit` `mm` returns
    one or more mesh body refs and `bbox` min `[0,0,0]`, max `[10,10,10]` within 0.01 mm, in both
    a parametric and a direct design.
- **REQ-DOC-019**: The document tool shall provide `close` (destructive), closing an open
  document without saving.
  - _Acceptance_: Without `confirm` it returns `CONFIRM_REQUIRED`; with `confirm: true` the
    document is closed, `list_open` no longer lists it, and the result reports
    `discarded_changes` true if it was modified.
- **REQ-DOC-020**: The document tool shall provide `datafile_delete` (destructive), deleting a
  cloud data file.
  - _Acceptance_: Without `confirm` it returns `CONFIRM_REQUIRED`; with `confirm: true` a
    following `datafile_get` on its id returns `NOT_FOUND`.
- **REQ-DOC-021**: The document tool shall address documents, folders and files uniformly: a
  document by name or creation id (default the active document); a folder or file by id
  (`urn:` prefix) or by a `/`-separated path in the active hub; ambiguous names return
  `AMBIGUOUS` with candidates.
  - _Acceptance_: Every action taking `document`, `folder` or `file` accepts both forms; two open
    documents with the same name addressed by name return `AMBIGUOUS` with both summaries in
    `error.candidates`; no open document returns `NO_DOCUMENT`.
- **REQ-DOC-022**: The document tool shall declare every action with `undo_group=False`, the
  per-action timeout of design § 3, and feature detection that returns `UNSUPPORTED` when the
  running Fusion lacks a used API member.
  - _Acceptance_: `help` for each action reports the timeout listed in design § 3; a unit test
    with the member removed from the fake returns `UNSUPPORTED` for each feature-detected member
    listed in design § 4; no document action emits `PTransaction` text commands.
