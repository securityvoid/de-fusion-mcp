# design.md — docs/specs/document/
> De-Fusion MCP — `document` tool

## 1. Overview
All code is in `De-Fusion/defusion/tools/document.py`, imported by
`De-Fusion/defusion/tools/__init__.py` (core design § 2). The module docstring's first line is the
tool summary: `Open, save, close, import and export Fusion documents and manage cloud files.`
Everything not stated here (schema, validation, `confirm`, dispatch, jobs, results, paging, help,
errors, units, profiles) is core behaviour: core design § 4, § 5, § 8. Error codes are the core set
(core design § 5.10); this set adds none.

Every action is declared `@action("document", access=..., timeout=..., undo_group=False)`
(core design § 5.7: document open/close/save must not run inside a `PTransaction`; import into a
new document changes the active document mid-call). Each action does all its Fusion work inside
its one dispatcher item, so `NOT_EXECUTED` means nothing ran; a call that outlives its timeout
while running becomes a job (core design § 4) whose `done` result is the action's normal result.

Thirteen actions take a list of 1 to 50 entries and return per-entry results (§ 2.7, core design
§ 5.12). Every action declares `edits_document` (§ 3; core design § 5.1, § 5.13): actions that edit
an open document make a saved read-only one editable first, and `save`/`save_as` make the saved
file read-only again (§ 6.4, § 6.5). With a folder limit (core design § 6.4) every `folder`/`file`
address and every save target must be inside it (§ 2.8).

`Document.save`, `saveAs`, `close` and `Documents.add`/`open` are not allowed inside Fusion
Command event handlers. The core dispatcher runs items from a CustomEvent handler, not a Command,
so they are allowed here; no action in this set may wrap its work in a Command.

## 2. Shared helpers (module-private, in `document.py`)

### 2.1 Document selector
Parameter `document: str | None = None`. `_doc(ctx, document)`:
1. `None` → `ctx.app.activeDocument`; `None` → `ctx.fail("NO_DOCUMENT", "No document is open")`.
2. Else scan `ctx.app.documents` (`count`/`item(i)`): exact `doc.name == document`; if none, exact
   `doc.creationId == document`.
3. 0 matches → `NOT_FOUND` (hint: call `list_open`); > 1 → `AMBIGUOUS` with
   `candidates=[_doc_summary(d) ...]` (hint: pass the `id`).

### 2.2 Folder and file addresses
Parameters `folder: str` and `file: str`. A value starting with `urn:` is an id; anything else is
a path.
- Id: `ctx.app.data.findFolderById(v)` / `ctx.app.data.findFileById(v)`; `None` or a raised
  exception → `NOT_FOUND`.
- Path: `segs = [s for s in v.strip().strip("/").split("/") if s]`; empty → `INVALID_PARAMS`.
  `segs[0]` is a project name in `ctx.app.data.activeHub.dataProjects`; its `rootFolder` is the
  start. Each next folder segment is matched by exact `name` among `folder.dataFolders`. For a
  file, the last segment is matched by exact `name` among the final folder's `dataFiles`
  (`DataFile.name` carries no extension). 0 matches → `NOT_FOUND` with message
  `Path segment '<seg>' not found under '<path so far>'`; > 1 → `AMBIGUOUS` with each candidate's
  folder/file summary. Names containing `/` can only be addressed by id.
- `guard.folder_path(f)` (core design § 5.13: walk `f.parentFolder` until
  `f.isRoot`, prepend `f.parentProject.name`, join with `/`; a project root folder's path is the
  project name; any exception → `None`). `_file_path(df)` = `guard.folder_path(df.parentFolder) + "/" +
  df.name`, `None` when the folder path is `None`.
- Folder limit: `_folder(ctx, v)` and `_file(ctx, v)` end, after resolving by id or path, with
  `guard.check_folder(ctx, guard.folder_path(folder))` / `guard.check_folder(ctx, guard.folder_path(df.parentFolder))`
  (core design § 5.13), so every action addressing a folder or file is limited in one place (§ 2.8).

### 2.3 Hub selector
`hub: str | None = None`. `None` → `ctx.app.data.activeHub`. Else match `DataHub.id == hub`, then
exact `name`; 0 → `NOT_FOUND`; > 1 → `AMBIGUOUS`.

### 2.4 Summaries
- `_file_ref(df)` → `{"id": <df.id if it starts with "urn:" else null>, "name": df.name,
  "path": _file_path(df)}`. A just-saved document's `dataFile.id` is a local path until upload
  completes, hence `null`; the path still addresses the file once it is in the folder.
- `_doc_summary(doc)` →
  ```json
  {"name": "...", "id": "<creationId>", "active": true, "visible": true,
   "saved": true, "modified": false, "type": "design|drawing|other",
   "design_type": "parametric|direct|null", "file": {"id": "...", "name": "...", "path": "..."}}
  ```
  `saved` = `doc.dataFile is not None`; `file` = `_file_ref(doc.dataFile)` or `null`; `type` from
  `doc.objectType`: `adsk::fusion::FusionDocument` → `design`, contains `Drawing` → `drawing`,
  else `other`; `design_type` from `adsk.fusion.Design.cast(doc.products.itemByProductType(
  "DesignProductType")).designType` (`ParametricDesignType`/`DirectDesignType`), `null` when not a
  design.
- `_folder_summary(f)` → `{"kind": "folder", "id", "name", "path"}`.
- `_file_summary(df)` → `{"kind": "file", "id", "name", "extension": df.fileExtension,
  "version_number": df.versionNumber, "path"}`.
