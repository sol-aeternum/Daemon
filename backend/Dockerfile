FROM python:3.11-slim

# Reuse the same pinned uv as the root Dockerfile (ghcr.io/astral-sh/uv:0.9.26).
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

ENV UV_NO_PROGRESS=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app

WORKDIR /app

# Copy lockfile first for layer caching; uv verifies and installs from uv.lock
# (stale or malformed locks fail the build instead of being ignored).
COPY pyproject.toml uv.lock ./

# Reproducible install from the lockfile only:
# - --locked              fail if uv.lock is out of date with pyproject.toml
# - --no-dev              runtime dependencies only
# - --no-install-project  do not build/install the local project (code is
#                         bind-mounted over /app by Compose and found via
#                         PYTHONPATH=/app)
# The environment lives in /opt/venv so a `.:/app` bind mount cannot shadow it.
RUN uv sync --locked --no-dev --no-install-project

# Copy application code for image-only runs; Compose mounts .:/app over this.
COPY orchestrator ./orchestrator
COPY db ./db
COPY config ./config
COPY providers ./providers
COPY scripts ./scripts
COPY migrations ./migrations

EXPOSE 8000

# uvicorn resolves from /opt/venv/bin via PATH, preserving the Compose
# `uvicorn ...` / `python ...` commands unchanged.
CMD ["uvicorn", "orchestrator.main:app", "--host", "0.0.0.0", "--port", "8000"]
