import time

import pytest

from defusion.errors import ActionError


def ok(r):
    assert r.is_error is False, r.structured_content
    return r.structured_content["result"]


def err(r):
    assert r.is_error is True, r.structured_content
    return r.structured_content["error"]


def raises_action(fn, *a, **k):
    with pytest.raises(ActionError) as e:
        fn(*a, **k)
    return e.value


def wait_for(cond, timeout=3):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.005)
    return False
