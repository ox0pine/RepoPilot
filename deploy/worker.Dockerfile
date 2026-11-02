FROM ghcr.io/astral-sh/uv:0.12.22 AS uv
FROM docker:27-cli AS docker_cli
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
    && apt-get install -y --no-install-recommends git openssh-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=docker_cli /usr/local/bin/docker /usr/local/bin/docker

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-editable

ENV PATH="/app/.venv/bin:$PATH"
CMD ["python", "-m", "repopilot.worker"]
