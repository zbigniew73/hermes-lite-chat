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
guesser, not one spreading attempts across concurrent connections. The
lockout check is skipped entirely for a credential pair that's already in the
short-lived verify cache, so an active, already-authenticated session is
never blocked by unrelated noise against the same IP (e.g. behind a reverse
proxy, or an attacker sharing a NAT with the real admin) — only requests that
still need a fresh scrypt check are gated.
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
            await self._deny(scope, receive, send, 403, "Cross-origin request rejected")
            return

        creds = self._decode_credentials(scope)
        credentials_sent = creds is not None
        # /api/auth/logout deliberately sends bogus credentials to provoke the
        # 401 that evicts the browser's cached ones (see the logout() doc
        # comment in app.js) — that is its entire mechanism, not a real login
        # guess, so it must never feed the brute-force counter or be blocked
        # by it (that would silently break logout instead of a real attack).
        is_logout_probe = scope["type"] == "http" and scope.get("path") == "/api/auth/logout"

        cached_ok = credentials_sent and auth_store.cached_verify(*creds)

        client_host = self._client_host(scope)
        if not cached_ok and not is_logout_probe and auth_store.is_locked_out(client_host):
            await self._deny(scope, receive, send, 429, "Too many failed attempts, try again shortly")
            return

        if credentials_sent and not cached_ok and not is_logout_probe:
            # About to pay for a real scrypt check (or this is a doomed
            # guess) — count it now, before the executor hop below, so a
            # burst of concurrent attempts can't all start before any one of
            # them finishes and gets counted (see auth_store.record_attempt).
            auth_store.record_attempt(client_host)

        authorized = cached_ok
        if credentials_sent and not cached_ok:
            username, password = creds
            loop = asyncio.get_running_loop()
            authorized = await loop.run_in_executor(None, auth_store.verify, username, password)
            if authorized:
                auth_store.remember_verified(username, password)

        if authorized:
            if credentials_sent:
                auth_store.record_success(client_host)
            await self.app(scope, receive, send)
            return

        await self._deny(
            scope,
            receive,
            send,
            401,
            "Unauthorized",
            headers={"WWW-Authenticate": f'Basic realm="{REALM}"'},
        )

    @staticmethod
    async def _deny(
        scope: Scope,
        receive: Receive,
        send: Send,
        status_code: int,
        detail: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        if scope["type"] == "http":
            response = PlainTextResponse(detail, status_code=status_code, headers=headers)
            await response(scope, receive, send)
        else:
            await send({"type": "websocket.close", "code": 1008})

    @staticmethod
    def _client_host(scope: Scope) -> str:
        client = scope.get("client")
        return client[0] if client else "unknown"

    @staticmethod
    def _decode_credentials(scope: Scope) -> tuple[str, str] | None:
        headers = dict(scope.get("headers") or [])
        raw = headers.get(b"authorization", b"")
        if not raw.startswith(b"Basic "):
            return None
        try:
            decoded = base64.b64decode(raw[6:]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
        username, sep, password = decoded.partition(":")
        if not sep:
            return None
        return username, password

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
