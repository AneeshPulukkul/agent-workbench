"""Versioned domain contracts. Single import surface for gateway/orchestrator."""

from .a2a import (
    A2ATaskError,
    A2ATaskRequest,
    A2ATaskResult,
    A2ATaskStatus,
    AgentAuthentication,
    AgentCard,
    AgentSkill,
)
from .approvals import (
    Approval,
    ApprovalDecision,
    ApprovalDecisionRequest,
    PolicyDecision,
)
from .errors import ErrorCode, ErrorEnvelope
from .events import AgentEvent, EventType
from .run import (
    AgentRequest,
    AgentResult,
    EvidenceKind,
    EvidenceReference,
    Finding,
    ProposedAction,
    RiskLevel,
    Run,
    RunBudget,
    RunStatus,
    SeverityLevel,
)
from .tools import (
    AuthorizationDecision,
    SideEffect,
    ToolCategory,
    ToolInvocation,
    ToolInvocationStatus,
    ToolMetadata,
)

SCHEMA_VERSION = "1.0"

__version__ = "1.0.0"

__all__ = [
    "SCHEMA_VERSION",
    "__version__",
    "AgentRequest",
    "AgentResult",
    "Run",
    "RunBudget",
    "RunStatus",
    "SeverityLevel",
    "RiskLevel",
    "EvidenceKind",
    "EvidenceReference",
    "Finding",
    "ProposedAction",
    "AgentEvent",
    "EventType",
    "ToolCategory",
    "SideEffect",
    "ToolInvocationStatus",
    "AuthorizationDecision",
    "ToolMetadata",
    "ToolInvocation",
    "A2ATaskStatus",
    "A2ATaskRequest",
    "A2ATaskError",
    "A2ATaskResult",
    "AgentSkill",
    "AgentAuthentication",
    "AgentCard",
    "Approval",
    "ApprovalDecision",
    "ApprovalDecisionRequest",
    "PolicyDecision",
    "ErrorCode",
    "ErrorEnvelope",
]
