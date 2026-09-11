import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, createRun, fetchEvents, streamUrl } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("api client", () => {
  it("builds streamUrl with after_sequence", () => {
    expect(streamUrl("run_1", 42)).toBe("/v1/runs/run_1/events?after_sequence=42");
  });

  it("createRun posts and returns envelope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ run_id: "run_1", status: "created", stream_url: "/v1/runs/run_1/events", schema_version: "1.0" }),
      }),
    );
    const res = await createRun({ objective: "Investigate checkout errors" });
    expect(res.run_id).toBe("run_1");
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe("/v1/runs");
  });

  it("fetchEvents passes after_sequence", async () => {
    const f = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ run_id: "r", events: [] }) });
    vi.stubGlobal("fetch", f);
    await fetchEvents("run_9", 7);
    expect(String(f.mock.calls[0][0])).toContain("after_sequence=7");
  });

  it("wraps network failures as retryable ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("boom")),
    );
    await expect(createRun({ objective: "x" })).rejects.toMatchObject({ code: "network_error", retryable: true });
  });

  it("maps HTTP errors with retryable for 5xx", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 503, json: async () => ({ message: "busy" }) }),
    );
    const err = (await createRun({ objective: "x" }).catch((e) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.retryable).toBe(true);
  });
});
