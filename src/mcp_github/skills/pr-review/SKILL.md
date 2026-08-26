---
description: Review a GitHub pull request by posting inline code comments and submitting a review decision
---

# PR Review

Post targeted inline comments on specific lines, then submit a review decision of approve, request changes, or comment.

## Prerequisites

- Read the `pr-analysis` skill first and analyse the PR before reviewing it
- `repo_owner`, `repo_name` and `pr_number` for the target PR
- GitHub token with `repo` write access

## Workflow

1. **Analyse the PR** - follow the `pr-analysis` skill to read the diff, metadata and CI status
2. **Post inline comments** - call `add_inline_pr_comment` once per line needing feedback
3. **Post a general comment** - optionally call `add_pr_comments` for remarks not tied to a line
4. **Submit the decision** - call `update_reviews` with `APPROVE`, `REQUEST_CHANGES` or `COMMENT`

## Tool Parameters

### `add_inline_pr_comment`

| Parameter | Type | Description |
|---|---|---|
| `repo_owner` | str | GitHub organisation or username |
| `repo_name` | str | Repository name |
| `pr_number` | int | Pull request number |
| `path` | str | File path relative to repo root, e.g. `src/mcp_github/auth.py` |
| `line` | int | Line number in the **new** file, the right side of the diff |
| `comment_body` | str | Markdown comment text |

Returns `CommentData` with `id`, `body`, `author`, `html_url`, `created_at`.

The line must fall inside the diff hunks of the PR. GitHub rejects a comment on
an unchanged line outside any hunk, and on a path not present in the diff.

### `add_pr_comments`

| Parameter | Type | Description |
|---|---|---|
| `repo_owner` | str | GitHub organisation or username |
| `repo_name` | str | Repository name |
| `pr_number` | int | Pull request number |
| `comment` | str | Markdown comment text for the PR conversation thread |

Returns `CommentData`. This posts to the issue-comment thread, so it is a
standalone comment rather than part of a review.

### `update_reviews`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `pr_number` | int | - | Pull request number |
| `event` | str | - | One of `APPROVE`, `REQUEST_CHANGES`, `COMMENT` |
| `body` | str \| None | `None` | Summary body for the review |

## Review Decision Guide

| Decision | When to use |
|---|---|
| `APPROVE` | Concerns addressed, the change is correct and safe to merge |
| `REQUEST_CHANGES` | Blocking issues: bugs, security problems, missing tests |
| `COMMENT` | Non-blocking feedback or questions, no decision yet |

GitHub rejects `APPROVE` and `REQUEST_CHANGES` on your own PR. Use `COMMENT`
when reviewing a PR you authored.

## Best Practices

- Post every inline comment before calling `update_reviews`
- Use `add_inline_pr_comment` for line-specific feedback and `add_pr_comments` for high-level remarks
- Always pass a `body` to `update_reviews` giving the rationale for the decision
- Check `is_draft` via `list_open_issues_prs` before approving, and never approve a draft
- `REQUEST_CHANGES` blocks the merge on a repo with required reviews until it is dismissed, so reserve it for genuinely blocking problems
- Reference issue numbers where relevant, e.g. `Fixes #42`
