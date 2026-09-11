"""Tenant authN/authZ for the MCP gateway.

Security contract (P0-2 fix):
- Tenant, scopes, and user ALWAYS come from the gateway identity
  (:class:`packages.security.identity.Identity`, attached by
  ``OIDCAuthMiddleware`` as ``request.state.identity``), never from
  client-supplied ``X-Tenant-ID`` / ``X-Scopes`` / ``X-Approved`` /
  ``X-User-ID`` headers.
- ``X-Tenant-ID`` from the client is untrusted: when present it must equal
  the identity tenant or the request is rejected (tenant mismatch) — it can
  never widen access.
- ``X-Approved`` from the client is NEVER trusted: approval must be a
  persisted human ``Approval`` row checked server-side (see executor write
  gating). :func:`from_headers` always returns ``approved=False``; use
  :func:`from_identity` with an explicitly server-verified ``approved`` flag.
- ``X-Scopes`` from the client is ignored outside local/test mock mode. In
  prod (``AUTH_MODE=oidc`` or ``APP_ENV`` outside ``{local, test}``) identity
  scopes win unconditionally.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

try:  # gateway identity type (no hard import cycle at runtime)
    from packages.security.identity import Identity
except Exception:  # pragma: no cover
    Identity = object  # type: ignore[assignment,misc]


class AuthError(Exception):
    def __init__(self, message: str, code: str = "auth_error") -> None:
        super().__init__(message)
        self.code = code


class ForbiddenError(AuthError):
    def __init__(self, message: str = "forbidden") -> None:
        super().__init__(message, "forbidden")


@dataclass
class AuthContext:
    tenant_id: str
    scopes: list[str] = field(default_factory=list)
    user_id: str = "analyst_1"
    approved: bool = False

    def require_scope(self, required: list[str]) -> None:
        if "*" in self.scopes:
            return
        missing = [s for s in required if s not in self.scopes]
        if missing:
            raise ForbiddenError(f"missing required scope(s): {','.join(missing)}")


def _norm(headers: Mapping[str, str]) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in headers.items()}


def _mock_mode() -> bool:
    return os.getenv("AUTH_MODE", "mock") == "mock" and os.getenv("APP_ENV", "local") in (
        "local",
        "test",
    )


def from_identity(identity: Identity, *, approved: bool = False) -> AuthContext:
    """Build AuthContext from a verified gateway identity (canonical path).

    ``approved`` must only be True when the caller has verified a persisted
    human Approval row server-side — never from client headers.
    """
    return AuthContext(
        tenant_id=str(getattr(identity, "tenant_id", "")),
        scopes=list(getattr(identity, "scopes", []) or []),
        user_id=str(getattr(identity, "user_id", "") or "analyst_1"),
        approved=bool(approved),
    )


def from_headers(headers: Mapping[str, str], *, identity: Identity | None = None) -> AuthContext:
    """Build AuthContext from HTTP headers + (when available) gateway identity.

    When ``identity`` is provided, tenant/scopes/user come from it; client
    ``X-Tenant-ID`` may only match it (mismatch -> ForbiddenError) and
    ``X-Approved``/``X-Scopes`` are ignored (approved always False here).

    Without ``identity`` (legacy/test path): the Authorization Bearer token,
    when present, is resolved via ``packages.security.identity`` so tenant
    still comes from the token. Bare ``X-Tenant-ID`` without a token is only
    honored in local/test mock mode (test harness); otherwise 401.
    ``X-Approved`` is never honored — always False.
    """
    h = _norm(headers)
    authz = h.get("authorization", "")
    if authz and not authz.startswith("Bearer "):
        raise AuthError("malformed Authorization header")

    if identity is not None:
        tenant = str(getattr(identity, "tenant_id", "") or "").strip()
        if not tenant:
            raise AuthError("gateway identity missing tenant")
        claimed = h.get("x-tenant-id", "").strip()
        if claimed and claimed != tenant:
            raise ForbiddenError("tenant mismatch")
        return AuthContext(
            tenant_id=tenant,
            scopes=list(getattr(identity, "scopes", []) or []),
            user_id=str(getattr(identity, "user_id", "") or "analyst_1"),
            approved=False,  # never trust X-Approved
        )

    # No gateway identity attached: try Bearer -> token identity.
    if authz.startswith("Bearer "):
        from starlette.requests import Request as _Request

        from packages.security.identity import IdentityError, resolve_identity

        raw = [(k.encode(), v.encode()) for k, v in h.items()]
        try:
            ident = resolve_identity(_Request({"type": "http", "headers": raw}))
        except IdentityError as e:
            raise AuthError(str(e)) from e
        claimed = h.get("x-tenant-id", "").strip()
        if claimed and claimed != ident.tenant_id:
            raise ForbiddenError("tenant mismatch")
        return AuthContext(
            tenant_id=ident.tenant_id,
            scopes=list(ident.scopes),
            user_id=ident.user_id,
            approved=False,
        )

    # No bearer, no gateway identity: mock-local test harness only.
    # The tenant label must still be explicit (missing -> 401, as before);
    # in mock mode it selects the test tenant, while X-Approved stays ignored.
    if _mock_mode():
        from packages.security.identity import local_dev_identity

        mock_ident = local_dev_identity()
        claimed = h.get("x-tenant-id", "").strip()
        if not claimed:
            raise AuthError("missing X-Tenant-ID")
        # Test harness may target an arbitrary tenant label in mock mode:
        # allow it, but scopes stay server-defaulted unless X-Scopes given
        # (local/test only). X-Approved still ignored.
        tenant = claimed if claimed and claimed != mock_ident.tenant_id else mock_ident.tenant_id
        raw_scopes = h.get("x-scopes", "").strip()
        scopes = (
            [s.strip() for s in raw_scopes.split(",") if s.strip()]
            if raw_scopes
            else list(mock_ident.scopes)
        )
        user = h.get("x-user-id", "").strip() or mock_ident.user_id
        return AuthContext(tenant_id=tenant, scopes=scopes, user_id=user, approved=False)
    raise AuthError("missing bearer token")


def check_tenant(auth: AuthContext, requested: str | None) -> str:
    """Return effective tenant or raise. Cross-tenant access is denied."""
    if requested and requested != auth.tenant_id:
        raise ForbiddenError("tenant mismatch")
    return auth.tenant_id


__all__ = [
    "AuthContext",
    "AuthError",
    "ForbiddenError",
    "check_tenant",
    "from_headers",
    "from_identity",
]
