"""Call pipeline tests: error order, undo grouping, change summary, threads, jobs, inline calls."""
import logging
import threading
from typing import Any
import time

import pytest
from adsk import core

from defusion import actions, results
from defusion.actions import action, call, call_inline
from defusion.context import Ctx
from defusion.errors import ActionError
from defusion.types import Body, Length
from helpers import err


@pytest.fixture
def env(registry, dispatcher, fake_design):
    """Registry + registered dispatcher + design; returns a tiny harness."""
    class H:
        pass

    h = H()
    h.design, h.app, h.registry = fake_design, core.Application.get(), registry
    h.seen = []
    return h


def run(tool, act, params=None, allowed=None, units=None, **kw):
    args = {"action": act}
    if params is not None:
        args["params"] = params
    if units is not None:
        args["units"] = units
    allowed = allowed if allowed is not None else frozenset(a.key for a in actions.registry())
    return call(tool, args, allowed, ["all"], **kw)


def ok(r):
    assert r.is_error is False, r.structured_content
    return r.structured_content


@pytest.fixture
def basic(env):
    reg = env.registry

    @action("model", access="write")
    def make(ctx: Ctx, name: str = "B") -> dict:
        """Make a body."""
        env.seen.append(("fn", threading.get_ident()))
        ctx.component().add_body(name)
        return {"made": name}

    @action("model", access="read")
    def peek(ctx: Ctx) -> dict:
        """Peek."""
        env.seen.append(("fn", threading.get_ident()))
        return {"n": 1}

    @action("model", access="write")
    def noop(ctx: Ctx) -> dict:
        """Do nothing."""
        return {}

    @action("model", access="destructive")
    def purge(ctx: Ctx, what: str = "x") -> dict:
        """Purge."""
        env.seen.append(("purge", what))
        return {"purged": what}

    return env


# ---- ordering of errors (REQ-013) ----------------------------------------------------------------------

def test_not_allowed_before_unknown(basic):
    e = err(run("model", "ghost", {}, allowed=frozenset({"model.peek"})))
    assert e["code"] == "NOT_ALLOWED" and "model.ghost" in e["message"] and e["hints"] == ["peek"]


def test_unknown_action_when_allowed_but_unregistered(basic):
    e = err(run("model", "ghost", {}, allowed=frozenset({"model.ghost", "model.peek"})))
    assert e["code"] == "UNKNOWN_ACTION" and e["hints"] == ["peek"]


def test_not_allowed_by_profile_hides_registered_action(basic):
    assert err(run("model", "make", {}, allowed=frozenset({"model.peek"})))["code"] == "NOT_ALLOWED"


def test_denied_note_in_not_allowed(basic):
    r = run("model", "make", {}, allowed=frozenset(), denied_note="config error: bad port")
    assert "config error: bad port" in err(r)["message"]


def test_invalid_params_hints_and_schema(basic):
    e = err(run("model", "make", {"name": 5, "bogus": 1}))
    assert e["code"] == "INVALID_PARAMS"
    assert any(h.startswith("bogus:") for h in e["hints"]) and any(h.startswith("name:") for h in e["hints"])
    assert e["schema"]["properties"]["name"]["type"] == "string"
    assert err(run("model", "make", "notanobject"))["code"] == "INVALID_PARAMS"


def test_missing_params_defaults_to_empty(basic):
    assert ok(run("model", "peek"))["result"] == {"n": 1}


def test_invalid_units_on_read_action_and_hidden_action(basic):
    assert err(run("model", "peek", {}, units={"length": "furlong"}))["code"] == "INVALID_PARAMS"
    assert err(run("model", "peek", {}, units={"bogus": "mm"}))["code"] == "INVALID_PARAMS"
    r = run("model", "make", {}, allowed=frozenset({"model.peek"}), units={"length": "furlong"})
    assert err(r)["code"] == "NOT_ALLOWED"


