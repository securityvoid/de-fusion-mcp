"""history tool tests (fake adsk)."""
import json
import os
import sys
import types as pytypes

import pytest
from adsk import core, fusion

import defusion
from helpers import raises_action


def _import_history():
    """Import defusion.tools.history; when sibling tool modules are not written yet, stand in an
    empty ``defusion.tools`` package so the real package __init__ does not run."""
    try:
        import defusion.tools.history as mod
        return mod
    except (ImportError, RuntimeError):
        pkg = pytypes.ModuleType("defusion.tools")
        pkg.__path__ = [os.path.join(os.path.dirname(defusion.__file__), "tools")]
        sys.modules["defusion.tools"] = pkg
        import importlib
        return importlib.import_module("defusion.tools.history")


history = _import_history()

from defusion.context import Ctx  # noqa: E402
from defusion.errors import ActionError  # noqa: E402
from defusion.units import OutputUnits  # noqa: E402


def make_ctx(app, allowed=()):
    return Ctx.for_app(app, allowed, [], OutputUnits.for_call(None))


@pytest.fixture
def ctx(fake_app, fake_design):
    return make_ctx(fake_app)


@pytest.fixture
def direct_ctx(fake_app):
    fake_app.activeDocument = core.Document("Doc", {"DesignProductType": fusion.Design(parametric=False)})
    return make_ctx(fake_app)


def build(design, *kinds):
    """Timeline items: 's' = sketch, 'e' = extrude. Returns the TimelineObjects."""
    root = design.rootComponent
    out = []
    for i, k in enumerate(kinds):
        ent = root.add_sketch("Sketch%d" % i) if k == "s" else root.add_feature("Extrude%d" % i)
        out.append(ent.timelineObject)
    return out


# ---- helpers (TASK-HIS-001) ----------------------------------------------------------------------


def test_helpers_item_resolves_index_and_passes_objects(ctx, fake_design):
    items = build(fake_design, "s", "e")
    tl = fake_design.timeline
    assert history._item(tl, 1) is items[1]
    assert history._item(tl, items[0]) is items[0]
    for bad in (-1, 2):
        e = raises_action(history._item, tl, bad)
        assert e.code == "INVALID_PARAMS" and "out of range 0..1" in e.message


def test_helpers_item_json_shape(ctx, fake_design):
    s, e = build(fake_design, "s", "e")
    j = history._item_json(ctx, e)
    assert j == {
        "index": 1, "name": "Extrude1", "type": "ExtrudeFeature",
        "entity": {"type": "ExtrudeFeature", "name": "Extrude1", "token": e.entity.entityToken,
                   "component": "Root"},
        "is_group": False, "suppressed": False, "rolled_back": False,
        "health": "healthy", "message": None, "group": None,
    }
    e.healthState = fusion.FeatureHealthStates.ErrorFeatureHealthState
    e.errorOrWarningMessage = "boom"
    e.isSuppressed = True
    j = history._item_json(ctx, e)
    assert (j["health"], j["message"], j["suppressed"]) == ("error", "boom", True)
    fake_design.timeline.markerPosition = 1
    assert history._item_json(ctx, e)["rolled_back"] is True


def test_helpers_item_json_guards_each_read(ctx, fake_design):
    (s,) = build(fake_design, "s")

    class Broken:
        @property
        def objectType(self):
            raise RuntimeError("x")

    s.entity = Broken()
    j = history._item_json(ctx, s)
    assert j["type"] is None and j["name"] is None
    assert j["index"] == 0 and j["health"] == "healthy"


def test_helpers_item_json_group(ctx, fake_design):
    a, b = build(fake_design, "s", "e")
    g = fake_design.timeline.timelineGroups.add(0, 1)
    ja = history._item_json(ctx, a)
    jg = history._item_json(ctx, g)
    assert ja["group"] == g.index
    assert jg["is_group"] is True and jg["type"] == "TimelineGroup" and jg["entity"] is None


def test_helpers_with_collapsed_retries_once(ctx, fake_design):
    a, b = build(fake_design, "s", "e")
    g = fake_design.timeline.timelineGroups.add(0, 1)
    g.isCollapsed = False
    calls = []

    def write():
        calls.append(1)
        b.isSuppressed = True

    history._with_collapsed(b, write)
    assert len(calls) == 2 and b.isSuppressed is True and g.isCollapsed is True


def test_helpers_with_collapsed_second_failure_is_fusion_error(ctx, fake_design):
    a, b = build(fake_design, "s", "e")
    g = fake_design.timeline.timelineGroups.add(0, 1)
    g.isCollapsed = False

    def always():
        raise RuntimeError("nope")

    e = raises_action(history._with_collapsed, b, always)
    assert e.code == "FUSION_ERROR" and e.message == "nope"
    assert e.hints == ["item is inside timeline group %s" % g.name]


def test_helpers_with_collapsed_no_group_reraises_original(ctx, fake_design):
    (a,) = build(fake_design, "s")

    def boom():
        raise ValueError("plain")

    with pytest.raises(ValueError):
        history._with_collapsed(a, boom)


def test_helpers_snapshot_addressing(ctx, fake_design):
    build(fake_design, "s")
    snaps = fake_design.snapshots
    for n in ("A", "B", "A"):
        snaps.hasPendingSnapshot = True
        snaps.add().name = n
    assert history._snapshot(snaps, 1).name == "B"
    assert history._snapshot(snaps, "B") is snaps.item(1)
    assert raises_action(history._snapshot, snaps, 3).code == "INVALID_PARAMS"
    assert raises_action(history._snapshot, snaps, "Z").code == "NOT_FOUND"
    e = raises_action(history._snapshot, snaps, "A")
    assert e.code == "AMBIGUOUS"
    assert e.extra["candidates"] == [{"index": 0, "name": "A"}, {"index": 2, "name": "A"}]
    assert history._snapshot_json(1, snaps.item(1)) == {"index": 1, "name": "B", "timeline_index": 0}


def test_design_type_helpers_refuse_direct_design(direct_ctx):
    assert raises_action(history._timeline, direct_ctx).code == "WRONG_DESIGN_TYPE"
    assert raises_action(history._snapshots, direct_ctx).code == "WRONG_DESIGN_TYPE"


def test_unsupported_snapshots_member_missing(ctx, fake_design):
    fake_design._has_snapshots = False
    e = raises_action(history._snapshots, ctx)
    assert e.code == "UNSUPPORTED" and "snapshots" in e.message


def test_unsupported_need_and_false_return():
    e = raises_action(history._need, object(), "timelineGroups", "Timeline")
    assert e.code == "UNSUPPORTED" and "Timeline.timelineGroups" in e.message
    e = raises_action(history._ok, False, "Timeline.moveToEnd")
    assert e.code == "FUSION_ERROR" and e.message == "Timeline.moveToEnd returned false"
    assert history._ok(True, "x") is True


# ---- list, snapshot_list (TASK-HIS-002) ------------------------------------------------------------


def test_list_items_and_paging(ctx, fake_design):
    build(fake_design, "s", "e")
    r = history.list_items(ctx)
    assert [(i["index"], i["type"], i["health"], i["group"]) for i in r["items"]] == [
        (0, "Sketch", "healthy", None), (1, "ExtrudeFeature", "healthy", None)]
    assert (r["marker"], r["count"], r["total"], r["next_offset"]) == (2, 2, 2, None)
    r = history.list_items(ctx, limit=1)
    assert len(r["items"]) == 1 and r["next_offset"] == 1
    r = history.list_items(ctx, offset=1, limit=1)
    assert r["items"][0]["index"] == 1 and r["next_offset"] is None


def test_list_marker_and_rolled_back(ctx, fake_design):
    build(fake_design, "s", "e", "e")
    fake_design.timeline.markerPosition = 1
    r = history.list_items(ctx)
    assert r["marker"] == 1 and [i["rolled_back"] for i in r["items"]] == [False, True, True]


def test_list_inserts_groups_missing_from_items_in_index_order():
    class Obj:
        def __init__(self, index, group=False):
            self.index, self.isGroup = index, group

    objs = [Obj(0), Obj(2), Obj(3)]
    g = Obj(1, True)

    class TL:
        count = 3
        timelineGroups = [g, objs[0]]  # a group already among the items is not inserted twice

        def item(self, i):
            return objs[i]

    assert [o.index for o in history._ordered_objects(TL())] == [0, 1, 2, 3]
    assert history._ordered_objects(TL())[1] is g


def test_list_without_groups_member(ctx, fake_design):
    build(fake_design, "s")
    fake_design.timeline._has_groups = False
    assert history.list_items(ctx)["total"] == 1


def test_snapshot_list_shape_and_pending(ctx, fake_design):
    build(fake_design, "s", "e")
    snaps = fake_design.snapshots
    snaps.hasPendingSnapshot = True
    r = history.snapshot_list(ctx)
    assert r["items"] == [] and r["pending"] is True and r["total"] == 0
    snaps.add().name = "Pose A"
    r = history.snapshot_list(ctx)
    assert r["items"] == [{"index": 0, "name": "Pose A", "timeline_index": 1}]
    assert r["pending"] is False


def test_snapshot_list_builds_only_the_page_slice(ctx, fake_design, monkeypatch):
    build(fake_design, "s")
    snaps = fake_design.snapshots
    for _ in range(500):
        snaps.add()
    built = []
    real = history._snapshot_json
    monkeypatch.setattr(history, "_snapshot_json", lambda i, s: built.append(i) or real(i, s))
    r = history.snapshot_list(ctx, limit=10)
    assert len(r["items"]) == 10 and built == list(range(10))
    assert r["total"] == 500 and r["next_offset"] == 10


# ---- set_marker, reorder, set_suppressed, rename (TASK-HIS-003) -------------------------------------


def validate(tool_action, params):
    """Run the registered action's pydantic model on ``params`` (union and Literal handling)."""
    act = defusion.actions.registry().get("history", tool_action)
    return act.model.model_validate(params)


