FROM docker.io/python:3.14-slim

LABEL org.opencontainers.image.description="MCP for GitHub PR, Issues, Tags and Releases"
LABEL org.opencontainers.image.authors="Said Sef"
LABEL org.opencontainers.image.documentation="https://github.com/saidsef/mcp-github-pr-issue-analyser/blob/main/README.md"
LABEL org.opencontainers.image.source="https://github.com/saidsef/mcp-github-pr-issue-analyser.git"
LABEL org.opencontainers.image.licenses="Apache License, Version 2.0"

ARG PORT=8081

ENV MCP_ENABLE_REMOTE="true"
ENV PORT=${PORT}
ENV FASTMCP_HOME=/tmp

WORKDIR /app
COPY pyproject.toml README.md /app/
COPY src src

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl unzip ca-certificates && \
    ARCH=$(uname -m) && \
    case "$ARCH" in \
      x86_64)  DENO_ARCH="x86_64-unknown-linux-gnu" ;; \
      aarch64) DENO_ARCH="aarch64-unknown-linux-gnu" ;; \
      *) echo "Unsupported arch: $ARCH" && exit 1 ;; \
    esac && \
    curl -fsSL "https://github.com/denoland/deno/releases/latest/download/deno-${DENO_ARCH}.zip" -o /tmp/deno.zip && \
    unzip /tmp/deno.zip -d /usr/bin && \
    chmod +x /usr/bin/deno && \
    rm /tmp/deno.zip && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir uv && \
    uv pip install --system .

EXPOSE ${PORT}/tcp

CMD ["mcp-github-pr-issue-analyser"]
