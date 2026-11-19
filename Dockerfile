# syntax=docker/dockerfile:1.7
# Multi-stage build: the builder has uv and build caches; the runtime image gets
# only the finished virtualenv and the data file.

ARG PYTHON_IMAGE=python:3.12-slim

FROM ghcr.io/astral-sh/uv:0.13.9 AS uv

# ---------------------------------------------------------------- builder
FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# 1) Dependencies only. This layer is reused until pyproject.toml/uv.lock change,
#    so editing source code doesn't reinstall every package.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# 2) The project itself, installed non-editable so the venv is self-contained.
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ---------------------------------------------------------------- runtime
# Same base as the builder, so the venv's python symlinks stay valid.
FROM ${PYTHON_IMAGE}
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY data ./data
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    NL_DOCS_PATH=/app/data/handbook.json

# Numeric non-root UID: works with Kubernetes runAsNonRoot, which can't verify a name.
USER 10001
EXPOSE 8000
CMD ["uvicorn", "--factory", "north_lite.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
