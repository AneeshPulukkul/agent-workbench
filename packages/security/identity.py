"""Identity: OIDC bearer auth + local-dev identity (Spec §10).

Rules:
- ``AUTH_MODE=mock`` (a.k.a. local-dev) only when ``APP_ENV in {local, test}``.
  Production (``APP_ENV not in {local,test}``) with ``AUTH_MODE=mock`` refuses
  to resolve identity (fail-closed).
- OIDC mode (``AUTH_MODE=oidc``) is fail-closed:
  ``OIDC_ISSUER_URL`` and ``OIDC_AUDIENCE`` must be non-empty (enforced at
  startup via :func:`validate_auth_config` and per-token), ``alg=none`` is
  always rejected, ``iss``/``aud``/``exp`` are strictly enforced, and the
  JWT signature is verified when ``OIDC_JWKS_URL`` (or ``OIDC_JWKS_JSON``)
  is configured. When ``AUTH_MODE=oidc`` and no JWKS source is configured,
  unsigned/unverifiable tokens are rejected (fail-closed) — signature
  verification happens at the IdP/gateway in prod; the in-process JWKS hook
  here is the drop-in described in
  ``docs/operations/migration-mock-to-enterprise.md`` (M1).
- Tenant always comes from the token (``tid``/``tenant_id`` claim), never
  from client-supplied headers/body alone.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class AuthMode(StrEnum):
    MOCK = "mock"
    OIDC = "oidc"


@dataclass
class Identity:
    user_id: str
    tenant_id: str
    scopes: list[str] = field(default_factory=list)
    mode: str = "mock"
    expires_at: float | None = None

    def has_scope(self, scope: str) -> bool:
        return "*" in self.scopes or scope in self.scopes


class IdentityError(Exception):
    def __init__(self, message: str, *, status: int = 401, code: str = "unauthorized") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _b64_json(part: str) -> dict[str, object]:
    padded = part + "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode()).decode("utf-8"))  # type: ignore[no-any-return]


# --- OIDC startup guard + JWKS verify hook -----------------------------------

JwtVerifier = Callable[[str, dict[str, object]], None]
"""Signature verifier hook: ``fn(token, jwks) -> None``; raise IdentityError on failure."""

_JWT_VERIFIER: JwtVerifier | None = None
_JWKS_CACHE: dict[str, object] | None = None


def set_jwt_verifier(fn: JwtVerifier | None) -> None:
    """Inject (or clear) the JWT signature verifier. Test/prod hook."""
    global _JWT_VERIFIER
    _JWT_VERIFIER = fn


def clear_jwks_cache() -> None:  # test hook
    global _JWKS_CACHE
    _JWKS_CACHE = None


def validate_auth_config() -> None:
    """Fail startup/deploy when OIDC is selected without issuer/audience.

    Raises:
        RuntimeError: if ``AUTH_MODE=oidc`` and ``OIDC_ISSUER_URL`` or
            ``OIDC_AUDIENCE`` is empty, or if ``AUTH_MODE`` is unknown.
    """
    mode = os.getenv("AUTH_MODE", "mock").strip().lower()
    if mode not in ("mock", "oidc"):
        raise RuntimeError(f"unknown AUTH_MODE={mode!r}; expected 'mock' or 'oidc'")
    if mode == "oidc":
        iss = os.getenv("OIDC_ISSUER_URL", "").strip()
        aud = os.getenv("OIDC_AUDIENCE", "").strip()
        missing = [
            name
            for name, val in (("OIDC_ISSUER_URL", iss), ("OIDC_AUDIENCE", aud))
            if not val
        ]
        if missing:
            raise RuntimeError(
                f"AUTH_MODE=oidc requires non-empty {', '.join(missing)} "
                "(fail-closed; set OIDC_ISSUER_URL/OIDC_AUDIENCE or use AUTH_MODE=mock "
                "in local/test only)"
            )


def _parse_header(token: str) -> dict[str, object]:
    parts = token.split(".")
    if len(parts) != 3:
        raise IdentityError("malformed bearer token", status=401, code="unauthorized")
    try:
        padded = parts[0] + "=" * (-len(parts[0]) % 4)
        header = json.loads(base64.urlsafe_b64decode(padded.encode()).decode("utf-8"))
    except Exception as e:
        raise IdentityError("malformed bearer token header", status=401) from e
    if not isinstance(header, dict):
        raise IdentityError("malformed bearer token header", status=401)
    return header  # type: ignore[no-any-return]


def _load_jwks() -> dict[str, object] | None:
    """Return JWKS dict from OIDC_JWKS_JSON or OIDC_JWKS_URL, or None if unset."""
    inline = os.getenv("OIDC_JWKS_JSON", "").strip()
    if inline:
        try:
            doc = json.loads(inline)
        except Exception as e:
            raise IdentityError("invalid OIDC_JWKS_JSON", status=401) from e
        if not isinstance(doc, dict):
            raise IdentityError("invalid OIDC_JWKS_JSON", status=401)
        return doc
    url = os.getenv("OIDC_JWKS_URL", "").strip() or os.getenv("OIDC_JWKS_URI", "").strip()
    if not url:
        return None
    global _JWKS_CACHE
    if _JWKS_CACHE is not None:
        return _JWKS_CACHE
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
            doc = json.loads(resp.read().decode("utf-8"))
    except IdentityError:
        raise
    except Exception as e:
        raise IdentityError("unable to fetch OIDC JWKS", status=401) from e
    if not isinstance(doc, dict):
        raise IdentityError("invalid OIDC JWKS document", status=401)
    _JWKS_CACHE = doc
    return doc


def _default_verify_signature(token: str, jwks: dict[str, object]) -> None:
    """Best-effort RS256 verification using PyJWT/cryptography when available.

    Raises IdentityError when the libraries are absent (fail-closed) or when
    the key/signature is invalid. Prefers the injected hook when set.
    """
    if _JWT_VERIFIER is not None:
        _JWT_VERIFIER(token, jwks)
        return
    header = _parse_header(token)
    kid = str(header.get("kid") or "")
    keys = jwks.get("keys")
    if not isinstance(keys, list) or not keys:
        raise IdentityError("OIDC JWKS has no keys", status=401)
    key_doc = None
    for k in keys:
        if isinstance(k, dict) and (not kid or str(k.get("kid") or "") == kid):
            key_doc = k
            break
    if key_doc is None:
        raise IdentityError("token kid not found in OIDC JWKS", status=401)
    try:
        import jwt  # type: ignore[import-not-found]
        from jwt import PyJWK  # type: ignore[import-not-found]
    except Exception as e:
        raise IdentityError(
            "JWKS verification requires PyJWT/cryptography (or inject set_jwt_verifier); "
            "failing closed",
            status=401,
        ) from e
    try:
        signing_key = PyJWK(key_doc).key  # type: ignore[no-untyped-call]
        aud = os.getenv("OIDC_AUDIENCE", "").strip() or None
        iss = os.getenv("OIDC_ISSUER_URL", "").strip() or None
        jwt.decode(
            token,
            signing_key,
            algorithms=["RS256", "ES256"],
            audience=aud,
            issuer=iss,
            options={"require": ["exp", "iss", "aud"]},
        )
    except IdentityError:
        raise
    except Exception as e:
        raise IdentityError(f"token signature verification failed: {e}", status=401) from e


def _enforce_signature(token: str) -> None:
    """Enforce JWT signature policy. Called for every bearer token.

    - ``alg=none`` (or missing alg) is always rejected (forged JWT).
    - When ``AUTH_MODE=oidc``: if a JWKS source is configured, verify the
      signature (kid lookup + crypto); otherwise fail closed (reject).
    - When ``AUTH_MODE=mock`` (local/test): unsigned test tokens (``alg=none``)
      are still rejected; HMAC/RS-signed tokens without a JWKS source skip
      crypto verification (legacy local behavior) but remain subject to
      iss/aud/exp checks.
    """
    header = _parse_header(token)
    alg = str(header.get("alg") or "")
    if alg.lower() == "none" or not alg:
        raise IdentityError("token signature missing (alg=none rejected)", status=401)
    mode = os.getenv("AUTH_MODE", "mock").strip().lower()
    jwks = _load_jwks()
    if jwks is not None:
        _default_verify_signature(token, jwks)
        return
    if mode == "oidc":
        raise IdentityError(
            "token signature cannot be verified (no OIDC_JWKS_URL/OIDC_JWKS_JSON); "
            "failing closed in AUTH_MODE=oidc",
            status=401,
        )
    # mock mode without JWKS: structural checks in parse_bearer_identity apply.


def parse_bearer_identity(token: str) -> Identity:
    """Parse an OIDC-style JWT (structure + claims + signature policy).

    Accepted claims: ``sub`` (user), ``tid`` or ``tenant_id`` (tenant),
    ``scope`` (space-separated) or ``scp`` (list), ``iss``, ``aud``, ``exp``.
    ``OIDC_ISSUER_URL``/``OIDC_AUDIENCE`` env vars are enforced when set;
    in ``AUTH_MODE=oidc`` they are required (see :func:`validate_auth_config`)
    and ``iss``/``aud`` mismatches — including missing claims — are rejected.
    ``alg=none`` is always rejected; in ``AUTH_MODE=oidc`` the signature must
    verify against ``OIDC_JWKS_URL``/``OIDC_JWKS_JSON`` (fail-closed).
    """
    _enforce_signature(token)
    parts = token.split(".")
    if len(parts) != 3:
        raise IdentityError("malformed bearer token", status=401, code="unauthorized")
    try:
        payload = _b64_json(parts[1])
    except Exception as e:
        raise IdentityError("malformed bearer token payload", status=401) from e
    sub = str(payload.get("sub") or payload.get("upn") or payload.get("email") or "")
    tenant = str(payload.get("tid") or payload.get("tenant_id") or payload.get("tenant") or "")
    if not sub:
        raise IdentityError("token missing sub claim", status=401)
    if not tenant:
        raise IdentityError("token missing tenant claim (tid)", status=401)
    raw_scope = payload.get("scope", payload.get("scp", ""))
    if isinstance(raw_scope, list):
        scopes = [str(s) for s in raw_scope]
    else:
        scopes = [s for s in str(raw_scope).split() if s]
    if not scopes:
        scopes = ["case.read"]
    exp = payload.get("exp")
    if exp is not None:
        try:
            if float(str(exp)) < time.time():
                raise IdentityError("token expired", status=401, code="token_expired")
        except IdentityError:
            raise
        except Exception as e:
            raise IdentityError("invalid exp claim", status=401) from e
    expected_iss = os.getenv("OIDC_ISSUER_URL", "").strip()
    expected_aud = os.getenv("OIDC_AUDIENCE", "").strip()
    auth_mode = os.getenv("AUTH_MODE", "mock").strip().lower()
    if auth_mode == "oidc":
        # Fail-closed: issuer/audience must be configured AND present in token.
        if not expected_iss or not expected_aud:
            raise IdentityError(
                "OIDC not configured (OIDC_ISSUER_URL/OIDC_AUDIENCE required)", status=401
            )
        if str(payload.get("iss") or "") != expected_iss:
            raise IdentityError("unexpected token issuer", status=401)
        aud = payload.get("aud")
        auds = aud if isinstance(aud, list) else [aud]
        if expected_aud not in [str(a) for a in auds]:
            raise IdentityError("unexpected token audience", status=401)
        if exp is None:
            raise IdentityError("token missing exp claim", status=401)
        return Identity(
            user_id=sub,
            tenant_id=tenant,
            scopes=scopes,
            mode="oidc",
            expires_at=float(str(exp)),
        )
    if expected_iss and payload.get("iss") and str(payload["iss"]) != expected_iss:
        raise IdentityError("unexpected token issuer", status=401)
    if expected_aud and payload.get("aud"):
        aud = payload["aud"]
        auds = aud if isinstance(aud, list) else [aud]
        if expected_aud not in [str(a) for a in auds]:
            raise IdentityError("unexpected token audience", status=401)
    return Identity(
        user_id=sub,
        tenant_id=tenant,
        scopes=scopes,
        mode="oidc",
        expires_at=float(str(exp)) if exp is not None else None,
    )


def local_dev_identity() -> Identity:
    """Explicit local-dev identity. Guarded: only APP_ENV in {local, test}."""
    env = os.getenv("APP_ENV", "local")
    if env not in ("local", "test"):
        raise IdentityError("mock identity forbidden outside local/test", status=500)
    if os.getenv("AUTH_MODE", "mock") != "mock":
        raise IdentityError("mock identity requires AUTH_MODE=mock", status=500)
    return Identity(
        user_id=os.getenv("MOCK_USER_ID", "analyst-local"),
        tenant_id=os.getenv("MOCK_TENANT_ID", "tenant-local"),
        scopes=["case.read", "case.write", "case.approve", "tools.read"],
        mode="mock",
    )


def resolve_identity(request: Request) -> Identity:
    """Resolve identity for a request: Bearer token wins, else local-dev guard."""
    authz = request.headers.get("authorization", "")
    if authz:
        if not authz.startswith("Bearer "):
            raise IdentityError("malformed Authorization header", status=401)
        token = authz[len("Bearer ") :].strip()
        # Local test shortcut: opaque dev tokens "local:<user>:<tenant>[:scopes]".
        if token.startswith("local:"):
            ident = local_dev_identity()
            chunks = token.split(":")
            if len(chunks) >= 3:
                ident.user_id = chunks[1] or ident.user_id
                ident.tenant_id = chunks[2] or ident.tenant_id
            if len(chunks) >= 4 and chunks[3]:
                ident.scopes = [s for s in chunks[3].split(",") if s]
            return ident
        return parse_bearer_identity(token)
    # No bearer -> only allowed in local/test mock mode.
    if os.getenv("AUTH_MODE", "mock") == "mock":
        return local_dev_identity()
    raise IdentityError("missing bearer token", status=401)


class OIDCAuthMiddleware(BaseHTTPMiddleware):
    """Attach ``request.state.identity`` or return 401 ErrorEnvelope.

    Open paths (no auth): /health, /ready, /docs, /openapi.json, /v1/openapi.json, /.
    """

    OPEN_PREFIXES = ("/health", "/ready", "/docs", "/openapi.json", "/v1/openapi.json")

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        if path == "/" or path.startswith(self.OPEN_PREFIXES):
            return await call_next(request)
        try:
            request.state.identity = resolve_identity(request)
        except IdentityError as e:
            return JSONResponse(
                status_code=e.status,
                content={"code": e.code, "message": str(e), "retryable": False},
            )
        return await call_next(request)


__all__ = [
    "AuthMode",
    "Identity",
    "IdentityError",
    "JwtVerifier",
    "OIDCAuthMiddleware",
    "clear_jwks_cache",
    "local_dev_identity",
    "parse_bearer_identity",
    "resolve_identity",
    "set_jwt_verifier",
    "validate_auth_config",
]
