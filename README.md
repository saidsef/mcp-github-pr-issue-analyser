# MCP for GitHub PR, Issues, Tags and Releases

[![CI](https://github.com/saidsef/mcp-github-pr-issue-analyser/actions/workflows/ci.yml/badge.svg)](https://github.com/saidsef/mcp-github-pr-issue-analyser/actions/workflows/ci.yml) [![Tag and Release](https://github.com/saidsef/mcp-github-pr-issue-analyser/actions/workflows/tag_release.yml/badge.svg)](https://github.com/saidsef/mcp-github-pr-issue-analyser/actions/workflows/tag_release.yml) [![Maintainability](https://qlty.sh/gh/saidsef/projects/mcp-github-pr-issue-analyser/maintainability.svg)](https://qlty.sh/gh/saidsef/projects/mcp-github-pr-issue-analyser) [![Codacy Badge](https://app.codacy.com/project/badge/Grade/9ca2ee03cbfa4407944a2450b1719d5d)](https://app.codacy.com/gh/saidsef/mcp-github-pr-issue-analyser/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_grade)

The [Model Context Protocol](https://www.anthropic.com/news/model-context-protocol) (MCP) is an open standard that enables seamless integration between Large Language Models (LLMs) and external tools. Whilst it can be implemented in any AI system, including custom LLM setups, the degree of integration and optimisation varies based on the model's architecture and capabilities.

This MCP application serves as a bridge between LLMs and GitHub's repository management features, offering automated analysis of pull requests and comprehensive issue management. It provides a robust set of tools to fetch PR details, create issues, and update issues directly from your desktop LLM. The application is designed with modularity in mind, supporting extensibility via the MCP tool interface and seamless integration with existing workflows.

The toolset enables automated PR analysis, issue tracking, tagging and release management through a standardised MCP interface, making it ideal for teams seeking to streamline their GitHub workflow automation.

## Features

| Function                                 | Description                                                                                       |
|------------------------------------------|---------------------------------------------------------------------------------------------------|
| Analyse GitHub Pull Requests and fetch diffs         | Retrieve the diff/patch for any PR in a repository.                                                |
| Fetch content and metadata for specific PRs          | Get PR title, description, author, timestamps, and state.                                          |
| Fetch linked issues for a PR                         | Get the issues that will auto-close when a PR is merged, via GraphQL `closingIssuesReferences`.    |
| Fetch CI status checks for a PR                      | Get check run conclusions and legacy commit status for a PR's HEAD commit.                         |
| Create Pull Requests                                 | Open new PRs with title, body, head/base branch, and draft option.                                 |
| Update PR title and description                      | Change the title and body of any PR.                                                               |
| Merge Pull Requests                                  | Merge a PR using merge, squash, or rebase method.                                                  |
| Add comments to PRs                                  | Post general comments to a PR thread.                                                              |
| Add inline review comments to PRs                    | Comment on specific lines in PR files for code review.                                             |
| Submit PR Reviews                                    | Approve, request changes, or comment on a PR review.                                               |
| Update PR Assignees                                  | Assign or update users on a PR or issue.                                                           |
| Create and update GitHub Issues                      | Open new issues or update existing ones with title, body, labels, and state.                       |
| List all open Issues or Pull Requests                | View all open PRs or issues for any user or organisation.                                          |
| Create tags and releases                             | Tag repository commits and publish releases with changelogs.                                       |
| Search GitHub Users                                  | Retrieve user profile information via GraphQL.                                                     |
| Get User Activity                                    | Fetch commit, PR, issue, and review contributions with org/repo/date filtering.                    |

## Requirements

- Python 3.12+
- GitHub Personal Access Token (with `repo` scope) **or** a GitHub OAuth App (client ID, secret, and a public base URL)

## Authentication

Two auth modes are supported. The active mode is selected automatically from environment variables.

| Mode | When active | Token used for API calls |
|------|-------------|--------------------------|
| **Static token** (default) | `GITHUB_TOKEN` set; no `GITHUB_OAUTH_*` vars | Server's `GITHUB_TOKEN` for all calls |
| **GitHub OAuth2** | `GITHUB_TOKEN` + all three `GITHUB_OAUTH_*` vars set | Each user's own `gho_*` token |

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GITHUB_TOKEN` | Yes | GitHub PAT with `repo` scope; used as the Bearer token in static-token HTTP mode |
| `MCP_ENABLE_REMOTE` | No | Any non-empty value enables HTTP mode (required for OAuth2) |
| `GITHUB_OAUTH_CLIENT_ID` | OAuth2 only | GitHub OAuth App client ID |
| `GITHUB_OAUTH_CLIENT_SECRET` | OAuth2 only | GitHub OAuth App client secret |
| `GITHUB_OAUTH_BASE_URL` | OAuth2 only | Public base URL of the MCP server (used for the OAuth2 redirect) |
| `REDIS_HOST_PORT` | No | Redis connection string. Accepts `host:port` or a full URI: `redis://[:password@]host:port[/db]` (plaintext) or `rediss://[:password@]host:port[/db]` (TLS). When set, OAuth token state is stored in Redis instead of in-process memory. |
| `REDIS_PASSWORD` | No | Redis AUTH password fallback — used when the password is not embedded in the URI. |
| `PORT` | No (default `8081`) | HTTP server port |
| `HOST` | No (default `localhost`) | HTTP server host |
| `GITHUB_API_TIMEOUT` | No (default `5`) | Timeout in seconds for GitHub API requests |

> To create a GitHub OAuth App, go to **Settings → Developer settings → OAuth Apps → New OAuth App** and set the Authorization callback URL to `<GITHUB_OAUTH_BASE_URL>/auth/callback` (e.g. `https://mcp.example.com/auth/callback`).

## Architecture Diagram

```ascii
                              +------------------------+
                              |                        |
                              |    MCP Client/User     |
                              |                        |
                              +------------------------+
                                         |
                                         | (stdio/http)
                                         v
                              +------------------------+
                              |      Auth Layer        +-->+------------------------+
                              |   (auth.py)            |   | OAuth Token Store      |
                              |                        |   | MemoryStore (default)  |
                              | stdio : no auth        |   | RedisStore             |
                              | http  : APIKeyVerifier |   |   (REDIS_HOST_PORT set)|
                              | oauth : GitHub OAuth2  |   |   redis:// / rediss:// |
                              |   (DCR + token proxy)  |   +------------------------+
                              +------------------------+             |
                                         |                           v
                                         |                     +----------+
                                         |                     |  Redis   |
                                         |                     +----------+
                                         v
                              +------------------------+
                              |    PRIssueAnalyser     |
                              |    (FastMCP Server)    |
                              +------------------------+
                                         |
                                         | (API calls)
                                         v
                              +------------------------+
                              |   GitHub Integration   |
                              +------------------------+
                                         |
                     +-------------------+-------------------+
                     | (REST API)                            | (GraphQL API)
                     v                                       v
   +---------------------------------------+   +-----------------------------+
   |                                       |   |                             |
   | PRs (diff, content, status, linked    |   | User Search & Activity      |
   | issues, reviews, comments, merge)     |   | (contributions, profile)    |
   |                                       |   |                             |
   | Issues (create, update, list, assign) |   | PR Linked Issues            |
   |                                       |   | PR Status Checks            |
   | Tags and Releases                     |   |                             |
   +---------------------------------------+   +-----------------------------+
```

### Tool Categories

1. PR Management: Fetch diffs, content, linked issues, CI status - create, review, merge, and update
2. Issue Tracking: Create, update, list, and assign
3. Release Management: Tags and releases
4. User Search: Profile lookup and activity tracking via GraphQL

### Main Flows

- MCP Client: Interacts via stdio or streamable HTTP
- Auth Layer: Selects APIKeyVerifier (static token) or GitHub OAuth2 provider; token state in MemoryStore or RedisStore
- PRIssueAnalyser: FastMCP server - handles tool registration and request routing
- GitHub Integration: All GitHub API calls (REST v3 + GraphQL v4)

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

Alternatively, launch MCP in `http` mode.
```sh
export GITHUB_TOKEN="<github-token>"
export MCP_ENABLE_REMOTE=true
uvx ./
```
> You can access it via `http` i.e. `http(s)://localhost:8081/mcp`
> In HTTP mode, clients must authenticate with `Authorization: Bearer <GITHUB_TOKEN>`.

Alternatively, launch MCP in `http` mode with GitHub OAuth2 authentication.
```sh
export GITHUB_TOKEN="<github-token>"
export MCP_ENABLE_REMOTE=true
export GITHUB_OAUTH_CLIENT_ID="<oauth-app-client-id>"
export GITHUB_OAUTH_CLIENT_SECRET="<oauth-app-client-secret>"
export GITHUB_OAUTH_BASE_URL="https://<your-public-host>"
uvx ./
```
> In OAuth2 mode, users authenticate via GitHub's OAuth flow. Each user's own GitHub token is used for API calls.

Alternatively, run via Docker using the published image.
```sh
docker run -e GITHUB_TOKEN="<github-token>" \
  -p 8081:8081 \
  ghcr.io/saidsef/mcp-github-pr-issue-analyser:latest
```

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

## Source

Our latest and greatest source of *mcp-github-pr-issue-analyser* can be found on [GitHub]. [Fork us](https://github.com/saidsef/mcp-github-pr-issue-analyser/fork)!

## Contributing

We would :heart: you to contribute by making a [pull request](https://github.com/saidsef/mcp-github-pr-issue-analyser/pulls).

Please read the official [Contribution Guide](./CONTRIBUTING.md) for more information on how you can contribute.
