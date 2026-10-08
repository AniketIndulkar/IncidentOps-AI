# syntax=docker/dockerfile:1

# ---- Stage 1: build the virtualenv with uv ----------------------------------------------------
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv

# Compile .pyc at build time (faster startup); copy files instead of hardlinking from the cache;
# use the image's Python, never download one.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first: this layer is reused until pyproject.toml/uv.lock change.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Then our own package, installed non-editable so the runtime image doesn't need src/.
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable


# ---- Stage 2: small runtime image -------------------------------------------------------------
FROM python:3.13-slim

# Run as an unprivileged user, never root.
RUN useradd --create-home --uid 10001 app

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY apps ./apps
COPY migrations ./migrations
COPY alembic.ini ./
COPY docker/entrypoint.sh ./entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--app-dir", "apps/api", "--host", "0.0.0.0", "--port", "8000"]
