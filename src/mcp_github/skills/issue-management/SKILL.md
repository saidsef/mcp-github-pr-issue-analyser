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

## Filtering Guide

| Filter | Returns issues/PRs where you are... |
|---|---|
| `involves` | Involved in any way (author, assignee, mentioned, subscribed) |
| `assigned` | Assigned as the responsible party |
| `created` | The original author |
| `mentioned` | Mentioned by @username |

## Best Practices

- Always search for duplicates with `list_open_issues_prs` before creating a new issue
- Write issue bodies in Markdown; include steps to reproduce for bugs, or acceptance criteria for features
- Use labels consistently — common labels: `bug`, `enhancement`, `documentation`, `good first issue`
- Close issues with `state="closed"` once resolved rather than deleting them
- Reference related PRs in issue bodies (e.g. `Resolved by #123`)
- The `mcp` label is auto-added and identifies issues created via this tool
