# =============================================================================
# hermes-lite-chat
# =============================================================================
# Strona   : automation-ai.eu - Automatyzacja AI
# Autor    : Zbigniew Czechowski
# WWW      : https://automation-ai.eu/
# E-mail   : kontakt@automation-ai.eu
# Licencja : MIT
# =============================================================================

"""HTTP Basic Auth gate covering HTTP routes, static files, and the /ws/pty
WebSocket handshake alike (a plain @app.middleware("http") would miss the
WebSocket scope entirely, so this is a raw ASGI middleware instead).

Also enforces same-origin on WebSocket handshakes and on any HTTP method that
can change state (everything but GET/HEAD/OPTIONS): browsers attach cached
Basic Auth credentials to same-origin requests regardless of which page
triggered them, so auth alone stops neither Cross-Site WebSocket Hijacking
nor plain CSRF via a cross-origin form POST.

Also throttles repeated failed credential guesses per client IP (see
auth_store.is_locked_out) — scrypt's cost alone only slows a single-threaded
guesser, not one spreading attempts across concurrent connections.
"""

import asyncio
import base64
from urllib.parse import urlsplit

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app import auth_store

REALM = "hermes-lite-chat"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class BasicAuthMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        # Same-origin check first: this is not an auth check, so valid
        # credentials must not be able to bypass it. Applies to WebSocket
        # handshakes and to any state-changing HTTP method.
        needs_same_origin = scope["type"] == "websocket" or (
            scope["type"] == "http" and scope["method"] not in _SAFE_METHODS
        )
        if needs_same_origin and not self._is_same_origin(scope):
            if scope["type"] == "http":
                response = PlainTextResponse("Cross-origin request rejected", status_code=403)
                await response(scope, receive, send)
            else:
                await send({"type": "websocket.close", "code": 1008})
            return

        client_host = self._client_host(scope)
        if auth_store.is_locked_out(client_host):
            if scope["type"] == "http":
                response = PlainTextResponse(
                    "Too many failed attempts, try again shortly", status_code=429
                )
                await response(scope, receive, send)
            else:
                await send({"type": "websocket.close", "code": 1008})
            return

        headers = dict(scope.get("headers") or [])
        credentials_sent = headers.get(b"authorization", b"").startswith(b"Basic ")

        if await self._is_authorized(scope):
            if credentials_sent:
                auth_store.record_success(client_host)
            await self.app(scope, receive, send)
            return

        # Only count it as a brute-force attempt if credentials were actually
        # guessed — the first, credential-less request every browser sends to
        # trigger the login prompt is not an attack.
        if credentials_sent:
            auth_store.record_failure(client_host)

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
    def _client_host(scope: Scope) -> str:
        client = scope.get("client")
        return client[0] if client else "unknown"

    @staticmethod
    def _is_same_origin(scope: Scope) -> bool:
        """Allow requests with no Origin (curl, wscat, native clients) and
        requests whose Origin host[:port] matches the Host header."""
        headers = dict(scope.get("headers") or [])
        raw_origin = headers.get(b"origin")
        if not raw_origin:
            return True
        try:
            origin = raw_origin.decode("latin-1")
            host = headers.get(b"host", b"").decode("latin-1")
        except UnicodeDecodeError:
            return False
        if not host:
            return False
        parsed = urlsplit(origin)
        if not parsed.netloc:
            return False
        # netloc may carry userinfo; strip it before comparing.
        origin_authority = parsed.netloc.rsplit("@", 1)[-1]
        if origin_authority.lower() == host.lower():
            return True
        # Origin omits the default port for its scheme; Host may carry it
        # explicitly (or vice versa). Normalize before giving up.
        default_port = {"http": "80", "https": "443", "ws": "80", "wss": "443"}.get(
            parsed.scheme.lower()
        )
        if default_port and ":" not in origin_authority:
            origin_authority = f"{origin_authority}:{default_port}"
        host_authority = host
        if default_port and ":" not in host_authority:
            host_authority = f"{host_authority}:{default_port}"
        return origin_authority.lower() == host_authority.lower()

    @staticmethod
    async def _is_authorized(scope: Scope) -> bool:
        headers = dict(scope.get("headers") or [])
        raw = headers.get(b"authorization", b"")
        if not raw.startswith(b"Basic "):
            return False
        try:
            decoded = base64.b64decode(raw[6:]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return False
        username, sep, password = decoded.partition(":")
        if not sep:
            return False

        # Cheap path: these exact credentials verified moments ago.
        if auth_store.cached_verify(username, password):
            return True

        # scrypt takes ~100ms; keep it off the event loop.
        loop = asyncio.get_running_loop()
        ok = await loop.run_in_executor(None, auth_store.verify, username, password)
        if ok:
            auth_store.remember_verified(username, password)
        return ok
