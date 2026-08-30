# Client configuration

Every client needs a server name, plus either a command to launch the server over stdio or a URL to reach it over HTTP. Pick the section that matches how you run the server, see [Installation](./installation.md).

| Transport | Auth | Use when |
|-----------|------|----------|
| stdio | `GITHUB_TOKEN` in `env` | Running locally, one user, simplest setup |
| HTTP | `Authorization: Bearer <GITHUB_TOKEN>` header | Shared server, all callers share one GitHub identity |
| HTTP | GitHub OAuth2 | Shared server, each user acts as themselves |

In OAuth2 mode the client discovers the auth server, registers itself and opens a browser for consent. There is no token in the config file.

## Where the config goes

| Client | Path | Format |
|--------|------|--------|
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows | JSON, `mcpServers` |
| Claude Code | `~/.claude.json`, or `.mcp.json` in the project root | JSON, `mcpServers` |
| Cursor | `~/.cursor/mcp.json`, or `.cursor/mcp.json` in the project root | JSON, `mcpServers` |
| Codex | `~/.codex/config.toml`, or `.codex/config.toml` in the project root | TOML, `mcp_servers` |
| VS Code | `.vscode/mcp.json` for a workspace, or user settings under `mcp.servers` | JSON, `servers` |

## Claude Desktop, Claude Code and Cursor

These three share the `mcpServers` schema, so one config works for all of them.

<details>
<summary>stdio with a personal access token</summary>

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
</details>

<details>
<summary>Remote HTTP with a static token</summary>

```json
{
  "mcpServers": {
    "github_prs_issues": {
      "type": "http",
      "url": "https://mcp.example.com/mcp",
      "headers": {
        "Authorization": "Bearer <your-github-token>"
      }
    }
  }
}
```
</details>

<details>
<summary>Remote HTTP with GitHub OAuth2</summary>

```json
{
  "mcpServers": {
    "github_prs_issues": {
      "type": "http",
      "url": "https://mcp.example.com/mcp"
    }
  }
}
```

No token goes in the file. On first use the client registers itself with the server and opens GitHub's consent screen. Approve the `repo`, `read:org` and `user` scopes.
</details>

## Claude Code CLI

```sh
# stdio with a personal access token
claude mcp add github_prs_issues \
  --env GITHUB_TOKEN=<your-github-token> \
  -- uvx https://github.com/saidsef/mcp-github-pr-issue-analyser.git

# remote HTTP with a static token
claude mcp add --transport http github_prs_issues https://mcp.example.com/mcp \
  --header "Authorization: Bearer <your-github-token>"

# remote HTTP with GitHub OAuth2
claude mcp add --transport http github_prs_issues https://mcp.example.com/mcp
```

Add `--scope project` to write the entry to `.mcp.json` and share it with the repository. Run `/mcp` inside Claude Code to check the connection and to trigger the OAuth flow.

## Codex CLI

Codex uses TOML rather than the `mcpServers` JSON schema, so its entries look nothing like the ones above.

<details>
<summary>stdio with a personal access token</summary>

```toml
[mcp_servers.github_prs_issues]
command = "uvx"
args = ["https://github.com/saidsef/mcp-github-pr-issue-analyser.git"]

[mcp_servers.github_prs_issues.env]
GITHUB_TOKEN = "<your-github-token>"
```

The same entry from the CLI:

```sh
codex mcp add github_prs_issues \
  --env GITHUB_TOKEN=<your-github-token> \
  -- uvx https://github.com/saidsef/mcp-github-pr-issue-analyser.git
```
</details>

<details>
<summary>Remote HTTP with a static token</summary>

```toml
[mcp_servers.github_prs_issues]
url = "https://mcp.example.com/mcp"
bearer_token_env_var = "GITHUB_TOKEN"
```

`bearer_token_env_var` names the variable Codex reads the token from, so the token itself stays out of the config file. Export `GITHUB_TOKEN` before starting Codex.
</details>

<details>
<summary>Remote HTTP with GitHub OAuth2</summary>

```toml
[mcp_servers.github_prs_issues]
url = "https://mcp.example.com/mcp"
```

Then authenticate:

```sh
codex mcp login github_prs_issues
```

Codex registers itself through dynamic client registration and opens GitHub's consent screen. Approve the `repo`, `read:org` and `user` scopes.
</details>

## VS Code

VS Code nests the servers under `servers` rather than `mcpServers`, and can prompt for the token instead of storing it, via an `inputs` entry.

<details>
<summary>stdio with a personal access token</summary>

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
        "https://github.com/saidsef/mcp-github-pr-issue-analyser.git"
      ],
      "env": {
        "GITHUB_TOKEN": "${input:github-token}"
      }
    }
  }
}
```
</details>

<details>
<summary>Remote HTTP with a static token</summary>

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
      "type": "http",
      "url": "https://mcp.example.com/mcp",
      "headers": {
        "Authorization": "Bearer ${input:github-token}"
      }
    }
  }
}
```
</details>

<details>
<summary>Remote HTTP with GitHub OAuth2</summary>

```json
{
  "servers": {
    "github-prs-issues": {
      "type": "http",
      "url": "https://mcp.example.com/mcp"
    }
  }
}
```
</details>

## Verifying the connection

```sh
# static token
curl -s -H "Authorization: Bearer <your-github-token>" \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
  https://mcp.example.com/mcp

# OAuth2: the discovery document should list the authorisation endpoints
curl -s https://mcp.example.com/.well-known/oauth-authorization-server
```

An unauthenticated request to `/mcp` returns `401` with a `WWW-Authenticate` header in both HTTP modes. `/metrics` stays unauthenticated so Prometheus can scrape it.

## Common problems

| Symptom | Cause |
|---------|-------|
| `401` on every call in static-token mode | The bearer token does not match the server's `GITHUB_TOKEN` exactly |
| OAuth redirect rejected by GitHub | The OAuth App callback URL is not `<GITHUB_OAUTH_BASE_URL>/auth/callback` |
| Client re-authenticates after every restart | Token state is in memory. Set `DYNAMODB_TABLE_NAME` or `REDIS_HOST_PORT` |
| Server starts in stdio mode when HTTP was wanted | `MCP_ENABLE_REMOTE` is unset, or set to something other than `true`, `1`, `yes` or `on` |
| Connection refused from another host | `HOST` defaults to `localhost`. Bind to `0.0.0.0` or the pod IP |
