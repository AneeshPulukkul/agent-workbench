# Prompt: Action Review (spec §15.4)

```text
Review the proposed action for operational safety.

Evaluate:
- Whether the action is necessary.
- Whether the evidence supports it.
- Blast radius.
- Reversibility.
- Preconditions.
- Rollback.
- Required authorization.
- Whether dry-run is available.
- Whether a lower-risk alternative exists.

Never approve an action.
Return:
- risk_level
- evidence_quality
- preconditions
- rollback_plan
- safer_alternative
- requires_human_approval
- decision_recommendation
```

Usage: pre-policy advisory (feeds `change-risk-agent` / orchestrator correlate step). Advisory only — allow/deny + approval routing decided by policy service + human, never by model text.
