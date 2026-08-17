FROM ghcr.io/astral-sh/uv:latest AS uv
FROM python:3.12-slim-bookworm

COPY --from=uv /uv /uvx /bin/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy \
    HOME=/tmp \
    HF_HOME=/models/huggingface \
    PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium ffmpeg ca-certificates curl fonts-dejavu-core fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --extra dev --no-install-project

COPY src ./src
COPY tests ./tests
COPY scripts ./scripts
RUN uv sync --frozen --extra dev \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data/video-evidence-mcp/app/cache /data/video-evidence-mcp/app/tmp /models/huggingface \
    && chown -R app:app /data/video-evidence-mcp /models

USER app
EXPOSE 8787
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=4 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=3)" || exit 1
CMD ["video-evidence-server"]
