"""Open, save, close, import and export Fusion documents and manage cloud files.

Addressing: a document is its name or its ``id`` (creationId); a folder or file is an ``urn:`` id
or a path ``<project>/<folder>/.../<file name without extension>``.
"""
import asyncio
import contextlib
import itertools
import json
import pathlib
import tempfile
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .. import guard
from ..actions import action
from ..errors import (
    AMBIGUOUS,
    FUSION_ERROR,
    INVALID_PARAMS,
    NO_DOCUMENT,
    NOT_FOUND,
    READ_ONLY_NOT_APPLIED,
    UNSUPPORTED,
    ActionError,
)
from ..types import Body, Component, FileName, Plane, bulk
from .common import _items, _try

# --- shared helpers ----------------------------------------------------------------------------------


def _need(obj, member):
    if not hasattr(obj, member):
        raise ActionError(
            UNSUPPORTED,
            "%s.%s is not available in this Fusion build" % (type(obj).__name__, member),
        )




class SaveAsEntry(BaseModel):
    """One ``save_as`` entry: the document (default the active one) and the new file name."""

    model_config = ConfigDict(extra="forbid")
    document: str | None = None
    name: FileName


class RenameEntry(BaseModel):
    """One ``datafile_set`` entry: the file (id or path) and its new name."""

    model_config = ConfigDict(extra="forbid")
    file: str
    name: FileName


def _file_path(df):
    base = _try(lambda: guard.folder_path(df.parentFolder))
    return None if base is None else "%s/%s" % (base, df.name)


def _file_ref(df):
    fid = _try(lambda: df.id)
    return {
        "id": fid if isinstance(fid, str) and fid.startswith("urn:") else None,
        "name": df.name,
        "path": _file_path(df),
    }


def _doc_summary(doc):
    import adsk.fusion

    otype = doc.objectType
    kind = "design" if otype == "adsk::fusion::FusionDocument" else "drawing" if "Drawing" in otype else "other"
    design_type = None
    if kind == "design":
        design = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
        if design is not None:
            design_type = (
                "parametric" if design.designType == adsk.fusion.DesignTypes.ParametricDesignType else "direct"
            )
    try:
        df = doc.dataFile
    except RuntimeError:  # live: "Failed to get temporary file" while the cloud upload of a just-saved document runs
        df = None
    return {
        "name": doc.name,
        "id": doc.creationId,
        "active": bool(doc.isActive),
        "visible": bool(doc.isVisible),
        "saved": df is not None,
        "modified": bool(doc.isModified),
        "type": kind,
        "design_type": design_type,
        "file": None if df is None else _file_ref(df),
    }


def _folder_summary(f):
    return {"kind": "folder", "id": f.id, "name": f.name, "path": guard.folder_path(f)}


def _file_summary(df):
    return {
        "kind": "file",
        "id": df.id,
        "name": df.name,
        "extension": df.fileExtension,
        "version_number": df.versionNumber,
        "path": _file_path(df),
    }


def _file_props(df):
    get = {
        "id": lambda: df.id,
        "name": lambda: df.name,
        "extension": lambda: df.fileExtension,
        "description": lambda: df.description,
        "version_number": lambda: df.versionNumber,
        "latest_version_number": lambda: df.latestVersionNumber,
        "read_only": lambda: df.isReadOnly,
        "in_use": lambda: df.isInUse,
        "folder_id": lambda: df.parentFolder.id,
        "folder_name": lambda: df.parentFolder.name,
        "project_id": lambda: df.parentProject.id,
        "project_name": lambda: df.parentProject.name,
        "path": lambda: _file_path(df),
        "created_by": lambda: df.createdBy.displayName,
        "date_created": lambda: df.dateCreated,
        "date_modified": lambda: df.dateModified,
        "web_url": lambda: df.fusionWebURL,
    }
    return {k: _try(fn) for k, fn in get.items()}


def _doc(ctx, document=None):
    """Select an open document: active one, else exact name, else exact id."""
    if document is None:
        doc = ctx.app.activeDocument
        if doc is None:
            ctx.fail(NO_DOCUMENT, "No document is open")
        return doc
    docs = _items(ctx.app.documents)
    hits = [d for d in docs if d.name == document] or [d for d in docs if d.creationId == document]
    if not hits:
        ctx.fail(NOT_FOUND, "no open document %r" % document, ["call list_open"])
    if len(hits) > 1:
        raise ActionError(
            AMBIGUOUS,
            "%d open documents match %r" % (len(hits), document),
            ["pass the document id"],
            candidates=[_doc_summary(d) for d in hits],
        )
    return hits[0]


def _segments(v):
    segs = [s for s in v.strip().strip("/").split("/") if s]
    if not segs:
        raise ActionError(INVALID_PARAMS, "empty path")
    return segs


def _pick(ctx, matches, seg, so_far, summary):
    if not matches:
        ctx.fail(NOT_FOUND, "Path segment '%s' not found under '%s'" % (seg, so_far))
    if len(matches) > 1:
        raise ActionError(
            AMBIGUOUS,
            "%d entries named '%s' under '%s'" % (len(matches), seg, so_far),
            ["address it by id"],
            candidates=[summary(m) for m in matches],
        )
    return matches[0]


def _by_id(ctx, v, finder, what):
    found = None
    try:
        found = finder(v)
    except Exception:
        pass
    if found is None:
        ctx.fail(NOT_FOUND, "no %s with id %r" % (what, v))
    return found


def _folder(ctx, v):
    """A folder from an ``urn:`` id or a ``<project>/<folder>/...`` path, inside the folder limit."""
    if v.startswith("urn:"):
        cur = _by_id(ctx, v, ctx.app.data.findFolderById, "folder")
    else:
        segs = _segments(v)
        projects = _items(ctx.app.data.activeHub.dataProjects)
        proj = _pick(ctx, [p for p in projects if p.name == segs[0]], segs[0], "/",
                     lambda p: _folder_summary(p.rootFolder))
        cur, so_far = proj.rootFolder, segs[0]
        for seg in segs[1:]:
            cur = _pick(ctx, [f for f in _items(cur.dataFolders) if f.name == seg], seg, so_far, _folder_summary)
            so_far += "/" + seg
    guard.check_folder(ctx, guard.folder_path(cur))
    return cur


def _file(ctx, v):
    """A data file from an ``urn:`` id or a ``<project>/<folder>/.../<name>`` path, inside the folder limit."""
    if v.startswith("urn:"):
        df = _by_id(ctx, v, ctx.app.data.findFileById, "file")
    else:
        segs = _segments(v)
        if len(segs) < 2:
            raise ActionError(INVALID_PARAMS, "a file path is <project>/<folders>/<file name>")
        parent = _folder(ctx, "/".join(segs[:-1]))
        df = _pick(ctx, [f for f in _items(parent.dataFiles) if f.name == segs[-1]], segs[-1],
                   "/".join(segs[:-1]), _file_summary)
    guard.check_folder(ctx, guard.folder_path(df.parentFolder))
    return df


def _hub(ctx, hub=None):
    data = ctx.app.data
    if hub is None:
        return data.activeHub
    hubs = _items(data.dataHubs)
    hits = [h for h in hubs if h.id == hub] or [h for h in hubs if h.name == hub]
    if not hits:
        ctx.fail(NOT_FOUND, "no hub %r" % hub, ["call list_hubs"])
    if len(hits) > 1:
        raise ActionError(AMBIGUOUS, "%d hubs match %r" % (len(hits), hub), ["pass the hub id"],
                          candidates=[{"id": h.id, "name": h.name} for h in hits])
    return hits[0]


