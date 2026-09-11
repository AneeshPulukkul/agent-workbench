import { describe, expect, it } from "vitest";
import { batchToAgUi, isRenderable, toAgUi } from "./agui";
import type { CanonicalEvent } from "./types";

function ev(partial: Partial<CanonicalEvent>): CanonicalEvent {
  return {
    schema_version: "1.0",
    event_id: "e1",
    run_id: "run_1",
    sequence: 1,
    type: "message.delta",
    timestamp: new Date().toISOString(),
    data: {},
    safe_for_ui: true,
    ...partial,
  } as CanonicalEvent;
}

describe("agui adapter", () => {
  it("only renders safe_for_ui events", () => {
    expect(toAgUi(ev({ data: { delta: "hi" } }))).not.toBeNull();
    expect(toAgUi(ev({ safe_for_ui: false, data: { delta: "hi" } }))).toBeNull();
    expect(toAgUi(ev({ sensitive: true, safe_for_ui: true }))).toBeNull();
    expect(isRenderable(ev({ sensitive: true, safe_for_ui: true }))).toBe(false);
  });

  it("maps canonical types to AG-UI types", () => {
    expect(toAgUi(ev({ type: "run.started", sequence: 1 }))?.type).toBe("RUN_STARTED");
    expect(
      toAgUi(ev({ type: "message.delta", sequence: 2, data: { delta: "hello" } }))?.data.delta,
    ).toBe("hello");
    expect(toAgUi(ev({ type: "tool.started", sequence: 3, data: { tool_name: "telemetry.query_metrics" } }))?.type).toBe(
      "TOOL_CALL_START",
    );
    expect(toAgUi(ev({ type: "agent.delegated", sequence: 4, data: { agent: "observability-agent" } }))?.type).toBe(
      "ACTIVITY",
    );
    expect(
      toAgUi(
        ev({
          type: "finding.created",
          sequence: 5,
          data: { finding: { title: "t", summary: "s", confidence: 0.8, evidence_refs: ["telemetry://m/1"] } },
        }),
      )?.type,
    ).toBe("STATE_SNAPSHOT");
    expect(
      toAgUi(ev({ type: "approval.required", sequence: 6, data: { actions: [], approvals: [] } }))?.type,
    ).toBe("FRONTEND_INTERACTION");
    expect(toAgUi(ev({ type: "run.completed", sequence: 7, data: {} }))?.type).toBe("RUN_FINISHED");
  });

  it("tolerates unknown future types with a generic envelope", () => {
    const m = toAgUi(ev({ type: "galaxy.new_thing", sequence: 9, data: { foo: 1 } }));
    expect(m?.type).toBe("UNKNOWN");
    expect((m?.data as Record<string, unknown>).canonical_type).toBe("galaxy.new_thing");
  });

  it("batch preserves order and drops non-renderable", () => {
    const out = batchToAgUi([
      ev({ sequence: 3, type: "tool.started", data: { tool_name: "a" } }),
      ev({ sequence: 1, type: "message.delta", data: { delta: "x" } }),
      ev({ sequence: 2, type: "message.delta", safe_for_ui: false, data: { delta: "secret" } }),
    ]);
    expect(out.map((e) => e.sequence)).toEqual([1, 3]);
  });
});
