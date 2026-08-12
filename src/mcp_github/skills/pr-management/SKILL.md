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

1. Confirm the PR is approved and all checks pass (check status via `get_pr_status_checks` and `get_pr_content`)
2. Call `merge_pr` with the appropriate merge method
3. **Always confirm with the user in chat before calling `merge_pr` — the tool does not prompt**

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

## Title Convention

PR titles and commit subjects share one shape with issue titles:

```
<type>(<scope>): <short prose summary>
```

| Part | Rules |
|---|---|
| `<type>` | One of `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `perf`, `ci`, `build` |
| `<scope>` | Lowercase area the change touches -- `auth`, `tools`, `deps`, `cache`, `skills`, `readme`, `k8s`. Use `/` for a compound scope (`docker/k8s`) |
| Summary | Prose, not a slug. Lowercase start, imperative mood, no trailing full stop, roughly 72 characters or fewer |

This applies to `title` in `create_pr`, `new_title` in `update_pr_description`, and
`commit_title` in `merge_pr`. When squashing, set `commit_title` explicitly so the
subject landing on the default branch keeps this form rather than inheriting a
branch commit -- append the PR reference, e.g. `feat(auth): support GitHub App tokens (#123)`.

Examples:

- `fix(tools): handle empty diff response from the compare endpoint`
- `feat(cache): add Redis-backed PR diff cache`
- `chore(deps): bump fastmcp-slim to 3.4.7, refresh lock`

Avoid:

- Bare titles -- `Update README`
- Bracketed prefixes -- `[WIP] cache work`
- A kebab-case slug where prose belongs -- `fix(tools): empty-diff-handling`
- A type with no scope -- `fix: empty diff`

## Merge Method Guide

| Method | When to use |
|---|---|
| `squash` | Feature branches — keeps main history clean (default) |
| `merge` | When preserving full branch commit history is important |
| `rebase` | Linear history without a merge commit |

## Best Practices

- Title every PR as `<type>(<scope>): <prose summary>` -- see Title Convention above
- Pass an explicit `commit_title` in the same form when merging, so the branch history stays parseable
- Write PR bodies in Markdown; include a summary, motivation, and testing steps
- Always verify `mergeable` status before calling `merge_pr`
- Use `draft=True` for work-in-progress PRs to prevent premature merges
- Do not force-merge PRs with failing CI checks
- After merging, delete the head branch to keep the repo clean
