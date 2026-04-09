---
description: Review a GitHub PR by posting inline code comments and submitting a formal review decision
---

# PR Review

Post targeted inline comments on specific lines and submit a formal review (approve, request changes, or comment).

## Prerequisites

- Run the `pr-analysis` skill first to understand the PR before reviewing
- `repo_owner`, `repo_name`, `pr_number` for the target PR
- GitHub token with `repo` write access

## Workflow

1. **Analyse the PR** — use the `pr-analysis` skill to read diff and metadata
2. **Post inline comments** — call `add_inline_pr_comment` for each specific line that needs feedback
3. **Post a general comment** (optional) — call `add_pr_comments` for overall remarks not tied to a specific line
4. **Submit the review decision** — call `update_reviews` with APPROVE, REQUEST_CHANGES, or COMMENT

## Tool Parameters

### `add_inline_pr_comment`

| Parameter | Type | Description |
|---|---|---|
| `repo_owner` | str | GitHub organisation or username |
| `repo_name` | str | Repository name |
| `pr_number` | int | Pull request number |
| `path` | str | File path relative to repo root (e.g. `src/app/main.py`) |
| `line` | int | Line number in the **new** file (right side of diff) |
| `comment_body` | str | Markdown comment text |

### `add_pr_comments`

| Parameter | Type | Description |
|---|---|---|
| `repo_owner` | str | GitHub organisation or username |
| `repo_name` | str | Repository name |
| `pr_number` | int | Pull request number |
| `comment` | str | Markdown comment text for the overall PR thread |

### `update_reviews`

| Parameter | Type | Description |
|---|---|---|
| `repo_owner` | str | GitHub organisation or username |
| `repo_name` | str | Repository name |
| `pr_number` | int | Pull request number |
| `event` | str | One of: `APPROVE`, `REQUEST_CHANGES`, `COMMENT` |
| `body` | str (optional) | Summary body for the review |

## Review Decision Guide

| Decision | When to use |
|---|---|
| `APPROVE` | All concerns addressed, code is correct and safe to merge |
| `REQUEST_CHANGES` | Blocking issues found (bugs, security, missing tests) |
| `COMMENT` | Non-blocking feedback or questions, no decision yet |

## Best Practices

- Post all inline comments before calling `update_reviews` — they are grouped under the same review
- Use `add_inline_pr_comment` for specific code feedback; use `add_pr_comments` for high-level remarks
- Always include a `body` in `update_reviews` summarising the rationale for the decision
- Do not APPROVE a draft PR (`draft: true`)
- Reference issue numbers in comments where relevant (e.g. `Fixes #42`)
