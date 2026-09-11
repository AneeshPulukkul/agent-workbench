"""Evaluation dataset schema + seed cases (Spec §12.4).

Each case: case_id/request/context/expected_tools/forbidden/expected_findings/
risk/approval/reference. Stored as JSON (``dataset.json``) + typed loader here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

DATASET_PATH = Path(__file__).with_name("dataset.json")


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=128)
    user_request: str = Field(min_length=1, max_length=8192)
    available_context: dict[str, Any] = Field(default_factory=dict)
    expected_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    expected_findings: list[str] = Field(default_factory=list)
    expected_risk: str = Field(default="medium")
    expected_approval: bool = Field(default=False)
    reference_answer: str = Field(min_length=1, max_length=16000)


def load_dataset(path: Path | None = None) -> list[EvalCase]:
    raw = json.loads((path or DATASET_PATH).read_text(encoding="utf-8"))
    return [EvalCase(**c) for c in raw]


__all__ = ["DATASET_PATH", "EvalCase", "load_dataset"]