def test_marker_all_position_forms(ctx, fake_design):
    items = build(fake_design, "s", "e", "e")
    f = history.set_marker
    assert f(ctx, "begin") == {"marker": 0, "count": 3}
    assert f(ctx, "next")["marker"] == 1
    assert f(ctx, "end")["marker"] == 3
    assert f(ctx, "prev")["marker"] == 2
    assert f(ctx, 2)["marker"] == 2
    assert f(ctx, 0)["marker"] == 0
    assert f(ctx, items[1])["marker"] == 1  # marker just before the item
    assert f(ctx, 3)["marker"] == 3


def test_marker_boundaries_are_not_errors(ctx, fake_design):
    build(fake_design, "s")
    history.set_marker(ctx, "end")
    assert history.set_marker(ctx, "next")["marker"] == 1
    history.set_marker(ctx, "begin")
    assert history.set_marker(ctx, "prev")["marker"] == 0


def test_marker_out_of_range_is_invalid_params(ctx, fake_design):
    build(fake_design, "s", "e", "e")
    for bad in (4, -1):
        e = raises_action(history.set_marker, ctx, bad)
        assert e.code == "INVALID_PARAMS" and "0..3" in e.message


def test_marker_position_union_validates():
    assert validate("set_marker", {"position": "begin"}).position == "begin"
    assert validate("set_marker", {"position": 2}).position == 2
    ref = validate("set_marker", {"position": {"index": 1}}).position
    assert not isinstance(ref, (str, int))


def test_marker_item_inside_expanded_group_collapses_and_retries(ctx, fake_design):
    a, b = build(fake_design, "s", "e")
    g = fake_design.timeline.timelineGroups.add(0, 1)
    g.isCollapsed = False
    assert history.set_marker(ctx, b)["marker"] == 1 and g.isCollapsed is True


def test_reorder_swaps_independent_items(ctx, fake_design):
    a, b, c = build(fake_design, "s", "s", "e")
    r = history.reorder(ctx, 1, 0)
    assert r["moved"] is True and r["can_reorder"] is True and r["item"]["index"] == 0
    assert [o.entity.name for o in fake_design.timeline._items] == ["Sketch1", "Sketch0", "Extrude2"]
    assert [o.index for o in fake_design.timeline._items] == [0, 1, 2]
    r = history.reorder(ctx, a, "end")
    assert r["item"]["index"] == 2


def test_reorder_silent_noop_reports_not_moved(ctx, fake_design):
    build(fake_design, "a", "b")
    fake_design.timeline.reorder_noop = True
    r = history.reorder(ctx, 1, 0)
    assert r["can_reorder"] is True and r["moved"] is False and r["item"]["index"] == 1


def test_reorder_dry_run_changes_nothing(ctx, fake_design):
    a, b = build(fake_design, "s", "s")
    r = history.reorder(ctx, 1, 0, dry_run=True)
    assert r["can_reorder"] is True and r["moved"] is False and r["item"]["index"] == 1
    assert b.index == 1 and fake_design.timeline.item(0) is a


def test_reorder_dependency_refused(ctx, fake_design):
    s, e = build(fake_design, "s", "e")
    e._deps.append(s)
    r = history.reorder(ctx, e, 0, dry_run=True)
    assert r["can_reorder"] is False and r["moved"] is False
    x = raises_action(history.reorder, ctx, e, 0)
    assert x.code == "INVALID_PARAMS" and "cannot move Extrude1 before 0" in x.message
    assert x.hints == ["a feature cannot move before the items it depends on"]
    assert e.index == 1


def test_reorder_same_item_and_out_of_range(ctx, fake_design):
    build(fake_design, "s", "s")
    assert raises_action(history.reorder, ctx, 1, 1).code == "INVALID_PARAMS"
    assert raises_action(history.reorder, ctx, 5, 0).code == "INVALID_PARAMS"
    assert raises_action(history.reorder, ctx, 0, 9).code == "INVALID_PARAMS"


def test_reorder_before_union_validates():
    assert validate("reorder", {"item": 0, "before": "end"}).before == "end"
    assert validate("reorder", {"item": 0, "before": 1}).before == 1
    ref = validate("reorder", {"item": {"index": 0}, "before": {"index": 1}})
    assert not isinstance(ref.before, (str, int))


def test_reorder_inside_expanded_group_retries(ctx, fake_design):
    a, b, c = build(fake_design, "s", "s", "s")
    g = fake_design.timeline.timelineGroups.add(0, 1)
    g.isCollapsed = False
    assert history.reorder(ctx, 1, 0)["moved"] is True and g.isCollapsed is True


def test_suppress_and_unsuppress(ctx, fake_design):
    s, e = build(fake_design, "s", "e")
    assert history.set_suppressed(ctx, 1, True)["item"]["suppressed"] is True
    assert e.isSuppressed is True
    assert history.set_suppressed(ctx, e, False)["item"]["suppressed"] is False


def test_suppress_refuses_groups_and_retries_in_expanded_group(ctx, fake_design):
    a, b = build(fake_design, "s", "e")
    g = fake_design.timeline.timelineGroups.add(0, 1)
    g.isCollapsed = False
    assert history.set_suppressed(ctx, b, True)["item"]["suppressed"] is True
    assert raises_action(history.set_suppressed, ctx, g, True).code == "INVALID_PARAMS"


def test_rename_item_and_entity(ctx, fake_design):
    s, e = build(fake_design, "s", "e")
    r = history.rename(ctx, 1, "Base")
    assert r["item"]["name"] == "Base" and e.entity.name == "Base"
    assert history.list_items(ctx)["items"][1]["name"] == "Base"
    assert raises_action(history.rename, ctx, 1, "   ").code == "INVALID_PARAMS"
    assert e.entity.name == "Base"


def test_rename_inside_expanded_group_retries(ctx, fake_design):
    a, b = build(fake_design, "s", "e")
    g = fake_design.timeline.timelineGroups.add(0, 1)
    g.isCollapsed = False
    assert history.rename(ctx, b, "Z")["item"]["name"] == "Z" and g.isCollapsed is True


# ---- group_add, group_delete, delete_after_marker (TASK-HIS-004) -------------------------------------


def run_action(app, tool_action, params, allowed=None):
    """Drive the real pipeline (validation, confirm, dispatch) for a history action."""
    key = "history." + tool_action
    from defusion import actions
    res = actions.call("history", {"action": tool_action, "params": params}, {key}, [])
    return res.structured_content


@pytest.fixture
def pipeline(fake_app, fake_design, dispatcher):
    return lambda a, p: run_action(fake_app, a, p)


def test_group_add_returns_group_and_members(ctx, fake_design):
    build(fake_design, "s", "e", "e")
    r = history.group_add(ctx, 0, 1, name="G")
    assert r["group"]["name"] == "G" and r["group"]["is_group"] is True
    assert r["members"] == [0, 1]
    items = history.list_items(ctx)["items"]
    assert [i["group"] for i in items] == [r["group"]["index"], r["group"]["index"], None]


def test_group_add_start_after_end_and_unsupported_and_ungroupable(ctx, fake_design):
    build(fake_design, "s", "e")
    assert raises_action(history.group_add, ctx, 1, 0).code == "INVALID_PARAMS"
    fake_design.timeline._can_group = False
    e = raises_action(history.group_add, ctx, 0, 1)
    assert e.code == "FUSION_ERROR" and e.message == "range cannot be grouped"
    fake_design.timeline._has_groups = False
    assert raises_action(history.group_add, ctx, 0, 1).code == "UNSUPPORTED"


def test_group_delete_keeps_or_removes_contents(ctx, fake_design):
    build(fake_design, "s", "e", "e")
    g = history.group_add(ctx, 0, 1, name="G")
    gobj = fake_design.timeline.timelineGroups.item(0)
    r = history.group_delete(ctx, gobj)
    assert r == {"deleted": "G", "contents_deleted": False, "members": ["Sketch0", "Extrude1"]}
    assert fake_design.timeline.count == 3
    assert [i["group"] for i in history.list_items(ctx)["items"]] == [None, None, None]
    history.group_add(ctx, 0, 1)
    gobj = fake_design.timeline.timelineGroups.item(0)
    r = history.group_delete(ctx, gobj, delete_contents=True)
    assert r["contents_deleted"] is True and fake_design.timeline.count == 1


def test_group_delete_refuses_non_group(ctx, fake_design):
    build(fake_design, "s")
    e = raises_action(history.group_delete, ctx, 0)
    assert e.code == "INVALID_PARAMS"


def test_group_delete_requires_confirm(pipeline, fake_design):
    build(fake_design, "s", "e")
    history.group_add(Ctx.for_app(core.Application.get(), (), [], OutputUnits.for_call(None)), 0, 1)
    r = pipeline("group_delete", {"group": {"index": 0}})
    assert r["error"]["code"] == "CONFIRM_REQUIRED"
    assert fake_design.timeline.timelineGroups.count == 1


def test_delete_after_marker(ctx, fake_design):
    build(fake_design, "s", "e", "e")
    history.set_marker(ctx, 1)
    r = history.delete_after_marker(ctx)
    assert r == {"deleted": ["Extrude1", "Extrude2"], "count": 1}
    assert history.list_items(ctx)["total"] == 1


def test_delete_after_marker_nothing_after_does_not_call_fusion(ctx, fake_design, monkeypatch):
    build(fake_design, "s")
    called = []
    monkeypatch.setattr(type(fake_design.timeline), "deleteAllAfterMarker",
                        lambda self: called.append(1) or True)
    assert history.delete_after_marker(ctx) == {"deleted": [], "count": 1}
    assert called == []


def test_delete_after_marker_requires_confirm(pipeline, fake_design):
    build(fake_design, "s", "e")
    r = pipeline("delete_after_marker", {})
    assert r["error"]["code"] == "CONFIRM_REQUIRED" and fake_design.timeline.count == 2
    r = pipeline("delete_after_marker", {"confirm": True})
    assert r["result"] == {"deleted": [], "count": 2}


# ---- snapshot actions (TASK-HIS-005) -----------------------------------------------------------------


@pytest.fixture
def posed(ctx, fake_design):
    build(fake_design, "s", "e")
    fake_design.snapshots.hasPendingSnapshot = True
    return fake_design.snapshots


