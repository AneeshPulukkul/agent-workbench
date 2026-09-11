# Devcontainer: Python 3.12 + Node 20
FROM mcr.microsoft.com/devcontainers/python:3.12-bookworm

# Install Node.js 20 (nodesource) + Docker CLI plugin prerequisites
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && NODE_MAJOR=20 \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_$NODE_MAJOR.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y --no-install-recommends nodejs \
    && node --version && npm --version \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /workspaces/agent-operations-workbench
