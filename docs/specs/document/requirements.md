# requirements.md — docs/specs/document/
> De-Fusion MCP — `document` tool: open documents, cloud data navigation, data files, save, import and export

## Overview
The `document` tool manages Fusion documents and their cloud data files: listing and switching open
documents, creating, opening, saving and closing them, navigating hubs, projects and folders,
reading and changing data files, and moving geometry in and out of Fusion through files (export,
import, mesh import, upload of f3d/f3z archives). All actions are `@action("document", ...)` functions in
`De-Fusion/defusion/tools/document.py`; schema, validation, dispatch, results, errors, paging,
help and profiles come from the core framework.

Fourteen actions take lists (REQ-DOC-023). Acceptance criteria below that name one path, file or
document for such an action mean a one-entry list and its `results[0]`.

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
  - _Acceptance_: `save_as {"folder": "<project>", "documents": [{"name": "n"}]}` returns `saved: true` and a
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
  - _Acceptance_: `set_read_only {"read_only": false}` on a saved (locked) document returns
    `changed: true` and a following `datafile_get` reports `read_only: false`; `read_only: true`
    reverses it; repeating it returns `changed: false`; an unsaved document returns
    `INVALID_PARAMS`; a state not observed within 10 tries returns `READ_ONLY_NOT_APPLIED`
    (REQ-DOC-024).
- **REQ-DOC-015**: The document tool shall provide `datafile_set` (write, rename a data file; `name` is a
  core `FileName`; Fusion's `DataFile.description` has no setter, so descriptions are set only by `save`/`save_as`) and `datafile_refresh` (write, refresh its cached cloud state).
  - _Acceptance_: `datafile_set {"files": [{"file": f, "name": "n"}]}` returns `name` `n` and a
    following `datafile_get` agrees; an entry without `name` returns
    `INVALID_PARAMS`; `datafile_refresh` returns the data file properties.
- **REQ-DOC-016**: The document tool shall provide `datafile_copy` (write) and `datafile_move`
  (write), copying or moving a data file into another folder.
  - _Acceptance_: Copying a file into its own folder returns a new file with a different `id`;
    moving a file to another folder returns the file with the new `folder_id` and `path`.
- **REQ-DOC-017**: The document tool shall provide `import` (write), importing f3d (`.f3d` only),
  step, iges, sat or smt into a new document or into a component of the active design, and dxf or
  svg as sketches on a plane of the active design; an `.f3z` path, or `format` `f3z`, fails
  `INVALID_PARAMS` with the message "f3z cannot be imported directly; use document.datafile_upload
  then document.open" (Fusion documents `createFusionArchiveImportOptions` for `.f3d` only).
  - _Acceptance_: Importing a STEP file exported by `export` with `target` `new_document` creates
    an active document with at least one body; with `target` `active` it adds at least one
    occurrence or body to the target component; a dxf with `target` `new_document` returns
    `INVALID_PARAMS`; a path ending `.f3z`, `format` `f3z`, and `format` `f3d` with an `.f3z` path
    each return `INVALID_PARAMS` whose message contains `use document.datafile_upload then
    document.open`, with no Fusion import call.
- **REQ-DOC-018**: The document tool shall provide `import_mesh` (write), importing stl, obj or 3mf
  into a component of the active design with a stated file unit.
  - _Acceptance_: Importing an ASCII stl of a 10 mm cube spanning 0..10 with `unit` `mm` returns
    one or more mesh body refs and `bbox` min `[0,0,0]`, max `[10,10,10]` within 0.01 mm, in both
    a parametric and a direct design.
- **REQ-DOC-019**: The document tool shall provide `close` (destructive), closing an open
  document without saving; for a saved document the core guard unlocked, it first makes the file
  read-only again (REQ-DOC-028).
  - _Acceptance_: Without `confirm` it returns `CONFIRM_REQUIRED`; with `confirm: true` the
    document is closed, `list_open` no longer lists it, and the result reports
    `discarded_changes` true if it was modified and `relocked` (REQ-DOC-028).
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
- **REQ-DOC-023**: `import`, `import_mesh`, `export`, `open`, `save`, `save_as`, `close`,
  `set_read_only`, `datafile_set`, `datafile_copy`, `datafile_move`, `datafile_delete`,
  `datafile_refresh` and `datafile_upload` shall take a core `bulk` list of 1 to 50 entries (`paths`, `files`,
  `documents`, or `SaveAsEntry`/`RenameEntry` models), run each entry through `ctx.bulk` so one bad entry fails
  alone, and return `{"results", "ok", "failed"}`; shared parameters apply to every entry and their
  resolution errors fail the call; `close` and `datafile_delete` need `confirm` once per call
  (design § 2.7).
  - _Acceptance_: Unit: for each of the 14 actions a 3-entry call with a bad middle entry returns
    `ok: 2, failed: 1` in entry order; 0 or 51 entries and a singular `path`/`file`/`document`/`name`
    parameter each return `INVALID_PARAMS`, the singular one with hints carrying the action's parameter schema; `documents` omitted acts on the active document. Live (`bulk` scenario):
    closing two saved documents in one call and reopening them with a third, missing path returns
    `ok: 2, failed: 1` with `NOT_FOUND` for the third.
- **REQ-DOC-024**: `save` and `save_as` shall, when `read_only` (default true), run Make Read-Only
  on the saved document and verify the cloud file's `isReadOnly` with the core bounded retry (within
  10 tries, 1 s apart), returning the entry error `READ_ONLY_NOT_APPLIED` when not observed (the version
  stays saved); `save` shall capture the data file before `doc.save`, and a `save` whose upload is
  pending fails `NOT_ALLOWED` under a folder limit, else `INVALID_PARAMS`; `set_read_only` shall use the same core helper and error (design § 6.4, § 6.5, § 6.8;
  core design § 5.13).
  - _Acceptance_: Unit (core `VERIFY_DELAY` 0): the lock command follows the save and the entry
    reports `read_only: true` when the fake flips (also when `save` leaves `dataFile.id` a local path), `READ_ONLY_NOT_APPLIED` when it never does, and no
    command with `read_only: false`. Live (`save_locks` scenario): `datafile_get` reports `read_only`
    true after `save_as` and after `save`, and false after `save {"read_only": false}`.