- `_file_props(df)` (full) →
  ```json
  {"id": "...", "name": "...", "extension": "f3d", "description": "...",
   "version_number": 3, "latest_version_number": 3, "read_only": false, "in_use": false,
   "folder_id": "...", "folder_name": "...", "project_id": "...", "project_name": "...",
   "path": "...", "created_by": "...", "date_created": 0, "date_modified": 0, "web_url": "..."}
  ```
  Sources: `fileExtension`, `description`, `versionNumber`, `latestVersionNumber`, `isReadOnly`,
  `isInUse`, `parentFolder.id/.name`, `parentProject.id/.name`, `createdBy.displayName`,
  `dateCreated`, `dateModified` (epoch seconds), `fusionWebURL`. Each is read with
  `getattr(..., None)` inside `try`; a missing or raising property gives `null` (read-only
  properties are optional, not `UNSUPPORTED`).

### 2.5 Feature detection
`_need(obj, member)`: `hasattr(obj, member)` false → `ctx.fail("UNSUPPORTED", "<Class>.<member>
is not available in this Fusion build")`. Used for every member listed in § 4.

### 2.6 Local file paths
`_abs_path(p, must_exist)`: `pathlib.Path(p).expanduser()`; not `is_absolute()` → `INVALID_PARAMS`
("path must be absolute"); `must_exist` and not `is_file()` → `NOT_FOUND`; for output paths the
parent directory must exist (`NOT_FOUND`). The extension (lower-cased suffix without the dot)
selects the format when `format` is omitted; unknown extension → `INVALID_PARAMS` listing the
accepted ones.

### 2.7 Bulk entries
The list actions (§ 3, column *List param*) take a `bulk(...)` list (core design § 5.12). The singular
parameter names (`path`, `file`, `document`, and `name` on `save_as`/`datafile_set`) fail
core `INVALID_PARAMS` (`extra="forbid"`), whose hints carry the action's parameter schema, listing
the list parameter.
- String entries: `paths` (local file paths), `files` (cloud file id or path, § 2.2), `documents`
  (document selector, § 2.1). `documents` is `bulk(str) | None = None`; `None` means `[None]`, one
  entry for the active document (`input: null`).
- Model entries (pydantic, `extra="forbid"`): `SaveAsEntry {document: str | None = None, name:
  FileName}` and `RenameEntry {file: str, name: FileName}`.
- Every other parameter is shared by all entries. Shared parameters that need Fusion work
  (`folder` of `save_as`/`datafile_copy`/`datafile_move`, `require_design` and the target
  component of `import_mesh`, the `component`/`body` exclusivity of `export`) are resolved once
  before `ctx.bulk`; their errors fail the whole call.
- The per-entry function returns exactly the single-entry result this design gives for the action;
  the call's `result` is `{"results": [...], "ok": n, "failed": n}` in entry order.
- Entries run one at a time in order; a later entry sees the effects of earlier ones (e.g. a second
  `close` of the same document → `NOT_FOUND`; a second `export` to the same path → `INVALID_PARAMS`
  "file exists").
- `close` and `datafile_delete` need `confirm: true` once per call.
- Each action's docstring (shown by `help`) names its list parameter, the 1–50 bound, the shared
  parameters and the `{"results", "ok", "failed"}` result.

Live scenarios of other tool sets that call these actions use the list forms too; the shared
scenario helper `De-Fusion/defusion/tools/live_common.py` (`_fresh` closes its document with
`close {"documents": [<id>], "confirm": true}`) and the `inspect`/`history` scenarios use the list forms.

