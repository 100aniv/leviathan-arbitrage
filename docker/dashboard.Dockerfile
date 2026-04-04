# =============================================================================
# LEVIATHAN Dashboard — Next.js 14 Multi-stage Dockerfile
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: deps — install node_modules
# ---------------------------------------------------------------------------
FROM node:20-alpine AS deps

WORKDIR /app

RUN apk add --no-cache libc6-compat

COPY dashboard/package*.json ./
RUN npm ci --only=production

# ---------------------------------------------------------------------------
# Stage 2: development
# ---------------------------------------------------------------------------
FROM node:20-alpine AS development

WORKDIR /app

RUN apk add --no-cache libc6-compat

COPY dashboard/package*.json ./
RUN npm ci

COPY dashboard/ ./

ENV NODE_ENV=development
ENV NEXT_TELEMETRY_DISABLED=1

EXPOSE 3000

CMD ["npm", "run", "dev"]

# ---------------------------------------------------------------------------
# Stage 3: builder — Next.js production build
# ---------------------------------------------------------------------------
FROM node:20-alpine AS builder

WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY dashboard/ ./

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

# Server-side proxy target — injected at build time so Next.js rewrites() picks it up
ARG ENGINE_INTERNAL_URL=http://host.docker.internal:8000
ENV ENGINE_INTERNAL_URL=$ENGINE_INTERNAL_URL

RUN npm run build

# ---------------------------------------------------------------------------
# Stage 4: production — minimal runtime
# ---------------------------------------------------------------------------
FROM node:20-alpine AS production

WORKDIR /app

RUN apk add --no-cache wget && \
    addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD wget --quiet --tries=1 --spider http://localhost:3000/api/health || exit 1

CMD ["node", "server.js"]
