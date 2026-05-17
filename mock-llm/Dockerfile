# syntax=docker/dockerfile:1.7
# Phase 2 — mock LLM container.
# Two stages keep the runtime image free of build tooling and pip caches.

ARG PYTHON_VERSION=3.12

# ---------- 1. builder: install runtime deps into an isolated venv ----------
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt ./
RUN pip install --upgrade pip \
 && pip install -r requirements.txt

# ---------- 2. runtime: minimal image, non-root, venv copied in ----------
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# Dedicated unprivileged user. UID/GID 1001 avoids collisions with common
# distro users (1000 is usually the first interactive user).
RUN groupadd --system --gid 1001 mockllm \
 && useradd  --system --uid 1001 --gid mockllm --no-create-home --shell /usr/sbin/nologin mockllm

# Bring the prebuilt venv only — no pip, no apt, no compilers in the runtime.
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY app /app/app

USER mockllm
EXPOSE 8000

# Single uvicorn worker is intentional: the asyncio.Semaphore in
# app/simulator.py only meaningfully caps concurrency within one process.
# Horizontal scaling is the Kubernetes Deployment's job (replicas), not
# the container's.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
