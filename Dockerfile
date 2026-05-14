# Multi-stage build with two runtime images so the inference worker can
# carry torch + torchvision (~1 GB) while the rest of the services stay
# lean. Compose picks the right one via `build.target:` per service.

FROM python:3.11-slim AS builder
RUN pip install --no-cache-dir uv==0.11.3
WORKDIR /app
COPY pyproject.toml uv.lock ./

# Core-only venv — fastapi, sqlalchemy, paramiko, rq, hvac, etc.
FROM builder AS builder-core
RUN uv sync --frozen --no-install-project

# Core + ML venv — adds torch + torchvision + pillow under the [ml] extra.
FROM builder AS builder-ml
RUN uv sync --frozen --no-install-project --extra ml

# ----- Core runtime (api, migrate, worker-ingest) -----
FROM python:3.11-slim AS runtime-core
WORKDIR /app
COPY --from=builder-core /app/.venv /app/.venv
COPY alembic.ini ./
COPY app/ ./app/
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "app.workers.sftp_ingest"]

# ----- ML runtime (worker-inference) -----
# Carries the real classifier.pt baked under app/classifier/models/ via
# the `COPY app/` line below, so the worker passes
# assert_classifier_artifacts() at boot without volume mounts.
FROM python:3.11-slim AS runtime-ml
WORKDIR /app
COPY --from=builder-ml /app/.venv /app/.venv
COPY alembic.ini ./
COPY app/ ./app/
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "app.workers.inference"]
