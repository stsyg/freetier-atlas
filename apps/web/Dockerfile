# syntax=docker/dockerfile:1

# FreeTier Atlas web frontend image.
# Multi-stage: build the static assets with Node, then serve them with a tiny
# nginx runtime. The build context is apps/web (see docker-compose.yml), so all
# COPY paths are relative to this package.
FROM node:20-alpine AS build

ENV NPM_CONFIG_UPDATE_NOTIFIER=false \
    CI=true

# Registry used for `npm ci`. Defaults to the public npm registry so the build
# is portable for any contributor. Environments that must use a mirror or proxy
# (e.g. a corporate feed) override this via the NPM_REGISTRY build arg without
# changing the committed, public package-lock.json — npm's default
# replace-registry-host behaviour transparently maps the lockfile's public
# tarball URLs onto the configured registry.
ARG NPM_REGISTRY=https://registry.npmjs.org/

WORKDIR /app

# Install dependencies first for better layer caching. npm ci requires the
# committed package-lock.json and installs exactly the pinned versions.
COPY package.json package-lock.json ./
RUN npm ci --registry "$NPM_REGISTRY"

# Build the static site (tsc type-check + vite build -> /app/dist).
COPY . ./
RUN npm run build

# --- Runtime: static file server -------------------------------------------
FROM nginx:1.27-alpine AS runtime

# Serve the built SPA and reverse-proxy /api/ to the api service. The default
# server block is replaced with our config.
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

# nginx runs in the foreground under the base image's default entrypoint.
