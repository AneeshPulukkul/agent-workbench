import { useState } from "react";

export function CaseForm({
  onCreate,
  busy,
}: {
  onCreate: (input: { title: string; objective: string; service: string }) => void;
  busy: boolean;
}) {
  const [title, setTitle] = useState("Checkout error-rate spike");
  const [service, setService] = useState("checkout-api");
  const [objective, setObjective] = useState("Investigate elevated checkout API error rate");

  return (
    <form
      aria-label="create-case"
      className="aow-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (objective.trim()) onCreate({ title, objective: objective.trim(), service });
      }}
    >
      <label className="aow-field">
        Case title
        <input aria-label="case-title" className="aow-input" value={title} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <label className="aow-field">
        Service
        <input aria-label="case-service" className="aow-input" value={service} onChange={(e) => setService(e.target.value)} />
      </label>
      <label className="aow-field">
        Objective
        <textarea
          aria-label="case-objective"
          className="aow-input"
          value={objective}
          onChange={(e) => setObjective(e.target.value)}
          rows={3}
        />
      </label>
      <button type="submit" className="aow-btn aow-btn--primary" disabled={busy || !objective.trim()}>
        {busy ? "Creating…" : "Create run"}
      </button>
    </form>
  );
}
