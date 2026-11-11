"""Main-thread dispatcher and job registry (design § 4).

HTTP threads call ``run(fn, timeout)``; Fusion's main thread executes queued items from a custom
event handler. A call that is still queued at its timeout is cancelled; a call whose item is already
running becomes a registry job the client polls with ``server.job_status``."""
import threading
import time
import uuid
from collections import OrderedDict, deque

from . import results
from .errors import NOT_EXECUTED, ActionError, from_exception

EVENT_ID = "defusion.dispatch"
MAX_QUEUED = 64
MAX_JOBS = 100
REFIRE_SECONDS = 0.1

_lock = threading.Lock()
_queue = deque()
_jobs = OrderedDict()
_running = False  # main thread is inside an item (non re-entrant handler)
_app = None
_event = None
_handler = None
_timer = None


class _Item:
    def __init__(self, fn, label, job_value):
        self.id = uuid.uuid4().hex[:8]
        self.fn = fn
        self.label = label
        self.job_value = job_value
        self.state = "queued"  # queued | running | done | cancelled
        self.done = threading.Event()
        self.result = None
        self.exc = None
        self.job = False  # moved to the registry after the caller's timeout


# --- registration ---------------------------------------------------------------------------


def register():
    """Register custom event ``defusion.dispatch`` (main thread, from ``server.start``)."""
    global _app, _event, _handler
    import adsk.core

    class Handler(adsk.core.CustomEventHandler):
        def notify(self, args):
            _drain()

    _app = adsk.core.Application.get()
    _event = _app.registerCustomEvent(EVENT_ID)
    _handler = Handler()
    _event.add(_handler)


def unregister():
    global _app, _event, _handler, _timer
    with _lock:
        timer, _timer = _timer, None
    if timer is not None:
        timer.cancel()
    if _app is not None:
        if _event is not None and _handler is not None:
            _event.remove(_handler)
        _app.unregisterCustomEvent(EVENT_ID)
    _app = _event = _handler = None


# --- main-thread side -----------------------------------------------------------------------------


def _drain():
    """Custom-event handler body: run queued items in order; never re-entered (§ 4)."""
    global _running
    if _running:
        return
    _running = True
    try:
        while True:
            with _lock:
                if not _queue:
                    return
                item = _queue.popleft()
                item.state = "running"
            try:
                item.result = item.fn()
            except Exception as exc:  # stored and re-raised in the caller's thread
                item.exc = exc
            _finish(item)
    finally:
        _running = False


def _finish(item):
    with _lock:
        item.state = "done"
        if item.job:
            _complete_job(item)
    item.done.set()


def _complete_job(item):
    entry = _jobs.get(item.id)
    if entry is None:  # evicted while running
        return
    entry["finished"] = time.time()
    if item.exc is not None:
        entry["state"], entry["error"] = "failed", from_exception(item.exc).to_dict()
        return
    value = item.job_value(item.result) if item.job_value else item.result
    try:
        results.check_result(value)
    except ActionError as exc:
        entry["state"], entry["error"] = "failed", exc.to_dict()
        return
    entry["state"], entry["result"] = "done", value


# --- caller side ---------------------------------------------------------------------------------------


def _adsk_available():
    try:
        import adsk.core  # noqa: F401

        return True
    except ImportError:
        return False


def _fire():
    app = _app
    if app is None:
        return
    try:
        app.fireCustomEvent(EVENT_ID)
    except Exception:
        pass  # the re-fire timer retries


def _ensure_timer():
    global _timer
    with _lock:
        if _timer is not None:
            return
        _timer = threading.Timer(REFIRE_SECONDS, _tick)
        _timer.daemon = True
        timer = _timer
    timer.start()


def _tick():
    """Re-fire the event every 100 ms while items are queued (a fire can be dropped)."""
    global _timer
    with _lock:
        if not _queue or _app is None:
            _timer = None
            return
        _timer = threading.Timer(REFIRE_SECONDS, _tick)
        _timer.daemon = True
        timer = _timer
    _fire()
    timer.start()


def _outcome(item):
    if item.exc is not None:
        raise item.exc
    return item.result


def _new_job(label, job_id=None):
    """Add a running job to the registry (lock held by callers inside dispatch)."""
    job_id = job_id or uuid.uuid4().hex[:8]
    _jobs[job_id] = {
        "action": label,
        "state": "running",
        "started": time.time(),
        "finished": None,
    }
    while len(_jobs) > MAX_JOBS:
        _jobs.popitem(last=False)
    return job_id


def run(fn, timeout=30, label="", job_value=None):
    """Run ``fn`` on Fusion's main thread and return its result.

    ``label`` is the ``"<tool>.<action>"`` shown in the job registry; ``job_value`` maps the
    function's return value to the value stored for a job. Raises ``ActionError(NOT_EXECUTED)`` when
    the queue is full or the item was still queued at ``timeout``; returns
    ``{"status": "running", "job_id": ...}`` when it was already running."""
    if not _adsk_available():
        return fn()
    if _app is None:
        raise ActionError(NOT_EXECUTED, "the dispatcher is not running; restart the add-in")
    item = _Item(fn, label, job_value)
    with _lock:
        if len(_queue) >= MAX_QUEUED:
            raise ActionError(NOT_EXECUTED, "queue full; retry", hints=["retry shortly"])
        _queue.append(item)
    _fire()
    _ensure_timer()
    if item.done.wait(timeout):
        return _outcome(item)
    with _lock:
        if item.state == "queued":
            _queue.remove(item)
            item.state = "cancelled"
            busy = next(((k, j) for k, j in _jobs.items() if j["state"] == "running"), None)
            if busy:
                raise ActionError(
                    NOT_EXECUTED,
                    "Fusion is busy with job %s (%s); wait or poll server.job_status"
                    % (busy[0], busy[1]["action"]),
                    hints=["poll server.job_status with job_id"],
                    job_id=busy[0],
                )
            raise ActionError(NOT_EXECUTED, "did not run; safe to retry")
        if item.state == "running":
            item.job = True
            _new_job(item.label, item.id)
            return {
                "status": "running",
                "job_id": item.id,
                "hint": "still running; poll server.job_status with this job_id",
            }
    return _outcome(item)  # finished (or was failed) just as the timeout expired


# --- accessors (design § 12) ------------------------------------------------------------------------


def queue_depth():
    with _lock:
        return len(_queue)


def running_count():
    with _lock:
        return sum(1 for j in _jobs.values() if j["state"] == "running")


def get_job(job_id):
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job is not None else None


def fail_queued():
    """Fail every queued item with NOT_EXECUTED (``server.stop``)."""
    with _lock:
        items = list(_queue)
        _queue.clear()
        for item in items:
            item.state = "cancelled"
            item.exc = ActionError(NOT_EXECUTED, "the add-in is stopping; did not run; safe to retry")
    for item in items:
        item.done.set()


def clear_jobs():
    with _lock:
        _jobs.clear()
