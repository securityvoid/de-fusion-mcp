import asyncio
import json
import pathlib

import pytest

from defusion import auth
from defusion.config import Config


async def _inner(scope, receive, send):
    if scope["type"] == "lifespan":
        await send({"type": "lifespan.ok"})
        return
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def request(headers, token="s3cret", scope_type="http"):
    sent = []

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {"type": "http.request"}

    app = auth.BearerAuth(_inner, token)
    asyncio.run(app({"type": scope_type, "headers": headers}, receive, send))
    return sent


def status(sent):
    return sent[0]["status"] if sent[0]["type"] == "http.response.start" else sent[0]


def test_correct_token_passes():
    assert status(request([(b"authorization", b"Bearer s3cret")])) == 200
    assert status(request([(b"Authorization", b"Bearer s3cret")])) == 200


@pytest.mark.parametrize("headers", [
    [],
    [(b"authorization", b"Bearer wrong")],
    [(b"authorization", b"Bearer s3cre")],
    [(b"authorization", b"Bearer s3cret ")],
    [(b"authorization", b"bearer s3cret")],
    [(b"authorization", b"s3cret")],
    [(b"authorization", b"Bearer \xff\xfe")],
    [(b"x-other", b"Bearer s3cret")],
])
def test_bad_or_missing_header_is_401_json(headers):
    sent = request(headers)
    assert status(sent) == 401 and len(sent) == 2
    assert json.loads(sent[1]["body"]) == {"error": "unauthorized", "hint": "send Authorization: Bearer <token>"}
    assert (b"content-type", b"application/json") in sent[0]["headers"]


def test_non_ascii_token_round_trips():
    ok = "tök€n".encode("utf-8")
    assert status(request([(b"authorization", b"Bearer " + ok)], token="tök€n")) == 200
    assert status(request([(b"authorization", b"Bearer " + "tok".encode())], token="tök€n")) == 401


def test_lifespan_passes_and_websocket_is_closed():
    assert request([], scope_type="lifespan")[0] == {"type": "lifespan.ok"}
    assert request([], scope_type="websocket")[0]["type"] == "websocket.close"


def test_token_from_precedence():
    assert auth.token_from(Config(token="cfg"), {"DEFUSION_TOKEN": "env"}) == "cfg"
    assert auth.token_from(Config(), {"DEFUSION_TOKEN": "env"}) == "env"
    assert auth.token_from(Config(), {}) is None


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_blank_env_token_is_error(blank):
    with pytest.raises(ValueError, match="DEFUSION_TOKEN"):
        auth.token_from(Config(), {"DEFUSION_TOKEN": blank})
    assert auth.token_from(Config(token="cfg"), {"DEFUSION_TOKEN": blank}) == "cfg"


def test_token_accessor():
    auth.set_token("abc")
    assert auth.token() == "abc"
    auth.set_token(None)
    assert auth.token() is None


def test_uses_compare_digest():
    src = (pathlib.Path(auth.__file__)).read_text()
    assert "hmac.compare_digest" in src
