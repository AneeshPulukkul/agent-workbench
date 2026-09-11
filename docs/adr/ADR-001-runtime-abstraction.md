# ADR-001: AgentRuntime Abstraction (LangGraph behind Protocol)

- Status: Accepted. Date: 2026-09-11. Spec §2.3, Prompt 5.
- Context: Avoid lock-in to one agent framework; need usable default + deterministic tests + future MAF/SK swap.
- Decision: Domain `AgentRuntime(Protocol)` with `plan/synthesize/classify/summarize` streaming `AgentEvent`; default `LangGraphRuntime`; also `FakeRuntime` (tests/local) and `ProviderRuntime` seam; orchestrator never imports provider/framework SDK directly; model output validated vs Pydantic.
- Alternatives: LangGraph-direct (rejected: lock-in), MAF-first (rejected: team skillset + ecosystem), unconstrained ReAct loop (rejected: §5 bounded graph required).
- Consequences: + replaceability, testability; − small adapter cost, must keep Protocol minimal and versioned.
