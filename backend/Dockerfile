# Two final stages. `runtime` is the lean one and is the default, so an
# unqualified `docker build` never carries pytest. `dev` adds the test tooling
# and is what docker-compose.yaml asks for, because that compose file is the
# development environment: it bind-mounts the source and runs uvicorn with
# --reload. Deploying skewer means building without a target.
FROM python:3.12-slim AS base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# Split from `dev` so that editing application code does not reinstall the
# test tooling on every rebuild.
FROM base AS devdeps

COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt

FROM devdeps AS dev

COPY . .

FROM base AS runtime

COPY . .

RUN adduser --disabled-password --gecos "" appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"
USER appuser
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
