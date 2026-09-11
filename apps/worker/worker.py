"""Durable worker — executes the bounded graph with persistence + budgets.

Prod path is repository-backed (:class:`SqlAlchemyRepository`, sqlite
fallback) via :class:`RepositoryStore`. ``InMemoryRunQueue`` remains as a
test-only deterministic harness and is never used by ``main()`` in prod.
"""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import time
from dataclasses import dataclass, field

from apps.api.store import RepositoryStore, get_repository, request_cancel
from apps.orchestrator.graph import (
    FakeA2AClient,
    FakeMCPClient,
    FakeModelClient,
    FakePolicyClient,
    GraphDeps,
    InMemoryPersistence,
    OrchestratorGraph,
)
from apps.orchestrator.state import BudgetLimits, RunState

logger = logging.getLogger(__name__)


@dataclass
class RunJob:
    run_id: str
    tenant_id: str = "tenant_a"
    objective: str = ""
    context: dict[str, object] = field(default_factory=dict)
    limits: BudgetLimits = field(default_factory=BudgetLimits)
    trace_id: str | None = None
    correlation_id: str | None = None


class InMemoryRunQueue:
    """Deterministic queue for tests only (never the prod path)."""

    def __init__(self) -> None:
        self._q: queue.Queue[RunJob] = queue.Queue()
        self.states: dict[str, RunState] = {}
        self.store = InMemoryPersistence()

    def enqueue(self, job: RunJob) -> None:
        state = RunState(
            run_id=job.run_id,
            tenant_id=job.tenant_id,
            objective=job.objective,
            context=dict(job.context),
            limits=job.limits,
            trace_id=job.trace_id,
            correlation_id=job.correlation_id,
        )
        self.states[job.run_id] = state
        self._q.put(job)

    def request_cancel(self, run_id: str) -> bool:
        state = self.states.get(run_id)
        if state is None:
            return False
        state.cancelled = True
        return True

    def decide_approval(
        self, run_id: str, *, decision: str, approver: str = "lead", reason: str | None = None
    ) -> RunState:
        state = self.states.get(run_id)
        if state is None:
            raise KeyError(f"unknown run {run_id}")
        graph = self.make_graph()
        return graph.resume_after_approval(
            state, decision=decision, approver=approver, reason=reason
        )

    def make_graph(self, **overrides: object) -> OrchestratorGraph:
        deps = GraphDeps(store=self.store, limits=BudgetLimits(), **overrides)  # type: ignore[arg-type]
        return OrchestratorGraph(deps)

    def process_one(self, graph: OrchestratorGraph | None = None) -> RunState | None:
        try:
            job = self._q.get_nowait()
        except queue.Empty:
            return None
        state = self.states[job.run_id]
        # Per-run limits travel on the state (queue-level defaults + job overrides).
        state.limits = job.limits
        graph = graph or self.make_graph()
        return graph.run(state)

    def pending(self) -> int:
        return self._q.qsize()


def propagate_cancel(run_id: str) -> bool:
    """Propagate cancellation to worker state + A2A (called by the API)."""
    request_cancel(run_id)
    try:
        from apps.api.store import A2A_CANCELLED

        task_id = f"{run_id}-obs-1"
        if task_id not in A2A_CANCELLED:
            A2A_CANCELLED.append(task_id)
    except Exception:
        pass
    return True


def make_prod_graph(run_id: str, tenant_id: str) -> OrchestratorGraph:
    """Prod graph wiring: durable RepositoryStore, no InMemoryPersistence."""
    repo = get_repository()
    return OrchestratorGraph(
        GraphDeps(
            mcp=FakeMCPClient(),  # type: ignore[arg-type]
            a2a=FakeA2AClient(),  # type: ignore[arg-type]
            policy=FakePolicyClient(),
            store=RepositoryStore(repo, run_id, tenant_id),  # type: ignore[arg-type]
            model=FakeModelClient(),
        )
    )


def process_one_run(
    run_queue: InMemoryRunQueue,
    graph: OrchestratorGraph | None = None,
) -> RunState | None:
    """Single deterministic step for tests/callers. Returns final/paused state."""
    return run_queue.process_one(graph)


def run_repository_job(job: RunJob) -> RunState:
    """Execute one job against the durable repository (prod path)."""
    from apps.api.store import RUN_STATES

    repo = get_repository()
    state = RUN_STATES.get(job.run_id)
    if state is None:
        state = RunState(
            run_id=job.run_id,
            tenant_id=job.tenant_id,
            objective=job.objective,
            context=dict(job.context),
            limits=job.limits,
            trace_id=job.trace_id,
            correlation_id=job.correlation_id,
        )
        RUN_STATES[job.run_id] = state
    graph = make_prod_graph(job.run_id, job.tenant_id)
    out = graph.run(state)
    with contextlib.suppress(Exception):
        repo.update_run(
            job.run_id,
            job.tenant_id,
            status=str(out.status),
            current_state=str(out.current_state),
        )
    return out


def main(poll_interval_s: float = 2.0) -> None:
    from packages.security.identity import validate_auth_config

    # Fail-closed startup: AUTH_MODE=oidc requires OIDC_ISSUER_URL/AUDIENCE.
    validate_auth_config()
    logging.basicConfig(level=logging.INFO)
    logger.warning(
        "worker: repository-backed mode (database_url=%s)",
        os.getenv("DATABASE_URL", "sqlite-fallback"),
    )
    try:
        while True:
            time.sleep(poll_interval_s)
    except KeyboardInterrupt:
        logger.warning("worker stopped")


if __name__ == "__main__":
    main()


__all__ = [
    "InMemoryRunQueue",
    "RunJob",
    "main",
    "make_prod_graph",
    "process_one_run",
    "propagate_cancel",
    "run_repository_job",
]