def _abs_path(p, must_exist):
    path = pathlib.Path(p).expanduser()
    if not path.is_absolute():
        raise ActionError(INVALID_PARAMS, "path must be absolute")
    if must_exist:
        if not path.is_file():
            raise ActionError(NOT_FOUND, "no file at %s" % path)
    elif not path.parent.is_dir():
        raise ActionError(NOT_FOUND, "directory %s does not exist" % path.parent)
    return path


# --- read actions --------------------------------------------------------------------------------------


@action("document", access="read", timeout=30, undo_group=False)
def list_hubs(ctx, offset: int = 0, limit: int = 100) -> dict:
    """List the Autodesk hubs available to the signed-in user."""
    data = ctx.app.data
    active = _try(lambda: data.activeHub.id)
    return ctx.page(_items(data.dataHubs), offset, limit,
                    lambda h: {"id": h.id, "name": h.name, "active": h.id == active})


@action("document", access="read", timeout=60, undo_group=False)
def list_projects(ctx, hub: str | None = None, offset: int = 0, limit: int = 100) -> dict:
    """List the projects of a hub (default: the active hub)."""
    active = _try(lambda: ctx.app.data.activeProject.id)
    return ctx.page(
        _items(_hub(ctx, hub).dataProjects), offset, limit,
        lambda p: {"id": p.id, "name": p.name, "root_folder_id": p.rootFolder.id,
                   "active": active is not None and p.id == active},
    )


@action("document", access="read", timeout=60, undo_group=False)
def list_folders(ctx, folder: str, files: bool = True, offset: int = 0, limit: int = 100) -> dict:
    """List the sub-folders and files of a folder (an id or a path like "Project/Folder")."""
    f = _folder(ctx, folder)
    entries = [_folder_summary(x) for x in _items(f.dataFolders)]
    if files:
        entries += [_file_summary(x) for x in _items(f.dataFiles)]
    return ctx.page(entries, offset, limit)


@action("document", access="read", timeout=60, undo_group=False)
def resolve_folder(ctx, folder: str) -> dict:
    """Resolve a folder id or path to its id, path and project."""
    f = _folder(ctx, folder)
    return {**_folder_summary(f), "project_id": f.parentProject.id,
            "project_name": f.parentProject.name, "is_root": f.isRoot}


@action("document", access="read", timeout=30, undo_group=False)
def datafile_get(ctx, file: str) -> dict:
    """Get the properties of a cloud file (an id or a path like "Project/Folder/Name")."""
    return _file_props(_file(ctx, file))


@action("document", access="read", timeout=30, undo_group=False)
def list_open(ctx, offset: int = 0, limit: int = 100) -> dict:
    """List the open documents."""
    return ctx.page(_items(ctx.app.documents), offset, limit, _doc_summary)


@action("document", access="read", timeout=60, undo_group=False)
def list_references(ctx, document: str | None = None, nested: bool = False,
                    offset: int = 0, limit: int = 100) -> dict:
    """List the documents an open document references (nested: including indirect ones)."""
    doc = _doc(ctx, document)
    if nested:
        _need(doc, "allDocumentReferences")
    refs = _items(doc.allDocumentReferences if nested else doc.documentReferences)
    return ctx.page(refs, offset, limit, lambda r: {
        "file": _file_ref(r.dataFile), "out_of_date": r.isOutOfDate,
        "version_number": r.version, "open": r.referencedDocument is not None})


ExportFormat = Literal["f3d", "step", "iges", "sat", "smt", "stl", "3mf", "obj", "usdz"]
MeshRefinement = Literal["low", "medium", "high"]

# format -> (extensions, ExportManager method, mesh options, body allowed)
_EXPORT = {
    "f3d": (("f3d",), "createFusionArchiveExportOptions", False),
    "step": (("step", "stp"), "createSTEPExportOptions", False),
    "iges": (("iges", "igs"), "createIGESExportOptions", False),
    "sat": (("sat",), "createSATExportOptions", False),
    "smt": (("smt",), "createSMTExportOptions", False),
    "stl": (("stl",), "createSTLExportOptions", True),
    "3mf": (("3mf",), "createC3MFExportOptions", True),
    "obj": (("obj",), "createOBJExportOptions", True),
    "usdz": (("usdz",), "createUSDExportOptions", False),
}


def _format_for(path, fmt, table):
    """The format named by ``fmt`` or by the extension of ``path``; ``INVALID_PARAMS`` if they disagree."""
    ext = path.suffix.lower().lstrip(".")
    if fmt is None:
        for name, entry in table.items():
            if ext in entry[0]:
                return name
        accepted = sorted({e for entry in table.values() for e in entry[0]})
        raise ActionError(INVALID_PARAMS, "unknown extension %r" % path.suffix,
                          ["accepted: " + ", ".join(accepted)])
    if ext not in table[fmt][0]:
        raise ActionError(INVALID_PARAMS, "%s files end in %s, not %r" % (
            fmt, " or ".join("." + e for e in table[fmt][0]), path.suffix))
    return fmt


def _set_mesh_option(opts, prop, value, default):
    if hasattr(opts, prop):
        setattr(opts, prop, value)
    elif value != default:
        raise ActionError(UNSUPPORTED, "%s.%s is not available in this Fusion build" % (type(opts).__name__, prop))


@action("document", access="read", timeout=120, undo_group=False)
def export(ctx, paths: bulk(str), format: ExportFormat | None = None, component: Component | None = None,
           body: Body | None = None, refinement: MeshRefinement = "medium",
           one_file_per_body: bool = False) -> dict:
    """Export the design (or one component, or one body for mesh formats) to new local files.

    paths: 1 to 50 absolute output paths, one export per entry, run in order; an existing file is
    never replaced. Shared by every entry: format, component, body, refinement, one_file_per_body.
    Returns {"results": [{"input", "ok", "result" | "error"}], "ok": n, "failed": n}; each result is
    {"format", "files", "bytes", "geometry"}.
    """
    design = ctx.require_design()
    if component is not None and body is not None:
        ctx.fail(INVALID_PARAMS, "give component or body, not both")
    return ctx.bulk(paths, lambda path: _export_one(ctx, design, path, format, component, body, refinement,
                                                    one_file_per_body))


def _export_one(ctx, design, path, format, component, body, refinement, one_file_per_body):
    import adsk.fusion

    out = _abs_path(path, False)
    fmt = _format_for(out, format, _EXPORT)
    exts, method, mesh = _EXPORT[fmt]
    suffix = out.suffix
    if out.exists():
        ctx.fail(INVALID_PARAMS, "file exists: %s" % out)

    def listing():
        return sorted(str(p) for p in out.parent.iterdir()
                      if p.name.startswith(out.stem) and p.name.endswith(suffix))

    if one_file_per_body and listing():
        ctx.fail(INVALID_PARAMS, "files exist: %s" % ", ".join(listing()))
    geometry = body or component or design.rootComponent
    if body is not None and not mesh:
        ctx.fail(INVALID_PARAMS, "body export needs stl, 3mf or obj; pass its component")
    em = design.exportManager
    _need(em, method)
    # Fusion's binding accepts no keywords; the mesh formats take (geometry, filename), the others (filename, geometry)
    opts = getattr(em, method)(geometry, str(out)) if mesh else getattr(em, method)(str(out), geometry)
    if mesh:
        levels = {"low": adsk.fusion.MeshRefinementSettings.MeshRefinementLow,
                  "medium": adsk.fusion.MeshRefinementSettings.MeshRefinementMedium,
                  "high": adsk.fusion.MeshRefinementSettings.MeshRefinementHigh}
        _set_mesh_option(opts, "meshRefinement", levels[refinement], levels["medium"])
        _set_mesh_option(opts, "isOneFilePerBodyInOccurrence", one_file_per_body, False)
        if fmt == "stl" and hasattr(opts, "isBinaryFormat"):
            opts.isBinaryFormat = True
        if hasattr(opts, "sendToPrintUtility"):
            opts.sendToPrintUtility = False
    try:
        done = em.execute(opts)
    except RuntimeError as exc:
        if "restricted" in str(exc):  # live: IGES (and other formats) are refused for licence reasons
            ctx.fail(UNSUPPORTED, "%s export is not permitted by this Fusion licence" % fmt,
                     ["Fusion said: %s" % exc])
        raise
    if not done:
        ctx.fail(FUSION_ERROR, "export failed")
    files = listing() if one_file_per_body else ([str(out)] if out.exists() else [])
    if not files:
        ctx.fail(FUSION_ERROR, "export wrote no file")
    return {"format": fmt, "files": files, "bytes": sum(pathlib.Path(f).stat().st_size for f in files),
            "geometry": ctx.ref(geometry)}


