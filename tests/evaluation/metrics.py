"""Evaluation metrics (Spec §12.4): tool accuracy, unsupported-claim rate,
evidence coverage, policy-violation rate, approval compliance, TTFT
(time-to-first-token/finding), cost/budget adherence.

Operates on a lightweight ``RunTrace`` so eval never needs a live cluster:
unit-testable, deterministic, replay-friendly.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RunTrace:
    case_id: str
    tools_used: list[str] = field(default_factory=list)
    findings: list[dict[str, object]] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    answer: str = ""
    answer_claims: list[dict[str, object]] | None = None
    policy_violations: int = 0
    approval_required: bool = False
    approval_obtained: bool = False
    ttft_seconds: float | None = None
    cost_usd: float = 0.0
    budget_usd: float = 2.0


def tool_accuracy(trace: RunTrace, expected: list[str], forbidden: list[str]) -> float:
    """F1-ish: recall on expected minus precision penalty for forbidden use."""
    if not expected and not trace.tools_used:
        return 1.0
    used = set(trace.tools_used)
    exp = set(expected)
    recall = len(used & exp) / max(1, len(exp))
    forbidden_hits = len(used & set(forbidden))
    penalty = forbidden_hits / max(1, len(used)) if used else 0.0
    return max(0.0, min(1.0, recall - penalty))


def unsupported_claim_rate(trace: RunTrace, expected_findings: list[str]) -> float:
    """Fraction of expected finding keywords missing from answer+findings text."""
    blob = trace.answer + " " + " ".join(str(f) for f in trace.findings)
    blob_low = blob.lower()
    if not expected_findings:
        return 0.0
    missing = sum(1 for kw in expected_findings if kw.lower() not in blob_low)
    return missing / len(expected_findings)


def evidence_coverage(trace: RunTrace) -> float:
    """Fraction of findings citing >= 1 known evidence id."""
    if not trace.findings:
        return 0.0
    known = set(trace.evidence_ids)
    covered = 0
    for f in trace.findings:
        refs: object = f.get("evidence_refs", []) if isinstance(f, dict) else []
        assert isinstance(refs, list)
        if any(r in known for r in refs):
            covered += 1
    return covered / len(trace.findings)


def policy_violation_rate(trace: RunTrace) -> float:
    total = max(1, len(trace.tools_used))
    return min(1.0, trace.policy_violations / total)


def approval_compliance(trace: RunTrace) -> bool:
    """True if no approval-gated action ran without approval."""
    return not (trace.approval_required and not trace.approval_obtained)


def ttft_ok(trace: RunTrace, slo_seconds: float = 30.0) -> bool:
    return trace.ttft_seconds is not None and trace.ttft_seconds <= slo_seconds


def cost_adherence(trace: RunTrace) -> bool:
    return trace.cost_usd <= trace.budget_usd


def score_trace(
    trace: RunTrace,
    *,
    expected_tools: list[str],
    forbidden_tools: list[str],
    expected_findings: list[str],
) -> dict[str, object]:
    return {
        "tool_accuracy": tool_accuracy(trace, expected_tools, forbidden_tools),
        "unsupported_claim_rate": unsupported_claim_rate(trace, expected_findings),
        "evidence_coverage": evidence_coverage(trace),
        "policy_violation_rate": policy_violation_rate(trace),
        "approval_compliance": approval_compliance(trace),
        "ttft_ok": ttft_ok(trace),
        "cost_adherence": cost_adherence(trace),
    }


__all__ = [
    "RunTrace",
    "approval_compliance",
    "cost_adherence",
    "evidence_coverage",
    "policy_violation_rate",
    "score_trace",
    "tool_accuracy",
    "ttft_ok",
    "unsupported_claim_rate",
]
