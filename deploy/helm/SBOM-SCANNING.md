# SBOM / Signing / Scanning — image supply-chain policy (Spec §11.2)

All production images (`api`, `worker`, `mcp-server`, `a2a-*`, `ui`) must:

1. **Pinned base + lockfile**: Dockerfiles pin digest (`FROM python:3.12-slim@sha256:...`);
   Python deps from `requirements.lock` (`pip install --require-hashes`).
2. **SBOM**: generated at build time and attached to the image:
   ```bash
   syft packages dir:/app -o spdx-json=sbom.spdx.json
   # or: docker buildx build --sbom=true --provenance=true .
   ```
   Publish SBOM to the registry (`*.spdx.json` artifact per image tag).
3. **Signing**: cosign sign + verify in-cluster (admission controller):
   ```bash
   cosign sign --yes ghcr.io/org/agent-api:0.1.0
   cosign verify --certificate-identity-regexp '.*' ghcr.io/org/agent-api:0.1.0
   ```
4. **Scanning**: gate releases on HIGH/CRITICAL = 0 (or triaged exception):
   ```bash
   trivy image --severity HIGH,CRITICAL --exit-code 1 ghcr.io/org/agent-api:0.1.0
   grype ghcr.io/org/agent-api:0.1.0 --fail-on high
   ```
5. **Runtime hardening** (enforced in every chart under `deploy/helm/*`):
   non-root user, `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false`,
   `capabilities.drop: [ALL]`, `seccompProfile.type: RuntimeDefault`,
   least-privilege ServiceAccount (no cluster roles), NetworkPolicy default-deny
   + explicit egress, liveness/readiness probes, CPU/memory requests+limits.
6. **Secrets**: never baked into images; injected via external-secrets /
   KeyVault CSI (`agent-*-secrets` Secret refs in charts). `.env.example`
   contains placeholders only.

CI checklist: `helm lint`, `helm template`, `kubeconform`, trivy scan,
cosign verify before `helm upgrade`.
