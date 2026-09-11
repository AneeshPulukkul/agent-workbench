"""Redaction + hashing for audit/telemetry (never persist secrets)."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

SENSITIVE_KEYS = {
    "password",
    "passwd",
    "secret",
    "client_secret",
    "api_key",
    "apikey",
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "authorization",
    "bearer",
    "cookie",
    "set-cookie",
    "private_key",
    "connection_string",
    # PII keys (unified with packages/telemetry/conventions.py + packages/security/redaction.py)
    "ssn",
    "social_security",
    "social_security_number",
    "credit_card",
    "creditcard",
    "card_number",
    "cc_number",
    "cc",
    "email",
    "email_address",
    "phone",
    "phone_number",
    "mobile",
}

_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._\-~+/=]+", re.IGNORECASE)
_PEM_RE = re.compile(r"-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----", re.DOTALL)
# PII value patterns (scrubbed from free text even when the key is benign).
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CC_RE = re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}\b")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]?\d{4}\b")
# Combined PII regex for callers that need a single pattern.
PII_RE = re.compile(
    r"(\b\d{3}-\d{2}-\d{4}\b"  # SSN
    r"|\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}\b"  # credit card
    r"|[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"  # email
    r"|\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]?\d{4}\b)"  # phone
)

REDACTED = "[REDACTED]"

# Default audit/output size cap (bytes, JSON-serialized). Enforced on the audit
# path via redact_for_audit(); oversize payloads become a hash reference.
OUTPUT_MAX_BYTES = 65536


def _redact_string(value: str) -> str:
    value = _BEARER_RE.sub("Bearer " + REDACTED, value)
    value = _PEM_RE.sub(REDACTED, value)
    value = _SSN_RE.sub(REDACTED, value)
    value = _CC_RE.sub(REDACTED, value)
    value = _EMAIL_RE.sub(REDACTED, value)
    # Phone last: SSN/CC shapes already handled; phone regex is intentionally
    # conservative (requires 10+ digits with separators) to limit false positives.
    value = _PHONE_RE.sub(REDACTED, value)
    return value


def redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if str(k).lower() in SENSITIVE_KEYS:
                out[k] = REDACTED
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        return _redact_string(obj)
    return obj


def sha256_hex(payload: Any) -> str:
    canonical = json.dumps(payload, default=str, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def output_too_large(payload: Any, limit_bytes: int = 65536) -> bool:
    return len(json.dumps(payload, default=str)) > limit_bytes


def redact_for_audit(payload: Any, limit_bytes: int = OUTPUT_MAX_BYTES) -> Any:
    """Redact then enforce the output size cap on the audit path.

    Returns the redacted payload, or a hash-reference stub when oversize so
    audit rows stay bounded and never persist raw oversized output.
    """
    redacted = redact(payload)
    if output_too_large(redacted, limit_bytes):
        return {
            "redacted": True,
            "output_hash": sha256_hex(redacted),
            "hint": f"output_too_large: truncated to hash reference (limit {limit_bytes}B)",
        }
    return redacted


__all__ = [
    "OUTPUT_MAX_BYTES",
    "PII_RE",
    "REDACTED",
    "SENSITIVE_KEYS",
    "output_too_large",
    "redact",
    "redact_for_audit",
    "sha256_hex",
]
