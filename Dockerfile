# Pinned so two builds a week apart ship the same runtime. Bump the version and
# both checksums together. See #311.
ARG DENO_VERSION=v2.9.6

FROM docker.io/python:3.14-slim AS builder

ARG DENO_VERSION
ARG DENO_SHA256_X86_64=394f07f4da2bebe6ce6f1e7ce0fa16429b29b08c35e3fac3fe25972676dff4b2
ARG DENO_SHA256_AARCH64=9a46afc6c392c7cd2ff71a31558935545b46408d0e87f7a86908c712721c046e

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl unzip ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    ARCH=$(uname -m) && \
    case "$ARCH" in \
      x86_64)  DENO_ARCH="x86_64-unknown-linux-gnu";  DENO_SHA256="${DENO_SHA256_X86_64}" ;; \
      aarch64) DENO_ARCH="aarch64-unknown-linux-gnu"; DENO_SHA256="${DENO_SHA256_AARCH64}" ;; \
      *) echo "Unsupported arch: $ARCH" && exit 1 ;; \
    esac && \
    curl -fsSL "https://github.com/denoland/deno/releases/download/${DENO_VERSION}/deno-${DENO_ARCH}.zip" -o /tmp/deno.zip && \
    echo "${DENO_SHA256}  /tmp/deno.zip" | sha256sum -c - && \
    unzip /tmp/deno.zip -d /usr/local/bin && \
    chmod +x /usr/local/bin/deno && \
    rm /tmp/deno.zip

WORKDIR /app
COPY pyproject.toml README.md /app/
COPY src src

RUN pip install --no-cache-dir uv && \
    uv venv /opt/venv && \
    VIRTUAL_ENV=/opt/venv uv pip install --no-cache .

FROM docker.io/python:3.14-slim

ARG DENO_VERSION
ARG PORT=8081

LABEL org.opencontainers.image.description="MCP for GitHub PR, Issues, Tags and Releases"
LABEL org.opencontainers.image.authors="Said Sef"
LABEL org.opencontainers.image.documentation="https://github.com/saidsef/mcp-github-pr-issue-analyser/blob/main/README.md"
LABEL org.opencontainers.image.source="https://github.com/saidsef/mcp-github-pr-issue-analyser.git"
LABEL org.opencontainers.image.licenses="Apache License, Version 2.0"
LABEL io.deno.version="${DENO_VERSION}"

ENV MCP_ENABLE_REMOTE="true"
ENV PORT=${PORT}
ENV FASTMCP_HOME=/tmp
ENV PATH="/opt/venv/bin:${PATH}"

COPY --from=builder /usr/local/bin/deno /usr/local/bin/deno
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