def test_confirm_required_for_destructive(basic):
    e = err(run("model", "purge", {"what": "all"}))
    assert e["code"] == "CONFIRM_REQUIRED"
    assert basic.seen == []
    assert err(run("model", "purge", {"confirm": False}))["code"] == "CONFIRM_REQUIRED"
    assert err(run("model", "purge", {"confirm": "true"}))["code"] == "CONFIRM_REQUIRED"
    assert ok(run("model", "purge", {"what": "all", "confirm": True}))["result"] == {"purged": "all"}
    assert basic.seen == [("purge", "all")]  # confirm is not passed to the function


def test_invalid_params_beats_confirm_required(basic):
    assert err(run("model", "purge", {"what": 5}))["code"] == "INVALID_PARAMS"


def test_non_string_action(basic):
    assert err(call("model", {"action": 5}, frozenset({"model.peek"}), []))["code"] == "INVALID_PARAMS"


# ---- help ------------------------------------------------------------------------------------------------------------

def test_help_through_call(basic):
    allowed = frozenset({"model.peek", "model.make"})
    r = ok(run("model", "help", {}, allowed=allowed, units={"length": "furlong"}))  # help ignores units
    assert r["action"] == "help" and [a["name"] for a in r["result"]["actions"]] == ["make", "peek"]
    d = ok(run("model", "help", {"action": "peek"}, allowed=allowed))["result"]
    assert d["name"] == "peek" and d["access"] == "read"
    assert err(run("model", "help", {"action": "purge"}, allowed=allowed))["code"] == "NOT_ALLOWED"
    assert err(run("model", "help", {}, allowed=frozenset({"view.x"})))["code"] == "NOT_ALLOWED"


# ---- main thread, undo group, change summary (REQ-014, 026, 027, 005) -------------------------------------------------------

def test_sequence_and_threads(basic, monkeypatch):
    events = []
    app = basic.app
    orig = app.executeTextCommand
    monkeypatch.setattr(app, "executeTextCommand", lambda c: events.append((c, threading.get_ident())) or orig(c))
    real_snapshot = results.snapshot
    monkeypatch.setattr(results, "snapshot", lambda d, u: events.append(("snapshot", threading.get_ident())) or real_snapshot(d, u))
    orig_fn = basic.registry.get("model", "make").fn
    basic.registry.get("model", "make").fn = lambda ctx, **k: events.append(("fn", threading.get_ident())) or orig_fn(ctx, **k)
    core.reset_calls()
    r = ok(run("model", "make", {"name": "Z"}))
    assert [e[0] for e in events] == [
        "snapshot", 'PTransaction.Start "De-Fusion model.make"', "fn", "PTransaction.Commit", "snapshot"]
    main = app.main_thread_id
    assert {t for _, t in events} == {main}
    assert core.calling_threads() <= {main}  # every fake adsk call ran on the dispatcher thread
    assert r["tool"] == "model" and r["action"] == "make" and r["units"] == {"length": "mm", "angle": "deg"}
    assert "undo_grouped" not in r


def test_read_action_has_no_transaction_or_snapshot(basic, monkeypatch):
    monkeypatch.setattr(results, "snapshot", lambda *a: pytest.fail("snapshot on read"))
    r = ok(run("model", "peek", {}))
    assert basic.app.commands == [] and "changes" not in r


def test_changes_for_added_body_and_noop(basic):
    r = ok(run("model", "make", {}))
    assert r["changes"]["bodies"] == [0, 1]
    assert r["changes"]["bbox"][0] is None and r["changes"]["bbox"][1] == pytest.approx([0, 0, 0, 10, 10, 10])
    assert "timeline" not in r["changes"]
    assert ok(run("model", "noop", {}))["changes"] == {}


def test_bbox_failure_is_null_not_error(basic):
    b = basic.design.rootComponent.add_body("Broken")
    b._bbox_error = True
    r = ok(run("model", "make", {}))
    assert r["changes"] == {"bodies": [1, 2]}


