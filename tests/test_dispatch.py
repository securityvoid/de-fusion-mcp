import sys
import threading
import time

import adsk
import pytest
from adsk import core

from defusion import dispatch
from defusion.errors import ActionError
from helpers import wait_for


def bg(fn, *a, **k):
    """Run fn in a thread; returns (thread, box) where box gets 'ok' or 'exc'."""
    box = {}

    def target():
        try:
            box["ok"] = fn(*a, **k)
        except Exception as e:  # noqa: BLE001
            box["exc"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    return t, box


def test_run_returns_result_on_main_thread(dispatcher, fake_app):
    assert dispatcher.run(lambda: threading.get_ident()) == fake_app.main_thread_id


def test_exception_propagates(dispatcher):
    with pytest.raises(ZeroDivisionError):
        dispatcher.run(lambda: 1 / 0)
    assert dispatcher.run(lambda: "still works") == "still works"


def test_action_error_propagates_unchanged(dispatcher):
    def boom():
        raise ActionError("NOT_FOUND", "x", job_id="j1")

    with pytest.raises(ActionError) as e:
        dispatcher.run(boom)
    assert e.value.code == "NOT_FOUND" and e.value.extra == {"job_id": "j1"}


def test_dropped_first_fire_is_refired(dispatcher, fake_app):
    fake_app.drop_next_fires(1)
    start = time.time()
    assert dispatcher.run(lambda: 42, timeout=5) == 42
    assert len(core.FIRE_THREADS) >= 2
    assert time.time() - start >= dispatch.REFIRE_SECONDS * 0.5


def test_refires_every_100ms_while_queued(dispatcher, fake_app):
    fake_app.hold()
    n0 = len(core.FIRE_THREADS)
    t, box = bg(dispatcher.run, lambda: 1, 5)
    time.sleep(0.55)
    assert len(core.FIRE_THREADS) - n0 >= 4
    fake_app.release()
    t.join(3)
    assert box["ok"] == 1


def test_runs_inline_without_adsk(monkeypatch):
    monkeypatch.setitem(sys.modules, "adsk", None)
    monkeypatch.setitem(sys.modules, "adsk.core", None)
    assert dispatch.run(lambda: threading.get_ident()) == threading.get_ident()
    with pytest.raises(ValueError):
        dispatch.run(lambda: (_ for _ in ()).throw(ValueError("x")))


def test_items_run_in_order(dispatcher, fake_app):
    fake_app.hold()
    order = []
    ts = []
    for i in range(5):
        ts.append(bg(dispatcher.run, lambda i=i: order.append(i) or i, 5)[0])
        assert wait_for(lambda i=i: dispatcher.queue_depth() == i + 1)
    fake_app.release()
    for t in ts:
        t.join(3)
    assert order == [0, 1, 2, 3, 4]


# ---- timeouts (REQ-008) -------------------------------------------------------------------------

def test_queued_timeout_is_not_executed_and_never_runs(dispatcher, fake_app):
    fake_app.hold()
    ran = []
    with pytest.raises(ActionError) as e:
        dispatcher.run(lambda: ran.append(1), timeout=0.15)
    assert e.value.code == "NOT_EXECUTED" and "did not run; safe to retry" in e.value.message
    assert dispatcher.queue_depth() == 0
    fake_app.release()
    time.sleep(0.2)
    assert ran == []


def make_running_job(dispatcher, label="model.extrude", result=7, exc=None, timeout=0.3):
    started, go = threading.Event(), threading.Event()

    def fn():
        started.set()
        go.wait(5)
        if exc:
            raise exc
        return result

    box = {}
    t = threading.Thread(target=lambda: box.update(r=dispatcher.run(fn, timeout, label)), daemon=True)
    t.start()
    assert started.wait(2)
    t.join(3)
    return box["r"], go


def test_running_timeout_becomes_job(dispatcher):
    r, go = make_running_job(dispatcher)
    assert r["status"] == "running" and len(r["job_id"]) == 8 and "job_status" in r["hint"]
    job = dispatcher.get_job(r["job_id"])
    assert job["state"] == "running" and job["action"] == "model.extrude"
    assert job["finished"] is None and job["started"] <= time.time()
    assert dispatcher.running_count() == 1
    go.set()
    assert wait_for(lambda: dispatcher.get_job(r["job_id"])["state"] == "done")
    job = dispatcher.get_job(r["job_id"])
    assert job["result"] == 7 and job["finished"] >= job["started"]
    assert dispatcher.running_count() == 0


def test_job_that_raises_is_failed(dispatcher):
    r, go = make_running_job(dispatcher, exc=RuntimeError("kaput"))
    go.set()
    assert wait_for(lambda: dispatcher.get_job(r["job_id"])["state"] == "failed")
    err = dispatcher.get_job(r["job_id"])["error"]
    assert err["code"] == "FUSION_ERROR" and "kaput" in err["message"]


def test_job_with_action_error_keeps_code(dispatcher):
    r, go = make_running_job(dispatcher, exc=ActionError("NOT_FOUND", "gone", ["h"]))
    go.set()
    assert wait_for(lambda: dispatcher.get_job(r["job_id"])["state"] == "failed")
    assert dispatcher.get_job(r["job_id"])["error"]["code"] == "NOT_FOUND"


def test_oversize_job_result_is_failed_result_too_large(dispatcher):
    r, go = make_running_job(dispatcher, result={"s": "x" * 70000})
    go.set()
    assert wait_for(lambda: dispatcher.get_job(r["job_id"])["state"] == "failed")
    job = dispatcher.get_job(r["job_id"])
    assert job["error"]["code"] == "RESULT_TOO_LARGE" and "result" not in job


def test_job_value_maps_the_stored_result(dispatcher):
    started, go = threading.Event(), threading.Event()

    def fn():
        started.set()
        go.wait(5)
        return ("raw", {"extra": 1})

    box = {}
    t = threading.Thread(
        target=lambda: box.update(r=dispatcher.run(fn, 0.3, "a.b", job_value=lambda r: r[0])), daemon=True
    )
    t.start()
    t.join(3)
    go.set()
    jid = box["r"]["job_id"]
    assert wait_for(lambda: dispatcher.get_job(jid)["state"] == "done")
    assert dispatcher.get_job(jid)["result"] == "raw"


def test_queued_timeout_while_job_running_names_the_job(dispatcher):
    r, go = make_running_job(dispatcher, label="script.run")
    with pytest.raises(ActionError) as e:
        dispatcher.run(lambda: 1, timeout=0.15)
    assert e.value.code == "NOT_EXECUTED"
    assert e.value.message == "Fusion is busy with job %s (script.run); wait or poll server.job_status" % r["job_id"]
    assert e.value.extra["job_id"] == r["job_id"]
    go.set()
    assert wait_for(lambda: dispatcher.get_job(r["job_id"])["state"] == "done")
    assert dispatcher.run(lambda: 5) == 5


def test_queue_cap_of_64(dispatcher, fake_app):
    fake_app.hold()
    ran = []
    threads = []
    for i in range(64):
        threads.append(bg(dispatcher.run, lambda i=i: ran.append(i), 10)[0])
    assert wait_for(lambda: dispatcher.queue_depth() == 64)
    start = time.time()
    with pytest.raises(ActionError) as e:
        dispatcher.run(lambda: ran.append("65th"), timeout=10)
    assert time.time() - start < 1 and e.value.code == "NOT_EXECUTED" and "retry" in e.value.message
    assert dispatcher.queue_depth() == 64
    fake_app.release()
    for t in threads:
        t.join(5)
    assert sorted(ran) == list(range(64))


def test_exactly_one_of_run_or_cancel_wins(dispatcher):
    ran, outcomes = [], []
    for i in range(60):
        try:
            r = dispatcher.run(lambda i=i: ran.append(i) or "ran", timeout=0.001 * (i % 4))
            outcomes.append((i, "running" if isinstance(r, dict) else "ran"))
        except ActionError as e:
            assert e.code == "NOT_EXECUTED"
            outcomes.append((i, "cancelled"))
    time.sleep(0.3)
    for i, o in outcomes:
        assert (i in ran) == (o != "cancelled"), (i, o)


# ---- re-entrancy (REQ-048) ------------------------------------------------------------------------------

def test_handler_not_reentrant(dispatcher, fake_app):
    order = []
    inner_started = threading.Event()

    def first():
        order.append("first-start")
        t, box = bg(dispatcher.run, second, 5)
        assert wait_for(lambda: dispatcher.queue_depth() == 1)
        adsk.doEvents()  # re-delivers the custom event on the main thread (like adsk.doEvents)
        assert dispatcher.queue_depth() == 1  # the nested delivery drained nothing
        order.append("first-end")
        return box, t

    def second():
        order.append("second")
        return 2

    box, t = dispatcher.run(first, 5)
    t.join(3)
    assert box["ok"] == 2
    assert order == ["first-start", "first-end", "second"]


# ---- registry (REQ-009) ---------------------------------------------------------------------------------

def test_registry_keeps_last_100(dispatcher):
    ids = []
    with dispatch._lock:
        for i in range(101):
            ids.append(dispatch._new_job("t.a%d" % i))
    assert dispatcher.get_job(ids[0]) is None
    assert dispatcher.get_job(ids[1])["action"] == "t.a1"
    assert dispatcher.get_job(ids[100])["action"] == "t.a100"
    assert len(dispatch._jobs) == 100
    assert dispatcher.running_count() == 100


def test_get_job_unknown_and_copy(dispatcher):
    assert dispatcher.get_job("nope") is None
    r, go = make_running_job(dispatcher)
    j = dispatcher.get_job(r["job_id"])
    j["state"] = "tampered"
    assert dispatcher.get_job(r["job_id"])["state"] == "running"
    go.set()


def test_clear_jobs(dispatcher):
    with dispatch._lock:
        jid = dispatch._new_job("a.b")
    dispatcher.clear_jobs()
    assert dispatcher.get_job(jid) is None and dispatcher.running_count() == 0


# ---- accessors from a non-dispatcher thread (REQ-049) ------------------------------------------------------------

def test_accessors_from_worker_thread(dispatcher, fake_app):
    fake_app.hold()
    ran = []
    t1, _ = bg(dispatcher.run, lambda: ran.append(1), 5)
    assert wait_for(lambda: dispatcher.queue_depth() == 1)
    seen = {}
    core.reset_calls()
    t, _ = bg(lambda: seen.update(depth=dispatcher.queue_depth(), run=dispatcher.running_count(),
                                  job=dispatcher.get_job("zz")))
    t.join(2)
    assert seen == {"depth": 1, "run": 0, "job": None}
    assert core.CALLS == []  # accessors never touch adsk
    fake_app.release()
    t1.join(3)
    assert ran == [1]


# ---- stop support --------------------------------------------------------------------------------------------------

def test_fail_queued_gives_waiters_not_executed(dispatcher, fake_app):
    fake_app.hold()
    ran = []
    t, box = bg(dispatcher.run, lambda: ran.append(1), 10)
    assert wait_for(lambda: dispatcher.queue_depth() == 1)
    dispatcher.fail_queued()
    t.join(2)
    assert box["exc"].code == "NOT_EXECUTED"
    assert dispatcher.queue_depth() == 0
    fake_app.release()
    time.sleep(0.1)
    assert ran == []


def test_unregister_then_register_again(fake_app):
    dispatch.register()
    dispatch.unregister()
    dispatch.register()
    assert dispatch.run(lambda: 3) == 3
    dispatch.unregister()
    with pytest.raises(ActionError):
        dispatch.run(lambda: 3)