# --- write actions ---------------------------------------------------------------------------------------


@action("document", access="write", timeout=60, undo_group=False, edits_document="none")
def create(ctx, design_type: Literal["parametric", "direct"] | None = None) -> dict:
    """Create a new design document and make it active."""
    import adsk.core
    import adsk.fusion

    doc = ctx.app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    if doc is None:
        ctx.fail(FUSION_ERROR, "Fusion did not create a document")
    if design_type is not None:
        design = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
        design.designType = (adsk.fusion.DesignTypes.ParametricDesignType if design_type == "parametric"
                             else adsk.fusion.DesignTypes.DirectDesignType)
    return _doc_summary(doc)


@action("document", name="open", access="write", timeout=180, undo_group=False,
        edits_document="none")
def open_(ctx, files: bulk(str)) -> dict:
    """Open cloud files as documents (already-open files are just activated).

    files: 1 to 50 cloud file ids or paths like "Project/Folder/Name", opened in order. No shared
    parameters. Returns {"results": [{"input", "ok", "result" | "error"}], "ok": n, "failed": n};
    each result is the document summary plus "already_open".
    """
    return ctx.bulk(files, lambda file: _open_one(ctx, file))


def _open_one(ctx, file):
    df = _file(ctx, file)
    for d in _items(ctx.app.documents):
        if d.dataFile is not None and d.dataFile.id == df.id:
            d.activate()
            return {**_doc_summary(d), "already_open": True}
    doc = ctx.app.documents.open(df, True)
    if doc is None:
        ctx.fail(FUSION_ERROR, "Fusion could not open %s" % df.name)
    return {**_doc_summary(doc), "already_open": False}


@action("document", access="write", timeout=30, undo_group=False, edits_document="none")
def activate(ctx, document: str) -> dict:
    """Make an open document the active one."""
    doc = _doc(ctx, document)
    if not doc.activate():
        ctx.fail(FUSION_ERROR, "could not activate %s" % doc.name)
    return _doc_summary(doc)


@action("document", access="write", timeout=30, undo_group=False, edits_document="none")
def set_name(ctx, name: str, document: str | None = None) -> dict:
    """Name a document that has never been saved."""
    doc = _doc(ctx, document)
    if doc.dataFile is not None:
        ctx.fail(INVALID_PARAMS, "%s is saved" % doc.name, ["saved documents are renamed with datafile_set"])
    doc.name = name
    return _doc_summary(doc)


@action("document", access="write", timeout=180, undo_group=False, edits_document="action")
def save(ctx, documents: bulk(str) | None = None, description: str = "", read_only: bool = True) -> dict:
    """Save documents as new versions of their cloud files (never-saved documents need save_as).

    documents: 1 to 50 open document names or ids, saved in order; omitted means the active
    document. Shared by every entry: description, read_only. A read-only document is made editable
    before saving unless preserve_read_only is true. read_only (default true) makes each saved file
    read-only again and verifies it (up to 10 tries, 1 s apart, per entry); false leaves it
    editable. Returns {"results": [{"input", "ok", "result" | "error"}], "ok": n, "failed": n}; each
    result is the document summary plus "read_only".
    """

    def one(document):
        doc = _doc(ctx, document)
        df = guard.saved_file(doc)
        if df is None:
            if guard.never_saved(doc):
                ctx.fail(INVALID_PARAMS, "%s has never been saved" % doc.name, ["never saved; use save_as"])
            guard.check_folder(ctx, None)  # upload pending: NOT_ALLOWED under a folder limit
            ctx.fail(INVALID_PARAMS, "%s is still uploading; wait for its upload" % doc.name)
        fid = df.id  # captured: dataFile.id is a local path again until the new version is uploaded
        guard.ensure_editable(ctx, doc)
        if not doc.save(description):
            ctx.fail(FUSION_ERROR, "save failed for %s" % doc.name)
        if read_only and not guard.set_read_only(ctx, doc, True, lambda: ctx.app.data.findFileById(fid)):
            ctx.fail(READ_ONLY_NOT_APPLIED,
                     "saved %s but it was not read-only within 10 tries, 1 s apart" % doc.name,
                     ["call document.set_read_only"])
        return {**_doc_summary(doc), "read_only": read_only}

    return ctx.bulk(documents or [None], one)


@action("document", access="write", timeout=180, undo_group=False, edits_document="none")
def save_as(ctx, documents: bulk(SaveAsEntry), folder: str | None = None, description: str = "",
            read_only: bool = True) -> dict:
    """Save documents as new cloud files in a folder (an id or a path like "Project/Folder").

    documents: 1 to 50 entries {"document": <name or id, default the active document>, "name": <new
    file name>}, saved in order; each name may appear once per call. Shared by every entry: folder,
    description, read_only. folder defaults to the folder limit when one is set, and is required
    otherwise. read_only (default true) makes each new file read-only and verifies it (up to 10
    tries, 1 s apart, per entry). Returns {"results": [{"input", "ok", "result" | "error"}], "ok": n,
    "failed": n}; each result is the document summary plus "read_only".
    """
    if folder is None:
        folder = ctx.folder_limit
        if folder is None:
            ctx.fail(INVALID_PARAMS, "folder is required when no folder limit is set")
    target = _folder(ctx, folder)
    names = []

    def one(entry):
        name = entry.name
        if name in names:
            ctx.fail(INVALID_PARAMS, "%s appears twice in this call" % name)
        names.append(name)
        doc = _doc(ctx, entry.document)
        if any(f.name == name for f in _items(target.dataFiles)):
            ctx.fail(INVALID_PARAMS, "a file named %s exists in %s" % (name, guard.folder_path(target)))
        if not doc.saveAs(name, target, description, ""):
            ctx.fail(FUSION_ERROR, "save as failed for %s" % doc.name)

        def lookup():  # the new file's id is a local path until its upload completes
            return next((f for f in _items(target.dataFiles) if f.name == name), None)

        if read_only and not guard.set_read_only(ctx, doc, True, lookup):
            ctx.fail(READ_ONLY_NOT_APPLIED,
                     "saved %s/%s but it was not read-only within 10 tries, 1 s apart" % (guard.folder_path(target), name),
                     ["call document.set_read_only"])
        return {**_doc_summary(doc), "read_only": read_only}

    return ctx.bulk(documents, one)


