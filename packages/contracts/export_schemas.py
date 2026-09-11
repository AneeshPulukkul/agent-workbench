"""Export every contract model JSON Schema to packages/contracts/schemas/*.json.

Usage (from repo root):
    python packages/contracts/export_schemas.py
    python packages/contracts/export_schemas.py --check   # CI: fail if stale
    python -m packages.contracts.export_schemas
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import BaseModel

if __package__ in (None, ""):
    # Direct script execution: `python packages/contracts/export_schemas.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from packages.contracts import (
        A2ATaskError,
        A2ATaskRequest,
        A2ATaskResult,
        AgentAuthentication,
        AgentCard,
        AgentEvent,
        AgentRequest,
        AgentResult,
        AgentSkill,
        Approval,
        ApprovalDecisionRequest,
        ErrorEnvelope,
        EvidenceReference,
        Finding,
        PolicyDecision,
        ProposedAction,
        Run,
        RunBudget,
        ToolInvocation,
        ToolMetadata,
    )
else:  # `python -m packages.contracts.export_schemas`
    from . import (
        A2ATaskError,
        A2ATaskRequest,
        A2ATaskResult,
        AgentAuthentication,
        AgentCard,
        AgentEvent,
        AgentRequest,
        AgentResult,
        AgentSkill,
        Approval,
        ApprovalDecisionRequest,
        ErrorEnvelope,
        EvidenceReference,
        Finding,
        PolicyDecision,
        ProposedAction,
        Run,
        RunBudget,
        ToolInvocation,
        ToolMetadata,
    )

MODELS: dict[str, type[BaseModel]] = {
    "AgentRequest": AgentRequest,
    "Run": Run,
    "RunBudget": RunBudget,
    "Finding": Finding,
    "EvidenceReference": EvidenceReference,
    "ProposedAction": ProposedAction,
    "AgentResult": AgentResult,
    "AgentEvent": AgentEvent,
    "ToolMetadata": ToolMetadata,
    "ToolInvocation": ToolInvocation,
    "A2ATaskRequest": A2ATaskRequest,
    "A2ATaskResult": A2ATaskResult,
    "A2ATaskError": A2ATaskError,
    "AgentCard": AgentCard,
    "AgentSkill": AgentSkill,
    "AgentAuthentication": AgentAuthentication,
    "Approval": Approval,
    "ApprovalDecisionRequest": ApprovalDecisionRequest,
    "PolicyDecision": PolicyDecision,
    "ErrorEnvelope": ErrorEnvelope,
}

SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"


def export_schemas(out_dir: Path = SCHEMAS_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, model in sorted(MODELS.items()):
        schema = model.model_json_schema(mode="validation")
        path = out_dir / f"{name}.1_0.json"
        path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        written.append(path)
    # Manifest pins model -> file + schema_version for skill-pack generator.
    manifest = {
        "schema_version": "1.0",
        "models": {name: f"{name}.1_0.json" for name in sorted(MODELS)},
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    written.append(out_dir / "manifest.json")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if schemas are stale")
    parser.add_argument("--out", default=str(SCHEMAS_DIR))
    args = parser.parse_args(argv)
    out_dir = Path(args.out)
    if args.check:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            fresh = export_schemas(Path(tmp))
            for p in fresh:
                current = out_dir / p.name
                if not current.exists() or current.read_text(encoding="utf-8") != p.read_text(
                    encoding="utf-8"
                ):
                    print(f"STALE: {current}", file=sys.stderr)
                    return 1
        print("schemas up to date")
        return 0
    written = export_schemas(out_dir)
    print(f"wrote {len(written)} schemas to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