def test_write_without_document_has_empty_changes(basic):
    basic.app.activeDocument = None
    r = ok(run("model", "noop", {}))
    assert r["changes"] == {} and basic.app.commands == []


def test_write_that_deletes_the_design_has_empty_changes(basic, monkeypatch):
    calls = []
    real = results.snapshot

    def snapshot(d, u):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("4 : An API Object refers to a deleted Object")
        return real(d, u)

    monkeypatch.setattr(results, "snapshot", snapshot)
    assert ok(run("model", "noop", {}))["changes"] == {}


def test_undo_commands_on_success(basic):
    ok(run("model", "make", {}))
    assert basic.app.commands == ['PTransaction.Start "De-Fusion model.make"', "PTransaction.Commit"]


def test_failure_aborts_and_returns_error(basic):
    @action("model", access="write")
    def boom(ctx: Ctx) -> dict:
        """Boom."""
        raise RuntimeError("x")

    e = err(run("model", "boom", {}))
    assert e["code"] == "FUSION_ERROR" and "x" in e["message"] and "RuntimeError" in e["message"]
    assert basic.app.commands == ['PTransaction.Start "De-Fusion model.boom"', "PTransaction.Abort"]


def test_abort_raising_keeps_original_error(basic):
    basic.app.text_command_errors["Abort"] = RuntimeError("abort failed")

    @action("model", access="write")
    def boom(ctx: Ctx) -> dict:
        """Boom."""
        raise ActionError("NOT_FOUND", "original")

    e = err(run("model", "boom", {}))
    assert e["code"] == "NOT_FOUND" and e["message"] == "original"
    assert basic.app.commands[-1] == "PTransaction.Abort"


def test_start_failure_runs_ungrouped(basic):
    basic.app.text_command_errors["PTransaction.Start"] = RuntimeError("nope")
    r = ok(run("model", "make", {}))
    assert r["undo_grouped"] is False and r["changes"]["bodies"] == [0, 1]
    assert basic.app.commands == ['PTransaction.Start "De-Fusion model.make"']  # no Commit


def test_commit_failure_returns_result_ungrouped(basic, caplog):
    basic.app.text_command_errors["PTransaction.Commit"] = RuntimeError("nope")
    with caplog.at_level(logging.WARNING, logger="defusion"):
        r = ok(run("model", "make", {}))
    assert r["result"] == {"made": "B"} and r["undo_grouped"] is False
    assert any("Commit" in rec.message for rec in caplog.records)


def test_undo_group_false_issues_no_commands(basic):
    @action("document", access="write", undo_group=False)
    def save(ctx: Ctx) -> dict:
        """Save."""
        return {}

    r = ok(run("document", "save", {}))
    assert basic.app.commands == [] and "undo_grouped" not in r and r["changes"] == {}


def test_destructive_is_grouped(basic):
    ok(run("model", "purge", {"confirm": True}))
    assert basic.app.commands[0] == 'PTransaction.Start "De-Fusion model.purge"'


# ---- parameters, units, errors ---------------------------------------------------------------------------------------------

def test_typed_params_are_resolved(basic):
    b = basic.design.rootComponent.add_body("Target")
    got = {}

    @action("model", access="read")
    def use(ctx: Ctx, body: Body, d: Length, bodies: list[Body] = []) -> dict:
        """Use."""
        got.update(body=body, d=d, units=ctx.units, bodies=bodies, allowed=ctx.allowed, items=ctx.profile_items)
        return {"ref": ctx.ref(body), "cm": d.cm}

    r = ok(run("model", "use", {"body": "Target", "d": "1 in", "bodies": [{"token": b.entityToken}]},
               units={"length": "in"}))
    assert got["body"] is b and got["bodies"] == [b] and got["units"] == {"length": "in", "angle": "deg"}
    assert r["result"]["cm"] == pytest.approx(2.54) and r["units"] == {"length": "in", "angle": "deg"}
    assert r["result"]["ref"]["token"] == b.entityToken
    assert "model.use" in got["allowed"] and got["items"] == ["all"]


