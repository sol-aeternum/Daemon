FROM python:3.11-slim

# Copy uv binaries from the official image.
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

WORKDIR /app

ENV UV_NO_PROGRESS=1

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY orchestrator ./orchestrator

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "orchestrator.main:app", "--host", "0.0.0.0", "--port", "8000"]
