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

RUN pip install -r requirements.txt

RUN uv sync

EXPOSE ${PORT}/tcp

CMD ["uv", "run", "mcp-github-pr-issue-analyser"]
