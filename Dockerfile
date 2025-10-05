FROM docker.io/python:3.12-alpine

LABEL org.opencontainers.image.description="MCP for GitHub PR, Issues, Tags and Releases"
LABEL org.opencontainers.image.authors="Said Sef"
LABEL org.opencontainers.image.documentation="https://github.com/saidsef/mcp-github-pr-issue-analyser/blob/main/README.md"
LABEL org.opencontainers.image.source="https://github.com/saidsef/mcp-github-pr-issue-analyser.git"
LABEL org.opencontainers.image.licenses="Apache License, Version 2.0"

ENV MCP_ENABLE_REMOTE="true"

WORKDIR /app
COPY pyproject.toml requirements.txt /app/
COPY src src

RUN pip install -r requirements.txt

RUN uv sync

EXPOSE 8080

CMD ["uv", "run", "mcp-github-pr-issue-analyser"]
