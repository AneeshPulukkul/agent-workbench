"""Redaction facade (Spec §10): hashes + redacted JSON in audit by default.

Delegates to the battle-tested ``apps.mcp_server.redact`` implementation so
there is exactly one sensitive-key list. Adds the ``safe_for_ui`` gate:
payloads flagged ``sensitive=true`` are never returned to the UI unless the
producer explicitly set ``safe_for_ui=true``.
"""

from __future__ import annotations

from typing import Any

from apps.mcp_server.redact import (
    OUTPUT_MAX_BYTES,
    PII_RE,
    REDACTED,
    SENSITIVE_KEYS,
    output_too_large,
    redact,
    redact_for_audit,
    sha256_hex,
)


def safe_for_ui(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a UI-safe view: sensitive payloads become a hash reference."""
    if payload.get("sensitive") and not payload.get("safe_for_ui"):
        return {
            "redacted": True,
            "output_hash": sha256_hex(redact(payload)),
            "hint": "sensitive payload withheld (safe_for_ui=false)",
        }
    return redact(payload)  # type: ignore[no-any-return]


__all__ = [
    "OUTPUT_MAX_BYTES",
    "PII_RE",
    "REDACTED",
    "SENSITIVE_KEYS",
    "output_too_large",
    "redact",
    "redact_for_audit",
    "safe_for_ui",
    "sha256_hex",
]
