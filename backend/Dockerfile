# Development image, for docker-compose.yaml. It bind-mounts the source and
# runs uvicorn with --reload. Production is the root Dockerfile, which also
# builds the SPA into the same image.
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