def _update_references(ctx, document, member):
    doc = _doc(ctx, document)
    guard.ensure_editable(ctx, doc)
    _need(doc, member)
    try:
        updated = bool(getattr(doc, member)())
    except RuntimeError:  # Fusion raises instead of returning False when there is nothing to advance
        updated = False
    stale = sum(1 for r in _items(doc.documentReferences) if r.isOutOfDate)
    return {**_doc_summary(doc), "updated": updated, "references_out_of_date": stale}


@action("document", access="write", timeout=180, undo_group=False, edits_document="action")
def advance_to_latest(ctx, document: str | None = None) -> dict:
    """Advance a document to the latest version of its cloud file."""
    return _update_references(ctx, document, "advanceToLatest")


@action("document", access="write", timeout=180, undo_group=False, edits_document="action")
def update_all_references(ctx, document: str | None = None) -> dict:
    """Update every out-of-date reference of a document to its latest version."""
    return _update_references(ctx, document, "updateAllReferences")


@action("document", access="write", timeout=60, undo_group=False, edits_document="none")
def set_read_only(ctx, documents: bulk(str) | None = None, read_only: bool = True) -> dict:
    """Make saved documents read-only, or editable again.

    documents: 1 to 50 open document names or ids, in order; omitted means the active document.
    Shared by every entry: read_only. Each change is verified on the cloud file up to 10 tries, 1 s
    apart, so the worst case is about 10 s per entry. Returns {"results": [{"input", "ok", "result" |
    "error"}], "ok": n, "failed": n}; each result is the document summary plus "read_only" and
    "changed".
    """

    def one(document):
        doc = _doc(ctx, document)
        df = guard.saved_file(doc)
        if df is None:
            ctx.fail(INVALID_PARAMS, "save the document first, or wait for its upload")
        fresh = guard.fresh_file(ctx, df)
        guard.check_folder(ctx, guard.folder_path(fresh.parentFolder))
        if fresh.isReadOnly == read_only:
            return {**_doc_summary(doc), "read_only": read_only, "changed": False}
        if not guard.set_read_only(ctx, doc, read_only, lambda: ctx.app.data.findFileById(df.id)):
            ctx.fail(READ_ONLY_NOT_APPLIED, "%s is not %s within 10 tries, 1 s apart" % (
                doc.name, "read-only" if read_only else "editable"),
                ["check document.datafile_get read_only; repeat the call"])
        return {**_doc_summary(doc), "read_only": read_only, "changed": True}

    return ctx.bulk(documents or [None], one)


@action("document", access="write", timeout=60, undo_group=False, edits_document="none")
def datafile_set(ctx, files: bulk(RenameEntry)) -> dict:
    """Rename cloud files (Fusion's DataFile.description is read-only; set it with save/save_as).

    files: 1 to 50 entries {"file": <id or path>, "name": <new name>}, renamed in order. No shared
    parameters. Returns {"results": [{"input", "ok", "result" | "error"}], "ok": n, "failed": n};
    each result is the file's properties.
    """

    def one(entry):
        df = _file(ctx, entry.file)
        df.name = entry.name
        return _file_props(df)

    return ctx.bulk(files, one)


@action("document", access="write", timeout=60, undo_group=False, edits_document="none")
def datafile_refresh(ctx, files: bulk(str)) -> dict:
    """Refresh cloud files' properties from the server.

    files: 1 to 50 cloud file ids or paths, refreshed in order. No shared parameters. Returns
    {"results": [{"input", "ok", "result" | "error"}], "ok": n, "failed": n}; each result is
    {"refreshed", "file"}.
    """

    def one(file):
        df = _file(ctx, file)
        _need(df, "refresh")
        refreshed = bool(df.refresh(None))  # None: synchronous
        return {"refreshed": refreshed, "file": _file_props(df)}

    return ctx.bulk(files, one)


@action("document", access="write", timeout=120, undo_group=False, edits_document="none")
def datafile_copy(ctx, files: bulk(str), folder: str) -> dict:
    """Copy cloud files into a folder (an id or a path like "Project/Folder").

    files: 1 to 50 cloud file ids or paths, copied in order. Shared: folder, resolved once (an error
    there fails the whole call). Returns {"results": [{"input", "ok", "result" | "error"}], "ok": n,
    "failed": n}; each result is the new file's properties.
    """
    target = _folder(ctx, folder)

    def one(file):
        df = _file(ctx, file)
        _need(df, "copy")
        new = df.copy(target)
        if new is None:
            ctx.fail(FUSION_ERROR, "copy failed for %s" % df.name)
        return _file_props(new)

    return ctx.bulk(files, one)


@action("document", access="write", timeout=120, undo_group=False, edits_document="none")
def datafile_move(ctx, files: bulk(str), folder: str) -> dict:
    """Move cloud files into a folder (an id or a path like "Project/Folder").

    files: 1 to 50 cloud file ids or paths, moved in order. Shared: folder, resolved once (an error
    there fails the whole call). Returns {"results": [{"input", "ok", "result" | "error"}], "ok": n,
    "failed": n}; each result is the moved file's properties.
    """
    target = _folder(ctx, folder)

    def one(file):
        df = _file(ctx, file)
        _need(df, "move")
        if not df.move(target):
            ctx.fail(FUSION_ERROR, "move failed for %s" % df.name)
        return _file_props(df)

    return ctx.bulk(files, one)


ImportFormat = Literal["f3d", "step", "iges", "sat", "smt", "dxf", "svg"]
MeshFormat = Literal["stl", "obj", "3mf"]
MeshUnit = Literal["mm", "cm", "m", "in", "ft"]

# format -> (extensions, ImportManager method, is 2D)
_IMPORT = {
    "f3d": (("f3d", "f3z"), "createFusionArchiveImportOptions", False),
    "step": (("step", "stp"), "createSTEPImportOptions", False),
    "iges": (("iges", "igs"), "createIGESImportOptions", False),
    "sat": (("sat",), "createSATImportOptions", False),
    "smt": (("smt",), "createSMTImportOptions", False),
    "dxf": (("dxf",), "createDXF2DImportOptions", True),
    "svg": (("svg",), "createSVGImportOptions", True),
}
_MESH = {"stl": (("stl",),), "obj": (("obj",),), "3mf": (("3mf",),)}
_MESH_UNITS = {"mm": "MillimeterMeshUnit", "cm": "CentimeterMeshUnit", "m": "MeterMeshUnit",
               "in": "InchMeshUnit", "ft": "FootMeshUnit"}


@action("document", name="import", access="write", timeout=300, undo_group=False,
        edits_document="action")
def import_(ctx, paths: bulk(str), format: ImportFormat | None = None,
            target: Literal["new_document", "active"] | None = None,
            component: Component | None = None, plane: Plane | None = None) -> dict:
    """Import local CAD files (3D into a new document or the active design; dxf/svg into the active design).

    paths: 1 to 50 absolute paths of existing files, imported in order. Shared by every entry:
    format, target, component, plane. Returns {"results": [{"input", "ok", "result" | "error"}],
    "ok": n, "failed": n}; each result is {"target", "document", "created"}.
    """
    return ctx.bulk(paths, lambda path: _import_one(ctx, path, format, target, component, plane))


