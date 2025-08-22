FROM docker.io/python:3.12-alpine

LABEL org.opencontainers.image.description="MCP for GitHub PR, Issues, Tags and Releases"
LABEL org.opencontainers.image.authors="Said Sef"
LABEL org.opencontainers.image.documentation="https://github.com/saidsef/mcp-github-pr-issue-analyser/blob/main/README.md"
LABEL org.opencontainers.image.source="https://github.com/saidsef/mcp-github-pr-issue-analyser.git"
LABEL org.opencontainers.image.licenses="Apache License, Version 2.0"

ENV MCP_ENABLE_REMOTE="true"

WORKDIR /app

COPY requirements.txt src/mcp_github/*.py /app/

RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir uv

EXPOSE 8080 9090

CMD ["uvx", "./"]