def test_snapshot_add_named_and_nothing_pending(ctx, posed):
    r = history.snapshot_add(ctx, name="Pose A")
    assert r == {"snapshot": {"index": 0, "name": "Pose A", "timeline_index": 1}}
    assert history.snapshot_list(ctx)["pending"] is False
    e = raises_action(history.snapshot_add, ctx)
    assert e.code == "INVALID_PARAMS" and "no pending" in e.message
    assert posed.count == 1


def test_snapshot_add_default_name_when_none_given(ctx, posed):
    assert history.snapshot_add(ctx)["snapshot"]["name"] == "Snapshot1"


def test_snapshot_rename_by_index_and_name(ctx, posed):
    history.snapshot_add(ctx, name="Pose A")
    r = history.snapshot_rename(ctx, "Pose A", "Pose B")
    assert r["snapshot"]["name"] == "Pose B" and r["snapshot"]["index"] == 0
    assert history.snapshot_list(ctx)["items"][0]["name"] == "Pose B"
    assert history.snapshot_rename(ctx, 0, "Pose C")["snapshot"]["name"] == "Pose C"
    assert raises_action(history.snapshot_rename, ctx, "Nope", "x").code == "NOT_FOUND"
    assert raises_action(history.snapshot_rename, ctx, 4, "x").code == "INVALID_PARAMS"


def test_snapshot_ambiguous_name_lists_candidates(ctx, posed):
    for _ in range(2):
        posed.hasPendingSnapshot = True
        history.snapshot_add(ctx, name="Same")
    e = raises_action(history.snapshot_rename, ctx, "Same", "x")
    assert e.code == "AMBIGUOUS"
    assert e.extra["candidates"] == [{"index": 0, "name": "Same"}, {"index": 1, "name": "Same"}]
    assert history.snapshot_rename(ctx, 1, "Other")["snapshot"]["index"] == 1


def test_snapshot_revert_pending(ctx, posed):
    assert history.snapshot_revert_pending(ctx) == {"reverted": True}
    assert posed.reverted == 1 and posed.hasPendingSnapshot is False
    assert history.snapshot_revert_pending(ctx) == {"reverted": False}
    assert posed.reverted == 1


def test_snapshot_delete_by_name_and_index(ctx, posed):
    history.snapshot_add(ctx, name="Pose A")
    posed.hasPendingSnapshot = True
    history.snapshot_add(ctx, name="Pose B")
    assert history.snapshot_delete(ctx, "Pose B") == {"deleted": "Pose B", "count": 1}
    assert history.snapshot_delete(ctx, 0) == {"deleted": "Pose A", "count": 0}
    assert raises_action(history.snapshot_delete, ctx, "Pose A").code == "NOT_FOUND"


def test_snapshot_destructive_actions_need_confirm(pipeline, fake_design):
    build(fake_design, "s")
    fake_design.snapshots.hasPendingSnapshot = True
    for a, p in (("snapshot_revert_pending", {}), ("snapshot_delete", {"snapshot": 0})):
        assert pipeline(a, p)["error"]["code"] == "CONFIRM_REQUIRED"
    assert fake_design.snapshots.reverted == 0
    assert pipeline("snapshot_revert_pending", {"confirm": True})["result"] == {"reverted": True}


def test_snapshot_actions_unsupported_without_members(ctx, posed):
    class NoPending:
        def __init__(self):
            self.count = 0

    design = ctx.design
    design._snap = NoPending()
    for fn, args in ((history.snapshot_add, ()), (history.snapshot_revert_pending, ())):
        e = raises_action(fn, ctx, *args)
        assert e.code == "UNSUPPORTED" and "Snapshots." in e.message


def test_snapshot_actions_require_parametric(direct_ctx):
    for fn, args in ((history.snapshot_list, ()), (history.snapshot_add, ()),
                     (history.snapshot_rename, (0, "x")), (history.snapshot_revert_pending, ()),
                     (history.snapshot_delete, (0,))):
        assert raises_action(fn, direct_ctx, *args).code == "WRONG_DESIGN_TYPE"


# ---- guarded undo (TASK-HIS-006) ---------------------------------------------------------------------


def texts(app, undo=None, redo=None):
    """Script the fake's ``Transaction.Undo`` / ``Transaction.Redo`` text commands."""
    app.text_command_results["Transaction.Undo"] = undo or (lambda: "")
    app.text_command_results["Transaction.Redo"] = redo or (lambda: "")
    return app.commands


def test_undo_registered_without_undo_group():
    act = defusion.actions.registry().get("history", "undo")
    assert act.undo_group is False and act.access == "write"


def test_undo_plain(ctx, fake_design, fake_app):
    build(fake_design, "s", "e")
    cmds = texts(fake_app, undo=lambda: fake_design.timeline._items.pop())
    r = history.undo(ctx)
    assert r == {"undone": True, "design_type": "parametric"}
    assert fake_design.timeline.count == 1
    assert cmds == ["Transaction.Undo"]


def test_undo_flip_to_direct_redoes_once_and_fails(ctx, fake_design, fake_app):
    flip = lambda: setattr(fake_design, "designType", fusion.DesignTypes.DirectDesignType)  # noqa: E731
    unflip = lambda: setattr(fake_design, "designType", fusion.DesignTypes.ParametricDesignType)  # noqa: E731
    cmds = texts(fake_app, undo=flip, redo=unflip)
    e = raises_action(history.undo, ctx)
    assert e.code == "WRONG_DESIGN_TYPE"
    assert e.message.endswith("it was redone") and "drop the timeline" in e.message
    assert e.hints == ["remove the feature with history.set_suppressed or a delete action instead"]
    assert cmds == ["Transaction.Undo", "Transaction.Redo"]
    assert fake_design.designType == fusion.DesignTypes.ParametricDesignType


def test_undo_flip_with_failed_redo_says_so(ctx, fake_design, fake_app):
    texts(fake_app, undo=lambda: setattr(fake_design, "designType", fusion.DesignTypes.DirectDesignType))
    e = raises_action(history.undo, ctx)
    assert e.code == "WRONG_DESIGN_TYPE" and e.message.endswith("redo did not restore parametric mode")


def test_undo_allowed_in_direct_design(direct_ctx, fake_app):
    cmds = texts(fake_app)
    assert history.undo(direct_ctx) == {"undone": True, "design_type": "direct"}
    assert cmds == ["Transaction.Undo"]


def test_undo_without_design(fake_app):
    fake_app.activeDocument = None
    assert raises_action(history.undo, make_ctx(fake_app)).code == "NO_DESIGN"


# ---- dump_recipe core, sketches and unknown steps (TASK-HIS-007) -----------------------------------------


@pytest.fixture
def fake_describe(monkeypatch):
    """Replace the sketch set's describe with a recording stand-in (real one needs full sketch fakes)."""
    from defusion.tools import sketch as sketch_tool

    seen = []

    def describe(ctx, sk):
        seen.append((sk.name, ctx.design.timeline.markerPosition))
        return {"sketch": {"name": sk.name}}

    monkeypatch.setattr(sketch_tool, "describe", describe)
    return seen


def sketch_on(design, name, plane=None):
    sk = design.rootComponent.add_sketch(name)
    sk.referencePlane = plane or design.rootComponent.xYConstructionPlane
    return sk


def params_of(steps):
    return [(s["tool"], s["action"]) for s in steps]


def test_dump_sketch_yields_create_and_replay_pair(ctx, fake_design, fake_describe):
    sk = sketch_on(fake_design, "Sketch1")
    r = history.dump_recipe(ctx)
    src = {"index": 0, "name": "Sketch1", "type": "Sketch"}
    assert r["items"] == [
        {"tool": "sketch", "action": "create", "params": {"plane": "XY"}, "source": src},
        {"tool": "sketch", "action": "replay",
         "params": {"sketch": {"name": "Sketch1"}, "description": {"sketch": {"name": "Sketch1"}}},
         "source": src},
    ]
    assert fake_describe == [("Sketch1", 1)]  # described with the marker after the sketch
    assert (r["total"], r["offset"], r["next_offset"], r["marker"]) == (1, 0, None, 1)


def test_dump_sketch_plane_forms(ctx, fake_design, fake_describe):
    root = fake_design.rootComponent
    cp = root.add_construction_plane("Mid")
    body = root.add_body("B")
    face = body.add_face()
    sketch_on(fake_design, "A", root.xZConstructionPlane)
    sketch_on(fake_design, "B", root.yZConstructionPlane)
    sketch_on(fake_design, "C", cp)
    sketch_on(fake_design, "D", face)
    planes = [s["params"]["plane"] for s in history.dump_recipe(ctx)["items"] if s.get("action") == "create"]
    assert planes == ["XZ", "YZ", {"name": "Mid"}, {"body": {"name": "B"}, "index": 0}]


def test_dump_created_bodies_suppressed_and_groups(ctx, fake_design, fake_describe):
    root = fake_design.rootComponent
    sk = sketch_on(fake_design, "S")
    sk2 = sketch_on(fake_design, "S2")
    tl = fake_design.timeline
    orig = sk.timelineObject.rollTo
    sk.timelineObject.rollTo = lambda before: (None if before else root.add_body("Body1")) or orig(before)
    sk2.timelineObject.isSuppressed = True
    tl.timelineGroups.add(0, 1)
    items = history.dump_recipe(ctx)["items"]
    assert [s.get("bodies") for s in items] == [None, ["Body1"], None, None]
    assert [s.get("suppressed") for s in items] == [None, None, True, None]


