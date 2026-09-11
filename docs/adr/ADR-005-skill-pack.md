# ADR-005: Skill-Pack as Generated Contract Views

- Status: Accepted. Date: 2026-09-11. Locked dual-consumption requirement.
- Context: Support BYO-agents without `langgraph` dependency while reusing Gateway/MCP/A2A + server-side guarantees.
- Decision: `AGENTS.md + skills/*/SKILL.md` generated from contract schemas + ToolMetadata + Agent Cards; HTTP+JSON only; hard pin `skill==contract==tool`; server enforces policy/approval/durability/budgets; stale versions rejected with upgrade error; CI parity test blocks drift.
- Alternatives: Hand-written skills (rejected: drift), SDK distribution (rejected: language lock), client-side policy (rejected: unsafe).
- Consequences: + portable adoption, single enforcement point; − generator + versioning discipline required (W2 `make skill-pack`).
