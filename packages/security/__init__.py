"""Central security package (Spec Prompt 11+12 §10-§11).

Single import surface for authN/authZ, tenant isolation, tool allowlists,
resource authZ, input limits, timeouts/rate-limits, redaction, headers.
"""

from packages.security.allowlist import (
    ALLOWLIST_BY_ROLE,
    DEFAULT_ALLOWLIST,
    is_tool_allowed,
    validate_tool_call,
)
from packages.security.authz import (
    TENANT_NOT_FOUND_BEHAVIOR,
    AuthorizationError,
    NotFoundForIsolation,
    check_resource_access,
    check_tenant_access,
    require_scopes,
)
from packages.security.headers import SECURITY_HEADERS, SecurityHeadersMiddleware, build_headers
from packages.security.identity import (
    AuthMode,
    Identity,
    local_dev_identity,
    parse_bearer_identity,
    resolve_identity,
)
from packages.security.limits import (
    InputLimits,
    RateLimiter,
    TimeoutConfig,
    check_action_input,
    check_context,
    check_objective,
)
from packages.security.redaction import (
    REDACTED,
    SENSITIVE_KEYS,
    redact,
    safe_for_ui,
    sha256_hex,
)

__all__ = [
    "ALLOWLIST_BY_ROLE",
    "DEFAULT_ALLOWLIST",
    "REDACTED",
    "SECURITY_HEADERS",
    "SENSITIVE_KEYS",
    "TENANT_NOT_FOUND_BEHAVIOR",
    "AuthMode",
    "AuthorizationError",
    "Identity",
    "InputLimits",
    "NotFoundForIsolation",
    "RateLimiter",
    "SecurityHeadersMiddleware",
    "TimeoutConfig",
    "build_headers",
    "check_action_input",
    "check_context",
    "check_objective",
    "check_resource_access",
    "check_tenant_access",
    "is_tool_allowed",
    "local_dev_identity",
    "parse_bearer_identity",
    "redact",
    "require_scopes",
    "resolve_identity",
    "safe_for_ui",
    "sha256_hex",
    "validate_tool_call",
]