def test_dump_unknown_steps_with_parameters_and_reason(ctx, fake_design, fake_describe):
    class LoftFeature(fusion.Feature):
        pass

    root = fake_design.rootComponent
    loft = root.add_feature("Loft1", LoftFeature)
    p = fake_design.add_parameter("d1", 1.5, user=False)
    p.createdBy, p.expression = loft, "15 mm"
    other = fake_design.add_parameter("d2", 2.0, user=False)
    other.createdBy = root.add_feature("X")
    bad = sketch_on(fake_design, "Bad")

    class Sketch(fusion.Sketch):  # same type name; the plane read raises
        @property
        def referencePlane(self):
            raise RuntimeError("plane read failed")

    bad.__class__ = Sketch
    fake_design.timeline.markerPosition = 1
    r = history.dump_recipe(ctx)
    loft_step, _x, bad_step = r["items"]
    assert loft_step == {
        "kind": "unknown", "type": "LoftFeature", "name": "Loft1", "parameters": {"d1": "15 mm"},
        "reason": "unsupported timeline item type: LoftFeature",
        "source": {"index": 0, "name": "Loft1", "type": "LoftFeature"},
    }
    assert bad_step["kind"] == "unknown" and bad_step["reason"].startswith("RuntimeError: ")
    assert bad_step["parameters"] == {} and bad_step["type"] == "Sketch"
    assert r["marker"] == 1 and fake_design.timeline.markerPosition == 1  # restored after the raising item


def test_dump_pages_by_timeline_index(ctx, fake_design, fake_describe):
    for i in range(5):
        sketch_on(fake_design, "S%d" % i)
    fake_design.add_parameter("wall", 2.0)
    r = history.dump_recipe(ctx, offset=0, limit=2)
    assert [s["source"]["index"] for s in r["items"] if "source" in s] == [0, 0, 1, 1]
    assert params_of(r["items"])[0] == ("model", "parameter_create")
    assert (r["total"], r["next_offset"]) == (5, 2)
    r = history.dump_recipe(ctx, offset=2, limit=2)
    assert {s["source"]["index"] for s in r["items"]} == {2, 3} and r["next_offset"] == 4
    assert ("model", "parameter_create") not in params_of(r["items"])
    r = history.dump_recipe(ctx, offset=4, limit=2)
    assert {s["source"]["index"] for s in r["items"]} == {4} and r["next_offset"] is None


def test_dump_parameter_steps_follow_dependencies(ctx, fake_design, fake_describe):
    sketch_on(fake_design, "S")
    a = fake_design.add_parameter("a", 1.0)
    b = fake_design.add_parameter("b", 2.0)
    c = fake_design.add_parameter("c", 3.0)
    a.expression, b.expression, c.expression = "b * 2", "c / 2", "10 mm"
    a.dependencyParameters, b.dependencyParameters = [b], [c]
    b.unit, b.comment = "mm", "note"
    steps = [s for s in history.dump_recipe(ctx)["items"] if s["action"] == "parameter_create"]
    assert [s["params"]["name"] for s in steps] == ["c", "b", "a"]
    assert steps[1] == {"tool": "model", "action": "parameter_create",
                        "params": {"name": "b", "expression": "c / 2", "unit": "mm", "comment": "note"}}


def test_dump_marker_restore_that_does_not_take_effect(ctx, fake_design, fake_describe):
    sk = sketch_on(fake_design, "S")
    tl = fake_design.timeline
    tl.markerPosition = 0
    orig = sk.timelineObject.rollTo

    def roll(before):
        orig(before)
        tl._ignore_marker_set = not before  # the restore is ignored once the item is read
        return True

    sk.timelineObject.rollTo = roll
    e = raises_action(history.dump_recipe, ctx)
    assert e.code == "FUSION_ERROR" and e.message == "marker restore failed: at 1, expected 0"


def test_dump_marker_restore_that_raises(ctx, fake_design, fake_describe):
    sk = sketch_on(fake_design, "S")
    tl = fake_design.timeline
    tl.markerPosition = 0

    Base = type(tl)

    class Raising(Base):
        armed = False

        @property
        def markerPosition(self):
            return Base.markerPosition.fget(self)

        @markerPosition.setter
        def markerPosition(self, v):
            if self.armed:
                raise RuntimeError("setter failed")
            Base.markerPosition.fset(self, v)

    tl.__class__ = Raising
    orig = sk.timelineObject.rollTo

    def roll(before):
        orig(before)
        tl.armed = not before
        return True

    sk.timelineObject.rollTo = roll
    e = raises_action(history.dump_recipe, ctx)
    assert e.message == "marker restore failed: at 1, expected 0"


def test_dump_21_large_sketches_page_stays_under_64kib(pipeline, fake_design, monkeypatch):
    from defusion.tools import sketch as sketch_tool

    monkeypatch.setattr(sketch_tool, "describe",
                        lambda ctx, sk: {"curves": [{"i": i, "kind": "SketchLine", "pad": "x" * 20} for i in range(25)]})
    for i in range(21):
        sketch_on(fake_design, "Sketch%d" % i)
    r = pipeline("dump_recipe", {})
    assert "error" not in r, r
    assert len(json.dumps(r["result"])) < 64 * 1024
    assert r["result"]["next_offset"] == 20 and len(r["result"]["items"]) == 40


def test_dump_requires_parametric_design(direct_ctx):
    assert raises_action(history.dump_recipe, direct_ctx).code == "WRONG_DESIGN_TYPE"


def test_dump_skips_group_items(ctx, fake_design, fake_describe):
    sketch_on(fake_design, "S")
    tl = fake_design.timeline
    tl._items.append(fusion.TimelineGroup(1, [], tl))
    r = history.dump_recipe(ctx)
    assert r["total"] == 2 and params_of(r["items"]) == [("sketch", "create"), ("sketch", "replay")]


# ---- feature mapping (TASK-HIS-008) ---------------------------------------------------------------------

FO = fusion.FeatureOperations


def mp(value, expression=None, deps=()):
    """A ModelParameter; ``deps=None`` leaves ``dependencyParameters`` absent."""
    p = fusion.ModelParameter("p", value, expression)
    if deps is not None:
        p.dependencyParameters = list(deps)
    return p


class Props:
    def __init__(self, area, centroid):
        self.area, self.centroid = area, core.Point3D(*centroid)


class Scene:
    """Sketch1 with 2 profiles, Body1 with 3 faces and 3 edges, all in the root component."""

    def __init__(self, design):
        self.design, self.root = design, design.rootComponent
        self.sketch = self.root.add_sketch("Sketch1")
        self.sketch.referencePlane = self.root.xYConstructionPlane
        self.profiles = []
        for area, c in ((4.0, (0, 0, 0)), (9.0, (5, 0, 0))):
            pr = self.sketch.add_profile()
            pr.parentSketch = self.sketch
            pr.areaProperties = lambda a=area, c=c: Props(a, c)
            self.profiles.append(pr)
        self.body = self.root.add_body("Body1")
        self.faces = [self.body.add_face() for _ in range(3)]
        self.edges = [self.body.add_edge() for _ in range(3)]

    def feature(self, cls, name, **attrs):
        f = self.root.add_feature(name, cls)
        f.__dict__.update(attrs)
        return f

    def dump_steps(self, ctx):
        steps = history.dump_recipe(ctx)["items"]
        return [s for s in steps if s.get("source", {}).get("index", 0) >= 1 or s.get("kind")]


@pytest.fixture
def scene(fake_design, fake_describe):
    return Scene(fake_design)


def one_step(scene, ctx):
    (step,) = [s for s in scene.dump_steps(ctx)]
    return step


def extrude_attrs(scene, **over):
    a = dict(profile=scene.profiles[1], extentOne=fusion.DistanceExtentDefinition(distance=mp(1.0, "10 mm")),
             extentTwo=None, taperAngleOne=mp(0, "0 deg"), operation=FO.NewBodyFeatureOperation)
    a.update(over)
    return a


def test_dump_feature_extrude(ctx, scene):
    scene.feature(fusion.ExtrudeFeature, "Extrude1", **extrude_attrs(scene))
    step = one_step(scene, ctx)
    assert step == {
        "tool": "model", "action": "extrude",
        "params": {"profile": {"sketch": {"name": "Sketch1"}, "index": 1}, "distance": "10 mm",
                   "operation": "new_body"},
        "source": {"index": 1, "name": "Extrude1", "type": "ExtrudeFeature"},
    }


def test_dump_feature_extrude_values_expression_kept_or_fixed(ctx, scene):
    user = fusion.UserParameter("wall", 2.0)
    auto = mp(1.0, "d1 * 2")
    cases = [
        (mp(1.0, "wall / 2", deps=[user]), "wall / 2"),    # user parameter reference: expression kept
        (mp(1.2, "Sketch1_d * 2", deps=[auto]), "12 mm"),  # model parameter reference: fixed value
        (mp(0.75, "7.5 mm", deps=None), "7.5 mm"),         # no dependencyParameters attribute: fixed value
    ]
    for dist, _ in cases:
        scene.feature(fusion.ExtrudeFeature, "E", **extrude_attrs(
            scene, extentOne=fusion.DistanceExtentDefinition(distance=dist)))
    got = [s["params"]["distance"] for s in history.dump_recipe(ctx)["items"] if s.get("action") == "extrude"]
    assert got == [c[1] for c in cases]


def test_dump_feature_extrude_output_units_inches(fake_app, scene):
    scene.feature(fusion.ExtrudeFeature, "E", **extrude_attrs(
        scene, extentOne=fusion.DistanceExtentDefinition(distance=mp(2.54, "x", deps=None))))
    c = Ctx.for_app(fake_app, (), [], OutputUnits.for_call({"length": "in"}))
    assert one_step(scene, c)["params"]["distance"] == "1 in"


def test_dump_feature_extrude_profile_collection_gives_index_list(ctx, scene):
    scene.feature(fusion.ExtrudeFeature, "E", **extrude_attrs(scene, profile=scene.profiles))
    assert one_step(scene, ctx)["params"]["profile"] == {"sketch": {"name": "Sketch1"}, "index": [0, 1]}


@pytest.mark.parametrize("over,reason", [
    ({"extentTwo": object()}, "extrude has a second extent"),
    ({"taperAngleOne": mp(0.1, "5.7 deg")}, "extrude has a taper angle"),
    ({"extentOne": fusion.SymmetricExtentDefinition()}, "extrude extent is not a plain distance"),
])
def test_dump_feature_extrude_conditions_make_unknown(ctx, scene, over, reason):
    scene.feature(fusion.ExtrudeFeature, "E", **extrude_attrs(scene, **over))
    step = one_step(scene, ctx)
    assert step["kind"] == "unknown" and step["reason"] == reason and step["type"] == "ExtrudeFeature"


