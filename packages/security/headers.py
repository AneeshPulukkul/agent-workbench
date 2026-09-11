"""Security headers middleware (Spec §10 hardening)."""

from __future__ import annotations

import os

from starlette.middleware.base import BaseHTTPMiddleware

SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}

# HSTS is prod-only: emitting it in local/test (plain HTTP) would poison
# browsers into HTTPS-only for localhost. Enabled when APP_ENV=production.
HSTS_HEADER = "Strict-Transport-Security"
HSTS_VALUE = "max-age=63072000; includeSubDomains; preload"


def _is_prod(env: str | None = None) -> bool:
    val = (env if env is not None else os.getenv("APP_ENV", "")).strip().lower()
    return val in ("production", "prod")


def build_headers(env: str | None = None) -> dict[str, str]:
    headers = dict(SECURITY_HEADERS)
    if _is_prod(env):
        headers[HSTS_HEADER] = HSTS_VALUE
    return headers


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        for k, v in build_headers().items():
            response.headers.setdefault(k, v)
        return response


__all__ = [
    "HSTS_HEADER",
    "HSTS_VALUE",
    "SECURITY_HEADERS",
    "SecurityHeadersMiddleware",
    "build_headers",
]
