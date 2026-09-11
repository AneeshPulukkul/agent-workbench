# ADR-002: LiteLLM Router with FakeRuntime Fallback

- Status: Accepted. Date: 2026-09-11. Spec §2.2, §5, §12.2.
- Context: Need task-based routing (fast classification / reasoning diagnosis / small summarization), cost/latency control, local-no-credential runs, deterministic tests.
- Decision: Internal `packages/llm` adapter with LiteLLM-compatible interface; `ModelRouter` by task; `BudgetTracker` (calls/tokens/latency/cost); `FakeModel/FakeRuntime` default in Compose/tests; real providers only via config, never imported by orchestrator.
- Alternatives: Azure OpenAI SDK direct (rejected: provider lock, harder routing/fallback), provider-native multi-SDK (rejected: complexity).
- Consequences: + portability, budgets, offline tests; − must validate structured output per provider, track GenAI attr drift behind telemetry adapter.