def test_dump_feature_unnamed_reference_makes_unknown(ctx, scene):
    scene.edges[0].assemblyContext = object()
    es = fusion.ConstantRadiusFilletEdgeSet(edges=[scene.edges[0]], radius=mp(0.1, "1 mm"))
    scene.feature(fusion.FilletFeature, "F", edgeSets=[es])
    assert one_step(scene, ctx)["reason"] == "unnamed reference: BRepEdge"


def test_dump_feature_profile_not_found_is_unknown(ctx, scene):
    stray = fusion.Profile()
    stray.parentSketch = scene.sketch
    stray.areaProperties = lambda: Props(99.0, (0, 0, 0))
    scene.feature(fusion.ExtrudeFeature, "E", **extrude_attrs(scene, profile=stray))
    assert one_step(scene, ctx)["reason"] == "unnamed reference: Profile"


def test_dump_feature_revolve(ctx, scene):
    ext = fusion.AngleExtentDefinition(angle=mp(1.5707963267948966, "90 deg"), isSymmetric=True)
    scene.feature(fusion.RevolveFeature, "R", profile=scene.profiles[0], axis=scene.root.zConstructionAxis,
                  extentDefinition=ext, operation=FO.JoinFeatureOperation)
    assert one_step(scene, ctx)["params"] == {
        "profile": {"sketch": {"name": "Sketch1"}, "index": 0}, "axis": "Z", "angle": "90 deg",
        "extent": "symmetric", "operation": "join"}


def test_dump_feature_revolve_edge_axis_and_not_symmetric_and_bad_extent(ctx, scene):
    ext = fusion.AngleExtentDefinition(angle=mp(1.0, "57 deg", deps=None), isSymmetric=False)
    scene.feature(fusion.RevolveFeature, "R", profile=scene.profiles[0], axis=scene.edges[1],
                  extentDefinition=ext, operation=FO.CutFeatureOperation)
    scene.feature(fusion.RevolveFeature, "R2", profile=scene.profiles[0], axis=scene.edges[1],
                  extentDefinition=fusion.DistanceExtentDefinition(), operation=FO.CutFeatureOperation)
    a, b = [s for s in scene.dump_steps(ctx)]
    assert "extent" not in a["params"] and a["params"]["axis"] == {"body": {"name": "Body1"}, "index": 1}
    assert a["params"]["angle"] == "%s deg" % format(1.0 * 180 / 3.141592653589793, ".10g")
    assert b["kind"] == "unknown" and b["reason"] == "revolve extent is not an angle"


def _revolve_with_axis(scene, axis):
    ext = fusion.AngleExtentDefinition(angle=mp(1.0, "57 deg", deps=None), isSymmetric=False)
    scene.feature(fusion.RevolveFeature, "R", profile=scene.profiles[0], axis=axis,
                  extentDefinition=ext, operation=FO.CutFeatureOperation)


def test_dump_axis_sketch_line_found(ctx, scene):
    scene.sketch.add_line()
    line = scene.sketch.add_line()
    _revolve_with_axis(scene, line)
    st = one_step(scene, ctx)
    assert st["params"]["axis"] == {"sketch": {"name": "Sketch1"}, "line": 1}
    defusion.actions.registry().get(st["tool"], st["action"]).model.model_validate(st["params"])


def test_dump_axis_sketch_line_occurrence_context_is_unknown(ctx, scene):
    other = scene.root.add_sketch("Sketch2")
    line = other.add_line()
    other.assemblyContext = object()
    _revolve_with_axis(scene, line)
    (step,) = [x for x in scene.dump_steps(ctx) if x.get("type") == "RevolveFeature"]
    assert step["reason"] == "unnamed reference: SketchLine"


def test_dump_axis_sketch_line_not_found_is_unknown(ctx, scene):
    scene.sketch.add_line()
    stray = fusion.SketchLine(scene.sketch)
    _revolve_with_axis(scene, stray)
    assert one_step(scene, ctx)["reason"] == "unnamed reference: SketchLine"


def test_dump_profile_forms_validate_against_models(ctx, scene):
    s2 = scene.root.add_sketch("Sketch2")
    p2 = s2.add_profile(area=1.0, centroid=(0, 0, 0))
    ext = fusion.AngleExtentDefinition(angle=mp(1.0, "57 deg", deps=None), isSymmetric=False)
    line = scene.sketch.add_line()
    cases = {
        "int": scene.profiles[1],
        "list": scene.profiles,
        "two": [scene.profiles[0], p2, scene.profiles[1]],
    }
    expected = {
        "int": {"sketch": {"name": "Sketch1"}, "index": 1},
        "list": {"sketch": {"name": "Sketch1"}, "index": [0, 1]},
        "two": [{"sketch": {"name": "Sketch1"}, "index": [0, 1]}, {"sketch": {"name": "Sketch2"}, "index": 0}],
    }
    for k, prof in cases.items():
        scene.feature(fusion.ExtrudeFeature, "E_" + k, **extrude_attrs(scene, profile=prof))
        scene.feature(fusion.RevolveFeature, "R_" + k, profile=prof, axis=line, extentDefinition=ext,
                      operation=FO.CutFeatureOperation)
    steps = [s for s in scene.dump_steps(ctx) if s.get("action") in ("extrude", "revolve")]
    assert len(steps) == 6
    for st in steps:
        key = st["source"]["name"].split("_")[1]
        assert st["params"]["profile"] == expected[key]
        defusion.actions.registry().get(st["tool"], st["action"]).model.model_validate(st["params"])


def test_dump_profile_unmatched_is_unknown(ctx, scene):
    stray = fusion.Profile(area=99.0)
    stray.parentSketch = scene.sketch
    scene.feature(fusion.ExtrudeFeature, "E", **extrude_attrs(scene, profile=[scene.profiles[0], stray]))
    assert one_step(scene, ctx)["reason"] == "unnamed reference: Profile"


def test_dump_feature_fillet_and_chamfer(ctx, scene):
    scene.feature(fusion.FilletFeature, "F", edgeSets=[
        fusion.ConstantRadiusFilletEdgeSet(edges=[scene.edges[0], scene.edges[2]], radius=mp(0.2, "2 mm"))])
    scene.feature(fusion.ChamferFeature, "C", edgeSets=[
        fusion.EqualDistanceChamferEdgeSet(edges=[scene.edges[1]], distance=mp(0.1, "1 mm"))])
    scene.feature(fusion.FilletFeature, "F2", edgeSets=[
        fusion.ConstantRadiusFilletEdgeSet(edges=[], radius=mp(0.1)),
        fusion.ConstantRadiusFilletEdgeSet(edges=[], radius=mp(0.1))])
    scene.feature(fusion.FilletFeature, "F3", edgeSets=[fusion.VariableRadiusFilletEdgeSet(edges=[])])
    f, c, f2, f3 = scene.dump_steps(ctx)
    b = {"name": "Body1"}
    assert f["params"] == {"edges": [{"body": b, "index": 0}, {"body": b, "index": 2}], "radius": "2 mm"}
    assert (c["action"], c["params"]) == ("chamfer", {"edges": [{"body": b, "index": 1}], "distance": "1 mm"})
    assert f2["kind"] == "unknown" and f3["kind"] == "unknown"


def test_dump_feature_shell(ctx, scene):
    scene.feature(fusion.ShellFeature, "S", inputEntities=[scene.faces[1]], insideThickness=mp(0.2, "2 mm"))
    scene.feature(fusion.ShellFeature, "S2", inputEntities=[scene.body], insideThickness=mp(0.2, "2 mm"))
    a, b = scene.dump_steps(ctx)
    assert a["params"] == {"faces": [{"body": {"name": "Body1"}, "index": 1}], "thickness": "2 mm"}
    assert b["kind"] == "unknown" and b["reason"] == "shell inputs are not faces"


def pattern_attrs(scene, two=False):
    a = dict(inputEntities=[scene.body], directionOneEntity=scene.root.xConstructionAxis,
             quantityOne=mp(3, "3"), distanceOne=mp(5.0, "50 mm"),
             patternDistanceType=fusion.PatternDistanceType.ExtentPatternDistanceType,
             directionTwoEntity=None)
    if two:
        a.update(directionTwoEntity=scene.root.yConstructionAxis, quantityTwo=mp(2, "2", deps=None),
                 distanceTwo=mp(1.0, "10 mm"),
                 patternDistanceType=fusion.PatternDistanceType.SpacingPatternDistanceType)
    return a


def test_dump_feature_rect_pattern_one_and_two_directions(ctx, scene):
    scene.feature(fusion.RectangularPatternFeature, "P1", **pattern_attrs(scene))
    scene.feature(fusion.RectangularPatternFeature, "P2", **pattern_attrs(scene, two=True))
    scene.feature(fusion.RectangularPatternFeature, "P3", **{**pattern_attrs(scene), "inputEntities": [scene.faces[0]]})
    p1, p2, p3 = scene.dump_steps(ctx)
    assert p1["params"] == {"entities": [{"name": "Body1"}], "direction_one": "X", "quantity_one": "3",
                            "distance_one": "50 mm", "distance_type": "extent"}
    assert p2["params"]["direction_two"] == "Y" and p2["params"]["quantity_two"] == 2
    assert p2["params"]["distance_two"] == "10 mm" and p2["params"]["distance_type"] == "spacing"
    assert p3["kind"] == "unknown" and "not bodies or features" in p3["reason"]


def test_dump_feature_rect_pattern_of_features_uses_feature_names(ctx, scene):
    ext = scene.feature(fusion.ExtrudeFeature, "Extrude1", **extrude_attrs(scene))
    scene.feature(fusion.RectangularPatternFeature, "P", **{**pattern_attrs(scene), "inputEntities": [ext]})
    step = scene.dump_steps(ctx)[-1]
    assert step["params"]["entities"] == [{"name": "Extrude1"}]


