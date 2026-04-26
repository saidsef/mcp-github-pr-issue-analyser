# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MCP server providing GitHub integration for LLMs. Exposes tools for PR analysis, issue management, tags/releases, user search/activity, and IP info. Runs in stdio mode (IDE integration) or HTTP mode (remote access).

## Architecture

```
MCP Client (IDE/LLM)
    |
    | stdio or HTTP (streamable-http)
    v
PRIssueAnalyser (FastMCP) -> src/mcp_github/issues_pr_analyser.py
    |
    | calls
    v
GitHubIntegration -> src/mcp_github/github_integration.py
    |-> REST API (httpx) -> GitHub REST API v3
    |-> GraphQL API (GraphQLClient) -> GitHub GraphQL v4
IPIntegration -> src/mcp_github/ip_integration.py
    |-> REST API (httpx) -> ipinfo.io
```

**Tool Registration**: `_register_tools()` calls `register_tools()` on both `GitHubIntegration` and `IPIntegration` instances. `register_tools()` uses `inspect.getmembers()` to find all public methods and registers each as an MCP tool via `mcp.add_tool()`. **To add a new tool, add a public method with type-annotated parameters to either class** — it auto-registers.

**Python 3.14 compatibility**: Tool registration uses `inspect.isroutine()` (not `ismethod()` or `isfunction()`) because Python 3.14 changed bound method detection — `inspect.ismethod()` returns `False` for methods accessed through class instances.

**ResponseCachingMiddleware removed**: Was causing `tools/list` to fail with "TTL is invalid" error in FastMCP 3.2.4 when `ttl=0` was set. Removed since list operations are not cached anyway.

**FastMCP providers**: `Choice` and `GenerativeUI` from `fastmcp.apps` are added to the server in `__init__`. `fastmcp[apps]>=3.2.4` is in `pyproject.toml`.

**Skills directory**: `src/mcp_github/skills/` contains markdown skill files exposed as MCP resources via `SkillsDirectoryProvider` under the `skill://` URI scheme. Each subdirectory (e.g. `pr-analysis/`, `issue-management/`) contains a `SKILL.md` with workflow guidance for the LLM.

**Exposed MCP Tools** (from `GitHubIntegration`):

- `get_pr_diff` — raw patch/diff from patch-diff.githubusercontent.com
- `get_pr_content` — PR metadata (title, description, author, state)
- `update_pr_description` — PATCH PR body
- `create_pr` — open a new pull request
- `merge_pr` — merge a PR (merge/squash/rebase)
- `add_pr_comments` — add a general comment to a PR
- `add_inline_pr_comment` — add an inline review comment at a specific file/line
- `update_reviews` — submit a PR review (APPROVE/REQUEST_CHANGES/COMMENT)
- `update_assignees` — assign users to a PR or issue
- `create_issue` — create a GitHub issue (auto-adds `mcp` label)
- `update_issue` — update issue title/body/state/labels
- `list_open_issues_prs` — list open issues or PRs with filtering
- `get_latest_sha` — get HEAD SHA of default branch
- `create_tag` — create an annotated git tag
- `create_release` — publish a GitHub release
- `search_user` — user profile via GraphQL
- `get_user_activities` — commit/PR/issue/review contributions via GraphQL

From `IPIntegration`: `get_ipv4_info`, `get_ipv6_info`

**IPv6 socket override**: `IPIntegration.get_ipv6_info()` uses `httpx.HTTPTransport(local_address="::")` to force IPv6 socket family. Do not refactor this into a persistent setting.

**GraphQL Schema Notes** (from recent fixes):

- `CreatedCommitContribution`: has `commitCount`, `url`, `occurredAt` — NOT `commit { message }`
- `CreatedPullRequestReviewContribution`: has `pullRequestReview { state url }` — NOT `review`

## Development Commands

```bash
# Setup (Python >=3.14 required)
uv sync --group dev

# Run (stdio mode)
export GITHUB_TOKEN="<token>"
uvx ./

# Run (HTTP mode — streamable-http transport)
export GITHUB_TOKEN="<token>"
export MCP_ENABLE_REMOTE=true
uvx ./

# Docker build and run (HTTP mode)
docker build -t mcp-github .
docker run -e GITHUB_TOKEN="<token>" -p 8081:8081 mcp-github

# Code quality
ruff check . --fix
ruff format .
uv run pyright src/mcp_github/

# Tests (pytest is configured in pyproject.toml; no tests exist yet)
uv run pytest

# Regenerate requirements.txt after dependency changes (auto-generated — do not edit manually)
uv export --frozen --no-emit-project > requirements.txt

# Test HTTP auth (after starting server in HTTP mode)
curl -H "Authorization: Bearer <GITHUB_TOKEN>" http://localhost:8081/mcp
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GITHUB_TOKEN` | Yes | - | GitHub PAT with `repo` scope; also used as the bearer token in HTTP mode |
| `MCP_ENABLE_REMOTE` | No | unset | Any non-empty string enables HTTP/streamable-http mode |
| `PORT` | No | 8081 | HTTP server port |
| `HOST` | No | `localhost` | HTTP server host |
| `GITHUB_API_TIMEOUT` | No | `5` | Timeout in seconds for both REST and GraphQL requests |
| `GITHUB_OAUTH_CLIENT_ID` | No | unset | GitHub OAuth App client ID — enables OAuth2 auth path via `GitHubProvider` |
| `GITHUB_OAUTH_CLIENT_SECRET` | No | unset | GitHub OAuth App client secret — required alongside `GITHUB_OAUTH_CLIENT_ID` |
| `GITHUB_OAUTH_BASE_URL` | No | unset | Public base URL of the server — required by the OAuth2 redirect flow |
| `FASTMCP_HOME` | No | platformdirs user data dir | Directory for FastMCP state (OAuth client registrations, token store). Set to `/tmp` in the Dockerfile so the read-only K8s filesystem is not a problem. State stored here is ephemeral when backed by an emptyDir; clients re-register automatically after pod restarts. |

