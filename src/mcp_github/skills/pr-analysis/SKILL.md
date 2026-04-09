---
description: Analyse GitHub Pull Requests by fetching metadata and diffs to produce a comprehensive review summary
---

# PR Analysis

Retrieve and synthesise PR metadata and code changes to understand what a pull request does, why, and how.

## Prerequisites

- `repo_owner`, `repo_name`, and `pr_number` for the target PR
- GitHub token with `repo` read access

## Workflow

1. **Fetch PR metadata** — call `get_pr_content` to get title, description, author, state, base/head branches, and labels
2. **Fetch the diff** — call `get_pr_diff` to get the raw unified diff (patch format) of all changed files
3. **Synthesise** — combine metadata and diff to produce a structured analysis

## Tool Parameters

### `get_pr_content`

| Parameter | Type | Description |
|---|---|---|
| `repo_owner` | str | GitHub organisation or username |
| `repo_name` | str | Repository name |
| `pr_number` | int | Pull request number |

Returns: `PRContent` — title, body, author, state, draft status, base/head refs, labels, mergeable state.

### `get_pr_diff`

| Parameter | Type | Description |
|---|---|---|
| `repo_owner` | str | GitHub organisation or username |
| `repo_name` | str | Repository name |
| `pr_number` | int | Pull request number |

Returns: raw patch string (unified diff format). Each file section starts with `diff --git a/... b/...`.

## Analysis Output Structure

When summarising a PR, cover:
- **What**: short summary of the change
- **Why**: inferred from description and commit context
- **Scope**: files changed, lines added/removed
- **Risk areas**: large diffs, security-sensitive files, config changes, dependency updates
- **Missing items**: tests, docs updates, changelog entries

## Best Practices

- Always call `get_pr_content` before `get_pr_diff` — metadata gives context for interpreting the diff
- For large diffs (>500 lines), focus analysis on high-risk files first (auth, config, deps)
- A draft PR (`draft: true`) is not ready for merge review — note this in the analysis
- If `mergeable` is `false` or `null`, flag merge conflicts before proceeding
