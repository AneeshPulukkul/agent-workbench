import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ApprovalCard } from "./ApprovalCard";
import { AssistantStream } from "./AssistantStream";
import { DelegationTimeline } from "./DelegationTimeline";
import { FindingsPanel } from "./FindingsPanel";
import { ToolTimeline } from "./ToolTimeline";
import type { AgUiEvent } from "../lib/types";

function ag(partial: Partial<AgUiEvent>): AgUiEvent {
  return { type: "UNKNOWN", sequence: 1, data: {}, ...partial } as AgUiEvent;
}

describe("workbench components", () => {
  it("AssistantStream renders deltas and empty state", () => {
    const { rerender } = render(<AssistantStream events={[]} />);
    expect(screen.getByTestId("stream-empty")).toBeInTheDocument();
    rerender(
      <AssistantStream
        events={[ag({ type: "TEXT_MESSAGE_CONTENT", sequence: 1, data: { delta: "hello " } }), ag({ type: "TEXT_MESSAGE_CONTENT", sequence: 2, data: { delta: "world" } })]}
      />,
    );
    expect(screen.getByTestId("stream-text")).toHaveTextContent("hello");
  });

  it("ToolTimeline lists tool calls", () => {
    render(
      <ToolTimeline
        events={[
          ag({ type: "TOOL_CALL_START", sequence: 1, data: { tool_name: "telemetry.query_metrics" } }),
          ag({ type: "TOOL_CALL_FINISH", sequence: 2, data: { tool_name: "telemetry.query_metrics", status: "succeeded", latency_s: 0.4 } }),
        ]}
      />,
    );
    expect(screen.getAllByTestId("tool-entry")).toHaveLength(2);
  });

  it("DelegationTimeline lists A2A entries", () => {
    render(
      <DelegationTimeline
        events={[ag({ type: "ACTIVITY", sequence: 1, data: { agent: "observability-agent", skill_id: "correlate-service-symptoms", task_id: "t1" } })]}
      />,
    );
    expect(screen.getByTestId("delegation-entry")).toHaveTextContent("observability-agent");
  });

  it("FindingsPanel renders cards with evidence links", () => {
    render(
      <FindingsPanel
        events={[
          ag({
            type: "STATE_SNAPSHOT",
            sequence: 1,
            data: { title: "Spike", summary: "error rate up", confidence: 0.8, evidence_refs: ["telemetry://m/1"] },
          }),
        ]}
      />,
    );
    expect(screen.getByTestId("finding-card")).toHaveTextContent("Spike");
    expect(screen.getByTestId("evidence-link")).toBeInTheDocument();
  });

  it("ApprovalCard shows approve/reject and calls back", async () => {
    const onDecide = vi.fn();
    render(
      <ApprovalCard
        events={[
          ag({
            type: "FRONTEND_INTERACTION",
            sequence: 1,
            data: {
              actions: [{ action_id: "act_1", tool_name: "deployment.rollback", reason: "bad release", risk: "medium", rollback: "re-deploy" }],
              approvals: [{ approval_id: "appr_1", action_id: "act_1", decision: "pending" }],
            },
          }),
        ]}
        onDecide={onDecide}
        busy={false}
      />,
    );
    expect(screen.getByTestId("approval-action")).toHaveTextContent("deployment.rollback");
    screen.getByText("Approve").click();
    expect(onDecide).toHaveBeenCalledWith(
      "appr_1",
      "approved",
      expect.objectContaining({ approver: expect.any(String) }),
    );
  });

  it("ApprovalCard renders nothing without interaction events", () => {
    const { container } = render(<ApprovalCard events={[]} onDecide={() => {}} busy={false} />);
    expect(container).toBeEmptyDOMElement();
  });
});
