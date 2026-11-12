"""Bulk entries: types.bulk and ctx.bulk through actions.call (REQ-CORE-050, design § 5.12)."""
import pytest
from pydantic import BaseModel, ConfigDict

from defusion import actions
from defusion.actions import action, call
from defusion.context import Ctx
from defusion.errors import ActionError
from defusion.types import bulk
from helpers import err, ok


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str
    name: str = "n"


@pytest.fixture
def env(registry, dispatcher, fake_design):
    seen = []

    @action("document", access="write")
    def touch(ctx: Ctx, files: bulk(str), big: bool = False) -> dict:
        """Touch files."""

        def one(f):
            seen.append(f)
            if f == "missing":
                raise ActionError("NOT_FOUND", "no file %s" % f, hints=["h"], file=f)
            if f == "boom":
                raise RuntimeError("kaboom")
            return {"file": f, "pad": "x" * 2048} if big else {"file": f}

        return ctx.bulk(files, one)

    @action("document", access="destructive")
    def remove(ctx: Ctx, files: bulk(str)) -> dict:
        """Remove files."""
        return ctx.bulk(files, lambda f: seen.append(f) or {"removed": f})

    @action("document", access="write")
    def rename(ctx: Ctx, files: bulk(Entry)) -> dict:
        """Rename files."""
        return ctx.bulk(files, lambda e: seen.append(e) or {"renamed": e.file})

    return seen


def run(act, params):
    allowed = frozenset(a.key for a in actions.registry())
    return call("document", {"action": act, "params": params}, allowed, ["all"])


@pytest.mark.parametrize("n", [0, 51])
def test_bounds_invalid_params_and_fn_never_called(env, n):
    e = err(run("touch", {"files": ["f%d" % i for i in range(n)]}))
    assert e["code"] == "INVALID_PARAMS"
    assert env == []


def test_fifty_entries_succeed(env):
    files = ["f%d" % i for i in range(50)]
    r = ok(run("touch", {"files": files}))
    assert r["ok"] == 50 and r["failed"] == 0
    assert [x["input"] for x in r["results"]] == files
    assert all(x["ok"] is True and x["result"] == {"file": x["input"]} for x in r["results"])


def test_failed_entry_does_not_stop_later_ones(env):
    r = ok(run("touch", {"files": ["a", "missing", "c"]}))
    assert env == ["a", "missing", "c"]
    assert [x["input"] for x in r["results"]] == ["a", "missing", "c"]
    assert r["results"][0] == {"input": "a", "ok": True, "result": {"file": "a"}}
    assert r["results"][2]["ok"] is True
    bad = r["results"][1]
    assert bad["ok"] is False and "result" not in bad
    assert bad["error"] == {"code": "NOT_FOUND", "message": "no file missing", "hints": ["h"], "file": "missing"}
    assert (r["ok"], r["failed"]) == (2, 1)


def test_other_exception_is_fusion_error(env):
    r = ok(run("touch", {"files": ["boom"]}))
    e = r["results"][0]["error"]
    assert e["code"] == "FUSION_ERROR" and "kaboom" in e["message"] and "RuntimeError" in e["message"]


def test_all_failing_still_envelope_ok(env):
    res = run("touch", {"files": ["missing", "boom"]})
    assert res.is_error is False and res.structured_content["ok"] is True
    r = res.structured_content["result"]
    assert (r["ok"], r["failed"]) == (0, 2)


def test_destructive_needs_confirm_once(env):
    e = err(run("remove", {"files": ["a", "b"]}))
    assert e["code"] == "CONFIRM_REQUIRED" and env == []
    r = ok(run("remove", {"files": ["a", "b"], "confirm": True}))
    assert env == ["a", "b"] and r["ok"] == 2


def test_model_entry_input_is_model_dump(env):
    r = ok(run("rename", {"files": [{"file": "a"}, {"file": "b", "name": "z"}]}))
    assert [x["input"] for x in r["results"]] == [{"file": "a", "name": "n"}, {"file": "b", "name": "z"}]
    assert isinstance(env[0], Entry)
    assert err(run("rename", {"files": [{"file": "a", "bogus": 1}]}))["code"] == "INVALID_PARAMS"


def test_oversized_bulk_result_says_entries_ran(env):
    e = err(run("touch", {"files": ["f%d" % i for i in range(50)], "big": True}))
    assert len(env) == 50
    assert e["code"] == "RESULT_TOO_LARGE"
    assert "all entries ran" in e["message"]
    assert e["hints"] == ["check state with document.list_open or datafile_get", "send fewer entries per call"]
    assert not any("offset" in h or "limit" in h for h in e["hints"])
