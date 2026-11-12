"""Document guard: read-only state, auto-unlock and the folder limit
(REQ-CORE-051, REQ-CORE-052, REQ-CORE-054; design § 5.3, § 5.13)."""
from types import SimpleNamespace

import pytest
from adsk import core

from defusion import actions, guard, units
from defusion.actions import action, call, call_inline
from defusion.context import Ctx
from defusion.errors import ActionError
from helpers import err, raises_action

EDITABLE = "Commands.Start ActiveDocumentActivateCommand"
READ_ONLY_CMD = "Commands.Start ActiveDocumentDeactivateCommand"


class RaisingDoc(core.FusionDocument):
    """A document whose ``dataFile`` read raises (Fusion while a save is still uploading)."""

    dataFile = property(
        lambda s: (_ for _ in ()).throw(RuntimeError("Failed to get temporary file")),
        lambda s, v: None,
    )


@pytest.fixture(autouse=True)
def no_delay(monkeypatch):
    monkeypatch.setattr(guard, "VERIFY_DELAY", 0)


@pytest.fixture(autouse=True)
def clear_unlocked():
    guard.UNLOCKED.clear()
    yield
    guard.UNLOCKED.clear()


@pytest.fixture
def env(fake_app):
    """Project "P" with folders A, A/B and X; ``doc(folder)`` opens a document saved there."""
    data = fake_app.data
    proj = data.add_hub("Hub").add_project("P")
    a = proj.rootFolder.add_folder("A")
    folders = {"P": proj.rootFolder, "P/A": a, "P/A/B": a.add_folder("B"), "P/X": proj.rootFolder.add_folder("X")}

    def doc(where=None, name="Doc", read_only=False):
        df = folders[where].add_file(name) if where else None
        if df is not None:
            df.isReadOnly = read_only
        return fake_app.documents._new(name, df)

    def ctx(limit=None):
        c = Ctx.for_app(fake_app, frozenset(), [], units.OutputUnits.for_call(), limit)
        return c

    return SimpleNamespace(app=fake_app, data=data, folders=folders, doc=doc, ctx=ctx)


@pytest.fixture
def pipe(env, registry, dispatcher):
    """``env`` plus test actions: write ``edit``, read ``peek``, write ``"none"`` ``camera``."""
    env.seen = []

    @action("model", access="write")
    def edit(ctx: Ctx, fail: bool = False) -> dict:
        """Edit."""
        env.seen.append(("edit", ctx.preserve_read_only, ctx.folder_limit))
        if fail:
            raise RuntimeError("broke")
        return {"done": True}

    @action("model", access="read")
    def peek(ctx: Ctx) -> dict:
        """Peek."""
        env.seen.append("peek")
        return {}

    @action("view", access="write", edits_document="none")
    def camera(ctx: Ctx) -> dict:
        """Camera."""
        env.seen.append("camera")
        return {}

    @action("model", access="write")
    def outer(ctx: Ctx) -> dict:
        """Call edit inline."""
        return call_inline(ctx, "model", "edit", {"preserve_read_only": True})

    def run(tool, act, params=None, limit=None):
        allowed = frozenset(a.key for a in actions.registry())
        return call(tool, {"action": act, "params": params or {}}, allowed, ["all"], folder_limit=limit)

    env.run = run
    return env


# --- pipeline: auto-unlock (REQ-CORE-051) ------------------------------------------------------------

def test_write_on_read_only_doc_unlocks_before_undo_group(pipe):
    pipe.doc("P/A", "Part", read_only=True)
    r = pipe.run("model", "edit")
    assert r.is_error is False, r.structured_content
    assert r.structured_content["made_editable"] == ["Part"]
    cmds = pipe.app.commands
    assert cmds.index(EDITABLE) < next(i for i, c in enumerate(cmds) if c.startswith("PTransaction.Start"))
    assert pipe.seen == [("edit", False, None)]


def test_preserve_read_only_fails_read_only_without_commands(pipe):
    pipe.doc("P/A", "Part", read_only=True)
    e = err(pipe.run("model", "edit", {"preserve_read_only": True}))
    assert e["code"] == "READ_ONLY" and "Part" in e["message"] and e["hints"]
    assert pipe.app.commands == [] and pipe.seen == []


def test_unsaved_doc_no_lookup_no_command(pipe):
    pipe.doc(None, "New")
    pipe.data.find_file_reads = 0
    r = pipe.run("model", "edit")
    assert r.is_error is False and "made_editable" not in r.structured_content
    assert pipe.data.find_file_reads == 0
    assert not any(c.startswith("Commands.Start") for c in pipe.app.commands)


