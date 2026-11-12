"""Document guard: read-only state and the folder limit (design § 5.13)."""
import time

from .errors import FUSION_ERROR, NOT_ALLOWED, READ_ONLY, READ_ONLY_NOT_APPLIED, ActionError

VERIFY_TRIES = 10
VERIFY_DELAY = 1.0  # seconds between tries; unit tests set it to 0
_EVENTS_EVERY = 0.05


def saved_file(doc):
    """``doc.dataFile`` when it is a finished cloud file; ``None`` when never saved, unreadable or
    still uploading (an ``id`` that is not a ``urn:``)."""
    try:
        df = doc.dataFile
        return df if df is not None and str(df.id).startswith("urn:") else None
    except Exception:
        return None


def never_saved(doc):
    """True when the document has no data file; a raising read means upload pending (False)."""
    try:
        return doc.dataFile is None
    except Exception:
        return False


def folder_path(folder):
    """``"Project/Sub/..."`` for a cloud folder (a project root folder is the project name), or
    ``None`` when it cannot be read."""
    try:
        names = []
        while not folder.isRoot:
            names.append(folder.name)
            folder = folder.parentFolder
        return "/".join([folder.parentProject.name] + names[::-1])
    except Exception:
        return None


def norm(path):
    return [s for s in path.strip().strip("/").split("/") if s]


def norm_folder(value):
    """``value`` as a ``"A/B"`` string, or ``None`` when it has no segments."""
    parts = norm(value)
    return "/".join(parts) if parts else None


def check_folder(ctx, path):
    """Pass when no folder limit is set or ``path`` is inside it, else ``NOT_ALLOWED``."""
    limit = ctx.folder_limit
    if limit is None:
        return
    if path is None:
        raise ActionError(
            NOT_ALLOWED,
            "cannot tell the folder of the target; folder limit '%s' is set" % limit,
            hints=["address the file by a path inside the limit, or retry after its upload finishes"],
        )
    want = norm(limit)
    if norm(path)[: len(want)] == want:
        return
    raise ActionError(
        NOT_ALLOWED,
        "'%s' is outside the folder limit '%s'" % (path, limit),
        hints=["use a file or folder under %s; the limit comes from ?folder= or config folder" % limit],
    )


def _pause():
    """Wait ``VERIFY_DELAY`` seconds, letting Fusion process events every 50 ms."""
    import adsk

    end = time.monotonic() + VERIFY_DELAY
    while True:
        adsk.doEvents()
        left = end - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(_EVENTS_EVERY, left))


def wait_read_only(lookup, want):
    """True once a fresh ``DataFile`` from ``lookup()`` shows ``isReadOnly == want``."""
    for i in range(VERIFY_TRIES):
        if i:
            _pause()
        try:
            df = lookup()
        except Exception:
            df = None
        if df is not None and df.isReadOnly == want:
            return True
    return False


def set_read_only(ctx, doc, want, lookup):
    """Run Fusion's Make Read-Only (``want``) or Make Editable on ``doc`` and verify via ``lookup``."""
    prev = ctx.app.activeDocument
    switch = prev is not doc
    if switch and not doc.activate():
        raise ActionError(FUSION_ERROR, "could not activate %s" % doc.name)
    failed = True
    try:
        ctx.text_command(
            "Commands.Start ActiveDocumentDeactivateCommand"
            if want
            else "Commands.Start ActiveDocumentActivateCommand"
        )
        failed = False
    finally:
        if switch and prev is not None and not prev.activate() and not failed:
            raise ActionError(FUSION_ERROR, "could not re-activate %s" % prev.name)
    return wait_read_only(lookup, want)


def fresh_file(ctx, df):
    """The current ``DataFile`` for ``df``; ``df`` itself when the lookup finds nothing or raises
    (not yet listed right after a save)."""
    try:
        return ctx.app.data.findFileById(df.id) or df
    except Exception:
        return df


def ensure_editable(ctx, doc):
    """Folder-limit check, then make a saved read-only document editable (or ``READ_ONLY``)."""
    if doc is None or never_saved(doc):
        return
    df = saved_file(doc)
    if df is None:  # upload pending
        check_folder(ctx, None)
        return
    fresh = fresh_file(ctx, df)
    check_folder(ctx, folder_path(fresh.parentFolder))
    if not fresh.isReadOnly:
        return
    if ctx.preserve_read_only:
        raise ActionError(
            READ_ONLY,
            "%s is read-only and preserve_read_only is true" % doc.name,
            hints=["omit preserve_read_only to make it editable first, or call document.set_read_only "
                   "with read_only false"],
        )
    if not set_read_only(ctx, doc, False, lambda: ctx.app.data.findFileById(df.id)):
        raise ActionError(
            READ_ONLY_NOT_APPLIED,
            "%s did not become editable within 10 tries, 1 s apart" % doc.name,
            hints=["retry, or call document.set_read_only with read_only false and check datafile_get"],
        )
    ctx.made_editable.append(doc.name)