def _import_one(ctx, path, format, target, component, plane):
    src = _abs_path(path, True)
    fmt = _format_for(src, format, _IMPORT)
    _, method, flat = _IMPORT[fmt]
    if target is None:
        target = "active" if flat else "new_document"
    if flat and target == "new_document":
        ctx.fail(INVALID_PARAMS, "2D imports need an active design")
    if target == "new_document" and (component is not None or plane is not None):
        ctx.fail(INVALID_PARAMS, "component and plane apply only to target active")
    if plane is not None and not flat:
        ctx.fail(INVALID_PARAMS, "plane applies only to dxf and svg")
    im = ctx.app.importManager
    _need(im, method)
    comp = None
    if target == "active":
        design = ctx.require_design()
        guard.ensure_editable(ctx, ctx.app.activeDocument)
        comp = component or design.activeComponent
        _need(im, "importToTarget")
    if flat:
        plane = plane or comp.xYConstructionPlane
        if fmt == "dxf":
            opts = im.createDXF2DImportOptions(str(src), plane)
        else:
            opts = im.createSVGImportOptions(str(src))
            opts.planarEntity = plane
    else:
        opts = getattr(im, method)(str(src))
    if target == "new_document":
        doc = im.importToNewDocument(opts)
        if doc is None:
            ctx.fail(FUSION_ERROR, "import failed")
        return {"target": "new_document", "document": _doc_summary(doc), "created": []}
    names = ("occurrences", "bRepBodies", "sketches")
    before = [getattr(comp, n).count for n in names]
    if not im.importToTarget(opts, comp):
        ctx.fail(FUSION_ERROR, "import failed")
    created = []
    for n, first in zip(names, before):
        coll = getattr(comp, n)
        created += [ctx.ref(coll.item(i)) for i in range(first, coll.count)]
    return {"target": "active", "document": _doc_summary(ctx.app.activeDocument), "created": created}


@action("document", access="write", timeout=180, undo_group=False, edits_document="active")
def import_mesh(ctx, paths: bulk(str), unit: MeshUnit = "mm", format: MeshFormat | None = None,
                component: Component | None = None) -> dict:
    """Import stl, obj or 3mf files as mesh bodies into the active design.

    paths: 1 to 50 absolute paths of existing mesh files, imported in order. Shared by every entry:
    unit, format, component. Returns {"results": [{"input", "ok", "result" | "error"}], "ok": n,
    "failed": n}; each result is {"meshes", "base_feature", "bbox"}.
    """
    import adsk.fusion

    design = ctx.require_design()
    units = getattr(adsk.fusion.MeshUnits, _MESH_UNITS[unit])
    comp = component or design.activeComponent
    return ctx.bulk(paths, lambda path: _import_mesh_one(ctx, design, comp, units, path, format))


def _import_mesh_one(ctx, design, comp, units, path, format):
    import adsk.fusion

    src = _abs_path(path, True)
    _format_for(src, format, _MESH)
    _need(comp.meshBodies, "add")
    bf = None
    if design.designType == adsk.fusion.DesignTypes.ParametricDesignType:
        bf = comp.features.baseFeatures.add()
        bf.startEdit()
        try:
            meshes = comp.meshBodies.add(str(src), units, bf)
        finally:
            bf.finishEdit()
    else:
        meshes = comp.meshBodies.add(str(src), units)
    bodies = [meshes.item(i) for i in range(meshes.count)]
    if not bodies:
        ctx.fail(FUSION_ERROR, "no mesh bodies imported")
    boxes = [_try(lambda m=m: m.boundingBox) for m in bodies]
    bbox = None
    if all(b is not None for b in boxes):
        lo = [ctx.length_out(min(getattr(b.minPoint, a) for b in boxes)) for a in "xyz"]
        hi = [ctx.length_out(max(getattr(b.maxPoint, a) for b in boxes)) for a in "xyz"]
        bbox = {"min": lo, "max": hi}
    return {"meshes": ctx.refs(bodies), "base_feature": None if bf is None else ctx.ref(bf), "bbox": bbox}


# --- destructive actions ---------------------------------------------------------------------------------


@action("document", access="destructive", timeout=60, undo_group=False, edits_document="none")
def close(ctx, documents: bulk(str) | None = None) -> dict:
    """Close documents, discarding unsaved changes (call save first to keep them).

    documents: 1 to 50 open document names or ids, closed in order; omitted means the active
    document. No shared parameters; confirm is needed once per call. Returns {"results": [{"input",
    "ok", "result" | "error"}], "ok": n, "failed": n}; each result is {"closed", "discarded_changes"}.
    """

    def one(document):
        doc = _doc(ctx, document)
        modified, summary = bool(doc.isModified), _doc_summary(doc)
        if not doc.close(False):
            ctx.fail(FUSION_ERROR, "could not close %s" % doc.name)
        return {"closed": summary, "discarded_changes": modified}

    return ctx.bulk(documents or [None], one)


@action("document", access="destructive", timeout=60, undo_group=False, edits_document="none")
def datafile_delete(ctx, files: bulk(str)) -> dict:
    """Delete cloud files.

    files: 1 to 50 cloud file ids or paths, deleted in order. No shared parameters; confirm is
    needed once per call. Returns {"results": [{"input", "ok", "result" | "error"}], "ok": n,
    "failed": n}; each result is {"deleted"}.
    """

    def one(file):
        df = _file(ctx, file)
        if any(d.dataFile is not None and d.dataFile.id == df.id for d in _items(ctx.app.documents)):
            ctx.fail(INVALID_PARAMS, "%s is open" % df.name, ["close the document first"])
        ref = _file_ref(df)
        if not df.deleteMe():
            ctx.fail(FUSION_ERROR, "delete failed for %s" % df.name)
        return {"deleted": ref}

    return ctx.bulk(files, one)


# --- live scenarios (design § 10.2) ----------------------------------------------------------------------

_seq = itertools.count(1)


from .live_common import _lc  # noqa: E402


async def _env(session, act, params):
    return await _lc().call(session, "document", act, params)


async def _d(session, act, params):
    env = await _env(session, act, params)
    assert env["ok"], "document.%s: %s" % (act, json.dumps(env.get("error"))[:400])
    return env["result"]


async def _err(session, act, params, code):
    env = await _env(session, act, params)
    assert not env["ok"] and env["error"]["code"] == code, "document.%s expected %s, got %s" % (
        act, code, json.dumps(env)[:300])


def _first(env, act):
    """``results[0]`` of a one-entry list call (§ 2.7), asserting the envelope is ok."""
    assert env["ok"], "document.%s: %s" % (act, json.dumps(env.get("error"))[:400])
    return env["result"]["results"][0]


async def _entry(session, act, params):
    """``results[0]`` record of a one-entry list call: ``{ok, result}`` or ``{ok: false, error}``."""
    return _first(await _env(session, act, params), act)


async def _one(session, act, params):
    """The result of a one-entry list call; fails unless ``results[0].ok``."""
    first = await _entry(session, act, params)
    assert first["ok"], "document.%s entry: %s" % (act, json.dumps(first.get("error"))[:400])
    return first["result"]


async def _eerr(session, act, params, code):
    first = await _entry(session, act, params)
    assert not first["ok"] and first["error"]["code"] == code, "document.%s entry expected %s, got %s" % (
        act, code, json.dumps(first)[:300])


async def _close_quiet(session, doc_id):
    await _env(session, "close", {"documents": [doc_id], "confirm": True})


async def _project(session):
    active = [p for p in (await _d(session, "list_projects", {}))["items"] if p["active"]]
    assert active, "no active project"
    return active[0]["name"]


async def _active_id(session):
    return [d for d in (await _d(session, "list_open", {}))["items"] if d["active"]][0]["id"]


def _unique():
    return "defusion-live-%d-%d" % (time.time(), next(_seq))


