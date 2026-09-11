# Testing Strategy

> Spec §12. Targets: `make test = unit + contract + integration (+ eval/replay in M6)`. Deterministic by default via FakeRuntime/model + mock adapters.

## Layers

1. **Unit:** contracts (schema gen, enum/validation, unknown-field rejection), router/budget/validation, policy matrix, redaction, AG-UI map (unknown-type tolerance).
2. **Contract:** AG-UI event schema; MCP tool schemas + authZ failures; A2A Card + lifecycle; OTel propagation; `ErrorEnvelope` consistency. Skill-pack parity test: generated `SKILL.md` front-matter versions == contract versions == tool versions.
3. **Integration (disposable PG container):** persist-then-publish, `(run_id, sequence)` dedupe, replay order, approval persistence, tool audit rows, `deployment.rollback dry_run` mock-safety, duplicate idempotency keys.
4. **Replay:** persisted event log re-drives UI/debugging; worker-restart mid-run resumes from PG state.
5. **Failure injection (§12.3):** model/MCP timeout, MCP 403, malformed tool result, A2A down, duplicate delivery, worker restart, approval timeout, cancel, PG outage, prompt-injection runbook, dangerous tool recommendation, budget exhaustion — each asserts bounded, audited degradation.
6. **Evaluation (§12.4):** dataset `case_id, user_request, available_context, expected/forbidden_tools, expected_findings/risk/approval, reference_answer`; metrics: tool accuracy, unsupported-claim rate, evidence coverage, policy-violation rate, approval compliance, TTFB/TTFA, budget adherence, delegation usefulness.

## DoD gate (§16)

`make up && make migrate && make test` then: create run → AG-UI stream → MCP reads → A2A delegation → traces → structured recommendation → simulated remediation propose → approve/reject → replay → swap mock→enterprise adapter without orchestrator change → Helm deploy.