### 2.8 Folder limit
With `ctx.folder_limit` set (core design § 6.4, § 5.13): `_folder`/`_file` reject anything outside
it (`NOT_ALLOWED`, per entry in list actions, call-level for a shared `folder`); `save` (through
`guard.ensure_editable`), `save_as` (its folder) and `set_read_only` (the document's file folder) are
limited; `save_as` defaults its `folder` to the limit; `import` into the active design,
`import_mesh`, `advance_to_latest` and `update_all_references` are limited through
`guard.ensure_editable`. Not limited: `list_hubs`, `list_projects`, `list_open`, `list_references`,
`export`, `create`, `activate`, `set_name` (never-saved documents only), `close`, and imports into a
new document.

## 3. Action table
| Action | Access | Timeout (s) | `edits_document` | List param (§ 2.7) | Section |
|---|---|---|---|---|---|
| `list_open` | read | 30 | (read) | — | 5.1 |
| `list_hubs` | read | 30 | (read) | — | 5.2 |
| `list_projects` | read | 60 | (read) | — | 5.2 |
| `list_folders` | read | 60 | (read) | — | 5.3 |
| `resolve_folder` | read | 60 | (read) | — | 5.3 |
| `datafile_get` | read | 30 | (read) | — | 5.4 |
| `list_references` | read | 60 | (read) | — | 5.5 |
| `export` | read | 120 | (read) | `paths` | 5.6 |
| `create` | write | 60 | `none` | — | 6.1 |
| `open` | write | 180 | `none` | `files` | 6.2 |
| `activate` | write | 30 | `none` | — | 6.3 |
| `save` | write | 180 | `action` | `documents` | 6.4 |
| `save_as` | write | 180 | `none` | `documents` (`SaveAsEntry`) | 6.5 |
| `set_name` | write | 30 | `none` | — | 6.6 |
| `advance_to_latest` | write | 180 | `action` | — | 6.7 |
| `update_all_references` | write | 180 | `action` | — | 6.7 |
| `set_read_only` | write | 60 | `none` | `documents` | 6.8 |
| `datafile_set` | write | 60 | `none` | `files` (`RenameEntry`) | 6.9 |
| `datafile_refresh` | write | 60 | `none` | `files` | 6.9 |
| `datafile_copy` | write | 120 | `none` | `files` | 6.10 |
| `datafile_move` | write | 120 | `none` | `files` | 6.10 |
| `import` | write | 300 | `action` | `paths` | 6.11 |
| `import_mesh` | write | 180 | `active` | `paths` | 6.12 |
| `close` | destructive | 60 | `none` | `documents` | 7.1 |
| `datafile_delete` | destructive | 60 | `none` | `files` | 7.2 |

`edits_document` `action` and `active` add `preserve_read_only` (core design § 5.1). Timeouts cover
the whole call; a long list that outlives it becomes a job (core design § 4, § 5.12). The worst
case is about 10 s per guard or lock verify per entry, so a list of more than timeout/10 entries is
expected to become a job.

Python cannot name a function `import`; it is `def import_(...)` registered with
`name="import"`.

## 4. Feature-detected members
`Document.advanceToLatest`, `Document.updateAllReferences`, `Document.allDocumentReferences`
(only when `nested`), `DataFile.refresh`, `DataFile.copy`, `DataFile.move`, every
`ExportManager.create*ExportOptions` and `ImportManager.create*ImportOptions` method of § 8 and
§ 9, `ImportManager.importToTarget`, the mesh option properties of § 8.2, and
`MeshBodies.add`. Each missing member → `UNSUPPORTED` (§ 2.5). The unit tests remove each from the
fake and assert `UNSUPPORTED`.

## 5. Read actions
### 5.1 list_open
```python
def list_open(ctx, offset: int = 0, limit: int = 100) -> dict
```
Iterates `ctx.app.documents`; returns `ctx.page([_doc_summary(d) ...], offset, limit)`.

### 5.2 list_hubs, list_projects
```python
def list_hubs(ctx, offset: int = 0, limit: int = 100) -> dict
def list_projects(ctx, hub: str | None = None, offset: int = 0, limit: int = 100) -> dict
```
`list_hubs`: `ctx.app.data.dataHubs` → items `{"id", "name", "active": hub.id ==
data.activeHub.id}`. `list_projects`: hub per § 2.3, `hub.dataProjects` → items `{"id", "name",
"root_folder_id": p.rootFolder.id, "active": p.id == data.activeProject.id}` (`active` false when
`activeProject` is `None`).

### 5.3 list_folders, resolve_folder
```python
def list_folders(ctx, folder: str, files: bool = True, offset: int = 0, limit: int = 100) -> dict
def resolve_folder(ctx, folder: str) -> dict
```
`list_folders`: folder per § 2.2; items = `_folder_summary` of every `folder.dataFolders` entry,
then (when `files`) `_file_summary` of every `folder.dataFiles` entry; `ctx.page(...)`. Entries
whose `item(i)` raises (mid-upload) are skipped. `resolve_folder` → `_folder_summary(folder)` plus
`"project_id"`, `"project_name"`, `"is_root": folder.isRoot`.

### 5.4 datafile_get
```python
def datafile_get(ctx, file: str) -> dict
```
→ `_file_props(df)`.

### 5.5 list_references
```python
def list_references(ctx, document: str | None = None, nested: bool = False,
                    offset: int = 0, limit: int = 100) -> dict
```
`doc.documentReferences`, or `doc.allDocumentReferences` when `nested` (feature-detected). Item per
`DocumentReference r`: `{"file": _file_ref(r.dataFile), "out_of_date": r.isOutOfDate,
"version_number": r.version, "open": r.referencedDocument is not None}`.

### 5.6 export
```python
ExportFormat = Literal["f3d", "step", "iges", "sat", "smt", "stl", "3mf", "obj", "usdz"]
MeshRefinement = Literal["low", "medium", "high"]
def export(ctx, paths: bulk(str), format: ExportFormat | None = None,
           component: Component | None = None, body: Body | None = None,
           refinement: MeshRefinement = "medium", one_file_per_body: bool = False) -> dict
```
Step 1 runs once (call-level); steps 2–6 run per `path` entry of `paths` (§ 2.7).
1. `design = ctx.require_design()`. `component` and `body` both given → `INVALID_PARAMS`.
2. `path` per § 2.6 (output). `format` omitted → from extension; given and the extension is not
   one of that format's extensions (§ 8.1) → `INVALID_PARAMS`. Existing `path` → `INVALID_PARAMS`
   ("file exists"). With `one_file_per_body`, any file in `path.parent` whose name starts with
   `path.stem` and ends with the format extension also → `INVALID_PARAMS`. Both checks run before
   any write.
3. `geometry = body or component or design.rootComponent`. `body` with a non-mesh format →
   `INVALID_PARAMS` ("body export needs stl, 3mf or obj; pass its component").
4. `em = design.exportManager`; method per § 8.1 (feature-detected); call it with keyword arguments
   `filename=str(path), geometry=geometry` (keywords make the call independent of Fusion's
   per-method argument order).
5. Mesh formats: apply § 8.2. Then `em.execute(opts)`; `False` → `FUSION_ERROR` ("export
   failed").
6. Files: without `one_file_per_body`, `[str(path)]` if it exists, else `FUSION_ERROR`. With it,
   every file in `path.parent` whose name starts with `path.stem` and ends with the format
   extension, sorted; empty → `FUSION_ERROR`.

Entry result: `{"format": "stl", "files": ["/abs/a.stl"], "bytes": <sum of sizes>, "geometry":
ctx.ref(geometry)}`.

Export to a file is `read` (core design § 5.1); it never changes the design and only creates new files, never replacing one (step 2).

## 6. Write actions
### 6.1 create
```python
def create(ctx, design_type: Literal["parametric", "direct"] | None = None) -> dict
```
`doc = ctx.app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)` (new document is
visible and active). When `design_type` is given, set the design's `designType` to
`adsk.fusion.DesignTypes.ParametricDesignType` / `DirectDesignType`. → `_doc_summary(doc)`.
Core's live harness creates and closes its scratch document with `create` and `close`.

### 6.2 open
```python
def open(ctx, files: bulk(str)) -> dict
```
Per `file` entry (§ 2.7): resolve `df` (§ 2.2, folder-limited). If an open document has `dataFile` with `id == df.id`: `doc.activate()` and
return `{**_doc_summary(doc), "already_open": true}`. Else `doc = ctx.app.documents.open(df,
True)`; `None` → `FUSION_ERROR`; entry result `{**_doc_summary(doc), "already_open": false}`. Opening
downloads from the cloud, hence the 180 s timeout and job behaviour (§ 1). Name the Python function
`open_` with `name="open"` so the builtin is not shadowed.

### 6.3 activate
```python
def activate(ctx, document: str) -> dict
```
`doc.activate()`; `False` → `FUSION_ERROR`. → `_doc_summary(doc)`.

`_doc_summary` reads `doc.dataFile` and treats a `RuntimeError` from it (live: "Failed to get
temporary file" while a just-saved document is still uploading) as `file: null`.

### 6.4 save
```python
def save(ctx, documents: bulk(str) | None = None, description: str = "",
         read_only: bool = True) -> dict
```
Per `document` entry (§ 2.7):
1. `doc = _doc(ctx, document)`; `df = guard.saved_file(doc)`. `None` with `guard.never_saved(doc)` (a raising `dataFile` read is upload pending) →
   `INVALID_PARAMS` with hint `never saved; use save_as`; `None` otherwise (upload pending) →
   `NOT_ALLOWED` when `ctx.folder_limit` is set, else `INVALID_PARAMS` ("wait for its upload").
2. `guard.ensure_editable(ctx, doc)` (core design § 5.13: folder limit, then Make Editable when the file
   is read-only, or `READ_ONLY` with `preserve_read_only`).
3. `doc.save(description)` (`df` from step 1 is used in step 4); `False` → `FUSION_ERROR`.
4. `read_only` true: `guard.set_read_only(ctx, doc, True,
   lambda: ctx.app.data.findFileById(df.id))` false → `READ_ONLY_NOT_APPLIED` ("saved <name> but
   it was not read-only within 10 tries, 1 s apart", hint "call document.set_read_only"). The version is saved
   either way.
5. Entry result `{**_doc_summary(doc), "read_only": read_only}` (`read_only` true only when
   observed in step 4).

The upload continues after the call returns; `datafile_get` shows the new `version_number` once it
completes. `read_only: false` leaves the file editable.

### 6.5 save_as
```python
def save_as(ctx, documents: bulk(SaveAsEntry), folder: str | None = None,
            description: str = "", read_only: bool = True) -> dict
```
Call-level: `folder` omitted → `ctx.folder_limit` when set, else `INVALID_PARAMS` ("folder is
required when no folder limit is set"); resolve it once with `_folder` (§ 2.2; outside the limit →
`NOT_ALLOWED`, a limit naming a missing folder → `NOT_FOUND`).
Per entry `{document, name}` (`name: FileName`, core design § 5.4):
1. `name` equal to an earlier entry's `name` in this call → `INVALID_PARAMS` ("<name> appears
   twice in this call").
2. `doc = _doc(ctx, document)`. A `folder.dataFiles` entry with exact `name` → `INVALID_PARAMS`
   ("a file named <name> exists in <path>").
3. `doc.saveAs(name, folder, description, "")`; `False` → `FUSION_ERROR`.
4. `read_only` true: `guard.set_read_only(ctx, doc, True, lookup)` where `lookup` returns the
   `folder.dataFiles` entry with exact `name` or `None` (the new file's id is a local path until
   upload completes, so it is found by name); false → `READ_ONLY_NOT_APPLIED` ("saved <path> but
   it was not read-only within 10 tries, 1 s apart", hint "call document.set_read_only").
5. Entry result `{**_doc_summary(doc), "read_only": read_only}` (true only when observed).

On a document that was already saved this creates a new file and the open document then refers to
it. `save_as` declares `edits_document="none"`: the source document is not edited.

### 6.6 set_name
```python
def set_name(ctx, name: str, document: str | None = None) -> dict
```
not `guard.never_saved(doc)` → `INVALID_PARAMS` with hint `saved documents are renamed with
datafile_set`. `doc.name = name`. → `_doc_summary(doc)`.

### 6.7 advance_to_latest, update_all_references
```python
def advance_to_latest(ctx, document: str | None = None) -> dict
def update_all_references(ctx, document: str | None = None) -> dict
```
`guard.ensure_editable(ctx, doc)` (core design § 5.13), feature-detect, then call `doc.advanceToLatest()` /
`doc.updateAllReferences()`. The returned bool
is reported, not raised (a design with nothing to update can return `False`). Result:
`{**_doc_summary(doc), "updated": <bool>, "references_out_of_date": <count of
doc.documentReferences with isOutOfDate>}`.

### 6.8 set_read_only
```python
def set_read_only(ctx, documents: bulk(str) | None = None, read_only: bool = True) -> dict
```
Per `document` entry (§ 2.7):
1. `doc = _doc(ctx, document)`; `df = guard.saved_file(doc)`; `None` → `INVALID_PARAMS` ("save the
   document first, or wait for its upload").
2. `fresh = ctx.app.data.findFileById(df.id) or df`; a raising `findFileById` (file not yet
   listed right after a save) also falls back to `df`; `guard.check_folder(ctx, guard.folder_path(
   fresh.parentFolder))`.
3. `fresh.isReadOnly == read_only` → entry result `{**_doc_summary(doc), "read_only": read_only,
   "changed": false}`.
4. `guard.set_read_only(ctx, doc, read_only, lambda: ctx.app.data.findFileById(df.id))`
   (core design § 5.13: activate, Make Read-Only `ActiveDocumentDeactivateCommand` or Make Editable
   `ActiveDocumentActivateCommand`, re-activate the previous document, verify up to 10 × 1 s);
   false → `READ_ONLY_NOT_APPLIED` ("<name> is not <read-only|editable> within 10 tries, 1 s apart", hint "check
   document.datafile_get read_only; repeat the call").
5. Entry result `{**_doc_summary(doc), "read_only": read_only, "changed": true}`.

`doc.dataFile.isReadOnly` on the open document can read stale right after the change, so
verification always uses a fresh `findFileById`.

### 6.9 datafile_set, datafile_refresh
```python
def datafile_set(ctx, files: bulk(RenameEntry)) -> dict
def datafile_refresh(ctx, files: bulk(str)) -> dict
```
Per entry (§ 2.7). `datafile_set`: each entry is `{file, name}`; `name` is a required `FileName`
(core design § 5.4); an entry without it is `INVALID_PARAMS` (validation, whole call). Assign
`df.name` → `_file_props(df)`.
Live: `DataFile.description` has no setter, so a description is set only at `save`/`save_as`.
The `datafile_*` actions act on the cloud file, not on document content, so a read-only file is
renamed, refreshed, copied, moved or deleted without unlocking it (they declare
`edits_document="none"`); their live scenarios run on locked files (`_saved`).
`datafile_refresh`: feature-detect, `ok = df.refresh()`, → `{"refreshed": bool(ok), "file":
_file_props(df)}`.

### 6.10 datafile_copy, datafile_move
```python
def datafile_copy(ctx, files: bulk(str), folder: str) -> dict
def datafile_move(ctx, files: bulk(str), folder: str) -> dict
```
`target = _folder(ctx, folder)` once (call-level); then per `file` entry (§ 2.7). Copy: `new = df.copy(target)`; `None` → `FUSION_ERROR`; → `_file_props(new)`. Move:
`df.move(target)`; `False` → `FUSION_ERROR`; → `_file_props(df)` (re-read, now under `target`).

### 6.11 import
```python
ImportFormat = Literal["f3d", "step", "iges", "sat", "smt", "dxf", "svg"]
def import_(ctx, paths: bulk(str), format: ImportFormat | None = None,
            target: Literal["new_document", "active"] | None = None,
            component: Component | None = None, plane: Plane | None = None) -> dict
```
Steps 1–5 run per `path` entry of `paths` (§ 2.7); the other parameters are shared.
1. `path` per § 2.6 (must exist); format from extension (§ 9) when omitted; when given and the extension is not one of
   that format's extensions (§ 9) → `INVALID_PARAMS`, as export step 2.
2. `target` default: `new_document` for 3D formats, `active` for `dxf`/`svg`. `dxf`/`svg` with
   `new_document` → `INVALID_PARAMS` ("2D imports need an active design"). `component` or `plane`
   given with `new_document` → `INVALID_PARAMS`. `plane` given for a 3D format → `INVALID_PARAMS`.
3. `im = ctx.app.importManager`; options per § 9 (feature-detected). For `dxf`/`svg` the plane is
   `plane` resolved by core (§ 5.4) or the target component's `xYConstructionPlane`.
4. `new_document`: `doc = im.importToNewDocument(opts)`; `None` → `FUSION_ERROR`. Result
   `{"target": "new_document", "document": _doc_summary(doc), "created": []}`.
5. `active`: `ctx.require_design()`; `guard.ensure_editable(ctx, ctx.app.activeDocument)` (core design
   § 5.13); `comp = component or design.activeComponent`; record
   `comp.occurrences.count`, `comp.bRepBodies.count`, `comp.sketches.count`; `im.importToTarget(
   opts, comp)`; `False` → `FUSION_ERROR`. `created` = refs of the occurrences, bodies and sketches
   at indexes from the recorded counts to the new counts. Result `{"target": "active",
   "document": _doc_summary(ctx.app.activeDocument), "created": [...]}`.

### 6.12 import_mesh
```python
MeshFormat = Literal["stl", "obj", "3mf"]
MeshUnit = Literal["mm", "cm", "m", "in", "ft"]
def import_mesh(ctx, paths: bulk(str), unit: MeshUnit = "mm", format: MeshFormat | None = None,
                component: Component | None = None) -> dict
```
The pipeline guards the active document first (`edits_document="active"`). `design =
ctx.require_design()` and `comp` (step 2) are resolved once; steps 1 (path), 3 and 4 run per `path`
entry (§ 2.7).
1. `path` per § 2.6 (must exist; format only selects/validates the
   extension — `MeshBodies.add` reads the type from the file name).
2. `units` from § 9.2. `comp = component or design.activeComponent`.
3. Parametric design: `bf = comp.features.baseFeatures.add(); bf.startEdit()`; then
   `meshes = comp.meshBodies.add(str(path), units, bf)`; `bf.finishEdit()` in `finally`. Direct
   design: `meshes = comp.meshBodies.add(str(path), units)`; `bf = None`.
4. `meshes` is a `MeshBodyList` (`count`/`item(i)`); count 0 → `FUSION_ERROR` ("no mesh bodies
   imported").

Entry result: `{"meshes": [ctx.ref(m) ...], "base_feature": ctx.ref(bf) or null, "bbox": {"min": [x, y,
z], "max": [x, y, z]} or null}` where `bbox` is the union of `m.boundingBox` in output length units
(`ctx.length_out`), `null` if any mesh lacks `boundingBox`.

## 7. Destructive actions
### 7.1 close
```python
def close(ctx, documents: bulk(str) | None = None) -> dict
```
Per `document` entry (§ 2.7). Always `doc.close(False)` (never `close(True)`, which can show a modal save prompt; callers that
want to keep changes call `save` first). Record `modified = doc.isModified`, `summary =
_doc_summary(doc)` before. `False` → `FUSION_ERROR`. → `{"closed": summary, "discarded_changes":
modified}`. `confirm` is always required once per call (core design § 5.3), even for an unmodified document.

### 7.2 datafile_delete
```python
def datafile_delete(ctx, files: bulk(str)) -> dict
```
Per `file` entry (§ 2.7). An open document whose `dataFile.id == df.id` → `INVALID_PARAMS` ("close the document first").
`ref = _file_ref(df)`; `df.deleteMe()`; `False` → `FUSION_ERROR`. → `{"deleted": ref}`.

## 8. Export formats
### 8.1 Methods
| Format | Extensions | `ExportManager` method | `body` allowed |
|---|---|---|---|
| f3d | `.f3d` | `createFusionArchiveExportOptions` | no |
| step | `.step`, `.stp` | `createSTEPExportOptions` | no |
| iges | `.iges`, `.igs` | `createIGESExportOptions` | no |
| sat | `.sat` | `createSATExportOptions` | no |
| smt | `.smt` | `createSMTExportOptions` | no |
| stl | `.stl` | `createSTLExportOptions` | yes |
| 3mf | `.3mf` | `createC3MFExportOptions` | yes |
| obj | `.obj` | `createOBJExportOptions` | yes |
| usdz | `.usdz` | `createUSDExportOptions` | no |

IGES (and any other format the account's licence refuses; live: sat too) is licence-conditional: when `ExportManager.execute` raises a `RuntimeError` whose message
contains "restricted" (live: "...as the user is restricted"), `export` fails `UNSUPPORTED` ("<fmt>
export is not permitted by this Fusion licence", Fusion's message as a hint); the `export` live
scenario accepts that licence `UNSUPPORTED` for any format.

`createUSDExportOptions` exists only in recent builds and its accepted geometry is not documented
in the sources available here; it is feature-detected and proven by the `export_usdz` live
scenario. A Fusion build where it raises for a component geometry reports `FUSION_ERROR` with
Fusion's message.

### 8.2 Mesh options (stl, 3mf, obj)
| Param | Property | Value |
|---|---|---|
| `refinement` | `meshRefinement` | `adsk.fusion.MeshRefinementSettings.MeshRefinementLow` / `Medium` / `High` |
| `one_file_per_body` | `isOneFilePerBodyInOccurrence` | bool |
| (stl only) | `isBinaryFormat` | `True` |
| (all three) | `sendToPrintUtility` | `False` when the property exists |

A property missing on the options object: when the caller passed a non-default value for it →
`UNSUPPORTED`; otherwise it is skipped. `isBinaryFormat` and `sendToPrintUtility` are skipped when
missing.

## 9. Import formats
### 9.1 Options
| Format | Extensions | `ImportManager` call |
|---|---|---|
| f3d | `.f3d`, `.f3z` | `createFusionArchiveImportOptions(path)` |
| step | `.step`, `.stp` | `createSTEPImportOptions(path)` |
| iges | `.iges`, `.igs` | `createIGESImportOptions(path)` |
| sat | `.sat` | `createSATImportOptions(path)` |
| smt | `.smt` | `createSMTImportOptions(path)` |
| dxf | `.dxf` | `createDXF2DImportOptions(path, plane)` |
| svg | `.svg` | `createSVGImportOptions(path)`, then `opts.planarEntity = plane` |

### 9.2 Mesh units
`mm` → `adsk.fusion.MeshUnits.MillimeterMeshUnit`, `cm` → `CentimeterMeshUnit`, `m` →
`MeterMeshUnit`, `in` → `InchMeshUnit`, `ft` → `FootMeshUnit`. Mesh formats: `.stl`, `.obj`,
`.3mf`.

## 10. Testability
### 10.1 Unit (fake adsk)
`tests/test_document.py`. Extend `tests/fakes/adsk/core.py` and `tests/fakes/adsk/fusion.py` with:
`Documents` (`count`, `item`, `add`, `open`), `Document`/`FusionDocument` (`name`, `creationId`,
`isActive`, `isVisible`, `isModified`, `dataFile`, `objectType`, `products`, `activate`, `save`,
`saveAs`, `close`, `advanceToLatest`, `updateAllReferences`, `documentReferences`,
`allDocumentReferences`), `DocumentTypes`, `DocumentReference`, `Data` (`dataHubs`, `activeHub`,
`activeProject`, `findFileById`, `findFolderById`), `DataHub`, `DataProject`, `DataFolder`
(`dataFolders`, `dataFiles`, `parentFolder`, `parentProject`, `isRoot`), `DataFile` (all § 2.4
properties, `copy`, `move`, `refresh`, `deleteMe`), `ImportManager` (§ 9.1 methods,
`importToNewDocument`, `importToTarget`), `ExportManager` (§ 8.1 methods taking `filename`,
`geometry` keywords; `execute` writes a small file at `filename`), option objects with the § 8.2
properties, `MeshBodies.add`, `MeshBodyList`, `BaseFeatures.add`, `MeshUnits`,
`MeshRefinementSettings`, `DesignTypes`. Each fake method records its calls.

Tests call actions through the core pipeline (`actions.call("document", {...}, allowed,
output_units)`) and cover: every action's success result shape; § 2.1–2.3 selector errors
(`NO_DOCUMENT`, `NOT_FOUND`, `AMBIGUOUS` with candidates, id vs path); path segment message;
`CONFIRM_REQUIRED` for `close` and `datafile_delete`; every action-specific `INVALID_PARAMS` named
in § 5–7; § 4 `UNSUPPORTED` cases; `close` calls `close(False)`; `set_read_only` text commands and
re-activation; export keyword call, mesh options and `one_file_per_body` file listing (temp dir), existing `path` and existing per-body file → `INVALID_PARAMS` with files unchanged, `overwrite` → `INVALID_PARAMS`; `save_as`/`datafile_set` name `../x` → `INVALID_PARAMS`;
import target defaults and created-item diff; import_mesh base feature in parametric and none in
direct; `help` timeout per § 3; no `PTransaction` command recorded for any action.

List actions, guard and folder limit (with core `guard.VERIFY_DELAY` set to 0): for each of the 13
list actions, a 3-entry call with a bad middle entry gives `ok: 2, failed: 1` in entry order with
the middle entry's code, the singular parameter (`path`, `file`, `document`, top-level
`name`) → `INVALID_PARAMS`, 0 and 51 entries → `INVALID_PARAMS` with no Fusion call, and the
registered `edits_document` equals § 3; `documents` omitted acts on the active document;
`close`/`datafile_delete` without `confirm` → `CONFIRM_REQUIRED` with nothing closed or deleted;
a shared `folder` that does not resolve fails the call; `save_as` with a name repeated in the call
→ second entry `INVALID_PARAMS`; `save`/`save_as` issue `ActiveDocumentDeactivateCommand` after
the save and report `read_only: true` when the fake flips, `READ_ONLY_NOT_APPLIED` (version still
saved) when it never flips, and issue no command with `read_only: false`; `save` on a read-only
document issues `ActiveDocumentActivateCommand` before `save` and lists it in `made_editable`, and
with `preserve_read_only: true` the entry is `READ_ONLY` with no save; `set_read_only` uses the
core helper, re-activates the previous document and gives `READ_ONLY_NOT_APPLIED` when the fake
never flips; under folder limit `P/A`: `resolve_folder`, `list_folders`, `datafile_get`, `open`,
`datafile_*` on `P/X/...` → `NOT_ALLOWED` and on `P/A/B/...` → ok, `datafile_copy`/`move` to a
target outside → call-level `NOT_ALLOWED`, `save_as` without `folder` saves into `P/A` and with
`folder` `P/X` → `NOT_ALLOWED`, `save_as` without `folder` and no limit → `INVALID_PARAMS`, `save`
/`set_read_only`/`advance_to_latest` of a document saved in `P/X` → `NOT_ALLOWED`, `import`
`active`, `update_all_references` and `import_mesh` of a document saved in `P/X` → `NOT_ALLOWED`
while `new_document` succeeds; `advance_to_latest`, `update_all_references` and `import` `active`
with `preserve_read_only: true` on a read-only document → `READ_ONLY` with no Fusion call; and
`create`, `set_name`, `export` and `close` of never-saved documents succeed, while `save` of an
upload-pending document → `NOT_ALLOWED`.

### 10.2 Live
`LIVE_SCENARIOS` in `document.py`: list of core design § 10 tuples `(name, "tool.action",
async fn(session))`, calling actions with `live_check.call`, run by
`python scripts/live_check.py --only document[.<action>]`. Scenario names equal the action they
prove and are tagged `"document.<name>"`, except `export_usdz` (tagged `"document.export"`) and
`roundtrip` (tagged `"document.import"`), `bulk` (`"document.open"`), `save_locks`
(`"document.save"`), `auto_unlock` (`"sketch.create"`) and `folder_limit` (`"document.save_as"`). Cloud files are saved in the active project's root folder
(`list_projects` item with `active: true`; path = its name) under `defusion-live-<unix time>-<n>`
and each scenario deletes what it saved and closes what it opened in a `finally`. Local files go to
`tempfile.mkdtemp()`. Scenarios needing a B-Rep body call `live_check.box(session, 10, 10, 10)`
(core design § 10), a 10 mm cube in the active design.

Every call to a list action (§ 2.7) passes a one-entry list and reads `results[0]`, failing the scenario
unless `results[0].ok` is true (except where it expects an entry error); a step written below as "`open` the file" means
`open {"files": [<file>]}`. `_saved` (the helper giving a saved `defusion-live-*` document) relies
on `save_as` locking the file (§ 6.5) and still calls `set_read_only` true before closing it, so
every saved Fusion document ends read-only; scenarios that save again (`save`, `save_locks`) end
with the file read-only through `save`. Scenarios that edit a saved document rely on auto-unlock
(core design § 5.13). The run requires the config `folder` to be unset (core design § 10).

| Scenario | Steps and checks |
|---|---|
| `list_open` | `list_open` total ≥ 1, exactly one `active`. |
| `list_hubs` | ≥ 1 hub, one `active`. |
| `list_projects` | ≥ 1 project, each with `root_folder_id`; unknown `hub` → `NOT_FOUND`. |
| `resolve_folder` | resolve the active project name; `is_root` true; `<project>/no-such-<ts>` → `NOT_FOUND`. |
| `list_folders` | on the project root: every item has `kind`, `id`, `name`. |
| `create` | `create {"design_type": "direct"}` → `design_type` `direct`, active; close it (confirm). |
| `activate` | create a second doc, `activate` the scratch doc by `id`, `list_open` shows it active; close the second. |
| `set_name` | on a new unsaved doc → name changes; close it. |
| `save_as` | new doc, `save_as` to project root → `saved` true; repeat with same name → `INVALID_PARAMS`. |
| `save` | after `save_as`, `box`, `save {"description": "v2"}`; poll `datafile_get` by path up to 60 s for `version_number` ≥ 2. |
| `datafile_get` | same result by `id` and by `path`. |
| `close` | without confirm → `CONFIRM_REQUIRED`; with confirm → gone from `list_open`. |
| `open` | open the saved file by path → `already_open` false; again → true. |
| `set_read_only` | the file starts read-only (`save_as` locks it): `false` → `changed` true and `datafile_get` `read_only` false; `true` → `changed` true and `read_only` true; `true` again → `changed` false. |
| `datafile_set` | rename, `datafile_get` by the new path shows the name; no `name` → `INVALID_PARAMS`. |
| `datafile_refresh` | returns `file.id`. |
| `datafile_copy` | copy into the same folder → different `id`; delete the copy. |
| `datafile_move` | `save_as` a second file, move it to a subfolder of the project root found with `list_folders` (skip-PASS with reason `no subfolder` when none exists), check `folder_id`, move back. |
| `list_references` | scratch design → `total` 0. |
| `advance_to_latest` | on the scratch design → ok, `references_out_of_date` 0. |
| `update_all_references` | same. |
| `datafile_delete` | without confirm → `CONFIRM_REQUIRED`; with → `datafile_get` by id `NOT_FOUND`. |
| `export` | `box`; export stl, 3mf, obj, step, iges, sat (each may be `UNSUPPORTED` when the licence refuses it), smt, f3d → each file exists, size > 0; stl with `body` and `one_file_per_body` ok; relative path → `INVALID_PARAMS`. |
| `export_usdz` | export usdz → file exists, or `UNSUPPORTED` (both PASS; the line states which). |
| `import` | step from `export` → `new_document` with design; close it; same step `active` → `created` non-empty; generated DXF (one LINE entity, written by the scenario) `active` → one sketch created; dxf `new_document` → `INVALID_PARAMS`. |
| `import_mesh` | write an ASCII STL 10 mm cube at 0..10 to temp; `import_mesh` `unit` `mm` in the parametric scratch design and in a new direct design → `bbox` min `[0,0,0]`, max `[10,10,10]` ± 0.01; also export the cube to 3mf and obj and import each (or `UNSUPPORTED` for a format the build lacks). |
| `roundtrip` | f3d export of the box, `import` f3d `new_document`, close it. |
| `bulk` (tag `document.open`) | two `_saved` files A and B; `close {"documents": [A, B], "confirm": true}` → `ok: 2`; `open {"files": [<A path>, <B path>, "<project>/no-such-<ts>"]}` → `ok: 2, failed: 1`, entry 3 `NOT_FOUND`, entries in input order; `open {"files": []}` and 51 entries → `INVALID_PARAMS`; `open {"file": <A path>}` → `INVALID_PARAMS`; `datafile_get` A and B → `read_only` true. |
| `save_locks` (tag `document.save`) | `_saved`: `datafile_get` → `read_only` true right after `save_as`; `box` → envelope `made_editable` names the document, `datafile_get` `read_only` false; `save {"description": "v2"}` → entry `read_only` true and `datafile_get` `read_only` true; `box`, `save {"read_only": false}` → `datafile_get` `read_only` false. |
| `auto_unlock` (tag `sketch.create`) | `_saved` (locked): `sketch.create {"plane": "XY", "preserve_read_only": true}` → `READ_ONLY`, `datafile_get` still `read_only` true; `sketch.create {"plane": "XY"}` → ok with `made_editable` naming the document; `datafile_get` `read_only` false. |
| `folder_limit` (tag `document.save_as`) | session `open_session(folder=<project>)`: `create`, `save_as {"documents": [{"name": <unique>}]}` (no `folder`) → file path `<project>/<name>`, `read_only` true; clean it up as `_saved` does. Session `open_session(folder="<project>/defusion-live-none-<ts>")` with a `_saved` file F in the project root: `resolve_folder <project>` → `NOT_ALLOWED`; `datafile_get` F → `NOT_ALLOWED`; `open {"files": [F]}` → entry `NOT_ALLOWED`; `save_as` with `folder` `<project>` → `NOT_ALLOWED`; with F's document active, `sketch.create {"plane": "XY"}` → `NOT_ALLOWED`; `create` then `sketch.create {"plane": "XY"}` on the new unsaved document → ok; close it. |
