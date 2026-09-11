"""Shared FastAPI dependencies: identity-backed tenant/user + scope guards.

Local mode keeps the legacy ``X-Tenant-ID`` / ``X-User-ID`` headers working:
when no Bearer token is present, ``packages.security`` resolves the mock
local-dev identity (guarded to APP_ENV in {local, test}).
"""

from __future__ import annotations

import os

from fastapi import Depends, Header, Request

from packages.security.authz import AuthorizationError, require_scopes
from packages.security.identity import Identity


def get_identity(request: Request) -> Identity:
    ident = getattr(request.state, "identity", None)
    if isinstance(ident, Identity):
        return ident
    # Fallback for unit tests calling dependencies directly: legacy headers.
    return Identity(
        user_id=os.getenv("MOCK_USER_ID", "analyst-local"),
        tenant_id=os.getenv("MOCK_TENANT_ID", "tenant-local"),
        scopes=["case.read", "case.write", "case.approve", "tools.read"],
        mode="mock",
    )


def get_tenant_id(
    request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> str:
    ident = get_identity(request)
    # Legacy header may only *narrow* to the token tenant, never widen.
    if x_tenant_id and x_tenant_id != ident.tenant_id:
        raise AuthorizationError("tenant mismatch", status=404, code="not_found")
    return ident.tenant_id


def get_user_id(
    request: Request,
    x_user_id: str | None = Header(default=None),
) -> str:
    ident = get_identity(request)
    return x_user_id or ident.user_id


def require_scope(*scopes: str):  # type: ignore[no-untyped-def]
    def _guard(identity: Identity = Depends(get_identity)) -> Identity:
        require_scopes(identity, list(scopes))
        return identity

    return _guard