**HTTP auth**: In HTTP mode, `APIKeyVerifier` is wired with `GITHUB_TOKEN`. Clients must send `Authorization: Bearer <GITHUB_TOKEN>` — there is no separate API key.

**OAuth2 auth**: If all three `GITHUB_OAUTH_*` vars are set, `_select_auth()` routes to `GitHubProvider` (FastMCP's OAuth proxy) instead of `APIKeyVerifier`. `GitHubProvider` implements the full OAuth 2.1 + Dynamic Client Registration flow: MCP clients register at `/register`, get a UUID `client_id`, then authorize via the consent/GitHub OAuth flow. Client registrations are persisted in `FASTMCP_HOME/oauth-proxy/<key-fingerprint>/`. **If the server restarts and `FASTMCP_HOME` is ephemeral (emptyDir), cached client_ids become stale — instruct users to clear MCP client auth tokens and reconnect.**

**`TokenVerifier` subclass requirement**: Any subclass of `TokenVerifier` (or `AuthProvider`) **must call `super().__init__()`**. The parent sets `base_url`, `required_scopes`, `_mcp_path`, and `_resource_url` — FastMCP accesses these when building the HTTP app and will raise `AttributeError` if they are absent.

## Key Files

- **Entry point**: `src/mcp_github/issues_pr_analyser.py:main()` — also exposed as `mcp-github-pr-issue-analyser` script
- **Auth providers**: `src/mcp_github/auth.py` — `APIKeyVerifier`, `get_oauth_verifier()`, and token resolution
- **GraphQL queries**: `src/mcp_github/graphql_queries.py` — verify fields against GitHub schema before modifying
- **GraphQL client**: `src/mcp_github/graphql_client.py` — thin wrapper used by `github_integration.py` for all v4 API calls
- **Exception hierarchy**: `src/mcp_github/exceptions.py` — `MCPGitHubError` -> `GitHubAPIError` -> `GitHubAuthError` / `GitHubRateLimitError` / `GitHubNotFoundError` / `GitHubValidationError`; also `IPInfoError`
- **Return types**: `github_integration.py` defines TypedDicts as PEP 695 type aliases (`type PRContent = TypedDict(...)`) used as return type annotations
- **Skills**: `src/mcp_github/skills/` — MCP resources exposed under `skill://` URIs; loaded via `SkillsDirectoryProvider` at startup
- **Registry manifest**: `registry/server.yaml` — metadata for publishing to MCP server registries
- **Version**: managed by `setuptools-scm` from git tags; `src/mcp_github/_version.py` is auto-generated — do not edit

## Task Backlog

`tasks/` contains numbered markdown files for planned bug fixes, improvements, and features. Check here before starting new work to avoid duplicating planned effort.

## Testing

No tests currently exist. When adding tests, place them in `tests/` with `test_*.py` naming (configured in `pyproject.toml`).

## CI/CD

GitHub Actions in `.github/workflows/`:

- `ci.yml` — CodeQL, dependency review, Docker build/push to `ghcr.io/saidsef/mcp-github-pr-issue-analyser` (multi-platform: amd64, arm64), Trivy scan
- `tag_release.yml` — Automated semantic versioning and releases on push to main

CI auto-approves PRs after successful build. Do not manually update version numbers in PRs.

`uv.lock` should be committed — it is the authoritative lockfile for `uv sync` and ensures reproducible installs.

## Deployment

Kubernetes manifests in `deployment/` use Kustomize (`kustomization.yml`). The Dockerfile sets `MCP_ENABLE_REMOTE=true` and `PORT=8081` as defaults — the container always runs in HTTP mode.

**K8s read-only filesystem**: The pod spec sets `readOnlyRootFilesystem: true`. `requirements.txt` contains `-e .` (editable install, auto-generated by `uv export`) which would cause `setuptools-scm` to write `_version.py` and `.egg-info/` at runtime. The Dockerfile handles this by stripping `-e .` before installing and then doing a non-editable install:
```dockerfile
grep -v "^-e " requirements.txt > /tmp/requirements.txt && \
uv pip install --system -r /tmp/requirements.txt && \
uv pip install --system --no-deps .
```
Do not change `CMD` back to `uv run` — it re-triggers the editable build at pod startup.

To simulate K8s locally:
```sh
docker build -t mcp-test --build-arg SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0 .
docker run --read-only --tmpfs /tmp -e GITHUB_TOKEN="<token>" -p 8081:8081 mcp-test
```
