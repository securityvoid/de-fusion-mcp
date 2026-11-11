"""Bearer-token auth (design § 7)."""
import hmac
import json

_token = None

UNAUTHORIZED = {"error": "unauthorized", "hint": "send Authorization: Bearer <token>"}


def token_from(config, environ):
    """Effective token: config ``token``, else env ``DEFUSION_TOKEN``, else None.

    Raises ValueError when the env token is blank or whitespace-only (a config error, § 6.1)."""
    if config.token is not None:
        return config.token
    env = environ.get("DEFUSION_TOKEN")
    if env is None:
        return None
    if not env.strip():
        raise ValueError("DEFUSION_TOKEN must not be blank")
    return env


def set_token(value):
    """Record the effective token (called by ``server.start()``)."""
    global _token
    _token = value


def token():
    """The effective token after ``start()``; None when auth is off."""
    return _token


class BearerAuth:
    """ASGI middleware: every HTTP request needs ``Authorization: Bearer <token>``."""

    def __init__(self, app, token):
        self.app = app
        self._expected = ("Bearer " + token).encode("utf-8")

    async def __call__(self, scope, receive, send):
        kind = scope["type"]
        if kind == "lifespan":
            return await self.app(scope, receive, send)
        if kind == "http" and self._authorized(scope):
            return await self.app(scope, receive, send)
        if kind == "http":
            body = json.dumps(UNAUTHORIZED).encode("utf-8")
            await send({"type": "http.response.start", "status": 401, "headers": [
                (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                (b"www-authenticate", b"Bearer")]})
            await send({"type": "http.response.body", "body": body})
        else:
            await send({"type": "websocket.close", "code": 1008})

    def _authorized(self, scope):
        for name, value in scope.get("headers", ()):
            if name.lower() == b"authorization":
                try:
                    supplied = value.decode("utf-8").encode("utf-8")
                except UnicodeDecodeError:
                    return False
                return hmac.compare_digest(supplied, self._expected)
        return False
