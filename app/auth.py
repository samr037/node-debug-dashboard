"""Optional authentication in front of the whole dashboard.

This is a privileged workload on hostNetwork: the API exposes the process
table, container inventory, certificate details and hardware inventory of
the node, and it binds a port on the node's own address. Left open, anything
that can route to the node can read all of that.

Credentials are unset by default so upgrading does not lock anyone out.
Setting AUTH_TOKEN or AUTH_PASSWORD turns enforcement on.
"""

import base64
import secrets

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import AUTH_PASSWORD, AUTH_TOKEN, AUTH_USERNAME

# The kubelet cannot present credentials, so probes must stay reachable.
# It exposes nothing but a literal {"status": "ok"}.
EXEMPT_PATHS = frozenset({"/api/health"})


def auth_configured() -> bool:
    return bool(AUTH_TOKEN or AUTH_PASSWORD)


def _check_bearer(header: str) -> bool:
    if not AUTH_TOKEN or not header.startswith("Bearer "):
        return False
    return secrets.compare_digest(header[7:].strip(), AUTH_TOKEN)


def _check_basic(header: str) -> bool:
    if not AUTH_PASSWORD or not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:].strip()).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    username, _, password = decoded.partition(":")
    # Both compared, and both in constant time, so neither the username nor
    # the password can be recovered by timing the response.
    user_ok = secrets.compare_digest(username, AUTH_USERNAME)
    pass_ok = secrets.compare_digest(password, AUTH_PASSWORD)
    return user_ok and pass_ok


def is_authorized(header: str) -> bool:
    if not auth_configured():
        return True
    if not header:
        return False
    return _check_bearer(header) or _check_basic(header)


def _header(scope: Scope, name: bytes) -> str:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value.decode("latin-1")
    return ""


class AuthMiddleware:
    """Plain ASGI middleware, deliberately not BaseHTTPMiddleware.

    BaseHTTPMiddleware only sees requests with an "http" scope, so the
    container log WebSocket would have bypassed authentication entirely —
    and that endpoint streams arbitrary pod logs off the node.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or not auth_configured():
            await self.app(scope, receive, send)
            return

        if scope.get("path") in EXEMPT_PATHS or is_authorized(
            _header(scope, b"authorization")
        ):
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            # 1008 is "policy violation"; there is no HTTP status to send
            # once a WebSocket handshake is under way.
            await send({"type": "websocket.close", "code": 1008})
            return

        headers = {}
        if AUTH_PASSWORD:
            # Prompts the browser for credentials rather than showing JSON.
            headers["WWW-Authenticate"] = 'Basic realm="Node Debug Dashboard"'
        response = JSONResponse(
            {"detail": "Unauthorized"}, status_code=401, headers=headers
        )
        await response(scope, receive, send)