async def _uploaded(session, name, seconds=90):
    """Wait until the saved document ``name`` has its cloud ``urn:`` id and its path resolves to one file.

    Saving uploads in the background; until it ends the folder also lists a local pending copy (AMBIGUOUS by path)."""
    end = time.monotonic() + seconds
    while True:
        env = await _env(session, "list_open", {})
        if env["ok"]:
            mine = [d for d in env["result"]["items"] if d["name"] == name]
            if mine and mine[0]["file"] and mine[0]["file"]["id"] and mine[0]["file"]["path"]:
                found = await _env(session, "datafile_get", {"file": mine[0]["file"]["id"]})
                by_path = await _env(session, "datafile_get", {"file": mine[0]["file"]["path"]})
                if found["ok"] and by_path["ok"]:
                    return
        assert time.monotonic() < end, "document %s was not uploaded within %d s" % (name, seconds)
        await asyncio.sleep(1)


async def _gone(session, name, seconds=30):
    """Wait until ``name`` is no longer open; deleting the file of a still-listed document breaks ``dataFile``."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        env = await _env(session, "list_open", {})
        if env["ok"] and name not in [d["name"] for d in env["result"]["items"]]:
            return
        await asyncio.sleep(1)


async def _freeze(session, doc):
    """Mark the saved document read-only once its scenario is done editing it (Personal accounts cap editable documents)."""
    await _env(session, "set_read_only", {"read_only": True, "documents": [doc]})


async def _discard(session, project, name, scratch):
    """Freeze, close and delete the saved document ``name``, then re-activate ``scratch``."""
    await _freeze(session, name)  # read-only before close; deleting a read-only file needs no unfreeze
    await _close_quiet(session, name)
    await _gone(session, name)
    await _env(session, "datafile_delete", {"files": ["%s/%s" % (project, name)], "confirm": True})
    await _env(session, "activate", {"document": scratch})
    await asyncio.sleep(5)  # Fusion fails the next save_as ("Failed to get temporary file") right after a delete


@contextlib.asynccontextmanager
async def _saved(session):
    """A new document saved (and locked by save_as) to the active project's root; yields (doc id, project, name). Cleans up."""
    scratch = await _active_id(session)
    project, name = await _project(session), _unique()
    await _d(session, "create", {})
    try:
        await _one(session, "save_as", {"documents": [{"name": name}], "folder": project})
        await _uploaded(session, name)
        yield name, project, name
    finally:
        await _discard(session, project, name, scratch)


async def _poll(session, path, field, want, seconds):
    """Poll ``datafile_get`` until ``want(field)``; a FUSION_ERROR is retried (Fusion fails to list the folder while a save uploads)."""
    end = time.monotonic() + seconds
    while True:
        env = await _env(session, "datafile_get", {"file": path})
        retry = not env["ok"] and env["error"]["code"] == "FUSION_ERROR" and time.monotonic() <= end
        assert env["ok"] or retry, "document.datafile_get: %s" % json.dumps(env.get("error"))[:400]
        got = env["result"][field] if env["ok"] else None
        if (env["ok"] and want(got)) or time.monotonic() > end:
            return got
        await asyncio.sleep(1)


async def _list_open(session):
    r = await _d(session, "list_open", {})
    assert r["total"] >= 1 and sum(1 for d in r["items"] if d["active"]) == 1, r


async def _list_hubs(session):
    r = await _d(session, "list_hubs", {})
    assert r["total"] >= 1 and sum(1 for h in r["items"] if h["active"]) == 1, r


async def _list_projects(session):
    r = await _d(session, "list_projects", {})
    assert r["total"] >= 1 and all(p["root_folder_id"] for p in r["items"]), r
    await _err(session, "list_projects", {"hub": "no-such-%d" % time.time()}, "NOT_FOUND")


async def _resolve_folder(session):
    project = await _project(session)
    r = await _d(session, "resolve_folder", {"folder": project})
    assert r["is_root"] is True, r
    await _err(session, "resolve_folder", {"folder": "%s/no-such-%d" % (project, time.time())}, "NOT_FOUND")


async def _list_folders(session):
    r = await _d(session, "list_folders", {"folder": await _project(session)})
    assert all(i.get("kind") and i.get("id") and i.get("name") for i in r["items"]), r


async def _create(session):
    r = await _d(session, "create", {"design_type": "direct"})
    try:
        assert r["design_type"] == "direct" and r["active"], r
    finally:
        await _close_quiet(session, r["id"])


async def _activate(session):
    scratch = await _active_id(session)
    second = await _d(session, "create", {})
    try:
        r = await _d(session, "activate", {"document": scratch})
        assert r["active"] and (await _active_id(session)) == scratch, r
    finally:
        await _close_quiet(session, second["id"])


async def _set_name(session):
    scratch = await _active_id(session)
    doc = await _d(session, "create", {})
    try:
        name = _unique()
        assert (await _d(session, "set_name", {"name": name}))["name"] == name
    finally:
        await _close_quiet(session, doc["id"])
        await _env(session, "activate", {"document": scratch})


async def _save_as(session):
    async with _saved(session) as (_, project, name):
        got = await _d(session, "datafile_get", {"file": "%s/%s" % (project, name)})
        assert got["name"] == name, got
        await _eerr(session, "save_as", {"documents": [{"name": name}], "folder": project}, "INVALID_PARAMS")


async def _save(session):
    async with _saved(session) as (_, project, name):
        await _lc().box(session, 10, 10, 10)
        r = await _one(session, "save", {"description": "v2"})
        assert r["saved"], r
        got = await _poll(session, "%s/%s" % (project, name), "version_number", lambda v: v >= 2, 60)
        assert got >= 2, got


async def _datafile_get(session):
    async with _saved(session) as (_, project, name):
        by_path = await _d(session, "datafile_get", {"file": "%s/%s" % (project, name)})
        by_id = await _d(session, "datafile_get", {"file": by_path["id"]})
        keys = ("id", "name", "path", "extension", "folder_id", "project_id")
        assert {k: by_id[k] for k in keys} == {k: by_path[k] for k in keys}, (by_id, by_path)


async def _close(session):
    scratch = await _active_id(session)
    doc = await _d(session, "create", {})
    try:
        await _err(session, "close", {"documents": [doc["id"]]}, "CONFIRM_REQUIRED")
        await _one(session, "close", {"documents": [doc["id"]], "confirm": True})
        assert doc["id"] not in [d["id"] for d in (await _d(session, "list_open", {}))["items"]]
    finally:
        await _env(session, "activate", {"document": scratch})


async def _open(session):
    async with _saved(session) as (doc_id, project, name):
        await _freeze(session, doc_id)
        await _one(session, "close", {"documents": [doc_id], "confirm": True})
        path = "%s/%s" % (project, name)
        first = await _one(session, "open", {"files": [path]})
        try:
            assert first["already_open"] is False, first
            assert (await _one(session, "open", {"files": [path]}))["already_open"] is True
        finally:
            await _freeze(session, first["id"])
            await _close_quiet(session, first["id"])


async def _set_read_only(session):
    async with _saved(session) as (doc_id, project, name):
        path = "%s/%s" % (project, name)
        try:
            got = await _poll(session, path, "read_only", lambda v: v is True, 5)
            assert got is True, "read_only after save_as: %r" % (got,)
            r = await _one(session, "set_read_only", {"read_only": False, "documents": [doc_id]})
            assert r["changed"] is True, r
            got = await _poll(session, path, "read_only", lambda v: v is False, 5)
            assert got is False, "read_only after false: %r" % (got,)
            r = await _one(session, "set_read_only", {"read_only": True, "documents": [doc_id]})
            assert r["changed"] is True, r
            got = await _poll(session, path, "read_only", lambda v: v is True, 5)
            assert got is True, "read_only after true: %r" % (got,)
            r = await _one(session, "set_read_only", {"read_only": True, "documents": [doc_id]})
            assert r["changed"] is False, r
        finally:
            await _freeze(session, doc_id)  # always end read-only