def test_saved_editable_doc_one_lookup_no_command(pipe):
    pipe.doc("P/A")
    pipe.data.find_file_reads = 0
    r = pipe.run("model", "edit", {"preserve_read_only": True})
    assert r.is_error is False
    assert pipe.data.find_file_reads == 1
    assert not any(c.startswith("Commands.Start") for c in pipe.app.commands)
    assert pipe.seen == [("edit", True, None)]  # the function never receives preserve_read_only


def test_none_action_on_read_only_doc_no_command(pipe):
    pipe.doc("P/A", read_only=True)
    pipe.data.find_file_reads = 0
    assert pipe.run("view", "camera").is_error is False
    assert not any(c.startswith("Commands.Start") for c in pipe.app.commands)
    assert pipe.data.find_file_reads == 0


def test_made_editable_on_error_envelope(pipe):
    pipe.doc("P/A", "Part", read_only=True)
    r = pipe.run("model", "edit", {"fail": True})
    assert r.structured_content["error"]["code"] == "FUSION_ERROR"
    assert r.structured_content["made_editable"] == ["Part"]


def test_call_inline_drops_preserve_read_only_and_runs_no_guard(pipe):
    pipe.doc("P/A", "Part")
    pipe.data.find_file_reads = 0
    r = pipe.run("model", "outer")
    assert r.is_error is False, r.structured_content
    assert pipe.data.find_file_reads == 1  # the outer call's guard only
    assert pipe.seen == [("edit", False, None)]


def test_folder_limit_reaches_ctx(pipe):
    pipe.doc(None)
    pipe.run("model", "edit", limit="P/A")
    assert pipe.seen == [("edit", False, "P/A")]


# --- pipeline: folder limit (REQ-CORE-054) -------------------------------------------------------------

def test_write_outside_limit_not_allowed(pipe):
    pipe.doc("P/X", read_only=True)
    e = err(pipe.run("model", "edit", limit="P/A"))
    assert e["code"] == "NOT_ALLOWED" and "P/X" in e["message"] and "P/A" in e["message"]
    assert pipe.app.commands == [] and pipe.seen == []


def test_write_inside_limit_runs(pipe):
    pipe.doc("P/A/B")
    assert pipe.run("model", "edit", limit="P/A").is_error is False
    assert pipe.seen == [("edit", False, "P/A")]


def test_never_saved_doc_under_limit_runs(pipe):
    pipe.doc(None)
    assert pipe.run("model", "edit", limit="P/A").is_error is False


def test_upload_pending_local_id_under_limit_not_allowed(pipe):
    pipe.app.documents._new("Up", pipe.folders["P/A"].add_file("Up", pending=True))
    e = err(pipe.run("model", "edit", limit="P/A"))
    assert e["code"] == "NOT_ALLOWED" and pipe.seen == []


def test_raising_data_file_fails_closed_under_limit_and_runs_without(pipe):
    doc = RaisingDoc("Raising", {}, None)
    pipe.app.activeDocument = doc
    assert err(pipe.run("model", "edit", limit="P/A"))["code"] == "NOT_ALLOWED"
    assert pipe.seen == []
    assert pipe.run("model", "edit").is_error is False
    assert pipe.seen == [("edit", False, None)]


def test_read_action_outside_limit_runs(pipe):
    pipe.doc("P/X")
    assert pipe.run("model", "peek", limit="P/A").is_error is False
    assert pipe.seen == ["peek"]


# --- check_folder, folder_path, norm ---------------------------------------------------------------------

@pytest.mark.parametrize("path", ["P/A", "P/A/B/C", "/P/A/"])
def test_check_folder_inside(env, path):
    guard.check_folder(env.ctx("P/A"), path)


@pytest.mark.parametrize("path", ["P/AB", "P", "Q/A", "p/a"])
def test_check_folder_outside(env, path):
    e = raises_action(guard.check_folder, env.ctx("P/A"), path)
    assert e.code == "NOT_ALLOWED" and "'%s'" % path in e.message and "'P/A'" in e.message
    assert e.hints and "P/A" in e.hints[0]


def test_check_folder_unknown_path(env):
    e = raises_action(guard.check_folder, env.ctx("P/A"), None)
    assert e.code == "NOT_ALLOWED" and "'P/A'" in e.message and "cannot tell" in e.message
    assert e.hints and e.hints[0]


