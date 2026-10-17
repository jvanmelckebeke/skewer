# Production image: the SPA is built here and served by FastAPI from static/,
# so one container answers both the app and /api. The dev stack still uses
# backend/Dockerfile and the frontend's nginx image.

# Stage 1: build the frontend
FROM node:22-alpine AS frontend-build
RUN corepack enable && corepack prepare pnpm@11.3.0 --activate
WORKDIR /build
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ .
RUN pnpm build

# Stage 2: production image
FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
COPY --from=frontend-build /build/dist ./static

RUN adduser --disabled-password --gecos "" appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"
USER appuser
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
