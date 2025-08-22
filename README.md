# MCP for GitHub PR, Issues, Tags and Releases

The [Model Context Protocol](https://www.anthropic.com/news/model-context-protocol) (MCP) is an open standard that enables seamless integration between Large Language Models (LLMs) and external tools. Whilst it can be implemented in any AI system, including custom LLM setups, the degree of integration and optimisation varies based on the model's architecture and capabilities.

<a href="https://glama.ai/mcp/servers/@saidsef/mcp-github-pr-issue-analyser">
  <img width="380" height="200" src="https://glama.ai/mcp/servers/@saidsef/mcp-github-pr-issue-analyser/badge" alt="GitHub PR Issue Analyser MCP server" />
</a>

This MCP application serves as a bridge between LLMs and GitHub's repository management features, offering automated analysis of pull requests and comprehensive issue management. It provides a robust set of tools to fetch PR details, create issues, and update issues directly from your desktop LLM. The application is designed with modularity in mind, supporting extensibility via the MCP tool interface and seamless integration with existing workflows.

The toolset enables automated PR analysis, issue tracking, tagging and release management through a standardised MCP interface, making it ideal for teams seeking to streamline their GitHub workflow automation.

## Features

| Function                                 | Description                                                                                       |
|------------------------------------------|---------------------------------------------------------------------------------------------------|
| Analyse GitHub Pull Requests and fetch diffs         | Retrieve the diff/patch for any PR in a repository.                                                |
| Fetch content and metadata for specific PRs          | Get PR title, description, author, timestamps, and state.                                          |
| Update PR title and description                      | Change the title and body of any PR.                                                               |
| Add comments to PRs                                  | Post general comments to a PR thread.                                                              |
| Add inline review comments to PRs                    | Comment on specific lines in PR files for code review.                                             |
| Create and update GitHub Issues                      | Open new issues or update existing ones with title, body, labels, and state.                       |
| Create tags and releases                             | Tag repository commits and publish releases with changelogs.                                       |
| Retrieve IPv4 and IPv6 information                   | Get public IP address details for both IPv4 and IPv6.                                              |
| List all open Issues or Pull Requests                | View all open PRs or issues for any user or organisation.                                          |

## Requirements

- Python 3.11+
- GitHub Personal Access Token (with `repo` scope)

## Architecture Diagram

```ascii
                                     +------------------------+
                                     |                        |
                                     |    MCP Client/User     |
                                     |                        |
                                     +------------------------+
                                              |
                                              | (stdio/SSE)
                                              v
+--------------------+              +------------------------+
|                    |              |    PRIssueAnalyser     |
|   IP Integration   | <------------|    (FastMCP Server)    |
|   (ipinfo.io)      |              |                        |
+--------------------+              +------------------------+
                                              |
                                              | (API calls)
                                              v
                                   +------------------------+
                                   |   GitHub Integration   |
                                   +------------------------+
                                              |
                                              | (REST API)
                     +-------------------------+-------------------------+
                     |                         |                       |
              +-------------+           +--------------+        +-------------+
              | GitHub PRs  |           |GitHub Issues |        |GitHub Tags/ |
              | & Releases  |           |              |        | Releases    |
              +-------------+           +--------------+        +-------------+
```

### Features:

1. PR Management: Fetch, analyze, and update
2. Issue Tracking: Create and update
3. Release Management: Tags and releases
4. Network Info: IPv4/IPv6 details
5. Prometheus Metrics: Comprehensive observability and monitoring

### Main Flows:

- PRIssueAnalyser: Main MCP server handling tool registration and requests
- GitHub Integration: Manages all GitHub API interactions
- IP Integration: Handles IPv4/IPv6 information retrieval
- Metrics Integration: Provides Prometheus metrics for observability
- MCP Client: Interacts via stdio or Server-Sent Events (SSE)

## Local Installation

1. **Clone the repository:**
```sh
git clone https://github.com/saidsef/mcp-github-pr-issue-analyser.git
cd mcp-github-pr-issue-analyser
```

2. **Install dependencies:**

Launch MCP in `stdio` mode.
```sh
export GITHUB_TOKEN="<github-token>"
uvx ./
```

Alternatively, launch MCP in `sse` mode.
```sh
export GITHUB_TOKEN="<github-token>"
export MCP_ENABLE_REMOTE=true
uvx ./
```
> You can access it via `sse` i.e. `http(s)://localhost:8080/sse`

## Local Integration with IDEs and LLMs

To add an MCP server to your IDE or LLM, you need to add this section to the configuration file. The basic structure involves defining a server name and providing the command and any necessary arguments to run the server.

<details>
<summary>Claude / Cursor / Windsurf</summary>

```json
{
  "mcpServers": {
    "github_prs_issues": {
      "command": "uvx",
      "env": {
        "GITHUB_TOKEN": "<your-github-token>"
      },
      "args": [
        "https://github.com/saidsef/mcp-github-pr-issue-analyser.git",
      ]
    }
  }
}
```
</details>

<details>
<summary>VS Code</summary>

```json
{
  "inputs": [
    {
      "type": "promptString",
      "id": "github-token",
      "description": "Enter your GitHub token",
      "password": true
    }
  ],
  "servers": {
    "github-prs-issues": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "https://github.com/saidsef/mcp-github-pr-issue-analyser.git",
      ],
      "env": {
        "GITHUB_TOKEN": "${input:github-token}"
      }
    }
  }
}
```
</details>

## Prometheus Metrics

When running in remote mode (`MCP_ENABLE_REMOTE=true`), the application automatically starts a Prometheus metrics server to provide observability and monitoring capabilities.

### Metrics Configuration

- **Metrics Port**: Configurable via `METRICS_PORT` environment variable (default: 9090)
- **Metrics Endpoint**: `http://localhost:9090/metrics`
- **Health Endpoint**: `http://localhost:9090/health`

### Available Metrics

The application exposes the following Prometheus metrics:

| Metric Name | Type | Description | Labels |
|-------------|------|-------------|---------|
| `mcp_github_requests_total` | Counter | Total number of MCP tool requests | `tool`, `status` |
| `mcp_github_request_duration_seconds` | Histogram | Request duration in seconds | `tool` |
| `mcp_github_active_requests` | Gauge | Number of currently active requests | `tool` |
| `mcp_github_response_size_bytes` | Summary | Size of response in bytes | `tool` |
| `mcp_github_errors_total` | Counter | Total number of errors | `tool`, `error_type` |
| `mcp_github_app_info` | Gauge | Application information | `version`, `name` |

### Usage Examples

**Basic usage with metrics enabled:**
```bash
export GITHUB_TOKEN="<your-github-token>"
export MCP_ENABLE_REMOTE=true
export METRICS_PORT=9090
uvx ./
```

**Using Docker with metrics:**
```bash
docker run -e GITHUB_TOKEN="<token>" -e MCP_ENABLE_REMOTE=true -p 8080:8080 -p 9090:9090 saidsef/mcp-github-pr-issue-analyser
```

**Prometheus scraping configuration:**
```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'mcp-github-pr-issue-analyser'
    static_configs:
      - targets: ['localhost:9090']
    scrape_interval: 30s
    metrics_path: '/metrics'
```

**Grafana dashboard example queries:**
```promql
# Request rate by tool
rate(mcp_github_requests_total[5m])

# Average request latency
rate(mcp_github_request_duration_seconds_sum[5m]) / rate(mcp_github_request_duration_seconds_count[5m])

# Error rate by tool
rate(mcp_github_errors_total[5m])

# Active requests
mcp_github_active_requests
```

## Source

Our latest and greatest source of *mcp-github-pr-issue-analyser* can be found on [GitHub]. [Fork us](https://github.com/saidsef/mcp-github-pr-issue-analyser/fork)!

## Contributing

We would :heart: you to contribute by making a [pull request](https://github.com/saidsef/mcp-github-pr-issue-analyser/pulls).

Please read the official [Contribution Guide](./CONTRIBUTING.md) for more information on how you can contribute.