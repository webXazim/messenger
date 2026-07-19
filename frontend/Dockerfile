FROM node:22-alpine AS build

WORKDIR /app

COPY package*.json ./
RUN npm install --global npm@11.6.2 \
    && npm ci --no-audit --no-fund

COPY index.html vite.config.ts tsconfig*.json ./
COPY public ./public
COPY scripts ./scripts
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

RUN npm run test:realtime \
    && npm run test:support \
    && npm run test:call \
    && npm run build


FROM nginx:1.27-alpine AS runtime

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
