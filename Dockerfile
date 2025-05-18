FROM docker.io/python:3.12-alpine

ARG GITHUB_TOKEN

ENV MCP_ENABLE_REMOTE="true"

WORKDIR /app

COPY requirements.txt github_integration.py ip_integration.py issues_pr_analyser.py /app

RUN uv pip install --no-cache-dir -r requirements.txt

EXPOSE 8080

CMD ["python3", "issues_pr_analyser.py"]
