FROM python:3.11-slim-bookworm@sha256:2333bd330d12de02514770b3585cad313644316047cdee24a7acfdece6de6efb
COPY --from=ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424 /uv /bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app \
    HF_HUB_OFFLINE=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp
WORKDIR /app
COPY tts/pyproject.toml tts/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-cache
COPY tts/download_assets.py /build/download_assets.py
RUN python /build/download_assets.py && rm /build/download_assets.py
COPY tts /app/tts
COPY orchestrator/speech/contracts.py orchestrator/speech/stream_protocol.py orchestrator/speech/stream_response.py /app/orchestrator/speech/
COPY orchestrator/models.py orchestrator/request_body_limit.py /app/orchestrator/
USER 65532:65532
CMD ["uvicorn", "tts.app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--limit-concurrency", "16", "--no-access-log"]
