"""Scope authZ, tenant isolation (404 vs 403), resource authZ (Spec §10).

Tenant-isolation policy (decided once, per api-contracts.md):
- Cross-tenant access to a *specific resource id* (GET run, events,
  approvals, cancel, decide) returns **404 ``not_found``** to avoid
  existence leaks.
- Collection-level or scope failures (missing scope, bad role for an
  action the tenant owns) return **403 ``forbidden``**.
- ``TENANT_NOT_FOUND_BEHAVIOR = "not_found_404"`` documents the choice.
"""

from __future__ import annotations

from packages.security.identity import Identity


class AuthorizationError(Exception):
    def __init__(self, message: str, *, status: int = 403, code: str = "forbidden") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class NotFoundForIsolation(AuthorizationError):
    """Tenant mismatch disguised as 404 (no existence leak)."""

    def __init__(self, message: str = "not found") -> None:
        super().__init__(message, status=404, code="not_found")


TENANT_NOT_FOUND_BEHAVIOR = "not_found_404"


def require_scopes(identity: Identity, required: list[str]) -> None:
    """Raise 403 if any required scope is missing. ``*`` bypasses (tests)."""
    if "*" in identity.scopes:
        return
    missing = [s for s in required if s not in identity.scopes]
    if missing:
        raise AuthorizationError(
            f"missing required scope(s): {','.join(missing)}", status=403, code="forbidden"
        )


def _matches(pattern: str, scope: str) -> bool:
    # Support prefix wildcards like "tools.write.*".
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        return scope == pattern[:-2] or scope.startswith(pattern[:-2] + ".")
    return pattern == scope


def has_any_scope(identity: Identity, candidates: list[str]) -> bool:
    for pat in identity.scopes:
        for cand in candidates:
            if _matches(pat, cand):
                return True
    return False


def require_any_scope(identity: Identity, candidates: list[str]) -> None:
    if has_any_scope(identity, candidates):
        return
    raise AuthorizationError("insufficient scope for this action", status=403, code="forbidden")


def check_tenant_access(identity: Identity, resource_tenant_id: str | None) -> None:
    """Row-level tenant check. Mismatch -> 404 (not 403) by policy."""
    if resource_tenant_id is None:
        return
    if resource_tenant_id != identity.tenant_id:
        raise NotFoundForIsolation()


def check_resource_access(
    identity: Identity,
    *,
    resource_tenant_id: str | None,
    required_scopes: list[str] | None = None,
    owner_user_id: str | None = None,
    allow_owner_read: bool = True,
) -> None:
    """Combined resource authZ: tenant isolation first, then scopes.

    Tenant is always checked before scopes so cross-tenant probes cannot
    distinguish "no scope" from "no such resource".
    """
    check_tenant_access(identity, resource_tenant_id)
    if required_scopes:
        if (
            allow_owner_read
            and owner_user_id is not None
            and owner_user_id == identity.user_id
            and all(s in ("case.read", "tools.read") for s in required_scopes)
        ):
            return
        require_scopes(identity, required_scopes)


__all__ = [
    "TENANT_NOT_FOUND_BEHAVIOR",
    "AuthorizationError",
    "NotFoundForIsolation",
    "check_resource_access",
    "check_tenant_access",
    "has_any_scope",
    "require_any_scope",
    "require_scopes",
]
