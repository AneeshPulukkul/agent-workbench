# Domain Packs

A **domain pack** is the extension unit for the Agent Operations Workbench engine:
all solution-specific artifacts needed to turn the generic agentic stack into a
specific multi-agent application.

- **Engine** (`apps/`, `packages/`, `ui/`) is domain-agnostic and should not be
  forked per solution.
- **Packs** are additive. Each pack brings its own tool catalog + executors, A2A
  specialists, policy bundle, workflow spec, skills, and local mock fixtures.
- Selection: set `DOMAIN_PACK=<pack>` (default `itops`).

## Current packs

| Pack | Status | Description |
|---|---|---|
| `itops` | reference (this repo's default) | Incident/service case investigation with policy + human approval gates |
| `travel` | planned reference | Multi-agent travel planner: search, plan, hold, approve-and-book flight/hotel/cab |

## How to add a pack

1. Copy `travel/` (once published) as a template.
2. Redefine `catalog.py`, `executors/`, `specialists/`, `policies.py`,
   `workflow.py`, `skills/`, `fixtures/`.
3. Add additive contract schemas under `packages/contracts/` (never rename or
   remove existing models).
4. Keep the engine untouched except the pre-approved seams in
   `docs/EXTENDING.md` §2 (E1–E5).

Full design and worked example: `docs/EXTENDING.md`.