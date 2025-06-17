[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/saidsef-mcp-github-pr-issue-analyser-badge.png)](https://mseep.ai/app/saidsef-mcp-github-pr-issue-analyser)

# MCP for GitHub PR, Issues, Tags and Releases

The [Model Context Protocol](https://www.anthropic.com/news/model-context-protocol) (MCP) is an open standard that enables seamless integration between Large Language Models (LLMs) and external tools. Whilst it can be implemented in any AI system, including custom LLM setups, the degree of integration and optimisation varies based on the model's architecture and capabilities.

This MCP application serves as a bridge between LLMs and GitHub's repository management features, offering automated analysis of pull requests and comprehensive issue management. It provides a robust set of tools to fetch PR details, create issues, and update issues directly from your desktop LLM. The application is designed with modularity in mind, supporting extensibility via the MCP tool interface and seamless integration with existing workflows.

The toolset enables automated PR analysis, issue tracking, tagging and release management through a standardised MCP interface, making it ideal for teams seeking to streamline their GitHub workflow automation.

## Features

| Feature                     | Function Name                  | Description                                                                                   |
|----------------------------|--------------------------------|-----------------------------------------------------------------------------------------------|
| PR Content Retrieval       | `get_github_pr_content`        | Fetch PR metadata including title, description, author, and state.                            |
| PR Diff Analysis          | `get_github_pr_diff`           | Retrieve the diff/patch content showing file changes in the PR.                              |
| PR Description Updates     | `update_github_pr_description` | Update PR titles and descriptions with What/Why/How sections and file changes.               |
| PR General Comments        | `add_github_pr_comment`        | Add general discussion comments to pull requests.                                            |
| PR Inline Code Comments    | `add_github_pr_inline_comment` | Add inline review comments to specific lines in PR files for code review.                    |
| Issue Creation            | `create_github_issue`          | Create new issues with conventional commit prefixes (feat/fix/chore) and MCP label.          |
| Issue Updates             | `update_github_issue`          | Modify existing issues with new title, body, and state (open/closed).                        |
| Tag Management            | `create_github_tag`            | Create new git tags with associated messages for versioning.                                  |
| Release Management        | `create_github_release`        | Generate GitHub releases with automatic release notes and tag references.                     |
| Network Information       | `get_ipv4_ipv6_info`          | Fetch IPv4 and IPv6 network information for the system.                                      |
| MCP Tool Registration       | `_register_tools`         | Tools are registered and exposed via the MCP server for easy integration.                      |

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

### Main Flows:

- PRIssueAnalyser: Main MCP server handling tool registration and requests
- GitHub Integration: Manages all GitHub API interactions
- IP Integration: Handles IPv4/IPv6 information retrieval
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

## Local Integration with Desktop LLMs

To add an MCP server to your desktop LLM such as Claude etc.., you need to add this section to the configuration file. The basic structure involves defining a server name and providing the command and any necessary arguments to run the server.

```json
{
  "mcpServers": {
    "github_pr_issues": {
      "command": "uvx",
      "env": {
        "GITHUB_TOKEN": "<your-github-token>"
      },
      "args": [
        "https://github.com/saidsef/mcp-github-pr-issue-analyser.git"
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
