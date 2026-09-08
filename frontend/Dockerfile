# --- build the SPA ---------------------------------------------------------
FROM node:22-alpine AS build
RUN corepack enable && corepack prepare pnpm@11.3.0 --activate
WORKDIR /build

COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .
# `pnpm build` is `tsc -b && vite build`, so a type error fails the image build.
RUN pnpm build

# --- runtime: static files, plus the /api proxy to the backend --------------
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/dist /usr/share/nginx/html

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
