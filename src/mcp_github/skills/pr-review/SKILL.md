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
2. **Read what is already there** - call `list_pr_comments` with `kind="inline"` so a second review does not repeat the first
3. **Post inline comments** - call `add_inline_pr_comment` once per line needing feedback
4. **Post a general comment** - optionally call `add_pr_comments` for remarks not tied to a line
5. **Submit the decision** - call `update_reviews` with `APPROVE`, `REQUEST_CHANGES` or `COMMENT`

### Correcting a comment

1. Call `list_pr_comments` to find the comment and its `id`
2. Call `update_pr_comment` with that `id` and the same `kind` the listing used
3. Reply on a thread with `reply_to_review_comment` rather than opening a new one

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

### `list_pr_comments`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `pr_number` | int | - | Pull request number |
| `kind` | str | `conversation` | `conversation` for the PR thread, `inline` for review comments on lines |
| `per_page` | int | `50` | Results per page, 1 to 100 |
| `page` | int | `1` | Page number |

Returns `total`, `kind` and `comments`. Inline comments carry `path`, `line` and
`in_reply_to_id` on top of the usual `CommentData` fields, which is what lets a
review tell whether it has already spoken about a line.

### `update_pr_comment`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `comment_id` | int | - | The comment's own id, from `list_pr_comments`, not the PR number |
| `body` | str | - | Replacement Markdown text |
| `kind` | str | `conversation` | Which id space the `comment_id` came from |

Conversation and review comments are numbered separately, so a `kind` that does
not match where the id came from either fails or edits the wrong comment. Take
both from the same listing.

### `reply_to_review_comment`

| Parameter | Type | Description |
|---|---|---|
| `repo_owner` | str | GitHub organisation or username |
| `repo_name` | str | Repository name |
| `pr_number` | int | Pull request number |
| `comment_id` | int | The review comment being replied to, which picks the thread |
| `body` | str | Markdown reply text |

Only review comments have threads. A reply to a conversation comment is just
another `add_pr_comments` call.

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

- List the existing inline comments before reviewing again, otherwise the same remarks land twice
- Post every inline comment before calling `update_reviews`
- Use `add_inline_pr_comment` for line-specific feedback and `add_pr_comments` for high-level remarks
- Fix a wrong comment with `update_pr_comment` rather than posting a correction underneath it
- Always pass a `body` to `update_reviews` giving the rationale for the decision
- Check `is_draft` via `list_open_issues_prs` before approving, and never approve a draft
- `REQUEST_CHANGES` blocks the merge on a repo with required reviews until it is dismissed, so reserve it for genuinely blocking problems
- Reference issue numbers where relevant, e.g. `Fixes #42`