def test_unresolvable_ref_is_error_envelope_and_no_transaction(basic):
    @action("model", access="write")
    def use(ctx: Ctx, body: Body) -> dict:
        """Use."""

    e = err(run("model", "use", {"body": "Nope"}))
    assert e["code"] == "NOT_FOUND" and basic.app.commands == []


def test_output_units_default_from_argument(basic):
    r = ok(run("model", "peek", {}, output_units=__import__("defusion.units", fromlist=["x"]).OutputUnits("in", "rad")))
    assert r["units"] == {"length": "in", "angle": "rad"}
    r = ok(run("model", "peek", {}, units={"angle": "deg"}, output_units=__import__("defusion.units", fromlist=["x"]).OutputUnits("in", "rad")))
    assert r["units"] == {"length": "in", "angle": "deg"}


def test_action_error_extras_copied(basic):
    @action("model", access="read")
    def bad(ctx: Ctx) -> dict:
        """Bad."""
        raise ActionError("NOT_FOUND", "m", ["h"], job_id="j1", candidates=[{"a": 1}])

    e = err(run("model", "bad", {}))
    assert e["job_id"] == "j1" and e["candidates"] == [{"a": 1}] and e["hints"] == ["h"]


def test_result_too_large(basic):
    @action("model", access="read")
    def big(ctx: Ctx) -> dict:
        """Big."""
        return {"s": "x" * 70000}

    assert err(run("model", "big", {}))["code"] == "RESULT_TOO_LARGE"


def test_no_design_error(basic):
    @action("model", access="read")
    def needs(ctx: Ctx) -> dict:
        """Needs."""
        ctx.require_design()

    basic.app.activeDocument = None
    assert err(run("model", "needs", {}))["code"] == "NO_DESIGN"


# ---- main_thread=False (REQ-041) ------------------------------------------------------------------------------------------------

def test_main_thread_false_runs_without_dispatcher(basic):
    got = {}

    @action("server", access="read", main_thread=False)
    def status(ctx: Ctx, n: int = 0) -> dict:
        """Status."""
        got.update(app=ctx.app, ui=ctx.ui, document=ctx.document, design=ctx.design, thread=threading.get_ident(),
                   units=ctx.units)
        return {"n": n}

    basic.app.hold()
    start = time.time()
    r = ok(run("server", "status", {"n": 3}))
    assert time.time() - start < 1 and r["result"] == {"n": 3} and "changes" not in r
    assert got["thread"] == threading.get_ident() != basic.app.main_thread_id
    assert (got["app"], got["ui"], got["document"], got["design"]) == (None, None, None, None)
    assert basic.app.commands == []
    basic.app.release()


def test_main_thread_false_not_time_limited(basic):
    @action("server", access="read", timeout=0.05, main_thread=False)
    def slow(ctx: Ctx) -> dict:
        """Slow."""
        time.sleep(0.3)
        return {"done": True}

    assert ok(run("server", "slow", {}))["result"] == {"done": True}


# ---- timeouts and jobs -----------------------------------------------------------------------------------------------------------

def test_slow_action_becomes_job(basic, dispatcher):
    @action("script", access="write", timeout=0.2)
    def slow(ctx: Ctx) -> dict:
        """Slow."""
        time.sleep(0.6)
        return {"value": 7}

    r = ok(run("script", "slow", {}))
    assert r["result"]["status"] == "running"
    jid = r["result"]["job_id"]
    deadline = time.time() + 3
    while time.time() < deadline and dispatcher.get_job(jid)["state"] == "running":
        time.sleep(0.02)
    job = dispatcher.get_job(jid)
    assert job["state"] == "done" and job["result"] == {"value": 7} and job["action"] == "script.slow"