def test_dump_feature_circ_pattern_and_mirror(ctx, scene):
    scene.feature(fusion.CircularPatternFeature, "C", inputEntities=[scene.body], axis=scene.root.zConstructionAxis,
                  quantity=mp(4, "4"), totalAngle=mp(6.283185307179586, "360 deg"), isSymmetric=False)
    scene.feature(fusion.MirrorFeature, "M", inputEntities=[scene.body], mirrorPlane=scene.root.yZConstructionPlane)
    scene.feature(fusion.MirrorFeature, "M2", inputEntities=[scene.body], mirrorPlane=scene.faces[2])
    c, m, m2 = scene.dump_steps(ctx)
    assert c["params"] == {"entities": [{"name": "Body1"}], "axis": "Z", "quantity": "4",
                           "total_angle": "360 deg", "symmetric": False}
    assert m["params"] == {"entities": [{"name": "Body1"}], "plane": "YZ"}
    assert m2["params"]["plane"] == {"body": {"name": "Body1"}, "index": 2}


def test_dump_feature_combine(ctx, scene):
    tool = scene.root.add_body("Tool")
    scene.feature(fusion.CombineFeature, "Cmb", targetBody=scene.body, toolBodies=[tool],
                  operation=FO.CutFeatureOperation, isKeepToolBodies=True)
    assert one_step(scene, ctx)["params"] == {
        "target": {"name": "Body1"}, "tools": [{"name": "Tool"}], "operation": "cut", "keep_tools": True}


def hole_attrs(scene, **over):
    a = dict(holeType=fusion.HoleTypes.SimpleHoleType,
             extentDefinition=fusion.DistanceExtentDefinition(distance=mp(1.0, "10 mm")),
             holePositionDefinition=fusion.PointHolePositionDefinition(planarEntity=scene.faces[0],
                                                                      point=core.Point3D(1.25, 0, 0)),
             holeDiameter=mp(0.5, "5 mm"), tipAngle=mp(2.0943951023931953, "120 deg"))
    a.update(over)
    return a


def test_dump_feature_hole(ctx, scene):
    scene.feature(fusion.HoleFeature, "H", **hole_attrs(scene))
    assert one_step(scene, ctx)["params"] == {
        "face": {"body": {"name": "Body1"}, "index": 0}, "point": ["12.5 mm", "0 mm", "0 mm"],
        "diameter": "5 mm", "depth": "10 mm", "tip_angle": "120 deg"}


@pytest.mark.parametrize("over,reason", [
    ({"holeType": fusion.HoleTypes.CounterboreHoleType}, "hole is not a simple hole"),
    ({"extentDefinition": fusion.SymmetricExtentDefinition()}, "hole extent is not a distance"),
    ({"holePositionDefinition": object()}, "hole position is not a single point"),
])
def test_dump_feature_hole_conditions_make_unknown(ctx, scene, over, reason):
    scene.feature(fusion.HoleFeature, "H", **hole_attrs(scene, **over))
    assert one_step(scene, ctx)["reason"] == reason


def test_dump_feature_hole_without_position_definition_attribute(ctx, scene):
    attrs = hole_attrs(scene)
    del attrs["holePositionDefinition"]
    scene.feature(fusion.HoleFeature, "H", **attrs)
    assert one_step(scene, ctx)["reason"] == "hole position is not a single point"


def thread_info(**over):
    a = dict(threadType="ISO Metric profile", threadDesignation="M6x1", threadClass="6g", isInternal=False)
    a.update(over)
    return fusion.ThreadInfo(**a)


def test_dump_feature_thread_full_and_partial(ctx, scene):
    scene.feature(fusion.ThreadFeature, "T", inputEntities=[scene.faces[0]], threadInfo=thread_info(isRightHanded=True),
                  isModeled=True, isFullLength=True)
    scene.feature(fusion.ThreadFeature, "T2", inputCylindricalFace=scene.faces[1], threadInfo=thread_info(),
                  isModeled=False, isFullLength=False, threadLength=mp(1.0, "10 mm"), threadOffset=mp(0.1, "1 mm"))
    t, t2 = scene.dump_steps(ctx)
    assert t["params"] == {
        "faces": [{"body": {"name": "Body1"}, "index": 0}], "thread_type": "ISO Metric profile",
        "designation": "M6x1", "thread_class": "6g", "internal": False, "modeled": True,
        "full_length": True, "right_handed": True}
    assert t2["params"]["faces"] == [{"body": {"name": "Body1"}, "index": 1}]
    assert "right_handed" not in t2["params"]
    assert (t2["params"]["length"], t2["params"]["offset"], t2["params"]["full_length"]) == ("10 mm", "1 mm", False)


def test_dump_feature_construction_planes(ctx, scene):
    root = scene.root

    def plane(name, d):
        p = root.add_construction_plane(name)
        p.definition = d
        return p

    plane("P1", fusion.ConstructionPlaneOffsetDefinition(planarEntity=root.xYConstructionPlane, offset=mp(0.5, "5 mm")))
    plane("P2", fusion.ConstructionPlaneAtAngleDefinition(linearEntity=root.xConstructionAxis,
                                                          planarEntity=root.xZConstructionPlane, angle=mp(0.5235987755982988, "30 deg")))
    plane("P3", fusion.ConstructionPlaneMidplaneDefinition(planarEntityOne=root.xYConstructionPlane,
                                                           planarEntityTwo=scene.faces[0]))
    plane("P4", object())
    a, b, c, d = scene.dump_steps(ctx)
    assert a["params"] == {"method": "offset", "plane": "XY", "offset": "5 mm"}
    assert b["params"] == {"method": "angle", "axis": "X", "plane": "XZ", "angle": "30 deg"}
    assert c["params"] == {"method": "midplane", "plane": "XY", "plane_two": {"body": {"name": "Body1"}, "index": 0}}
    assert d["kind"] == "unknown"


def test_dump_feature_construction_axes(ctx, scene):
    root = scene.root
    a1 = root.add_construction_axis("A1")
    a1.definition = fusion.ConstructionAxisByLineDefinition(linearEntity=scene.edges[1])
    a2 = root.add_construction_axis("A2")
    a2.definition = fusion.ConstructionAxisTwoPlaneDefinition(planarEntityOne=root.xYConstructionPlane,
                                                              planarEntityTwo=root.yZConstructionPlane)
    a3 = root.add_construction_axis("A3")
    a3.definition = fusion.ConstructionAxisByLineDefinition(linearEntity=root.zConstructionAxis)
    x, y, z = scene.dump_steps(ctx)
    assert x["params"] == {"method": "edge", "edge": {"body": {"name": "Body1"}, "index": 1}}
    assert y["params"] == {"method": "two_planes", "plane": "XY", "plane_two": "YZ"}
    assert z["kind"] == "unknown"


