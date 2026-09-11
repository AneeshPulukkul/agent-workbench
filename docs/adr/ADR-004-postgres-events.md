# ADR-004: PostgreSQL for Runs, Events, Audit

- Status: Accepted. Date: 2026-09-11. Spec §9, §1.5, §2.2.
- Context: Need durable state, ordered replay, idempotent writes, audit, local-executable stack, AKS path (Azure DB for PG).
- Decision: PostgreSQL as system of record: `tenants/users/runs/run_events(unique run_id,sequence)/agent_tasks/tool_invocations( hashes+redacted, idempotency_key)/approvals/findings/evidence_refs/model_calls/audit_records/evaluation_cases`. Persist-then-publish; PG polling for SSE fan-out initially; Redis/NATS/Kafka later behind `EventService` interface.
- Alternatives: Event bus first (rejected: local complexity), NoSQL (rejected: transactional + ordering needs), raw-payload storage (rejected: privacy).
- Consequences: + ACID replay/audit, simple Compose; − polling latency at scale; mitigate via indexed `(run_id, sequence)` + later stream swap without API change.
