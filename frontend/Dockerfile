FROM node:22.14.0-bookworm-slim AS build

WORKDIR /app

# Keep installs reliable on small VPS hosts. The Debian-based Node image avoids
# the npm/Alpine crash seen as "Exit handler never called" during npm ci. Use
# bounded concurrency for the 512 MB production VPS. Prefer IPv4 because an
# advertised but unreachable IPv6 route makes npm wait through every retry.
ENV NODE_ENV=development \
    NODE_OPTIONS="--max-old-space-size=384 --dns-result-order=ipv4first" \
    npm_config_audit=false \
    npm_config_fund=false \
    npm_config_registry=https://registry.npmjs.org/ \
    npm_config_fetch_retries=3 \
    npm_config_fetch_retry_mintimeout=10000 \
    npm_config_fetch_retry_maxtimeout=60000 \
    npm_config_fetch_timeout=60000 \
    npm_config_maxsockets=2 \
    npm_config_jobs=1

COPY package.json package-lock.json ./
RUN --mount=type=cache,id=messenger-frontend-npm,target=/root/.npm,sharing=locked \
    npm ci --include=dev --no-audit --no-fund --prefer-offline \
    || { status=$?; tail -n 100 /root/.npm/_logs/*-debug-0.log; exit "$status"; }

COPY index.html vite.config.ts tsconfig.json ./
COPY public ./public
COPY src ./src

ARG VITE_API_BASE_URL
ARG VITE_AUTH_BASE_URL
ARG VITE_CENTRAL_AUTH_ORIGIN
ARG VITE_WS_BASE_URL
ARG VITE_SUPPORT_WS_URL
ARG VITE_APP_NAME
ARG VITE_SOCIAL_BASE_URL
ARG VITE_SUPPORT_PLANS_URL

ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV VITE_AUTH_BASE_URL=$VITE_AUTH_BASE_URL
ENV VITE_CENTRAL_AUTH_ORIGIN=$VITE_CENTRAL_AUTH_ORIGIN
ENV VITE_WS_BASE_URL=$VITE_WS_BASE_URL
ENV VITE_SUPPORT_WS_URL=$VITE_SUPPORT_WS_URL
ENV VITE_APP_NAME=$VITE_APP_NAME
ENV VITE_SOCIAL_BASE_URL=$VITE_SOCIAL_BASE_URL
ENV VITE_SUPPORT_PLANS_URL=$VITE_SUPPORT_PLANS_URL

RUN npm run build

FROM nginx:1.27-alpine AS runtime

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