def rot_z(deg):
    import math
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [c, -s, 0, 0, s, c, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def move_feature(scene, values):
    return scene.feature(fusion.MoveFeature, "Mv", inputEntities=[scene.body], transform=fusion.ArrayMatrix(values))


def test_dump_feature_move_decomposition(ctx, scene):
    import math
    ident = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    move_feature(scene, [1, 0, 0, 1.25, 0, 1, 0, 0, 0, 0, 1, -0.5, 0, 0, 0, 1])           # translation only
    move_feature(scene, rot_z(90))                                                         # +Z rotation
    move_feature(scene, rot_z(-90))                                                        # same as -Z by +90
    move_feature(scene, [1, 0, 0, 0, 0, -1, 0, 0, 0, 0, -1, 0, 0, 0, 0, 1])               # 180 deg about X
    r = rot_z(30)
    r[3], r[7] = 1.0, 2.0
    move_feature(scene, r)                                                                 # both
    s = 1 / math.sqrt(2)
    move_feature(scene, [0.5, 0.5, s, 0, 0.5, 0.5, -s, 0, -s, s, 0, 0, 0, 0, 0, 1])        # axis not origin
    move_feature(scene, ident)                                                             # no motion
    steps = scene.dump_steps(ctx)
    p = [x.get("params") for x in steps]
    b = [{"name": "Body1"}]
    assert p[0] == {"bodies": b, "translate": ["12.5 mm", "0 mm", "-5 mm"]}
    assert p[1] == {"bodies": b, "rotate_axis": "Z", "angle": "90 deg"}
    assert p[2] == {"bodies": b, "rotate_axis": "Z", "angle": "-90 deg"}
    assert p[3]["rotate_axis"] == "X" and p[3]["angle"] == "180 deg"
    assert p[4] == {"bodies": b, "translate": ["10 mm", "20 mm", "0 mm"], "rotate_axis": "Z", "angle": "30 deg"}
    assert steps[5]["kind"] == "unknown" and steps[5]["reason"] == "rotation axis is not an origin axis"
    assert p[6] == {"bodies": b}


def test_dump_feature_move_without_transform_is_unknown(ctx, scene):
    scene.feature(fusion.MoveFeature, "Mv", inputEntities=[scene.body])
    assert one_step(scene, ctx)["reason"] == "move feature has no transform"


def registered_action(tool, name):
    import importlib
    for mod in ("sketch", "model"):
        try:
            importlib.import_module("defusion.tools." + mod)
        except ImportError:
            pass
    act = defusion.actions.registry().get(tool, name)
    assert act is not None, "%s.%s is not registered: update design § 6/§ 7.3 to the sketch/model names" % (tool, name)
    return act


def feature_rows(scene):
    """One fake per § 7.3 row (plus the sketch pair and a parameter step)."""
    root = scene.root
    scene.feature(fusion.ExtrudeFeature, "E", **extrude_attrs(scene))
    scene.feature(fusion.RevolveFeature, "R", profile=scene.profiles[0], axis=root.zConstructionAxis,
                  extentDefinition=fusion.AngleExtentDefinition(angle=mp(1.0, "57 deg"), isSymmetric=True),
                  operation=FO.JoinFeatureOperation)
    scene.feature(fusion.FilletFeature, "F", edgeSets=[
        fusion.ConstantRadiusFilletEdgeSet(edges=[scene.edges[0]], radius=mp(0.2, "2 mm"))])
    scene.feature(fusion.ChamferFeature, "Ch", edgeSets=[
        fusion.EqualDistanceChamferEdgeSet(edges=[scene.edges[1]], distance=mp(0.1, "1 mm"))])
    scene.feature(fusion.ShellFeature, "S", inputEntities=[scene.faces[1]], insideThickness=mp(0.2, "2 mm"))
    scene.feature(fusion.RectangularPatternFeature, "RP", **pattern_attrs(scene, two=True))
    scene.feature(fusion.CircularPatternFeature, "CP", inputEntities=[scene.body], axis=root.zConstructionAxis,
                  quantity=mp(4, "4"), totalAngle=mp(6.283185307179586, "360 deg"), isSymmetric=False)
    scene.feature(fusion.MirrorFeature, "M", inputEntities=[scene.body], mirrorPlane=root.yZConstructionPlane)
    scene.feature(fusion.CombineFeature, "Cmb", targetBody=scene.body, toolBodies=[root.add_body("Tool")],
                  operation=FO.JoinFeatureOperation, isKeepToolBodies=False)
    scene.feature(fusion.HoleFeature, "H", **hole_attrs(scene))
    scene.feature(fusion.ThreadFeature, "T", inputEntities=[scene.faces[0]], threadInfo=thread_info(isRightHanded=True),
                  isModeled=False, isFullLength=False, threadLength=mp(1.0, "10 mm"), threadOffset=mp(0.0, "0 mm"))
    p = root.add_construction_plane("P1")
    p.definition = fusion.ConstructionPlaneOffsetDefinition(planarEntity=root.xYConstructionPlane, offset=mp(0.5, "5 mm"))
    p = root.add_construction_plane("P2")
    p.definition = fusion.ConstructionPlaneAtAngleDefinition(linearEntity=root.xConstructionAxis,
                                                             planarEntity=root.xZConstructionPlane, angle=mp(0.5, "30 deg"))
    p = root.add_construction_plane("P3")
    p.definition = fusion.ConstructionPlaneMidplaneDefinition(planarEntityOne=root.xYConstructionPlane,
                                                              planarEntityTwo=root.yZConstructionPlane)
    a = root.add_construction_axis("A1")
    a.definition = fusion.ConstructionAxisByLineDefinition(linearEntity=scene.edges[1])
    a = root.add_construction_axis("A2")
    a.definition = fusion.ConstructionAxisTwoPlaneDefinition(planarEntityOne=root.xYConstructionPlane,
                                                             planarEntityTwo=root.yZConstructionPlane)
    move_feature(scene, rot_z(90))


def test_dump_feature_every_step_validates_against_registered_action_models(ctx, scene):
    feature_rows(scene)
    user = scene.design.add_parameter("wall", 2.0)
    user.unit, user.comment = "mm", ""
    steps = history.dump_recipe(ctx, limit=100)["items"]
    assert not [s for s in steps if s.get("kind") == "unknown"]
    seen = set()
    for s in steps:
        act = registered_action(s["tool"], s["action"])
        act.model.model_validate(s["params"])
        seen.add("%s.%s" % (s["tool"], s["action"]))
    expected = {
        "sketch.create", "sketch.replay", "model.parameter_create", "model.extrude", "model.revolve",
        "model.fillet", "model.chamfer", "model.shell", "model.rect_pattern", "model.circ_pattern",
        "model.mirror", "model.combine", "model.hole", "model.thread", "model.construction_plane",
        "model.construction_axis", "model.move",
    }
    assert seen == expected


# ---- coil dump (TASK-HIS-009) -------------------------------------------------------------------------------

PLANE_REF = {"type": "ConstructionPlane", "name": "Mid", "token": "tok-x", "component": "Root"}


def coil_request(**over):
    params = {"plane": dict(PLANE_REF), "center": ["0 mm", "0 mm"], "diameter": "30 mm",
              "coil_type": "revolution_height", "revolutions": "3", "height": "d1 * 2", "pitch": None,
              "taper_angle": "0 deg", "section": "circular", "section_position": "on_center",
              "section_size": "1 mm", "clockwise": False, "operation": "new_body",
              "participant_bodies": [{"type": "BRepBody", "name": "B", "token": "tok-b", "component": "Sub"},
                                     {"type": "BRepBody", "name": "C", "token": "tok-c", "component": None}],
              "component": {"type": "Component", "name": "Sub", "token": "tok-s", "component": None},
              "name": None}
    params.update(over)
    return {"format": "defusion.request/1", "tool": "model", "action": "coil", "params": params}


def add_coil(scene, ctx, request=None, base="Coil base"):
    """A base sketch then a CoilFeature with matching model parameters; returns the coil."""
    d = scene.design
    d.add_parameter("d1", 1.0)  # user parameter used by the request's height expression
    sk = scene.root.add_sketch(base)
    sk.referencePlane = scene.root.xYConstructionPlane
    coil = scene.root.add_feature("Coil1", fusion.CoilFeature)
    if request is not None:
        ctx.attr_set(coil, "request", request)
    for role, value, expr in (("Diameter", 3.0, "30 mm"), ("Revolutions", 3.0, "3"), ("Height", 2.0, "d1 * 2"),
                              ("Taper Angle", 0.0, "0 deg"), ("Section Size", 0.1, "1 mm"), ("Other", 7.0, "7")):
        p = d.add_parameter("c_" + role.replace(" ", ""), value, user=False)
        p.role, p.expression, p.createdBy = role, expr, coil
    return coil


def test_dump_coil_step_verbatim_params_with_token_free_refs(ctx, scene):
    add_coil(scene, ctx, coil_request())
    steps = scene.dump_steps(ctx)
    (step,) = steps  # the base sketch yields no step
    assert step["tool"] == "model" and step["action"] == "coil" and "stale" not in step
    expected = coil_request()["params"]
    expected["plane"] = {"name": "Mid", "component": "Root"}
    expected["participant_bodies"] = [{"name": "B", "component": "Sub"}, {"name": "C"}]
    expected["component"] = {"name": "Sub"}
    assert step["params"] == expected
    assert step["source"] == {"index": 2, "name": "Coil1", "type": "CoilFeature"}


def test_dump_coil_plain_origin_plane_string_untouched(ctx, scene):
    add_coil(scene, ctx, coil_request(plane="XZ", participant_bodies=None, component=None))
    (step,) = scene.dump_steps(ctx)
    assert step["params"]["plane"] == "XZ" and step["params"]["participant_bodies"] is None


def test_dump_coil_base_sketch_follows_request_name(ctx, scene):
    add_coil(scene, ctx, coil_request(name="Spring"), base="Spring base")
    assert [s["action"] for s in scene.dump_steps(ctx)] == ["coil"]
    steps = history.dump_recipe(ctx)["items"]
    assert [s["source"]["index"] for s in steps if s["action"] == "coil"] == [2]


def test_dump_coil_sketch_with_other_name_is_not_a_base_sketch(ctx, scene):
    add_coil(scene, ctx, coil_request(), base="My sketch")
    steps = history.dump_recipe(ctx)["items"]
    assert [s["action"] for s in steps[-3:]] == ["create", "replay", "coil"]
    assert [s["source"]["name"] for s in steps[-3:]] == ["My sketch", "My sketch", "Coil1"]


def test_dump_coil_base_sketch_check_never_raises(ctx, scene):
    tl = scene.design.timeline
    assert history._is_coil_base_sketch(ctx, tl, 0) is False
    assert history._is_coil_base_sketch(ctx, tl, 99) is False
    add_coil(scene, ctx, coil_request())
    assert history._is_coil_base_sketch(ctx, tl, 1) is True
    assert history._is_coil_base_sketch(ctx, tl, 2) is False


def test_dump_coil_stale_by_parameter_role_with_current_expressions(ctx, scene):
    add_coil(scene, ctx, coil_request())
    scene.design._param_objs[-6].value = 4.0   # Diameter edited to 40 mm
    scene.design._param_objs[-6].expression = "40 mm"
    (step,) = scene.dump_steps(ctx)
    assert step["stale"] is True
    assert step["current"] == {"diameter": "40 mm", "revolutions": "3", "height": "d1 * 2",
                               "taper_angle": "0 deg", "section_size": "1 mm"}
    assert step["params"]["diameter"] == "30 mm"  # the request stays the source of truth


def test_dump_coil_not_stale_within_tolerance_and_unmatched_roles_ignored(ctx, scene):
    add_coil(scene, ctx, coil_request())
    scene.design._param_objs[-6].value = 3.0000001
    scene.design._param_objs[-1].value = 123.0   # role "Other" matches nothing
    (step,) = scene.dump_steps(ctx)
    assert "stale" not in step and "current" not in step


def test_dump_coil_null_request_values_are_not_compared(ctx, scene):
    add_coil(scene, ctx, coil_request(height=None))
    scene.design._param_objs[-4].value = 99.0   # Height
    (step,) = scene.dump_steps(ctx)
    assert "stale" not in step


def test_dump_coil_unevaluable_request_value_is_stale(ctx, scene):
    add_coil(scene, ctx, coil_request(height="nonsense + 1"))
    (step,) = scene.dump_steps(ctx)
    assert step["stale"] is True and step["current"]["height"] == "d1 * 2"


@pytest.mark.parametrize("request_value", [
    None,
    "not json {",
    ["list"],
    {"format": "other/1", "tool": "model", "action": "coil", "params": {}},
    {"format": "defusion.request/1", "tool": "sketch", "action": "coil", "params": {}},
    {"format": "defusion.request/1", "tool": "model", "action": "extrude", "params": {}},
    {"format": "defusion.request/1", "tool": "model", "action": "coil", "params": "x"},
])
def test_dump_coil_missing_or_malformed_request_is_unknown(ctx, scene, request_value):
    coil = add_coil(scene, ctx, None)
    if request_value == "not json {":
        coil.attributes.add("defusion", "request", request_value)
    elif request_value is not None:
        ctx.attr_set(coil, "request", request_value)
    steps = history.dump_recipe(ctx)["items"]
    unknown = steps[-1]
    assert unknown["kind"] == "unknown" and unknown["reason"] == "coil has no stored request"
    assert unknown["parameters"]["c_Diameter"] == "30 mm"
    assert [s.get("action") for s in steps[-3:-1]] == ["create", "replay"]  # base sketch not skipped


# ---- apply_recipe_step (TASK-HIS-010) ------------------------------------------------------------------------


@pytest.fixture
def steps_registry(registry, fake_design):
    """Fake step targets in an isolated registry; ``calls`` records what ran."""
    from defusion.actions import action

    calls = []

    @action("model", access="write", name="extrude")
    def extrude(ctx, distance: str, name: str | None = None) -> dict:
        """Extrude."""
        calls.append(("model.extrude", distance))
        root = ctx.design.rootComponent
        root.add_feature("Extrude1")
        root.add_body("Body1")
        root.add_body("Body2")
        return {"feature": "Extrude1"}

    @action("model", access="write", name="make_plane")
    def make_plane(ctx) -> dict:
        """Plane."""
        ctx.design.rootComponent.add_construction_plane("Plane1")
        return {}

    @action("model", access="destructive", name="purge")
    def purge(ctx) -> dict:
        """Purge."""
        calls.append(("model.purge",))
        return {}

    @action("document", access="write", name="export")
    def export(ctx) -> dict:
        """Export."""
        calls.append(("document.export",))
        return {}

    @action("cam", access="write", name="post_process")
    def post_process(ctx) -> dict:
        """Post."""
        calls.append(("cam.post_process",))
        return {}

    return calls


ALL = ("model.extrude", "model.make_plane", "model.purge", "document.export", "cam.post_process")


def apply_ctx(app, allowed=ALL):
    return make_ctx(app, allowed)


def test_apply_runs_step_and_restores_names(fake_app, fake_design, steps_registry):
    c = apply_ctx(fake_app)
    step = {"tool": "model", "action": "extrude", "params": {"distance": "10 mm"},
            "source": {"index": 1, "name": "Base", "type": "ExtrudeFeature"}, "bodies": ["Plate", "Lid"]}
    r = history.apply_recipe_step(c, step)
    assert steps_registry == [("model.extrude", "10 mm")]
    assert r["step"] == {"tool": "model", "action": "extrude"} and r["result"] == {"feature": "Extrude1"}
    assert fake_design.timeline.item(0).entity.name == "Base"
    assert [b.name for b in fake_design.rootComponent.bRepBodies] == ["Plate", "Lid"]
    assert [x["name"] for x in r["renamed"]] == ["Base", "Plate", "Lid"]
    assert "warnings" not in r and "stale" not in r


def test_apply_body_count_mismatch_warns_and_renames_pairwise(fake_app, fake_design, steps_registry):
    r = history.apply_recipe_step(apply_ctx(fake_app), {
        "tool": "model", "action": "extrude", "params": {"distance": "1 mm"}, "bodies": ["Only", "B", "C"]})
    assert r["warnings"] == ["created 2 bodies, recipe lists 3"]
    assert [b.name for b in fake_design.rootComponent.bRepBodies] == ["Only", "B"]
    r = history.apply_recipe_step(apply_ctx(fake_app), {
        "tool": "model", "action": "extrude", "params": {"distance": "1 mm"}, "bodies": ["Z"]})
    assert r["warnings"] == ["created 2 bodies, recipe lists 1"]


def test_apply_suppressed_and_stale_echo(fake_app, fake_design, steps_registry):
    r = history.apply_recipe_step(apply_ctx(fake_app), {
        "tool": "model", "action": "extrude", "params": {"distance": "1 mm"},
        "suppressed": True, "stale": True, "current": {"diameter": "4 mm"}})
    assert r["stale"] is True and fake_design.timeline.item(0).isSuppressed is True


def test_apply_source_name_needs_exactly_one_new_item(fake_app, fake_design, steps_registry):
    root = fake_design.rootComponent
    r = history.apply_recipe_step(apply_ctx(fake_app), {
        "tool": "model", "action": "make_plane", "params": {}, "source": {"name": "Mid"}})
    assert root.constructionPlanes.item(0).name == "Mid" and r["renamed"][0]["name"] == "Mid"
    # a step that adds no timeline item renames nothing
    from defusion.actions import action

    @action("model", access="write", name="noop")
    def noop(ctx) -> dict:
        """No-op."""
        return {}

    r = history.apply_recipe_step(apply_ctx(fake_app, ALL + ("model.noop",)), {
        "tool": "model", "action": "noop", "params": {}, "source": {"name": "X"}})
    assert r["renamed"] == []


def test_apply_in_direct_design_still_renames_bodies(fake_app, steps_registry):
    d = fusion.Design(parametric=False)
    fake_app.activeDocument = core.Document("Doc", {"DesignProductType": d})
    history.apply_recipe_step(apply_ctx(fake_app), {
        "tool": "model", "action": "extrude", "params": {"distance": "1 mm"},
        "source": {"name": "Base"}, "bodies": ["Plate", "Lid"]})
    assert [b.name for b in d.rootComponent.bRepBodies] == ["Plate", "Lid"]


def test_apply_unknown_step_refused_with_reason(fake_app, fake_design, steps_registry):
    e = raises_action(history.apply_recipe_step, apply_ctx(fake_app), {
        "kind": "unknown", "type": "LoftFeature", "name": "Loft1", "reason": "unsupported", "parameters": {}})
    assert e.code == "INVALID_PARAMS" and e.message == "unknown step LoftFeature Loft1 cannot be replayed"
    assert e.hints == ["unsupported"] and steps_registry == []


@pytest.mark.parametrize("tool,action_name", [
    ("document", "export"), ("cam", "post_process"), ("model", "purge"), ("script", "execute_python")])
def test_apply_refuses_non_step_tools_and_destructive_actions(fake_app, fake_design, steps_registry, tool, action_name):
    e = raises_action(history.apply_recipe_step, apply_ctx(fake_app, ALL + ("script.execute_python",)),
            {"tool": tool, "action": action_name, "params": {}})
    assert e.code == "INVALID_PARAMS" and e.message == "%s.%s cannot be a recipe step" % (tool, action_name)
    assert steps_registry == []  # nothing ran


@pytest.mark.parametrize("bad,fragment", [
    ({"tool": "model", "params": {}}, "step.action"),
    ({"tool": "model", "action": "extrude"}, "step.params"),
    ({"tool": 3, "action": "extrude", "params": {}}, "step.tool"),
    ({"tool": "model", "action": "extrude", "params": {}, "extra": 1}, "'extra'"),
    ({"tool": "model", "action": "extrude", "params": {}, "bodies": "x"}, "step.bodies"),
    ({"tool": "model", "action": "extrude", "params": {}, "bodies": [1]}, "step.bodies"),
    ({"tool": "model", "action": "extrude", "params": {}, "suppressed": "yes"}, "step.suppressed"),
])
def test_apply_key_checks(fake_app, fake_design, steps_registry, bad, fragment):
    e = raises_action(history.apply_recipe_step, apply_ctx(fake_app), bad)
    assert e.code == "INVALID_PARAMS" and fragment in e.message and steps_registry == []


def test_apply_profile_without_target_action_is_not_allowed(fake_app, fake_design, steps_registry):
    e = raises_action(history.apply_recipe_step, apply_ctx(fake_app, ("model.make_plane",)),
            {"tool": "model", "action": "extrude", "params": {"distance": "1 mm"}})
    assert e.code == "NOT_ALLOWED" and steps_registry == []


def test_apply_misspelt_param_is_invalid_params_with_schema(fake_app, fake_design, steps_registry):
    e = raises_action(history.apply_recipe_step, apply_ctx(fake_app),
            {"tool": "model", "action": "extrude", "params": {"distanse": "1 mm"}})
    assert e.code == "INVALID_PARAMS" and "properties" in e.extra["schema"] and steps_registry == []


def test_apply_inner_error_propagates_unchanged(fake_app, fake_design, registry):
    from defusion.actions import action

    @action("model", access="write", name="boom")
    def boom(ctx) -> dict:
        """Boom."""
        raise ActionError("NOT_FOUND", "no such thing")

    e = raises_action(history.apply_recipe_step, apply_ctx(fake_app, ("model.boom",)),
            {"tool": "model", "action": "boom", "params": {}})
    assert e.code == "NOT_FOUND" and e.message == "no such thing"


def test_apply_registered_as_write_with_step_object():
    act = defusion.actions.registry().get("history", "apply_recipe_step")
    assert act.access == "write" and act.timeout == 180 and act.undo_group is True


# ---- REQ-HIS-021: every parametric action refuses a direct design -----------------------------------------------

PARAMETRIC_CALLS = {
    "list_items": {}, "snapshot_list": {}, "dump_recipe": {}, "set_marker": {"position": "end"},
    "reorder": {"item": 0, "before": "end"}, "set_suppressed": {"item": 0, "suppressed": True},
    "rename": {"item": 0, "name": "x"}, "group_add": {"start": 0, "end": 1},
    "snapshot_add": {}, "snapshot_rename": {"snapshot": 0, "name": "x"}, "group_delete": {"group": 0},
    "delete_after_marker": {}, "snapshot_revert_pending": {}, "snapshot_delete": {"snapshot": 0},
}


def test_design_type_fourteen_parametric_actions_refuse_direct_designs(direct_ctx):
    assert len(PARAMETRIC_CALLS) == 14
    for fn_name, kwargs in PARAMETRIC_CALLS.items():
        e = raises_action(getattr(history, fn_name), direct_ctx, **kwargs)
        assert e.code == "WRONG_DESIGN_TYPE", fn_name


def test_design_type_exempt_actions_do_not_require_parametric():
    for name in ("undo", "apply_recipe_step"):
        assert name not in PARAMETRIC_CALLS


def test_unsupported_group_actions_without_timeline_groups(ctx, fake_design):
    build(fake_design, "s", "e")
    fake_design.timeline._has_groups = False
    assert raises_action(history.group_add, ctx, 0, 1).code == "UNSUPPORTED"
    assert raises_action(history.group_delete, ctx, 0).code == "UNSUPPORTED"
