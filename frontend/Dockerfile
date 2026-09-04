# Build the SPA, then serve the static output from nginx.
# The app calls /api relatively (api.ts: `const BASE = ''`), so no API base URL is
# baked into the bundle -- Traefik routes crm.host/api to the API container on the
# same origin. Same-origin is load-bearing, not a convenience: the session cookie
# is SameSite=Lax and would never be attached cross-site.
FROM node:22-slim AS build
WORKDIR /build

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
# `npm run build` is `tsc -b && vite build`, so a type error fails the image build.
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /build/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
