# Enterprise IQ web UI — Next.js 15 (App Router) on Alpine, standalone build.
#
# Build (locally):  docker build -t enterpriseiq-webui ./webui
# Run:              docker run --rm -p 3000:3000 -e MCP_BASE_URL=... enterpriseiq-webui
#
# Multi-stage so the runtime image stays tiny (~180 MB) and only contains the
# Node runtime + the standalone server bundle.
#
# NODE_IMAGE override: defaults to docker.io's node:22-alpine for local
# builds. ACR Tasks builds (via azd) override this to our ACR mirror
# (acrenterpriseiq<suffix>.azurecr.io/node:22-alpine) — see azure.yaml — so we
# don't get rate-limited by Docker Hub on every redeploy. Re-mirror with:
#   az acr import --name <acr-name> --source docker.io/library/node:22-alpine \
#                 --image node:22-alpine --force

ARG NODE_IMAGE=node:22-alpine

# ─────────────────────────── Stage 1: deps ───────────────────────────
FROM ${NODE_IMAGE} AS deps
WORKDIR /app
ARG NPM_REGISTRY=https://registry.npmjs.org/

# Install OS deps Next.js + sharp need on Alpine.
RUN apk add --no-cache libc6-compat

COPY package.json package-lock.json .npmrc ./
RUN npm install --global npm@11.12.1 --registry="$NPM_REGISTRY" --ignore-scripts --no-audit --no-fund \
 && npm ci --registry="$NPM_REGISTRY" --no-audit --no-fund

# ─────────────────────────── Stage 2: build ──────────────────────────
FROM deps AS builder
WORKDIR /app
COPY . .

# NEXT_PUBLIC_* vars are inlined into the client bundle at build time, so
# they must be available during `next build`. Pass them with --build-arg.
#
# AAD client/tenant IDs are intentionally NOT here — they're served at
# runtime by /api/me from the Container App's AAD_CLIENT_ID /
# AAD_TENANT_ID env vars. Build-arg propagation through ACR Tasks remote
# builds has historically been flaky; runtime config sidesteps that
# entirely and lets ops rotate the app registration without a rebuild.
ARG NEXT_PUBLIC_APP_NAME="Fabric Foundry GitHubCopilot IQ Accelerator"
ARG NEXT_PUBLIC_BRAND_RED="#00A859"
ENV NEXT_PUBLIC_APP_NAME=$NEXT_PUBLIC_APP_NAME \
    NEXT_PUBLIC_BRAND_RED=$NEXT_PUBLIC_BRAND_RED

ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ─────────────────────────── Stage 3: runtime ────────────────────────
FROM ${NODE_IMAGE} AS runner
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# Non-root user (matches Next's documented standalone deploy guidance).
RUN addgroup --system --gid 1001 nodejs \
 && adduser  --system --uid 1001 --ingroup nodejs nextjs

# Standalone output bundles only what's needed.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone   ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static       ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public             ./public

USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
