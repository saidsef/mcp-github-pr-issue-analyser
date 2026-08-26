---
description: Manage the lifecycle of a GitHub PR - create, update the description, assign, refresh the branch and merge
---

# PR Management

Open pull requests, keep them current, and merge them once they are ready.

## Prerequisites

- `repo_owner` and `repo_name` for the target repository
- The `head` branch must already be pushed before creating a PR
- GitHub token with `repo` write access

## Workflow

### Opening a PR

1. Call `create_pr` with title, body, head branch and base branch
2. Pass `draft=True` while the work is still in progress

### Updating a PR

1. Call `update_pr_description` to revise the title and body. Both are required and replace the existing values
2. Call `update_assignees` to set the assignees
3. Call `update_pr_branch` when the base branch has moved on and the PR needs the latest upstream commits

### Merging a PR

1. Call `get_pr_status_checks` and require `overall == "passing"`, per the `pr-analysis` skill
2. Confirm the review decision is an approval
3. **Ask the user in chat and get an explicit yes before calling `merge_pr`. The tool does not prompt and the merge cannot be undone**
4. Call `merge_pr` with an explicit `commit_title`

## Tool Parameters

### `create_pr`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `title` | str | - | PR title, see Title Convention |
| `body` | str | - | PR description in Markdown |
| `head` | str | - | Source branch name |
| `base` | str | - | Target branch name, e.g. `main` |
| `draft` | bool | `False` | Open as a draft PR |

### `update_pr_description`

| Parameter | Type | Description |
|---|---|---|
| `repo_owner` | str | GitHub organisation or username |
| `repo_name` | str | Repository name |
| `pr_number` | int | Pull request number |
| `new_title` | str | Replacement title, required |
| `new_description` | str | Replacement body in Markdown, required |

Returns `PRContent`. Both fields are sent on every call, so pass the current
value for whichever one you are not changing or it will be overwritten. Fetch
the current values with `get_pr_content` first.

### `update_assignees`

| Parameter | Type | Description |
|---|---|---|
| `repo_owner` | str | GitHub organisation or username |
| `repo_name` | str | Repository name |
| `issue_number` | int | PR or issue number, PRs are issues for this endpoint |
| `assignees` | list[str] | GitHub usernames to assign |

### `update_pr_branch`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `pr_number` | int | - | Pull request number |
| `expected_head_sha` | str \| None | `None` | Fail unless the head still matches this SHA |

Merges the base branch into the head branch, adding a merge commit to the PR
branch. Pass `expected_head_sha` to avoid racing a push from someone else.
This does not resolve conflicts, and a conflicting update fails and the branch must
be fixed locally.

### `merge_pr`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `pr_number` | int | - | Pull request number |
| `commit_title` | str \| None | `None` | Merge commit title, GitHub generates one if omitted |
| `commit_message` | str \| None | `None` | Merge commit body |
| `merge_method` | str | `squash` | One of `merge`, `squash`, `rebase` |

## Title Convention

PR titles and commit subjects share one shape with issue titles:

```
<type>(<scope>): <short prose summary>
```

| Part | Rules |
|---|---|
| `<type>` | One of `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `perf`, `ci`, `build` |
| `<scope>` | Lowercase area touched: `auth`, `tools`, `deps`, `cache`, `skills`, `readme`, `k8s`. Use `/` for a compound scope such as `docker/k8s` |
| Summary | Prose, not a slug. Lowercase start, imperative mood, no trailing full stop, roughly 72 characters or fewer |

This governs `title` in `create_pr`, `new_title` in `update_pr_description` and
`commit_title` in `merge_pr`. Always set `commit_title` explicitly when
squashing, otherwise the subject landing on the default branch inherits a
branch commit. Append the PR reference, e.g.
`feat(auth): support GitHub App tokens (#123)`.

Examples:

- `fix(tools): handle empty diff response from the compare endpoint`
- `feat(cache): add Redis-backed PR diff cache`
- `chore(deps): bump fastmcp-slim to 3.4.7, refresh lock`

Avoid bare titles such as `Update README`, bracketed prefixes such as
`[WIP] cache work`, a kebab-case slug where prose belongs such as
`fix(tools): empty-diff-handling`, and a type with no scope such as
`fix: empty diff`.

## Merge Method Guide

| Method | When to use |
|---|---|
| `squash` | Feature branches, keeps the default branch history readable. The default |
| `merge` | When the individual branch commits are worth preserving |
| `rebase` | Linear history with no merge commit. Fails if the branch does not replay cleanly |

## Best Practices

- Title every PR as `<type>(<scope>): <prose summary>`, see Title Convention above
- Pass `commit_title` in the same form when merging so the branch history stays parseable
- Write PR bodies in Markdown with a summary, the motivation, and how it was tested
- Gate the merge on `get_pr_status_checks` returning `overall="passing"`. `unknown` is not a pass
- Use `draft=True` for work in progress, since a draft cannot be merged
- Read the current title and body with `get_pr_content` before `update_pr_description`, since both fields are replaced
- Delete the head branch after merging