def test_queued_timeout_is_not_executed_error(basic):
    @action("model", access="read", timeout=0.15)
    def quick(ctx: Ctx) -> dict:
        """Quick."""
        basic.seen.append("ran")
        return {}

    basic.app.hold()
    e = err(run("model", "quick", {}))
    assert e["code"] == "NOT_EXECUTED" and "safe to retry" in e["message"]
    basic.app.release()
    time.sleep(0.2)
    assert "ran" not in basic.seen


# ---- logging (REQ-037) ----------------------------------------------------------------------------------------------------------------

def test_one_log_line_per_call(basic, caplog):
    with caplog.at_level(logging.INFO, logger="defusion"):
        run("model", "peek", {})
        run("model", "ghost", {}, allowed=frozenset())
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("call ")]
    assert len(lines) == 2
    assert "tool=model" in lines[0] and "action=peek" in lines[0] and "duration_ms=" in lines[0] and "outcome=ok" in lines[0]
    assert "action=ghost" in lines[1] and "outcome=NOT_ALLOWED" in lines[1]


# ---- call_inline (REQ-043) --------------------------------------------------------------------------------------------------------------

@pytest.fixture
def nested(basic):
    @action("history", access="write")
    def inner(ctx: Ctx, n: int = 1) -> dict:
        """Inner."""
        ctx.component().add_body("inner%d" % n)
        return {"inner": n}

    @action("history", access="destructive")
    def wipe(ctx: Ctx) -> dict:
        """Wipe."""
        return {"wiped": True}

    @action("history", access="write")
    def outer(ctx: Ctx, which: str = "inner", n: int = 2, extra: dict[str, Any] | None = None) -> dict:
        """Outer."""
        if which == "inner":
            return {"got": call_inline(ctx, "history", "inner", {"n": n})}
        return {"got": call_inline(ctx, "history", which, extra or {})}

    return basic


def test_inline_one_transaction_pair_and_raw_result(nested):
    r = ok(run("history", "outer", {}))
    assert r["result"] == {"got": {"inner": 2}}
    assert nested.app.commands == ['PTransaction.Start "De-Fusion history.outer"', "PTransaction.Commit"]
    assert r["changes"]["bodies"] == [0, 1]


def test_inline_not_allowed_and_unknown(nested):
    allowed = frozenset({"history.outer"})
    e = err(run("history", "outer", {}, allowed=allowed))
    assert e["code"] == "NOT_ALLOWED" and "history.inner" in e["message"]
    allowed = frozenset({"history.outer", "history.ghost"})
    assert err(run("history", "outer", {"which": "ghost"}, allowed=allowed))["code"] == "UNKNOWN_ACTION"


def test_inline_validates_and_confirms(nested):
    assert err(run("history", "outer", {"n": "x"}))["code"] == "INVALID_PARAMS"
    assert err(run("history", "outer", {"which": "wipe"}))["code"] == "CONFIRM_REQUIRED"
    r = ok(run("history", "outer", {"which": "wipe", "extra": {"confirm": True}}))
    assert r["result"] == {"got": {"wiped": True}}


def test_inline_resolves_refs_and_takes_no_snapshot(nested, monkeypatch):
    b = nested.design.rootComponent.add_body("T")

    @action("history", access="read")
    def read_it(ctx: Ctx, body: Body) -> dict:
        """Read."""
        return {"is": body is b}

    ctx = Ctx.for_app(nested.app, frozenset({"history.read_it"}), [], __import__("defusion.units", fromlist=["x"]).OutputUnits())
    monkeypatch.setattr(results, "snapshot", lambda *a: pytest.fail("snapshot in call_inline"))
    assert call_inline(ctx, "history", "read_it", {"body": "T"}) == {"is": True}
    assert nested.app.commands == []
    with pytest.raises(ActionError) as e:
        call_inline(ctx, "history", "read_it", {"body": "nope"})
    assert e.value.code == "NOT_FOUND"
