---
description: Manage the full lifecycle of a GitHub PR — create, update description, assign reviewers, and merge
---

# PR Management

Create new pull requests, keep them up to date, and safely merge them when ready.

## Prerequisites

- `repo_owner` and `repo_name` for the target repository
- Branches must exist before creating a PR (`head` branch must be pushed)
- GitHub token with `repo` write access

## Workflow

### Opening a PR
1. Call `create_pr` with title, body, head branch, and base branch
2. Optionally mark as `draft=True` if not ready for review

### Updating a PR
1. Call `update_pr_description` to revise the title or body
2. Call `update_assignees` to assign or reassign users

### Merging a PR
1. Confirm the PR is approved and all checks pass (check `mergeable` via `get_pr_content`)
2. Call `merge_pr` with the appropriate merge method
3. **Always confirm with the user before merging**

## Tool Parameters

### `create_pr`
| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | — | GitHub organisation or username |
| `repo_name` | str | — | Repository name |
| `title` | str | — | PR title |
| `body` | str | — | PR description (Markdown) |
| `head` | str | — | Source branch name |
| `base` | str | — | Target branch name (e.g. `main`) |
| `draft` | bool | `False` | Open as draft PR |

### `update_pr_description`
| Parameter | Type | Description |
|---|---|---|
| `repo_owner` | str | GitHub organisation or username |
| `repo_name` | str | Repository name |
| `pr_number` | int | Pull request number |
| `new_title` | str | Updated PR title |
| `new_description` | str | Updated PR body (Markdown) |

### `update_assignees`
| Parameter | Type | Description |
|---|---|---|
| `repo_owner` | str | GitHub organisation or username |
| `repo_name` | str | Repository name |
| `issue_number` | int | PR or issue number |
| `assignees` | list[str] | GitHub usernames to assign |

### `merge_pr`
| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | — | GitHub organisation or username |
| `repo_name` | str | — | Repository name |
| `pr_number` | int | — | Pull request number |
| `commit_title` | str | optional | Custom merge commit title |
| `commit_message` | str | optional | Custom merge commit message |
| `merge_method` | str | `squash` | One of: `merge`, `squash`, `rebase` |

## Merge Method Guide

| Method | When to use |
|---|---|
| `squash` | Feature branches — keeps main history clean (default) |
| `merge` | When preserving full branch commit history is important |
| `rebase` | Linear history without a merge commit |

## Best Practices

- Write PR bodies in Markdown; include a summary, motivation, and testing steps
- Always verify `mergeable` status before calling `merge_pr`
- Use `draft=True` for work-in-progress PRs to prevent premature merges
- Do not force-merge PRs with failing CI checks
- After merging, delete the head branch to keep the repo clean
