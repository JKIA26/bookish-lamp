# agent-service Dockerfile
#
# Notes on current state (see engineering/service-architecture.md):
# - Sessions are in-memory only (orchestrators/agent_loop.py's _sessions dict) —
#   this container has NO persistent state. Restarting it wipes every active
#   session. Real Postgres persistence per service-architecture.md is still
#   an open item, not implemented here.
# - seed_demo_allowlists() runs on startup — replace with real per-org
#   allowlist loading before this goes anywhere near production traffic.

FROM python:3.12-slim AS base

# Faster, quieter, more predictable Python behavior in a container
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first so this layer caches independently of code changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Now copy the actual service code
COPY . .

# Run as a non-root user — no reason this process needs root
RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# PORT defaults to 8000 for local `docker run`, but Render sets its own
# $PORT at runtime and routes traffic there — the container MUST bind to
# whatever Render assigns, not a hardcoded port, or the deploy fails with
# the platform unable to reach the service.
ENV PORT=8000

# Basic container-level health check hitting the app's own /health route
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://localhost:{os.environ.get(\"PORT\",8000)}/health')" || exit 1

# main:app matches the FastAPI instance in main.py.
# Shell form (not exec-array form) so $PORT actually expands at container
# start — Render overrides this env var, and exec-array CMD would NOT
# substitute it.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT}
