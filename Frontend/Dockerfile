# Faro frontend — production Next.js standalone build.
# Build context is the Frontend/ directory:
#   docker build --build-arg BACKEND_URL=http://api:8010 Frontend/
#
# BACKEND_URL is baked at BUILD time: next.config.mjs rewrites() is evaluated
# during `next build`, so point it at the in-network API address (compose
# service name), not a public URL.

FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:20-alpine AS builder
WORKDIR /app
ARG BACKEND_URL=http://api:8010
ENV BACKEND_URL=$BACKEND_URL \
    NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=5000 \
    HOSTNAME=0.0.0.0

RUN addgroup -S faro && adduser -S faro -G faro
COPY --from=builder --chown=faro:faro /app/.next/standalone ./
COPY --from=builder --chown=faro:faro /app/.next/static ./.next/static
COPY --from=builder --chown=faro:faro /app/public ./public
USER faro

EXPOSE 5000
CMD ["node", "server.js"]