- **REQ-DOC-025**: With a folder limit, every `folder`/`file` address (`_folder`/`_file`), the
  `save`/`save_as`/`set_read_only` targets, the `datafile_upload` folder and every document edit through `guard.ensure_editable`
  shall be inside the limit or fail `NOT_ALLOWED`; `save_as` shall default `folder` to the limit
  (no limit and no `folder` → `INVALID_PARAMS`) and `datafile_upload` shall default it to the limit,
  else the active project's root folder; the not-limited actions are listed in design § 2.8.
  - _Acceptance_: Unit with limit `P/A`: every case in design § 10.1.
    Live (`folder_limit` scenario): under the project-root limit `save_as` without `folder` saves
    into the project root; under a sub-path limit the project-root file is `NOT_ALLOWED` for
    `datafile_get`, `open` and editing, `save_as` into the root and `datafile_upload` into the root
    are `NOT_ALLOWED`, and a new unsaved document is editable.
- **REQ-DOC-026**: Each document action shall declare the `edits_document` value of design § 3;
  `save`, `advance_to_latest`, `update_all_references` and `import` into the active design shall
  call `guard.ensure_editable` on the document they edit, so a saved read-only document is made
  editable first (or `READ_ONLY` with `preserve_read_only: true`) (core design § 5.13).
  - _Acceptance_: Unit: the registry shows the § 3 values; `save` of a read-only fake document runs
    Make Editable before `save` and lists it in `made_editable`; with `preserve_read_only: true` the
    entry is `READ_ONLY` and nothing is saved. Live (`auto_unlock` scenario): `sketch.create` on a
    locked saved document with `preserve_read_only: true` returns `READ_ONLY` and without it
    succeeds with `made_editable` and leaves the file editable.
- **REQ-DOC-027**: Each list action's docstring (its `help` text) shall name its list parameter,
  the 1–50 bound, the shared parameters and the `{"results", "ok", "failed"}` result; `save` and
  `save_as` shall document `read_only`, and `save_as` its folder-limit default (design § 2.7).
  - _Acceptance_: A unit test calls `help` for each of the 14 actions and asserts the help text
    contains the list parameter name, `50` and `results`; `save`/`save_as`/`datafile_upload` help
    contains `read_only`; `save_as` and `datafile_upload` help contain `folder limit`; `close` help
    contains `relocked`; `datafile_upload` help contains `f3z` and `document.open`.
