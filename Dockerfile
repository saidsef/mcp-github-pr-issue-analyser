FROM docker.io/python:3.14-alpine

LABEL org.opencontainers.image.description="MCP for GitHub PR, Issues, Tags and Releases"
LABEL org.opencontainers.image.authors="Said Sef"
LABEL org.opencontainers.image.documentation="https://github.com/saidsef/mcp-github-pr-issue-analyser/blob/main/README.md"
LABEL org.opencontainers.image.source="https://github.com/saidsef/mcp-github-pr-issue-analyser.git"
LABEL org.opencontainers.image.licenses="Apache License, Version 2.0"

ARG PORT=8081

ENV MCP_ENABLE_REMOTE="true"
ENV PORT=${PORT}

WORKDIR /app
COPY pyproject.toml requirements.txt /app/
COPY src src

ARG SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${SETUPTOOLS_SCM_PRETEND_VERSION}

RUN apk add -U curl py3-uv && \
    grep -v "^-e " requirements.txt > /tmp/requirements.txt && \
    uv pip install --system -v -r /tmp/requirements.txt && \
    uv pip install --system --no-deps .

EXPOSE ${PORT}/tcp

CMD ["mcp-github-pr-issue-analyser"]
