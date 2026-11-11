# Helpers shared by the live scenarios of the tool modules (not a tool module).
import contextlib
import json
import sys

_MM = {"length": "mm", "angle": "deg"}


def _lc():
    """The live_check module driving the run (``__main__`` when run as a script)."""
    main = sys.modules.get("__main__")
    if main is not None and hasattr(main, "call") and hasattr(main, "open_session"):
        return main
    import live_check

    return live_check


async def _do(session, tool, act, params):
    env = await _lc().call(session, tool, act, params, units=_MM)
    assert env["ok"], "%s.%s: %s" % (tool, act, json.dumps(env.get("error"))[:400])
    return env["result"]


@contextlib.asynccontextmanager
async def _fresh(session):
    """A new design for one scenario, closed afterwards (the harness's scratch design stays untouched)."""
    lc = _lc()
    scratch = [d for d in (await _do(session, "document", "list_open", {}))["items"] if d["active"]][0]["id"]
    doc = await _do(session, "document", "create", {})
    try:
        yield
    finally:
        await lc.call(session, "document", "close", {"document": doc["id"], "confirm": True})
        await lc.call(session, "document", "activate", {"document": scratch})


def _near(got, want, tol=1e-3):
    """Assert ``got`` equals ``want`` (numbers or equal-length sequences) within ``tol``."""
    if not isinstance(got, (list, tuple)):
        got, want = [got], [want]
    assert len(got) == len(want) and all(abs(a - b) <= tol for a, b in zip(got, want)), (got, want)
    return True
