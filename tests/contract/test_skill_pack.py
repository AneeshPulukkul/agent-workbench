"""Contract: skill-pack is a generated view of contracts (skill==contract==tool)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from apps.mcp_server.catalog import TOOL_METADATA, TOOL_VERSION
from packages.contracts import SCHEMA_VERSION
from packages.contracts import __version__ as CONTRACT_VERSION

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS = ["investigate-incident", "correlate-symptoms", "review-action"]

LANGGRAPH_IMPORT = re.compile(r"(?mi)^\s*(import|from)\s+langgraph\b|pip\s+install[^\n]*langgraph")


def _read(skill: str) -> str:
    p = REPO_ROOT / "skills" / skill / "SKILL.md"
    assert p.exists(), f"missing skills/{skill}/SKILL.md (run: python skills/_generator.py)"
    return p.read_text(encoding="utf-8")


def _frontmatter(text: str) -> dict[str, object]:
    assert text.startswith("---"), "SKILL.md must start with YAML frontmatter"
    end = text.index("---", 3)
    raw = text[3:end]
    fm: dict[str, object] = {}
    current_list_key: str | None = None
    for line in raw.splitlines():
        if re.match(r"^\s{2}-\s+", line) and current_list_key:
            assert isinstance(fm[current_list_key], list)
            fm[current_list_key].append(line.strip()[2:].strip())  # type: ignore[union-attr]
            continue
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip().strip('"').strip("'")
        if val == "":
            fm[key] = []
            current_list_key = key
        else:
            fm[key] = val
            current_list_key = None
    return fm


def _manifest() -> dict[str, str]:
    data = json.loads((REPO_ROOT / "packages" / "contracts" / "schemas" / "manifest.json").read_text())
    return dict(data["models"])


@pytest.mark.parametrize("skill", SKILLS)
def test_frontmatter_pins(skill: str) -> None:
    fm = _frontmatter(_read(skill))
    for key in ("name", "version", "description", "allowed-tools",
                "contract_version", "tool_version", "input-schema", "output-schema"):
        assert fm.get(key), f"{skill}: frontmatter missing {key}"
    assert fm["name"] == skill
    assert str(fm["version"]) == CONTRACT_VERSION == TOOL_VERSION, \
        f"{skill}: skill==contract==tool violated ({fm['version']} vs {CONTRACT_VERSION} vs {TOOL_VERSION})"
    assert str(fm["contract_version"]) == SCHEMA_VERSION


@pytest.mark.parametrize("skill", SKILLS)
def test_schema_refs_resolve(skill: str) -> None:
    fm = _frontmatter(_read(skill))
    manifest = _manifest()
    refs = [str(fm["input-schema"])] + [s.strip() for s in str(fm["output-schema"]).split(",")]
    assert refs, f"{skill}: no schema refs"
    for ref in refs:
        model = ref.split("@")[0].strip()
        assert model in manifest, f"{skill}: schema ref {ref} not in manifest.json"
        assert (REPO_ROOT / "packages" / "contracts" / "schemas" / manifest[model]).exists(), \
            f"{skill}: schema file missing for {ref}"
    body = _read(skill)
    assert "schema_version" in body and "ErrorEnvelope" in body


@pytest.mark.parametrize("skill", SKILLS)
def test_allowed_tools_known(skill: str) -> None:
    fm = _frontmatter(_read(skill))
    allowed = fm["allowed-tools"]
    assert isinstance(allowed, list) and allowed
    for tool in allowed:
        assert tool in TOOL_METADATA, f"{skill}: unknown tool {tool}"
        assert TOOL_METADATA[tool].version == TOOL_VERSION
    if skill in ("investigate-incident", "correlate-symptoms"):
        assert "deployment.rollback" not in allowed, f"{skill} must not list write tools"


@pytest.mark.parametrize("skill", SKILLS)
def test_no_langgraph_import(skill: str) -> None:
    body = _read(skill)
    assert not LANGGRAPH_IMPORT.search(body), f"{skill}: must not import langgraph (HTTP+JSON only)"


def test_generator_in_sync() -> None:
    sys_path_inserted = str(REPO_ROOT) not in __import__("sys").path
    if sys_path_inserted:
        __import__("sys").path.insert(0, str(REPO_ROOT))
    from skills._generator import render_all

    rendered = render_all()
    for rel, content in rendered.items():
        p = REPO_ROOT / rel
        assert p.exists(), f"missing {rel}"
        assert p.read_text(encoding="utf-8") == content, \
            f"DRIFT: {rel} — regenerate with `python skills/_generator.py`"
