# MCP GitHub Issues Create/Update and PR Analyse

The [Model Context Protocol](https://www.anthropic.com/news/model-context-protocol) (MCP) is an open standard that can be implemented in any AI system, including Custom LLM setups. However, the degree of integration and optimisation varies based on the model's architecture and capabilities.

This MCP application analyses GitHub pull requests and managing issues. It provides a set of tools to fetch PR details, create issues, and update issues directly from your desktop LLM as part of an automated workflow. The application is designed for integration with other systems and supports extensibility via the MCP tool interface.

## Features

| Feature                     | Function Name             | Description                                                                                   |
|-----------------------------|---------------------------|-----------------------------------------------------------------------------------------------|
| Fetch Pull Request Details  | `fetch_pr`                | Retrieve metadata and content for any GitHub pull request.                                    |
| Update PR Description       | `update_pr_description`   | Update the description of a GitHub pull request, including file changes and context.           |
| Create GitHub Issues        | `create_github_issue`     | Easily create new issues from a PR with custom titles and bodies, including label support.     |
| Update GitHub Issues        | `update_github_issue`     | Update existing issues with new titles and descriptions.                                       |
| MCP Tool Registration       | `_register_tools`         | Tools are registered and exposed via the MCP server for easy integration.                      |

## Requirements

- Python 3.11+
- GitHub Personal Access Token (with `repo` scope)

## Architecture Diagram

```ascii
+-------------------+         +--------------------------+
|                   |         |                          |
|      LLM 🤖       | <-----> |    MCP Server (FastMCP)  |
|                   |         |  (issues_pr_analyser.py) |
+-------------------+         +--------------------------+
                                         |
                                         |  (calls tools)
                                         v
                          +-------------------------------+
                          |   GitHub Integration (GI)     |
                          | (github_integration.py)       |
                          +-------------------------------+
                                         |
                                         |  (REST API calls)
                                         v
                              +------------------------+
                              |    GitHub API          |
                              +------------------------+
```

### Legend:

- LLM interacts with the MCP server (e.g., via stdio or other transport).
- MCP Server (FastMCP) hosts tools for PR analysis and issue management.
- GitHub Integration (GI) handles actual API requests to GitHub.
- GitHub API is the external service for PR and issue data.

### Main Flows:

- LLM requests (e.g., fetch PR, create/update issue) go to MCP Server.
- MCP Server delegates to GI for GitHub operations.
- GutHubIntegration class communicates with GitHub API and returns results up the stack.

## Local Installation

1. **Clone the repository:**
```sh
git clone https://github.com/saidsef/mcp-github-pr-issue-analyser.git
cd mcp-github-pr-issue-analyser
```

2. **Install dependencies:**
```sh
uv init
uv venv
uv pip install -r requirements.txt
```
## Local Integration with Desktop LLMs

To add an MCP server to your desktop LLM such as Claude, LM Studio etc.., you need to add this section to the configuration file. The basic structure involves defining a server name and providing the command and any necessary arguments to run the server.

```json
{
  "mcpServers": {
    "github_pr_issues": {
      "command": "uv",
      "env": {
        "GITHUB_TOKEN": "<your-github-token>"
      },
      "args": [
        "run",
        "--with",
        "mcp[cli]",
        "--with",
        "requests",
        "/path/to/mcp-github-pr-issue-analyser/issues_pr_analyser.py"
      ]
    }
  }
}
```

## Source

Our latest and greatest source of *mcp-github-pr-issue-analyser* can be found on [GitHub]. [Fork us](https://github.com/saidsef/mcp-github-pr-issue-analyser/fork)!

## Contributing

We would :heart: you to contribute by making a [pull request](https://github.com/saidsef/mcp-github-pr-issue-analyser/pulls).

Please read the official [Contribution Guide](./CONTRIBUTING.md) for more information on how you can contribute.
