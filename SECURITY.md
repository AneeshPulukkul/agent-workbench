# Security Policy

## Supported versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | Supported          |
| < 0.1.0 | Not supported      |

Only the latest `0.1.x` patch release receives security fixes.

## Reporting a vulnerability

Do not open a public issue. Use GitHub **Private vulnerability reporting**
(Security tab > Report a vulnerability) for this repository.

Include: affected version/commit, impact, and a minimal repro or proof of
concept. We will acknowledge within 3 business days, triage within 7 days,
and coordinate a fix and disclosure timeline with you.

## Authentication baseline (OIDC / JWKS, fail-closed)

- `AUTH_MODE=oidc` requires non-empty `OIDC_ISSUER_URL` and `OIDC_AUDIENCE`;
  startup fails otherwise.
- `alg=none` tokens are always rejected. `iss`, `aud`, and `exp` are strictly
  enforced against the configured issuer/audience.
- With `AUTH_MODE=oidc`, token signatures must verify against
  `OIDC_JWKS_URL` or `OIDC_JWKS_JSON`. If no JWKS source is configured, or
  verification libraries are missing, tokens are rejected (fail-closed).
- `AUTH_MODE=mock` is allowed only when `APP_ENV` is `local` or `test`.
  Production with mock auth refuses to resolve identity.
- Tenant identity always comes from the token (`tid` / `tenant_id`), never
  from client-supplied headers or body alone.

## Secrets handling

- Never commit real secrets, `.env` files, passwords, API keys, or private
  keys. Only safe placeholders belong in `.env.example`.
- Copy with `cp .env.example .env` and keep real values in the untracked
  `.env` only. Pre-commit runs `detect-private-key`.
- Write/destructive tools require server-side policy evaluation plus a
  persisted human `Approval` row; client claims never authorize execution.

## PII redaction scope

- Audit trails store hashes plus redacted JSON by default
  (`packages/security/redaction.py` over `apps/mcp_server/redact.py`).
- Payloads flagged `sensitive=true` are withheld from the UI unless the
  producer explicitly set `safe_for_ui=true` (hash reference returned instead).
- OTel content capture (`OTEL_CONTENT_CAPTURE`) is empty by default and never
  captures prompts/completions/secrets; secrets stay redacted in `dev-only` mode.
- Report redaction bypasses or PII leaks as security issues via the private
  channel above.
