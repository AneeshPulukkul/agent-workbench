"""Parity: catalog == graph == skills (P0-3 drift gate).

Single source: apps.mcp_server.catalog (TOOL_METADATA v1.0.0).
Verifies tools, versions, and timeouts match across:
- catalog (TOOL_METADATA / TOOL_TIMEOUTS / TOOL_VERSION)
- orchestrator graph (TOOL_REGISTRY / TOOL_TIMEOUTS / mcp_timeout_for)
- skills (SKILL.md front-matter allowed-tools + versions)
- A2A Agent Card (version 1.0.0, non-example URL, skill==contract==tool)
- PolicyDecision.rule_version default + state token/delegation limits

Run: pytest tests/contract/test_parity.py -q
Make: make check-parity
"""

from __future__ import annotations

import re
from pathlib import Path

from apps.a2a_agents.observability.agent import AGENT_CARD, AGENT_VERSION
from apps.mcp_server.catalog import TOOL_METADATA, TOOL_TIMEOUTS, TOOL_VERSION
from apps.orchestrator.graph import TOOL_REGISTRY, mcp_timeout_for
from apps.orchestrator.graph import TOOL_TIMEOUTS as GRAPH_TIMEOUTS
from apps.orchestrator.state import BudgetLimits
from packages.contracts import SCHEMA_VERSION, PolicyDecision
from packages.contracts import __version__ as CONTRACT_VERSION

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS = ["investigate-incident", "correlate-symptoms", "review-action"]


def _frontmatter(text: str) -> dict[str, object]:
    assert text.startswith("---")
    end = text.index("---", 3)
    raw = text[3:end]
    fm: dict[str, object] = {}
    current: str | None = None
    for line in raw.splitlines():
        if re.match(r"^\s{2}-\s+", line) and current:
            items = fm[current]
            assert isinstance(items, list)
            items.append(line.strip()[2:].strip())
            continue
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip().strip('"').strip("'")
        if val == "":
            fm[key] = []
            current = key
        else:
            fm[key] = val
            current = None
    return fm


def test_single_source_version_pin() -> None:
    assert TOOL_VERSION == "1.0.0"
    assert CONTRACT_VERSION == "1.0.0"
    assert SCHEMA_VERSION == "1.0"
    assert AGENT_VERSION == "1.0.0", "Agent Card version must be 1.0.0 (skill==contract==tool)"


def test_catalog_tool_set() -> None:
    assert set(TOOL_METADATA) == {
        "telemetry.query_metrics",
        "telemetry.query_logs",
        "telemetry.get_trace",
        "service.get_health",
        "knowledge.search",
        "knowledge.get_runbook",
        "deployment.get_current_release",
        "remediation.simulate",
        "deployment.rollback",
        "ticket.create",
    }
    for meta in TOOL_METADATA.values():
        assert meta.version == TOOL_VERSION == "1.0.0"
        assert meta.timeout_seconds in (15, 30, 60)
    # Timeout bands: read 15-30s, analysis/dry-run 60s, write 60s.
    assert TOOL_METADATA["telemetry.query_metrics"].timeout_seconds == 30
    assert TOOL_METADATA["service.get_health"].timeout_seconds == 15
    assert TOOL_METADATA["remediation.simulate"].timeout_seconds == 60
    assert TOOL_METADATA["deployment.rollback"].timeout_seconds == 60
    assert TOOL_METADATA["ticket.create"].timeout_seconds == 60


def test_graph_matches_catalog() -> None:
    assert set(TOOL_REGISTRY) == set(TOOL_METADATA), (
        f"graph/catalog drift: graph-only={set(TOOL_REGISTRY) - set(TOOL_METADATA)} "
        f"catalog-only={set(TOOL_METADATA) - set(TOOL_REGISTRY)}"
    )
    for name, spec in TOOL_REGISTRY.items():
        meta = TOOL_METADATA[name]
        assert spec.category == meta.category.value, name
        assert spec.side_effect == meta.side_effect.value, name
        assert spec.idempotent == meta.idempotent, name
        assert spec.approval_required == meta.approval_required, name
        assert float(spec.timeout_seconds) == float(meta.timeout_seconds), name
        assert spec.version == meta.version == "1.0.0", name


def test_timeouts_imported_from_catalog() -> None:
    assert dict(GRAPH_TIMEOUTS) == {k: float(v) for k, v in TOOL_TIMEOUTS.items()}
    for name, meta in TOOL_METADATA.items():
        assert mcp_timeout_for(name) == float(meta.timeout_seconds), name
    # No stale 10s default: every catalog timeout is 15-60s.
    assert all(v in (15.0, 30.0, 60.0) for v in GRAPH_TIMEOUTS.values())


def test_skills_match_catalog() -> None:
    for skill in SKILLS:
        text = (REPO_ROOT / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        fm = _frontmatter(text)
        assert str(fm["version"]) == CONTRACT_VERSION == TOOL_VERSION
        assert str(fm["contract_version"]) == SCHEMA_VERSION
        assert str(fm["tool_version"]) == TOOL_VERSION
        allowed = fm["allowed-tools"]
        assert isinstance(allowed, list) and allowed
        for tool in allowed:
            assert tool in TOOL_METADATA, f"{skill}: {tool} not in catalog"
            assert TOOL_METADATA[tool].version == "1.0.0"


def test_agent_card_version_and_url() -> None:
    assert AGENT_CARD["version"] == "1.0.0"
    assert "example.com" not in str(AGENT_CARD["url"]), "Card URL must be real placeholder"
    assert AGENT_CARD["skills"][0]["id"] == "correlate-service-symptoms"


def test_policy_decision_rule_version() -> None:
    d = PolicyDecision(allowed=True, requires_approval=False, reason="ok")
    assert d.rule_version == "1.0"
    d2 = PolicyDecision(
        allowed=True, requires_approval=True, reason="needs human", rule_version="1.0"
    )
    assert d2.rule_version == "1.0"


def test_state_token_and_delegation_limits() -> None:
    lim = BudgetLimits()
    assert lim.max_input_tokens == 60_000
    assert lim.max_output_tokens == 20_000
    assert lim.max_delegation_depth == 3
    # Legacy fields retained (additive only).
    assert lim.max_model_calls == 10
    assert lim.max_tool_calls == 20
    assert lim.max_delegations == 3