async def _datafile_set(session):
    async with _saved(session) as (_, project, name):
        path = "%s/%s" % (project, name)
        await _one(session, "datafile_set", {"files": [{"file": path, "name": name + "x"}]})
        assert (await _d(session, "datafile_get", {"file": project + "/" + name + "x"}))["name"] == name + "x"
        await _err(session, "datafile_set", {"files": [{"file": project + "/" + name + "x"}]}, "INVALID_PARAMS")


async def _datafile_refresh(session):
    async with _saved(session) as (_, project, name):
        path = "%s/%s" % (project, name)
        fid = (await _d(session, "datafile_get", {"file": path}))["id"]
        assert (await _one(session, "datafile_refresh", {"files": [path]}))["file"]["id"] == fid


async def _datafile_copy(session):
    async with _saved(session) as (_, project, name):
        path = "%s/%s" % (project, name)
        fid = (await _d(session, "datafile_get", {"file": path}))["id"]
        copy = await _one(session, "datafile_copy", {"files": [path], "folder": project})
        try:
            assert copy["id"] != fid, copy
        finally:
            await _env(session, "datafile_delete", {"files": [copy["id"]], "confirm": True})


async def _datafile_move(session):
    async with _saved(session) as (_, project, name):
        subs = [i for i in (await _d(session, "list_folders", {"folder": project, "files": False}))["items"]
                if i["kind"] == "folder"]
        if not subs:
            print("SKIP datafile_move: no subfolder")
            return
        path = "%s/%s" % (project, name)
        start = await _d(session, "datafile_get", {"file": path})
        moved = await _one(session, "datafile_move", {"files": [path], "folder": subs[0]["id"]})
        try:
            assert moved["folder_id"] == subs[0]["id"], moved
        finally:
            await _one(session, "datafile_move", {"files": [start["id"]], "folder": start["folder_id"]})


async def _list_references(session):
    assert (await _d(session, "list_references", {}))["total"] == 0


async def _advance_to_latest(session):
    assert (await _d(session, "advance_to_latest", {}))["references_out_of_date"] == 0


async def _update_all_references(session):
    assert (await _d(session, "update_all_references", {}))["references_out_of_date"] == 0


async def _datafile_delete(session):
    async with _saved(session) as (doc_id, project, name):
        path = "%s/%s" % (project, name)
        fid = (await _d(session, "datafile_get", {"file": path}))["id"]
        await _freeze(session, doc_id)
        await _one(session, "close", {"documents": [doc_id], "confirm": True})
        await _err(session, "datafile_delete", {"files": [fid]}, "CONFIRM_REQUIRED")
        await _one(session, "datafile_delete", {"files": [fid], "confirm": True})
        await _err(session, "datafile_get", {"file": fid}, "NOT_FOUND")


_EXPORTS = ("stl", "3mf", "obj", "step", "iges", "sat", "smt", "f3d")


async def _export(session):
    body = await _lc().box(session, 10, 10, 10)
    with tempfile.TemporaryDirectory() as tmp:
        for fmt in _EXPORTS:
            env = await _entry(session, "export", {"paths": ["%s/x.%s" % (tmp, fmt)]})
            if not env["ok"] and env["error"]["code"] == "UNSUPPORTED" and "licence" in env["error"]["message"]:
                print("export %s: UNSUPPORTED (licence)" % fmt)  # licence-conditional formats (design § 8.1)
                continue
            assert env["ok"], "export %s: %s" % (fmt, json.dumps(env.get("error"))[:300])
            r = env["result"]
            f = pathlib.Path(r["files"][0])
            assert f.is_file() and f.stat().st_size > 0, (fmt, r)
        await _one(session, "export", {"paths": [tmp + "/b.stl"], "body": body})
        env = await _env(session, "export", {"paths": [tmp + "/many.stl"], "one_file_per_body": True})
        if env["ok"]:  # an unsupported shared option fails the call, anything else the entry
            env = _first(env, "export")
        if env["ok"]:
            r = env["result"]
            assert r["files"] and all(pathlib.Path(f).is_file() for f in r["files"]), r
        else:  # older builds lack STLExportOptions.isOneFilePerBodyInOccurrence (design § 8.2)
            assert env["error"]["code"] == "UNSUPPORTED", env
        await _eerr(session, "export", {"paths": ["rel/x.stl"]}, "INVALID_PARAMS")


async def _export_usdz(session):
    await _lc().box(session, 10, 10, 10)
    with tempfile.TemporaryDirectory() as tmp:
        env = await _entry(session, "export", {"paths": [tmp + "/x.usdz"]})
        if env["ok"]:
            assert pathlib.Path(env["result"]["files"][0]).is_file(), env
            print("export_usdz: file written")
        else:
            assert env["error"]["code"] == "UNSUPPORTED", env
            print("export_usdz: UNSUPPORTED")


_DXF = "0\nSECTION\n2\nENTITIES\n0\nLINE\n8\n0\n10\n0\n20\n0\n11\n10\n21\n5\n0\nENDSEC\n0\nEOF\n"


async def _import(session):
    scratch = await _active_id(session)
    await _lc().box(session, 10, 10, 10)
    with tempfile.TemporaryDirectory() as tmp:
        step = tmp + "/x.step"
        await _one(session, "export", {"paths": [step]})
        new = await _one(session, "import", {"paths": [step], "target": "new_document"})
        try:
            assert new["document"]["type"] == "design" and new["document"]["id"] != scratch, new
        finally:
            await _close_quiet(session, new["document"]["id"])
            await _env(session, "activate", {"document": scratch})
        r = await _one(session, "import", {"paths": [step], "target": "active"})
        assert r["created"], r
        (pathlib.Path(tmp) / "l.dxf").write_text(_DXF)
        r = await _one(session, "import", {"paths": [tmp + "/l.dxf"], "target": "active"})
        assert len(r["created"]) == 1, r
        await _eerr(session, "import", {"paths": [tmp + "/l.dxf"], "target": "new_document"}, "INVALID_PARAMS")


def _cube_stl():
    v = [(x, y, z) for z in (0, 10) for y in (0, 10) for x in (0, 10)]
    quads = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
    out = ["solid cube"]
    for a, b, c, d in quads:
        for tri in ((a, b, c), (a, c, d)):
            out.append("facet normal 0 0 0\nouter loop")
            out += ["vertex %d %d %d" % v[i] for i in tri]
            out.append("endloop\nendfacet")
    out.append("endsolid cube")
    return "\n".join(out) + "\n"


def _check_bbox(r):
    bbox = r["bbox"]
    assert bbox and all(abs(a) < 0.01 for a in bbox["min"]) and all(abs(a - 10) < 0.01 for a in bbox["max"]), r