@pytest.mark.parametrize("path", ["P/A", "Q", None])
def test_check_folder_no_limit(env, path):
    guard.check_folder(env.ctx(), path)


def test_folder_path_and_norm(env):
    assert guard.folder_path(env.folders["P"]) == "P"
    assert guard.folder_path(env.folders["P/A/B"]) == "P/A/B"
    assert guard.folder_path(object()) is None
    assert guard.norm(" /P//A/ ") == ["P", "A"] and guard.norm(" / ") == []


def test_saved_file_and_never_saved(env):
    assert guard.saved_file(env.doc(None)) is None and guard.never_saved(env.doc(None)) is True
    saved = env.doc("P/A")
    assert guard.saved_file(saved) is saved.dataFile and guard.never_saved(saved) is False
    raising = RaisingDoc("R", {}, None)
    assert guard.saved_file(raising) is None and guard.never_saved(raising) is False


# --- wait_read_only / set_read_only (REQ-CORE-052) ---------------------------------------------------

def _counting(env, df):
    reads = []

    def lookup():
        reads.append(1)
        return env.data.findFileById(df.id)

    return lookup, reads


def test_set_read_only_observed_on_third_lookup(env):
    doc = env.doc("P/A")
    env.app.read_only_reads = 3
    lookup, reads = _counting(env, doc.dataFile)
    core.DO_EVENTS.clear()
    assert guard.set_read_only(env.ctx(), doc, True, lookup) is True
    assert len(reads) == 3 and env.app.commands == [READ_ONLY_CMD]
    assert len(core.DO_EVENTS) >= 2  # Fusion gets events between tries


def test_set_read_only_never_observed_after_ten_lookups(env):
    doc = env.doc("P/A")
    env.app.read_only_reads = None
    lookup, reads = _counting(env, doc.dataFile)
    assert guard.set_read_only(env.ctx(), doc, True, lookup) is False
    assert len(reads) == 10


def test_raising_lookup_is_not_observed(env):
    doc = env.doc("P/A")
    calls = []

    def lookup():
        calls.append(1)
        if len(calls) < 10:
            raise RuntimeError("3 : file not found")
        return doc.dataFile

    assert guard.wait_read_only(lookup, False) is True and len(calls) == 10
    assert guard.wait_read_only(lambda: (_ for _ in ()).throw(RuntimeError("x")), True) is False


def test_set_read_only_activates_and_restores_even_when_command_raises(env):
    doc = env.doc("P/A", "Target")
    prev = env.app.documents._new("Prev")
    env.app.text_command_errors["ActiveDocumentDeactivateCommand"] = RuntimeError("nope")
    e = raises_action(guard.set_read_only, env.ctx(), doc, True, lambda: doc.dataFile)
    assert e.code == "TEXT_COMMAND_FAILED"
    assert doc.calls == [("activate",)] and prev.calls == [("activate",)]
    assert env.app.activeDocument is prev


def test_set_read_only_activate_fails(env):
    doc = env.doc("P/A", "Target")
    env.app.documents._new("Prev")
    doc.fail.add("activate")
    e = raises_action(guard.set_read_only, env.ctx(), doc, True, lambda: doc.dataFile)
    assert e.code == "FUSION_ERROR" and "Target" in e.message and env.app.commands == []


def test_set_read_only_reactivate_fails_names_previous(env):
    doc = env.doc("P/A", "Target")
    prev = env.app.documents._new("Prev")
    prev.fail.add("activate")
    e = raises_action(guard.set_read_only, env.ctx(), doc, True, lambda: doc.dataFile)
    assert e.code == "FUSION_ERROR" and "could not re-activate Prev" in e.message


def test_set_read_only_reactivate_failure_does_not_mask_command_error(env):
    doc = env.doc("P/A", "Target")
    prev = env.app.documents._new("Prev")
    prev.fail.add("activate")
    env.app.text_command_errors["ActiveDocumentDeactivateCommand"] = RuntimeError("nope")
    e = raises_action(guard.set_read_only, env.ctx(), doc, True, lambda: doc.dataFile)
    assert e.code == "TEXT_COMMAND_FAILED" and prev.calls == [("activate",)]


def test_ensure_editable_never_unlocking(env):
    doc = env.doc("P/A", "Part", read_only=True)
    env.app.read_only_reads = None
    ctx = env.ctx()
    e = raises_action(guard.ensure_editable, ctx, doc)
    assert e.code == "READ_ONLY_NOT_APPLIED" and "Part" in e.message
    assert e.hints and "set_read_only" in e.hints[0]
    assert ctx.made_editable == []


