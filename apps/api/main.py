"""Agent Gateway API (mock-adapter local mode)."""

from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse

from apps.api.routes import approvals, health, runs
from packages.security.authz import AuthorizationError
from packages.security.headers import SecurityHeadersMiddleware
from packages.security.identity import IdentityError, OIDCAuthMiddleware, validate_auth_config
from packages.security.limits import LimitExceededError, RateLimitError

APP_VERSION = "0.1.0"


def _envelope(code: str, message: str, *, retryable: bool = False) -> dict[str, object]:
    return {"code": code, "message": message, "retryable": retryable}


def create_app() -> FastAPI:
    # Fail-closed startup: AUTH_MODE=oidc requires OIDC_ISSUER_URL/AUDIENCE.
    validate_auth_config()
    app = FastAPI(
        title="Agent Operations Workbench",
        version=APP_VERSION,
        docs_url="/docs",
        openapi_url="/v1/openapi.json",
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(OIDCAuthMiddleware)

    @app.exception_handler(AuthorizationError)
    async def _authz_handler(_req: Request, exc: AuthorizationError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=_envelope(exc.code, str(exc)))

    @app.exception_handler(IdentityError)
    async def _identity_handler(_req: Request, exc: IdentityError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=_envelope(exc.code, str(exc)))

    @app.exception_handler(RateLimitError)
    async def _rate_handler(_req: Request, exc: RateLimitError) -> JSONResponse:
        return JSONResponse(
            status_code=429, content=_envelope("rate_limited", str(exc), retryable=True)
        )

    @app.exception_handler(LimitExceededError)
    async def _limit_handler(_req: Request, exc: LimitExceededError) -> JSONResponse:
        return JSONResponse(status_code=400, content=_envelope(exc.code, str(exc)))

    @app.exception_handler(HTTPException)
    async def _http_handler(_req: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail:
            return JSONResponse(status_code=exc.status_code, content=detail)
        code = {400: "validation_error", 404: "not_found", 409: "conflict"}.get(
            exc.status_code, "internal_error"
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(code, str(detail)),
        )

    app.include_router(health.router)
    app.include_router(runs.router, prefix="/v1")
    app.include_router(approvals.router, prefix="/v1")
    return app


app = create_app()


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "agent-api",
        "version": APP_VERSION,
        "env": os.getenv("APP_ENV", "local"),
    }
