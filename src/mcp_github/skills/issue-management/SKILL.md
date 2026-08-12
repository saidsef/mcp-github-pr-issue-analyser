---
description: Create, update, and list GitHub issues with proper labels, state management, and duplicate checking
---

# Issue Management

Create new issues, update existing ones, and list open issues or PRs with filtering.

## Prerequisites

- `repo_owner` and `repo_name` for the target repository
- GitHub token with `repo` write access

## Workflow

### Creating an Issue

1. Call `list_open_issues_prs` to check for existing duplicates before creating
2. Call `create_issue` with title, body, and any additional labels
   - Note: the `mcp` label is automatically added to every created issue

### Updating an Issue

1. Call `update_issue` to change the title, body, state, or labels
2. Use `state="closed"` to close a resolved issue

### Listing Issues/PRs

1. Call `list_open_issues_prs` with filtering options to search for relevant items

## Tool Parameters

### `create_issue`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | — | GitHub organisation or username |
| `repo_name` | str | — | Repository name |
| `title` | str | — | Issue title |
| `body` | str | — | Issue description (Markdown) |
| `labels` | list[str] | `[]` | Additional labels (the `mcp` label is always added) |

### `update_issue`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | — | GitHub organisation or username |
| `repo_name` | str | — | Repository name |
| `issue_number` | int | — | Issue number |
| `title` | str | — | Updated title |
| `body` | str | — | Updated body (Markdown) |
| `labels` | list[str] | `[]` | Replacement label set |
| `state` | str | `open` | `open` or `closed` |

### `list_open_issues_prs`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | — | GitHub organisation or username |
| `issue` | str | `pr` | `pr` for pull requests, `issue` for issues |
| `filtering` | str | `involves` | GitHub filter: `involves`, `assigned`, `created`, `mentioned` |
| `per_page` | int | `50` | Results per page (max 100) |
| `page` | int | `1` | Page number |

## Title Convention

Every issue title uses the same shape as PR titles and commit subjects:

```
<type>(<scope>): <short prose summary>
```

| Part | Rules |
|---|---|
| `<type>` | One of `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `perf`, `ci`, `build` |
| `<scope>` | Lowercase area the change touches -- `auth`, `tools`, `deps`, `cache`, `skills`, `readme`, `k8s`. Use `/` for a compound scope (`docker/k8s`) |
| Summary | Prose, not a slug. Lowercase start, imperative mood, no trailing full stop, roughly 72 characters or fewer |

Examples:

- `fix(cache): redis client leaks connections after reconnect`
- `feat(auth): support GitHub App installation tokens`
- `chore(deps): bump fastmcp-slim to 3.4.7`
- `docs(readme): document the skill:// resource URIs`

Avoid:

- Bare titles -- `Update README`
- Bracketed prefixes -- `[BUG] cache broken`
- A kebab-case slug where prose belongs -- `fix(cache): redis-connection-leak`
- A type with no scope -- `fix: cache broken`

## Filtering Guide

| Filter | Returns issues/PRs where you are... |
|---|---|
| `involves` | Involved in any way (author, assignee, mentioned, subscribed) |
| `assigned` | Assigned as the responsible party |
| `created` | The original author |
| `mentioned` | Mentioned by @username |

## Best Practices

- Always search for duplicates with `list_open_issues_prs` before creating a new issue
- Title every issue as `<type>(<scope>): <prose summary>` -- see Title Convention above
- Keep the type honest: `fix` for defects, `feat` for new behaviour, `chore` for maintenance
- Write issue bodies in Markdown; include steps to reproduce for bugs, or acceptance criteria for features
- Use labels consistently — common labels: `bug`, `enhancement`, `documentation`, `good first issue`
- Close issues with `state="closed"` once resolved rather than deleting them
- Reference related PRs in issue bodies (e.g. `Resolved by #123`)
- The `mcp` label is auto-added and identifies issues created via this tool
