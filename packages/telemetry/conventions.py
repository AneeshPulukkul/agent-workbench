"""Telemetry attribute conventions (otel-plan.md).

Isolates all attribute names + redaction policy in one module so GenAI
semconv churn never touches business code.

Rules:
- Stable HTTP/DB/RPC names follow OTel semconv (``http.*``, ``db.*``).
- Custom attrs live under ``workbench.*``.
- GenAI attrs live behind :func:`genai_attrs` adapter (dev-status
  conventions per spec fn 6-8); swap mapping here when conventions stabilize.
- NEVER capture prompts/completions/secrets/PII by default. Content capture
  is gated by ``OTEL_CONTENT_CAPTURE=dev-only`` local opt-in flag, and even
  then secrets are still redacted.
"""

from __future__ import annotations

import hashlib
import os
import re

# ---------------------------------------------------------------------------
# Custom namespaces
# ---------------------------------------------------------------------------

RUN_ID = "workbench.run_id"
TENANT_HASH = "workbench.tenant_hash"
TOOL_NAME = "workbench.tool.name"
TOOL_VERSION = "workbench.tool.version"
TOOL_SIDE_EFFECT = "workbench.tool.side_effect"
TOOL_DRY_RUN = "workbench.tool.dry_run"
AGENT_NAME = "workbench.agent.name"
TASK_ID = "workbench.task.id"
SKILL_ID = "workbench.task.skill_id"
POLICY_DECISION = "workbench.policy.decision"
POLICY_REASON = "workbench.policy.reason"
BUDGET_CONSUMED = "workbench.budget.consumed_cost_usd"
BUDGET_LIMIT = "workbench.budget.max_cost_usd"
APPROVAL_ID = "workbench.approval.id"
APPROVAL_DECISION = "workbench.approval.decision"
APPROVAL_WAIT_S = "workbench.approval.wait_s"
RUN_STATUS = "workbench.run.status"
RUN_OUTCOME = "workbench.run.outcome"
ERROR_CODE = "workbench.error.code"

# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

_SECRET_HINTS = (
    "secret",
    "token",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "client_secret",
    "bearer",
    "authorization",
    "cookie",
    "set-cookie",
    "private_key",
    "ssn",
    "social_security",
    "credit_card",
    "creditcard",
    "card_number",
    "cc_number",
    "email",
    "phone",
)

_SECRET_VALUE_RE = re.compile(
    r"(?i)(bearer\s+[A-Za-z0-9._\-~+/=]+|api_key\s*=\s*\S+|"
    r"client_secret\s*=\s*\S+|password\s*=\s*\S+)"
)

# PII value patterns — unified with apps/mcp_server/redact.py.
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CC_RE = re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}\b")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]?\d{4}\b")
PII_RE = re.compile(
    r"(\b\d{3}-\d{2}-\d{4}\b"
    r"|\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}\b"
    r"|[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
    r"|\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]?\d{4}\b)"
)

REDACTED = "[redacted]"


def is_secret_key(key: str) -> bool:
    kl = key.lower()
    return any(h in kl for h in _SECRET_HINTS)


def redact_value(key: str, value: object) -> object:
    if is_secret_key(key):
        return REDACTED
    if isinstance(value, str) and _SECRET_VALUE_RE.search(value):
        return _SECRET_VALUE_RE.sub(REDACTED, value)
    return value


def redact_mapping(data: dict) -> dict:
    """Redact secret keys/values from a shallow mapping (non-recursive safe)."""
    out: dict = {}
    for k, v in data.items():
        if is_secret_key(str(k)):
            out[k] = REDACTED
        elif isinstance(v, dict):
            out[k] = redact_mapping(v)
        elif isinstance(v, list):
            out[k] = [
                redact_mapping(i) if isinstance(i, dict) else redact_value(str(k), i) for i in v
            ]
        else:
            out[k] = redact_value(str(k), v)
    return out


def scrub_message(msg: str) -> str:
    """Scrub bearer/api_key/client_secret + PII (ssn/cc/email/phone) from free text."""
    out = _SECRET_VALUE_RE.sub(REDACTED, msg)
    out = _SSN_RE.sub(REDACTED, out)
    out = _CC_RE.sub(REDACTED, out)
    out = _EMAIL_RE.sub(REDACTED, out)
    out = _PHONE_RE.sub(REDACTED, out)
    return out[:2000]


def hash_tenant(tenant_id: str | None) -> str | None:
    """Hash tenant id (sha256, truncated) — never put raw tenant in spans by default."""
    if not tenant_id:
        return None
    return "sha256:" + hashlib.sha256(tenant_id.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Content-capture gate
# ---------------------------------------------------------------------------


def content_capture_enabled() -> bool:
    """Local opt-in only: OTEL_CONTENT_CAPTURE=dev-only.

    Anything else (unset, empty, production values) -> False.
    """
    return os.getenv("OTEL_CONTENT_CAPTURE", "").strip().lower() == "dev-only"


def maybe_content(prompt_or_completion: str | None) -> str | None:
    """Return redacted content only when opt-in flag is set, else None."""
    if not prompt_or_completion:
        return None
    if not content_capture_enabled():
        return None
    # Even in dev-only mode, still scrub secrets.
    return scrub_message(prompt_or_completion)[:2000]


# ---------------------------------------------------------------------------
# GenAI adapter (isolated; swap when semconv stabilizes)
# ---------------------------------------------------------------------------


def genai_attrs(
    *,
    operation: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    tool_name: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: float | None = None,
) -> dict:
    """Build GenAI attribute mapping. Only stable scalar fields, no prompts."""
    attrs: dict = {}
    if operation:
        attrs["gen_ai.operation.name"] = operation
    if provider:
        attrs["gen_ai.provider.name"] = provider
    if model:
        attrs["gen_ai.request.model"] = model
        attrs["gen_ai.response.model"] = model
    if tool_name:
        attrs["gen_ai.tool.name"] = tool_name
    if input_tokens is not None:
        attrs["gen_ai.usage.input_tokens"] = int(input_tokens)
    if output_tokens is not None:
        attrs["gen_ai.usage.output_tokens"] = int(output_tokens)
    if cost_usd is not None:
        attrs["workbench.gen_ai.cost_usd"] = float(cost_usd)
    return attrs


__all__ = [
    "RUN_ID",
    "TENANT_HASH",
    "TOOL_NAME",
    "TOOL_VERSION",
    "TOOL_SIDE_EFFECT",
    "TOOL_DRY_RUN",
    "AGENT_NAME",
    "TASK_ID",
    "SKILL_ID",
    "POLICY_DECISION",
    "POLICY_REASON",
    "BUDGET_CONSUMED",
    "BUDGET_LIMIT",
    "APPROVAL_ID",
    "APPROVAL_DECISION",
    "APPROVAL_WAIT_S",
    "RUN_STATUS",
    "RUN_OUTCOME",
    "ERROR_CODE",
    "REDACTED",
    "PII_RE",
    "is_secret_key",
    "redact_value",
    "redact_mapping",
    "scrub_message",
    "hash_tenant",
    "content_capture_enabled",
    "maybe_content",
    "genai_attrs",
]