- **REQ-DOC-028**: `close` shall, before closing each saved document that is in the core
  `guard.UNLOCKED` registry, make its file read-only again through `guard.set_read_only` (verified,
  10 tries 1 s apart; the lock needs the document active) and return `relocked: bool` in the entry
  (true only when a fresh lookup, not the open document's own data file, observed read-only); a re-lock that fails or is outside the folder
  limit shall not block the close: the entry still closes, `relocked` is false and a `warnings`
  list names the document and says to call `document.set_read_only`; a close with the upload still
  pending (no saved file yet) also warns naming `document.set_read_only`, `relocked` false; the
  registry entry is dropped on every close outcome. Unsaved changes are still
  discarded (`discarded_changes`); `preserve_read_only` is unchanged (it prevents the unlock, so
  nothing is registered); a registry document that is no longer open is not relockable by `close`
  (`document.set_read_only` is the manual route) (design § 7.1; core design § 5.13).
  - _Acceptance_: Unit (core `VERIFY_DELAY` 0): after `save`'s guard unlocked a fake saved document
    (registry holds it), `close` issues `ActiveDocumentDeactivateCommand` before `close(False)`,
    returns `relocked: true` when the fake flips; a fake that
    never flips gives `relocked: false`, a `warnings` entry naming the document, and the document
    is still closed (`close(False)` called); in every case the registry no longer holds it;
    a fake whose fresh lookup fails while its own data file reads read-only gives `relocked: false`; a document outside the folder limit is closed with
    `relocked: false` and a warning and no command; an unlocked-then-already-locked file gives
    `relocked: true` with no command; an unlocked fake with a local-path `dataFile.id` (upload
    pending) closes with `relocked: false`, a warning naming `document.set_read_only` and an empty
    registry; a document not in the registry, and an unsaved one, give
    `relocked: false`, no `warnings` and no command; a modified document reports
    `discarded_changes` true. Live (`auto_unlock` scenario): after the unlocking write, `close`
    with `confirm` returns `relocked: true` and `datafile_get` reports `read_only` true.
- **REQ-DOC-029**: The document tool shall provide `datafile_upload` (write, `edits_document`
  `none`, bulk `paths`), uploading local `.f3d`/`.f3z` files to a cloud folder with
  `Folder.uploadFile` (Fusion documents `createFusionArchiveImportOptions` for `.f3d` only, so f3z
  needs the upload route) without opening them for the caller, and locking each uploaded file
  unless `read_only` is false. A multi-document `.f3z` makes Fusion create several cloud files
  (root, referenced components, extra files) while `uploadFile` returns only one (a child), so per
  entry the target folder's file ids are snapshotted (one folder scan before and one after) before the upload and, after `UploadFinished`,
  the created files are every file in the folder not in the snapshot plus the returned file (a file
  another writer adds to the folder during the upload cannot be told apart, so it appears in
  `created` and is locked);
  `read_only` locks and verifies every created file. Entries return `{id, name, path,
  version_number, read_only}` for the root (the created file whose name equals the local file
  stem, else the returned file), plus `created` (a list of `{id, name, path}` for all created
  files, root included) when more than one file was created; a
  missing local file is `NOT_FOUND`, an upload that fails or does not finish in time is
  `FUSION_ERROR` (`uploadFile` returning None or `UploadFailed` carries hint "check
  document.list_folders; if the file is absent, retry the upload"; a `FUSION_ERROR` upload may leave
  created cloud files behind, found with `document.list_folders`, then locked or deleted); a lock
  that is not observed, whose `guard.set_read_only` raises on any created file, or (`read_only` true) a created file
  that cannot be listed is `READ_ONLY_NOT_APPLIED` naming the id and
  cloud path of each file not locked, or "an unlisted file in <path>" (the upload happened); per-entry worst case is 120 s + 30 s +
  created_files × (open + 10 s lock verify), stated in the `datafile_upload` help; a stem repeated within one call is `INVALID_PARAMS`, and every `INVALID_PARAMS`
  carries a hint naming the action to take; a list over 2 entries is expected to become a job
  (design § 3, § 6.13); a temporary close or `prev.activate()` that returns `False` or raises adds a `warnings`
  entry ("closing the temporary document failed" / "re-activating the original document failed").
  - _Acceptance_: Unit: a fake `Folder.uploadFile` returning a finished future gives the entry
    shape with the folder default of design § 6.13; a future ending `UploadFailed` and one that
    never finishes (wait patched to 0) each give a `FUSION_ERROR` entry; a missing path gives
    `NOT_FOUND`, a relative path, other extension, existing name or repeated stem `INVALID_PARAMS` with a hint; a raising `guard.set_read_only` gives `READ_ONLY_NOT_APPLIED` with id and path; `read_only`
    true runs the lock path and reports `read_only: true` only when the fake flips,
    `READ_ONLY_NOT_APPLIED` when it never does, and false issues no text command; the previously
    active document is active again afterwards and the transient document is closed; a temporary `close` or `prev.activate()` returning `False` or raising
    gives `ok`, `read_only: true` and a `warnings` entry naming the document; a fake upload that
    creates two files in the folder returns the stem-named one with `created` listing both, locks
    both, and gives `READ_ONLY_NOT_APPLIED` naming the one that never flips; an unlisted created
    file gives `READ_ONLY_NOT_APPLIED` naming "an unlisted file" with `read_only` true and `ok` with false; `uploadFile` None gives the list_folders/retry hint;
    exactly one created file has no `created`; the help states the per-entry worst case. Live
    (`datafile_upload` scenario): design § 10.2.