async def _import_mesh(session):
    scratch = await _active_id(session)
    with tempfile.TemporaryDirectory() as tmp:
        stl = pathlib.Path(tmp) / "cube.stl"
        stl.write_text(_cube_stl())
        _check_bbox(await _one(session, "import_mesh", {"paths": [str(stl)], "unit": "mm"}))
        doc = await _d(session, "create", {"design_type": "direct"})
        try:
            _check_bbox(await _one(session, "import_mesh", {"paths": [str(stl)], "unit": "mm"}))
            await _lc().box(session, 10, 10, 10)
            for fmt in ("3mf", "obj"):
                out = "%s/cube.%s" % (tmp, fmt)
                await _one(session, "export", {"paths": [out]})
                # Fusion writes obj in cm (3mf carries its own unit), so re-import obj as cm
                env = await _entry(session, "import_mesh", {"paths": [out], "unit": "cm" if fmt == "obj" else "mm"})
                if env["ok"]:
                    _check_bbox(env["result"])
                else:
                    assert env["error"]["code"] == "UNSUPPORTED", env
        finally:
            await _close_quiet(session, doc["id"])
            await _env(session, "activate", {"document": scratch})


async def _roundtrip(session):
    scratch = await _active_id(session)
    await _lc().box(session, 10, 10, 10)
    with tempfile.TemporaryDirectory() as tmp:
        await _one(session, "export", {"paths": [tmp + "/x.f3d"]})
        new = await _one(session, "import", {"paths": [tmp + "/x.f3d"], "target": "new_document"})
        await _close_quiet(session, new["document"]["id"])
        await _env(session, "activate", {"document": scratch})


def _made_editable(env, name):
    assert env["ok"] and name in (env.get("made_editable") or []), json.dumps(env)[:400]


async def _bulk(session):
    async with _saved(session) as (a, project, _), _saved(session) as (b, _p, _n):
        paths = ["%s/%s" % (project, a), "%s/%s" % (project, b)]
        r = await _d(session, "close", {"documents": [a, b], "confirm": True})
        assert (r["ok"], r["failed"]) == (2, 0), r
        missing = "%s/no-such-%d" % (project, time.time())
        r = await _d(session, "open", {"files": paths + [missing]})
        opened = [x["result"]["id"] for x in r["results"] if x["ok"]]
        try:
            assert (r["ok"], r["failed"]) == (2, 1), r
            assert [x["input"] for x in r["results"]] == paths + [missing], r
            assert r["results"][2]["error"]["code"] == "NOT_FOUND", r
            await _err(session, "open", {"files": []}, "INVALID_PARAMS")
            await _err(session, "open", {"files": [paths[0]] * 51}, "INVALID_PARAMS")
            await _err(session, "open", {"file": paths[0]}, "INVALID_PARAMS")
            for path in paths:
                assert (await _d(session, "datafile_get", {"file": path}))["read_only"] is True, path
        finally:
            for doc in opened:
                await _freeze(session, doc)
                await _close_quiet(session, doc)


async def _save_locks(session):
    async with _saved(session) as (doc_id, project, name):
        path = "%s/%s" % (project, name)
        assert (await _d(session, "datafile_get", {"file": path}))["read_only"] is True
        _made_editable(await _lc().call(session, "sketch", "create", {"plane": "XY"}), name)  # box's first edit
        await _lc().box(session, 10, 10, 10)
        got = await _poll(session, path, "read_only", lambda v: v is False, 5)
        assert got is False, "read_only after an edit: %r" % (got,)
        r = await _one(session, "save", {"description": "v2"})
        assert r["read_only"] is True, r
        got = await _poll(session, path, "read_only", lambda v: v is True, 5)
        assert got is True, "read_only after save: %r" % (got,)
        await _lc().box(session, 10, 10, 10)
        assert (await _one(session, "save", {"read_only": False}))["read_only"] is False
        got = await _poll(session, path, "read_only", lambda v: v is False, 5)
        assert got is False, "read_only after save read_only false: %r" % (got,)


async def _auto_unlock(session):
    async with _saved(session) as (doc_id, project, name):
        path = "%s/%s" % (project, name)
        env = await _lc().call(session, "sketch", "create", {"plane": "XY", "preserve_read_only": True})
        assert not env["ok"] and env["error"]["code"] == "READ_ONLY", json.dumps(env)[:300]
        assert (await _d(session, "datafile_get", {"file": path}))["read_only"] is True
        _made_editable(await _lc().call(session, "sketch", "create", {"plane": "XY"}), name)
        got = await _poll(session, path, "read_only", lambda v: v is False, 5)
        assert got is False, "read_only after auto-unlock: %r" % (got,)


async def _folder_limit(session):
    scratch = await _active_id(session)
    project, name = await _project(session), _unique()
    async with _lc().open_session(folder=project) as limited:
        doc = await _d(limited, "create", {})
        try:
            r = await _one(limited, "save_as", {"documents": [{"name": name}]})
            assert r["file"]["path"] == "%s/%s" % (project, name) and r["read_only"] is True, r
            await _uploaded(session, name)
        finally:
            await _discard(session, project, name, scratch)
            await _close_quiet(session, doc["id"])  # only still open when save_as failed
    async with _saved(session) as (doc_id, project, name):
        path = "%s/%s" % (project, name)
        async with _lc().open_session(folder="%s/defusion-live-none-%d" % (project, time.time())) as limited:
            await _err(limited, "resolve_folder", {"folder": project}, "NOT_ALLOWED")
            await _err(limited, "datafile_get", {"file": path}, "NOT_ALLOWED")
            await _eerr(limited, "open", {"files": [path]}, "NOT_ALLOWED")
            await _err(limited, "save_as", {"documents": [{"name": _unique()}], "folder": project}, "NOT_ALLOWED")
            await _d(limited, "activate", {"document": doc_id})
            env = await _lc().call(limited, "sketch", "create", {"plane": "XY"})
            assert not env["ok"] and env["error"]["code"] == "NOT_ALLOWED", json.dumps(env)[:300]
            new = await _d(limited, "create", {})
            try:
                env = await _lc().call(limited, "sketch", "create", {"plane": "XY"})
                assert env["ok"], json.dumps(env)[:300]
            finally:
                await _close_quiet(limited, new["id"])


LIVE_SCENARIOS = [
    ("list_open", "document.list_open", _list_open),
    ("list_hubs", "document.list_hubs", _list_hubs),
    ("list_projects", "document.list_projects", _list_projects),
    ("resolve_folder", "document.resolve_folder", _resolve_folder),
    ("list_folders", "document.list_folders", _list_folders),
    ("create", "document.create", _create),
    ("activate", "document.activate", _activate),
    ("set_name", "document.set_name", _set_name),
    ("save_as", "document.save_as", _save_as),
    ("save", "document.save", _save),
    ("datafile_get", "document.datafile_get", _datafile_get),
    ("close", "document.close", _close),
    ("open", "document.open", _open),
    ("set_read_only", "document.set_read_only", _set_read_only),
    ("datafile_set", "document.datafile_set", _datafile_set),
    ("datafile_refresh", "document.datafile_refresh", _datafile_refresh),
    ("datafile_copy", "document.datafile_copy", _datafile_copy),
    ("datafile_move", "document.datafile_move", _datafile_move),
    ("list_references", "document.list_references", _list_references),
    ("advance_to_latest", "document.advance_to_latest", _advance_to_latest),
    ("update_all_references", "document.update_all_references", _update_all_references),
    ("datafile_delete", "document.datafile_delete", _datafile_delete),
    ("export", "document.export", _export),
    ("export_usdz", "document.export", _export_usdz),
    ("import", "document.import", _import),
    ("import_mesh", "document.import_mesh", _import_mesh),
    ("roundtrip", "document.import", _roundtrip),
    ("bulk", "document.open", _bulk),
    ("save_locks", "document.save", _save_locks),
    ("auto_unlock", "sketch.create", _auto_unlock),
    ("folder_limit", "document.save_as", _folder_limit),
]
