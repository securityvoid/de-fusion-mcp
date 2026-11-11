import pytest

from defusion import errors
from defusion.errors import ActionError, from_exception

DESIGN_CODES = """INVALID_PARAMS UNKNOWN_ACTION NOT_ALLOWED CONFIRM_REQUIRED NOT_FOUND AMBIGUOUS
NO_DOCUMENT NO_DESIGN WRONG_DESIGN_TYPE UNSUPPORTED TEXT_COMMAND_FAILED NOT_EXECUTED
RESULT_TOO_LARGE FUSION_ERROR INVALID_PROFILE""".split()


def test_to_dict_copies_extras():
    e = ActionError("NOT_FOUND", "m", job_id="j1", candidates=[{"a": 1}])
    assert e.to_dict() == {"code": "NOT_FOUND", "message": "m", "hints": [], "job_id": "j1",
                           "candidates": [{"a": 1}]}


def test_hints_listed():
    assert ActionError("X", "m", hints=("a", "b")).to_dict()["hints"] == ["a", "b"]


def test_from_exception():
    e = from_exception(RuntimeError("x"))
    assert e.code == "FUSION_ERROR"
    assert "x" in e.message and "RuntimeError" in e.message


def test_from_exception_keeps_action_error():
    a = ActionError("NOT_FOUND", "m")
    assert from_exception(a) is a


