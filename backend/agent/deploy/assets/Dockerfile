# syntax=docker/dockerfile:1 Written by the deployment agent.

FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN <%.InstallCommand%>

FROM node:20-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Runtime values come from Secrets Manager.
# These placeholders only keep the build parseable.
<%- template "dockerenv" .%>
ENV NEXT_TELEMETRY_DISABLED=1
RUN <%.BuildCommand%>
# Both must exist before the runtime stage copies them.
RUN test -f .next/standalone/server.js && mkdir -p public .next/static

FROM node:20-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=<%.Port%>
ENV HOSTNAME=0.0.0.0

# Not root. A container that is compromised should not also be
# privileged inside its own filesystem.
RUN addgroup --system --gid 1001 nodejs \
 && adduser  --system --uid 1001 nextjs

COPY --from=build --chown=nextjs:nodejs /app/.next/standalone ./
# Copy assets missing from the standalone tree.
COPY --from=build --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=build --chown=nextjs:nodejs /app/public ./public

USER nextjs
EXPOSE <%.Port%>
CMD ["node", "server.js"]
