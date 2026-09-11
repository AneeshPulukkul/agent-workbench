import { useEffect, useState } from "react";
import type { ConnState } from "../hooks/useRunEvents";
import type { AgUiEvent } from "../lib/types";

export type ThemeMode = "light" | "dark" | "auto";

const THEME_KEY = "aow-theme";

export function getStoredTheme(): ThemeMode {
  try {
    const v = window.localStorage.getItem(THEME_KEY);
    if (v === "light" || v === "dark" || v === "auto") return v;
  } catch {
    /* ignore */
  }
  return "auto";
}

export function applyTheme(mode: ThemeMode) {
  const root = document.documentElement;
  if (mode === "auto") {
    delete root.dataset.theme;
    root.removeAttribute("data-theme");
  } else {
    root.dataset.theme = mode;
  }
}

export function useTheme() {
  const [theme, setThemeState] = useState<ThemeMode>(() =>
    typeof window === "undefined" ? "auto" : getStoredTheme(),
  );
  useEffect(() => {
    applyTheme(theme);
    try {
      window.localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* ignore */
    }
  }, [theme]);
  return { theme, setTheme: setThemeState };
}

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  return (
    <label className="aow-theme-toggle">
      Theme
      <select
        aria-label="theme-toggle"
        data-testid="theme-toggle"
        value={theme}
        onChange={(e) => setTheme(e.target.value as ThemeMode)}
      >
        <option value="light">Light</option>
        <option value="dark">Dark</option>
        <option value="auto">Auto</option>
      </select>
    </label>
  );
}

export function RunHeader({
  runId,
  status,
  conn,
  lastSequence,
  onCancel,
  onReconnect,
  cancelling,
}: {
  runId: string | null;
  status: string | null;
  conn: ConnState;
  lastSequence: number;
  onCancel: () => void;
  onReconnect: () => void;
  cancelling: boolean;
}) {
  const pillClass = status ? "aow-pill aow-pill--active" : "aow-pill aow-pill--idle";
  return (
    <header className="aow-header">
      <div className="aow-brand">
        <span className="aow-mark" aria-hidden="true">
          AO
        </span>
        <div>
          <h1>Agent Operations Workbench</h1>
          <small>Investigate · Approve · Delegate</small>
        </div>
      </div>
      <div className="aow-header-meta">
        {runId && (
          <span data-testid="run-id" className="aow-run-id">
            run <code>{runId}</code>
          </span>
        )}
        {status && (
          <span data-testid="run-status" className={pillClass}>
            {status}
          </span>
        )}
        <span data-testid="conn-state" className="aow-conn" data-conn={conn}>
          stream: {conn}
        </span>
        <span data-testid="last-seq" className="aow-pill">
          seq: {lastSequence}
        </span>
        <ThemeToggle />
        {runId && (
          <>
            <button className="aow-btn" onClick={onCancel} disabled={cancelling} aria-label="cancel-run">
              {cancelling ? "Cancelling…" : "Cancel run"}
            </button>
            <button className="aow-btn" onClick={onReconnect} aria-label="reconnect">
              Reconnect / replay
            </button>
          </>
        )}
      </div>
    </header>
  );
}

export function UnknownEvents({ events }: { events: AgUiEvent[] }) {
  const unknowns = events.filter((e) => e.type === "UNKNOWN");
  if (unknowns.length === 0) return null;
  return (
    <section aria-label="unknown-events" className="aow-card">
      <h2>Other events ({unknowns.length})</h2>
      {unknowns.map((e, i) => (
        <details key={`${e.sequence}-${i}`} data-testid="unknown-event" className="aow-unknown-details">
          <summary>
            {(e.data.canonical_type as string) ?? "unknown"} · seq {e.sequence}
          </summary>
          <pre className="aow-unknown-pre">{JSON.stringify(e.data.payload ?? e.data, null, 2)}</pre>
        </details>
      ))}
    </section>
  );
}

export function ErrorBanner({ error, onRetry }: { error: string | null; onRetry: () => void }) {
  if (!error) return null;
  return (
    <div role="alert" className="aow-alert">
      <strong>Stream issue:</strong> {error}{" "}
      <button className="aow-btn" onClick={onRetry} aria-label="retry-stream">
        Retry
      </button>
    </div>
  );
}
