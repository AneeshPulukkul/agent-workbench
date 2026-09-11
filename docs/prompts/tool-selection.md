# Prompt: Tool Selection (spec §15.3)

```text
Select the minimum set of authorized tools needed to investigate the objective.

Only choose tools from the supplied tool catalog.

Constraints:
- Read-only tools may be selected automatically.
- Analysis tools require policy permission.
- Write and destructive tools may only be proposed, never directly selected for execution.
- Do not use a tool when its input is uncertain.
- Do not guess service names, identifiers, time ranges, or environments.
- Prefer narrow queries over broad data extraction.
- Respect the maximum tool-call budget.

Return:
- selected_tools
- purpose of each tool
- required arguments
- expected evidence
- missing inputs
- estimated cost and latency
```

Usage: plan phase of bounded graph; output is a proposal — execution still gated by allowlist + policy + (for write/destructive) human approval. Never emits executable commands directly.
