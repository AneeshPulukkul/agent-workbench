# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - Unreleased

### Added

- Gateway wiring: runs CRUD, sequence-ordered SSE events with `after_sequence` / `Last-Event-ID` replay, approvals decide, idempotent cancel.
- Auth fail-closed: OIDC JWKS signature verification (`iss`/`aud`/`exp`, `alg=none` rejected); tenant/scopes from verified identity only.
- Tool parity: 10 catalog tools (v1.0.0) with `skill == contract == tool` pin and `make check-parity` generator check.
- UI contract: AG-UI shell (Assistant, amber Approval card, timelines, Findings) with light/dark/auto theme and SSE reconnect.
- PII protection: hashes + redacted JSON in audit/persistence; `sensitive` gated by `safe_for_ui`; OTel content capture off by default.
- Evaluation: 12-case production-shaped dataset (rollback approval, budget exhaustion, A2A fallback, replay, DB outage, ticket approval, trace triage).
- Release pack: portable skill-pack (`AGENTS.md` + `skills/*/SKILL.md`, no `langgraph` client dep) plus tutorials, glossary, and FAQ docs.

### Fixed

- Double-decide returns 409 `conflict`/`approval_expired`; same `(approval, Idempotency-Key)` replays the stored outcome.
- Stale `schema_version` rejected with 400 `ErrorEnvelope` (upgrade-required) instead of silent mismatch.
- Docker Desktop API-version mismatch documented with `DOCKER_API_VERSION` workaround.

### Known limitations

- See `docs/operations/known-limitations.md` (mock adapters, per-replica rate limiting, no Kafka/ServiceBus, Python policy only, single-region).
