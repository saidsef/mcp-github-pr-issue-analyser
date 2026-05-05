FROM docker.io/python:3.14-alpine

LABEL org.opencontainers.image.description="MCP for GitHub PR, Issues, Tags and Releases"
LABEL org.opencontainers.image.authors="Said Sef"
LABEL org.opencontainers.image.documentation="https://github.com/saidsef/mcp-github-pr-issue-analyser/blob/main/README.md"
LABEL org.opencontainers.image.source="https://github.com/saidsef/mcp-github-pr-issue-analyser.git"
LABEL org.opencontainers.image.licenses="Apache License, Version 2.0"

ARG PORT=8081
ARG TARGETARCH

ENV MCP_ENABLE_REMOTE="true"
ENV PORT=${PORT}
ENV FASTMCP_HOME=/tmp

WORKDIR /app
COPY pyproject.toml requirements.txt /app/
COPY src src

ARG SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${SETUPTOOLS_SCM_PRETEND_VERSION}

RUN apk add -U curl unzip py3-uv && \
    if [ "$TARGETARCH" = "amd64" ]; then \
        curl -fsSL "https://github.com/denoland/deno/releases/download/v2.3.3/deno-x86_64-unknown-linux-musl.zip" \
            -o /tmp/deno.zip && \
        unzip /tmp/deno.zip -d /usr/bin && \
        rm /tmp/deno.zip; \
    fi && \
    grep -v "^-e " requirements.txt > /tmp/requirements.txt && \
    uv pip install --system -v -r /tmp/requirements.txt && \
    uv pip install --system --no-deps .

EXPOSE ${PORT}/tcp

CMD ["mcp-github-pr-issue-analyser"]
