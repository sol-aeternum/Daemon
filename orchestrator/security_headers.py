from __future__ import annotations

import secrets

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

STRICT_TRANSPORT_SECURITY = "max-age=63072000; includeSubDomains"
X_FRAME_OPTIONS = "DENY"
X_CONTENT_TYPE_OPTIONS = "nosniff"
REFERRER_POLICY = "strict-origin-when-cross-origin"
PERMISSIONS_POLICY = "camera=(), microphone=(self), geolocation=()"

SECURITY_HEADER_VALUES = {
    "Strict-Transport-Security": STRICT_TRANSPORT_SECURITY,
    "X-Frame-Options": X_FRAME_OPTIONS,
    "X-Content-Type-Options": X_CONTENT_TYPE_OPTIONS,
    "Referrer-Policy": REFERRER_POLICY,
    "Permissions-Policy": PERMISSIONS_POLICY,
}


def build_content_security_policy(nonce: str) -> str:
    return "; ".join(
        (
            "default-src 'self'",
            f"script-src 'self' 'nonce-{nonce}'",
            f"style-src 'self' 'nonce-{nonce}'",
            "img-src 'self' data: blob:",
            "font-src 'self' data:",
            "connect-src 'self'",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
        )
    )


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        nonce = secrets.token_urlsafe(16)

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADER_VALUES.items():
                    headers[name] = value
                headers["Content-Security-Policy"] = build_content_security_policy(nonce)
                headers["X-CSP-Nonce"] = nonce
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


class _OuterSecurityHeadersMiddleware:
    """Wraps the entire FastAPI ASGI stack (including Starlette's outermost
    ``ServerErrorMiddleware``) so that 500 responses generated for unhandled
    exceptions still carry the same security headers as normal responses.

    Starlette installs ``ServerErrorMiddleware`` at the very top of the
    stack, *outside* any user-added middleware; an exception that escapes a
    route therefore bypasses ``SecurityHeadersMiddleware`` and the resulting
    500 response is sent with only the auto-generated content headers. This
    middleware sits one layer above that and re-applies the static security
    headers + a freshly generated CSP on any response that lacks them.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        nonce = secrets.token_urlsafe(16)

        async def send_with_outer_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                # Only add headers that the inner stack missed (so a normal
                # response that already carries them is not double-keyed
                # with a different CSP nonce).
                headers = MutableHeaders(scope=message)
                has_csp = b"content-security-policy" in {k.lower() for k in headers.keys()}
                if not has_csp:
                    for name, value in SECURITY_HEADER_VALUES.items():
                        headers[name] = value
                    headers["Content-Security-Policy"] = build_content_security_policy(nonce)
                    headers["X-CSP-Nonce"] = nonce
            await send(message)

        await self.app(scope, receive, send_with_outer_security_headers)
