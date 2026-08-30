# MCP for GitHub PR, Issues, Tags and Releases

[![CI](https://github.com/saidsef/mcp-github-pr-issue-analyser/actions/workflows/ci.yml/badge.svg)](https://github.com/saidsef/mcp-github-pr-issue-analyser/actions/workflows/ci.yml) [![Tag and Release](https://github.com/saidsef/mcp-github-pr-issue-analyser/actions/workflows/tag_release.yml/badge.svg)](https://github.com/saidsef/mcp-github-pr-issue-analyser/actions/workflows/tag_release.yml) [![Maintainability](https://qlty.sh/gh/saidsef/projects/mcp-github-pr-issue-analyser/maintainability.svg)](https://qlty.sh/gh/saidsef/projects/mcp-github-pr-issue-analyser) [![Codacy Badge](https://app.codacy.com/project/badge/Grade/9ca2ee03cbfa4407944a2450b1719d5d)](https://app.codacy.com/gh/saidsef/mcp-github-pr-issue-analyser/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_grade)

An [MCP](https://www.anthropic.com/news/model-context-protocol) server that connects an LLM to GitHub's repository management features. It analyses pull requests, manages issues, and handles tags and releases, over stdio or HTTP, with a static token or GitHub OAuth2.

- **Pull requests** - fetch diffs, content, linked issues and CI status, create, comment, review, merge and update, close, retarget and flip draft status, read and correct posted comments
- **Issues** - create, update, list, search and assign, read a repository's labels, run milestones and file issues under them
- **Releases** - tag commits, publish releases, and list, correct or withdraw what is published
- **Users** - profile lookup, contribution activity and star growth via GraphQL
- **Repositories** - list what a user, an organisation or the caller owns, without knowing the names

The full tool list is in [docs/tools.md](./docs/tools.md).

## Quick start

```sh
export GITHUB_TOKEN="<github-token>"
uvx https://github.com/saidsef/mcp-github-pr-issue-analyser.git
```

Then add it to your client:

```json
{
  "mcpServers": {
    "github_prs_issues": {
      "command": "uvx",
      "args": [
        "https://github.com/saidsef/mcp-github-pr-issue-analyser.git"
      ],
      "env": {
        "GITHUB_TOKEN": "<your-github-token>"
      }
    }
  }
}
```

For HTTP mode, Docker, Kubernetes and OAuth2, see the documentation below.

## Documentation

The pages below are also published on [Read the Docs](https://mcp-github-pr-issue-analyser.readthedocs.io/en/latest/architecture/).

| Page | Contents |
|------|----------|
| [Installation](./docs/installation.md) | Requirements, running from source, Docker, Kubernetes |
| [Configuration](./docs/configuration.md) | Auth modes, environment variables, OAuth App setup, token stores |
| [Client configuration](./docs/mcp-clients.md) | Ready-to-paste configs for token and OAuth2, per client |
| [Tools](./docs/tools.md) | Every tool the server registers, and the skills that drive them |
| [Architecture](./docs/architecture.md) | Request path from client to GitHub API |
| [Metrics](./docs/metrics.md) | Prometheus endpoint, metric names, scrape setup |

## Requirements

Python 3.12+, and a GitHub personal access token with `repo` scope or a GitHub OAuth App.

## Source

Our latest and greatest source of *mcp-github-pr-issue-analyser* can be found on [GitHub](https://github.com/saidsef/mcp-github-pr-issue-analyser). [Fork us](https://github.com/saidsef/mcp-github-pr-issue-analyser/fork)!

## Contributing

We would :heart: you to contribute by making a [pull request](https://github.com/saidsef/mcp-github-pr-issue-analyser/pulls).

Please read the official [Contribution Guide](./CONTRIBUTING.md) for more information on how you can contribute.
