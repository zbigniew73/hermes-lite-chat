"""HTTP Basic Auth gate covering HTTP routes, static files, and the /ws/pty
WebSocket handshake alike (a plain @app.middleware("http") would miss the
WebSocket scope entirely, so this is a raw ASGI middleware instead).
"""

import base64

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app import auth_store

REALM = "hermes-lite-chat"


class BasicAuthMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        if self._is_authorized(scope):
            await self.app(scope, receive, send)
            return

        if scope["type"] == "http":
            response = PlainTextResponse(
                "Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": f'Basic realm="{REALM}"'},
            )
            await response(scope, receive, send)
        else:
            await send({"type": "websocket.close", "code": 1008})

    @staticmethod
    def _is_authorized(scope: Scope) -> bool:
        headers = dict(scope.get("headers") or [])
        raw = headers.get(b"authorization", b"")
        if not raw.startswith(b"Basic "):
            return False
        try:
            decoded = base64.b64decode(raw[6:]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return False
        username, _, password = decoded.partition(":")
        return auth_store.verify(username, password)