def test_ensure_editable_unlocks(env):
    doc = env.doc("P/A", "Part", read_only=True)
    ctx = env.ctx()
    guard.ensure_editable(ctx, doc)
    assert ctx.made_editable == ["Part"] and doc.dataFile.isReadOnly is False
    assert env.app.commands == [EDITABLE]


def test_ensure_editable_raising_lookup_falls_back_to_data_file(env, monkeypatch):
    doc = env.doc("P/A", "Part")

    def boom(_id):
        raise RuntimeError("file not found")

    monkeypatch.setattr(env.data, "findFileById", boom)
    ctx = env.ctx("P/A")
    guard.ensure_editable(ctx, doc)
    assert ctx.made_editable == [] and env.app.commands == []
    e = raises_action(guard.ensure_editable, env.ctx("P/X"), doc)
    assert e.code == "NOT_ALLOWED"


# --- UNLOCKED registry and fresh_file (REQ-CORE-056) -------------------------------------------------

def test_ensure_editable_registers_unlocked(env):
    doc = env.doc("P/A", "Part", read_only=True)
    guard.ensure_editable(env.ctx(), doc)
    assert guard.UNLOCKED == {doc.dataFile.id: "Part"}


def test_ensure_editable_registers_even_when_not_observed(env):
    doc = env.doc("P/A", "Part", read_only=True)
    env.app.read_only_reads = None
    raises_action(guard.ensure_editable, env.ctx(), doc)
    assert guard.UNLOCKED == {doc.dataFile.id: "Part"}


def test_ensure_editable_registers_nothing_otherwise(env):
    guard.ensure_editable(env.ctx(), env.doc("P/A", "Ed"))
    guard.ensure_editable(env.ctx(), env.doc(None, "New"))
    ro = env.doc("P/A", "Ro", read_only=True)
    ctx = env.ctx()
    ctx.preserve_read_only = True
    raises_action(guard.ensure_editable, ctx, ro)
    assert guard.UNLOCKED == {}


def test_set_read_only_want_observed_removes_entry(env):
    doc = env.doc("P/A", "Part")
    guard.UNLOCKED[doc.dataFile.id] = "Part"
    assert guard.set_read_only(env.ctx(), doc, True, lambda: env.data.findFileById(doc.dataFile.id))
    assert guard.UNLOCKED == {}


def test_set_read_only_not_observed_keeps_entry(env):
    doc = env.doc("P/A", "Part")
    guard.UNLOCKED[doc.dataFile.id] = "Part"
    env.app.read_only_reads = None
    assert not guard.set_read_only(env.ctx(), doc, True, lambda: env.data.findFileById(doc.dataFile.id))
    assert guard.UNLOCKED == {doc.dataFile.id: "Part"}


def test_set_read_only_editable_keeps_entry(env):
    doc = env.doc("P/A", "Part", read_only=True)
    guard.UNLOCKED[doc.dataFile.id] = "Part"
    assert guard.set_read_only(env.ctx(), doc, False, lambda: env.data.findFileById(doc.dataFile.id))
    assert guard.UNLOCKED == {doc.dataFile.id: "Part"}


def test_unlock_then_relock_round_trip_leaves_registry_empty(env):
    """The close re-lock path: ensure_editable registers, set_read_only(True) clears."""
    doc = env.doc("P/A", "Part", read_only=True)
    ctx = env.ctx()
    guard.ensure_editable(ctx, doc)
    assert doc.dataFile.id in guard.UNLOCKED
    assert guard.set_read_only(ctx, doc, True, lambda: env.data.findFileById(doc.dataFile.id))
    assert guard.UNLOCKED == {} and doc.dataFile.isReadOnly is True


def test_forget_removes_and_ignores_absent(env):
    guard.UNLOCKED["urn:a"] = "A"
    guard.forget("urn:a")
    guard.forget("urn:missing")
    assert guard.UNLOCKED == {}


def test_fresh_file_found_raises_or_none(env, monkeypatch):
    doc = env.doc("P/A", "Part")
    df = doc.dataFile
    found = env.data.findFileById(df.id)
    assert guard.fresh_file(env.ctx(), df) == (found, True)
    monkeypatch.setattr(env.data, "findFileById", lambda _id: None)
    assert guard.fresh_file(env.ctx(), df) == (df, False)

    def boom(_id):
        raise RuntimeError("x")

    monkeypatch.setattr(env.data, "findFileById", boom)
    assert guard.fresh_file(env.ctx(), df) == (df, False)
