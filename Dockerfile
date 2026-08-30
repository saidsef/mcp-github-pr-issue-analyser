# Deno and uv ship the binary in an image of their own, so dependabot bumps
# these FROM lines like any other image. See #323.
FROM denoland/deno:bin-2.9.6 AS deno
FROM ghcr.io/astral-sh/uv:0.12.7 AS uv

FROM docker.io/python:3.14-slim AS builder

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md /app/
COPY src src

# Build from the lock, so the image ships the dependency set CI resolved rather
# than whatever satisfies the ranges on the day it is built.
RUN UV_PROJECT_ENVIRONMENT=/opt/venv \
    uv sync --locked --no-dev --no-editable --no-cache

FROM docker.io/python:3.14-slim

ARG PORT=8081

LABEL org.opencontainers.image.description="MCP for GitHub PR, Issues, Tags and Releases"
LABEL org.opencontainers.image.authors="Said Sef"
LABEL org.opencontainers.image.documentation="https://github.com/saidsef/mcp-github-pr-issue-analyser/blob/main/README.md"
LABEL org.opencontainers.image.source="https://github.com/saidsef/mcp-github-pr-issue-analyser.git"
LABEL org.opencontainers.image.licenses="Apache License, Version 2.0"

ENV MCP_ENABLE_REMOTE="true"
ENV PORT=${PORT}
ENV FASTMCP_HOME=/tmp
ENV PATH="/opt/venv/bin:${PATH}"

COPY --from=deno /deno /usr/local/bin/deno
COPY --from=builder /opt/venv /opt/venv

# pip ships with the base image and nothing here runs it - the venv python has
# none of its own, hence the absolute path. Deno caches under $HOME/.cache,
# which uid 10000 has to own; the Kubernetes manifest mounts over that path.
RUN /usr/local/bin/python -m pip uninstall --yes pip && \
    mkdir -p /.cache && chown 10000:10000 /.cache

WORKDIR /app

USER 10000:10000

EXPOSE ${PORT}/tcp

CMD ["mcp-github-pr-issue-analyser"]
