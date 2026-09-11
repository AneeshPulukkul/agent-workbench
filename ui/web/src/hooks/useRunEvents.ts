import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { batchToAgUi, isTerminal } from "../lib/agui";
import { ApiError, fetchEvents, streamRunEvents } from "../lib/api";
import type { AgUiEvent, CanonicalEvent } from "../lib/types";

export type ConnState = "idle" | "connecting" | "live" | "reconnecting" | "closed" | "error";

export interface UseRunEvents {
  raw: CanonicalEvent[];
  agui: AgUiEvent[];
  conn: ConnState;
  error: string | null;
  lastSequence: number;
  reconnect: () => void;
  replay: (events: CanonicalEvent[]) => void;
}

export const MAX_FETCH_RETRIES = 5;
const FETCH_BASE_MS = 400;
const FETCH_CAP_MS = 8000;

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

function maxSeq(events: CanonicalEvent[]): number {
  let m = 0;
  for (const e of events) {
    if (typeof e?.sequence === "number" && e.sequence > m) m = e.sequence;
  }
  return m;
}

async function fetchWithRetry(
  runId: string,
  after: number,
  signal: AbortSignal,
  retries = MAX_FETCH_RETRIES,
): Promise<{ run_id: string; events: CanonicalEvent[] }> {
  let attempt = 0;
  let delay = FETCH_BASE_MS;
  for (;;) {
    try {
      return await fetchEvents(runId, after, signal);
    } catch (e) {
      if ((e as Error)?.name === "AbortError") throw e;
      const retryable = e instanceof ApiError ? e.retryable : true;
      if (!retryable || attempt >= retries) throw e;
      await sleep(delay);
      if (signal.aborted) {
        const ab = new Error("aborted");
        ab.name = "AbortError";
        throw ab;
      }
      attempt += 1;
      delay = Math.min(delay * 2, FETCH_CAP_MS);
    }
  }
}

/**
 * Streams a run: initial catch-up from persisted events (?after_sequence=),
 * then live SSE. Reconnect replays from lastSequence. Terminal AG-UI event
 * closes the stream. Unknown types are tolerated by the adapter; only
 * safe_for_ui events reach `agui`.
 */
export function useRunEvents(runId: string | null): UseRunEvents {
  const [bySeq, setBySeq] = useState<Map<number, CanonicalEvent>>(new Map());
  const [conn, setConn] = useState<ConnState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [epoch, setEpoch] = useState(0);
  const [lastSequence, setLastSequence] = useState(0);
  const seqRef = useRef(0);
  const terminalRef = useRef(false);

  const ingest = useCallback((events: CanonicalEvent[]) => {
    const valid = events.filter((e) => typeof e?.sequence === "number");
    if (valid.length === 0) return;
    const batchMax = maxSeq(valid);
    if (batchMax > seqRef.current) {
      seqRef.current = batchMax;
      setLastSequence(batchMax);
    }
    setBySeq((prev) => {
      const next = new Map(prev);
      for (const e of valid) {
        if (!next.has(e.sequence)) next.set(e.sequence, e);
      }
      return next;
    });
  }, []);

  const replay = useCallback(
    (events: CanonicalEvent[]) => {
      seqRef.current = 0;
      setLastSequence(0);
      terminalRef.current = false;
      setBySeq(new Map());
      setError(null);
      ingest(events);
    },
    [ingest],
  );

  const reconnect = useCallback(() => setEpoch((n) => n + 1), []);

  useEffect(() => {
    if (!runId) {
      setConn("idle");
      return;
    }
    let cancelled = false;
    const abort = new AbortController();
    const cleanupRef: { current: (() => void) | null } = { current: null };
    terminalRef.current = false;
    // Fresh run: drop previous run's events so sequences don't leak across runs.
    seqRef.current = 0;
    setLastSequence(0);
    setBySeq(new Map());
    setConn("connecting");
    setError(null);

    // Capture resume point BEFORE any async work: reading seqRef after the
    // fetch resolves would race with concurrent ingests/replays.
    const resumeFrom = seqRef.current;

    // 1. Catch-up from persisted events (reconnect/replay friendly), with
    // bounded retries + capped exponential backoff.
    fetchWithRetry(runId, resumeFrom, abort.signal)
      .then((snap) => {
        if (cancelled) return;
        ingest(snap.events ?? []);
        if (cancelled) return;
        setConn("live");
        // Resume the live stream from the max of the pre-fetch point and the
        // fetched batch — computed from values captured in this closure, not
        // a ref read that may have moved during the await.
        const fetchedMax = maxSeq(snap.events ?? []);
        const resumeSeq = Math.max(resumeFrom, fetchedMax, seqRef.current);
        // 2. Live SSE from last persisted sequence.
        const close = streamRunEvents(
          runId,
          {
            onEvent: (e) => {
              if (cancelled || terminalRef.current) return;
              ingest([e]);
              const mapped = batchToAgUi([e]);
              if (mapped.some((m) => isTerminal(m))) {
                terminalRef.current = true;
                setConn("closed");
                close();
              }
            },
            onError: (e) => {
              if (cancelled) return;
              setConn("reconnecting");
              setError(e instanceof ApiError ? e.message : (e as Error).message);
            },
            onOpen: () => {
              if (!cancelled) {
                setConn("live");
                setError(null);
              }
            },
          },
          { afterSequence: resumeSeq },
        );
        abort.signal.addEventListener("abort", close);
        cleanupRef.current = () => {
          close();
          abort.abort();
        };
      })
      .catch((e) => {
        if (cancelled) return;
        // Timeout state: surface, keep retryable via reconnect().
        if ((e as Error)?.name === "AbortError") return;
        setConn("error");
        const base = e instanceof ApiError ? e.message : (e as Error).message;
        setError(`Failed to load events after ${MAX_FETCH_RETRIES} attempts: ${base}`);
      });

    return () => {
      cancelled = true;
      cleanupRef.current?.();
      abort.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, epoch]);

  const raw = useMemo(() => [...bySeq.values()].sort((a, b) => a.sequence - b.sequence), [bySeq]);
  const agui = useMemo(() => batchToAgUi(raw), [raw]);

  return { raw, agui, conn, error, lastSequence, reconnect, replay };
}
