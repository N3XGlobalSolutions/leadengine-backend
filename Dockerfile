# syntax=docker/dockerfile:1

# ---- Builder stage: compile wheels with build tooling ----
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Build wheels into a directory we copy into the slim runtime (keeps build-essential
# OUT of the final image -> smaller image, smaller attack surface).
RUN pip install --no-cache-dir --upgrade pip \
    && pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ---- Runtime stage: slim, non-root, no build tooling ----
FROM python:3.12-slim AS runtime

# UTF-8 everywhere (prevents cp1252/emoji encoding crashes) + no .pyc + unbuffered logs
ENV PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

# Copy application code (see .dockerignore for what is excluded, e.g. .env, .venv, tests)
COPY . .

# Run as a non-root user
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Liveness healthcheck hits the cheap /livez probe (never touches the DB)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/livez', timeout=3).status==200 else 1)"

# NOTE: This runs a single web process. The app currently starts background SQS
# worker threads inside the FastAPI lifespan (see main.py). Before scaling the
# API horizontally (multiple workers/replicas), those workers MUST be moved to a
# dedicated worker service so they are not duplicated per replica. See
# MANUAL_ACTIONS_REQUIRED.md.
CMD ["fastapi", "run", "main.py", "--host", "0.0.0.0", "--port", "8000"]
