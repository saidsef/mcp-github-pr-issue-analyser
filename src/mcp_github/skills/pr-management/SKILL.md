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

1. Call `github_list_repo_labels` to see the label names the repository defines
2. Write the title to the Title Convention and the body to the PR Body template below
3. Call `github_create_pr` with that title, body, head branch, base branch and labels
4. Call `github_set_assignees` with whoever owns the work
5. Pass `draft=True` while the work is still in progress
6. The `mcp` label is appended automatically, so do not pass it yourself

### Updating a PR

1. Call `github_update_pr` to change any subset of the title, body, state, base branch and labels. This is the one to reach for, since it leaves the fields you omit alone
2. Call `github_set_pr_draft` to mark a draft ready for review, or to put a PR back into draft
3. Call `github_set_assignees` to set the assignees
4. Call `github_update_pr_branch` when the base branch has moved on and the PR needs the latest upstream commits

### Closing a PR

1. Call `github_update_pr` with `state="closed"`. Reopen with `state="open"`
2. Closing is not merging. A closed PR keeps its branch and its comments

### Merging a PR

1. Confirm the review decision is an approval
2. **Ask the user in chat and get an explicit yes before calling `github_merge_pr`. The tool does not prompt and the merge cannot be undone**
3. Call `github_merge_pr` with a `commit_title` in the Title Convention. The tool reads the checks itself and refuses unless `overall` is `passing`, so a separate `github_get_pr_status_checks` call is not needed
4. Pass `force=True` only when the user has accepted merging over checks that are failing, pending or absent

## Tool Parameters

### `github_create_pr`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `title` | str | - | PR title, see Title Convention |
| `body` | str | - | PR description in Markdown |
| `head` | str | - | Source branch name |
| `base` | str | - | Target branch name, e.g. `main` |
| `draft` | bool | `False` | Open as a draft PR |
| `labels` | list[str] \| None | `None` | Labels to apply. Omit to leave the PR unlabelled |

Returns `pr_url`, `pr_number`, `status` and `title`, plus `labels` whenever a
label set was passed.

The create endpoint takes no labels, so a set passed here is applied in a second
call against the issues endpoint, and `mcp` is appended as it is for
`github_create_issue`. Read the returned `labels` back, since writing them needs push
access on the repository.

### `github_update_pr`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `pr_number` | int | - | Pull request number |
| `title` | str \| None | `None` | Replacement title. Omit to leave it alone |
| `body` | str \| None | `None` | Replacement body in Markdown. Omit to leave it alone |
| `state` | str \| None | `None` | `open` or `closed` |
| `base` | str \| None | `None` | Branch to retarget the PR onto |
| `labels` | list[str] \| None | `None` | Replacement label set. Omit to keep the current labels |

Returns `PRContent`. Only the fields you pass are sent, so a title can change
without restating the body. A call supplying none of them is rejected before it
reaches GitHub.

`labels` replaces the whole set, so `[]` strips every label including `mcp`.
Unlike `github_create_pr`, this tool does not re-add `mcp`. Labels go to the issues
endpoint in a second call, and `PRContent` carries none of them, so read them
back with `github_get_issue` if they matter.

Retargeting `base` recomputes the diff against the new branch, so the file list
and the review comments' line anchors can both move.

### `github_set_pr_draft`

| Parameter | Type | Description |
|---|---|---|
| `repo_owner` | str | GitHub organisation or username |
| `repo_name` | str | Repository name |
| `pr_number` | int | Pull request number |
| `draft` | bool | `True` returns the PR to draft, `False` marks it ready for review |

Returns `pr_number`, `is_draft` and `url`. REST accepts `draft` only when the PR
is created, so this runs a GraphQL mutation and needs a token that can write to
the repository.

### `github_set_assignees`

| Parameter | Type | Description |
|---|---|---|
| `repo_owner` | str | GitHub organisation or username |
| `repo_name` | str | Repository name |
| `issue_number` | int | PR or issue number, PRs are issues for this endpoint |
| `assignees` | list[str] | GitHub usernames to assign |

### `github_update_pr_branch`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `pr_number` | int | - | Pull request number |
| `expected_head_sha` | str \| None | `None` | Fail unless the head still matches this SHA |

Merges the base branch into the head branch, adding a merge commit to the PR
branch. Read `head_sha` from `github_get_pr_content` and pass it as
`expected_head_sha` to avoid racing a push from someone else.
This does not resolve conflicts, and a conflicting update fails and the branch must
be fixed locally.

### `github_merge_pr`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `pr_number` | int | - | Pull request number |
| `commit_title` | str | - | Merge commit title in the Title Convention. Required, a call without one is refused |
| `commit_message` | str \| None | `None` | Merge commit body |
| `merge_method` | str | `squash` | One of `merge`, `squash`, `rebase` |
| `force` | bool | `False` | Merge although the checks are failing, pending or absent |

Two things are checked before GitHub is asked, and each refusal is a
`VALIDATION_ERROR` naming this skill. `commit_title` must be present and match
the pattern in `GITHUB_MERGE_COMMIT_TITLE_PATTERN`, which defaults to the Title
Convention. The PR's checks are read as `github_get_pr_status_checks` reads them,
and anything other than `passing` refuses the merge unless `force` is set. A
repository with no checks reads as `unknown`, so merging there needs `force`.

The head commit the checks were read against goes to GitHub with the merge, so
a push made in between is refused with a 409 rather than merged unchecked.

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

This governs `title` in `github_create_pr`, `title` in `github_update_pr` and
`commit_title` in `github_merge_pr`, where the server enforces it. Append the PR
reference, e.g. `feat(auth): support GitHub App tokens (#123)`.

The summary names the action and what it acts on, and stops there. What the
change displaces belongs in the body, where there is room to say why, so
`docs(site): publish the documentation on Read the Docs` beats the same line
with `instead of one long README` on the end. A title that leads with the fault
reads the wrong way round: `fix(api): retry a failed token refresh`, not
`fix(api): token refresh never retried`. A version being released never appears
in the title, though versions being moved do, as in `chore(deps): bump ruff from
0.16.7 to 0.16.8`.

Examples:

- `fix(tools): handle empty diff response from the compare endpoint`
- `feat(cache): add Redis-backed PR diff cache`
- `chore(deps): bump fastmcp-slim to 3.4.7, refresh lock`

Avoid bare titles such as `Update README`, bracketed prefixes such as
`[WIP] cache work`, a kebab-case slug where prose belongs such as
`fix(tools): empty-diff-handling`, and a type with no scope such as
`fix: empty diff`.

## PR Body

Five `##` sections, in this order, with nothing above the first or after the
last.

```
## Summary
## Related Issues
## Changes Made
## Testing
## Checklist
```

| Section | Holds |
|---|---|
| Summary | Two or three sentences on what the change does, in the reviewer's terms. Opens on the change rather than the problem, since the issue holds the problem |
| Related Issues | `Fixes #N.` and nothing else, which closes the issue on merge. `Refs #N.` where the PR leaves part of the issue undone |
| Changes Made | Grouped by what the change does, never by which file it touched. Five bullets is the ceiling. This is where the decision that was not obvious goes |
| Testing | The commands and what they returned, one bullet each. Not how the checking was done, not what the new tests assert |
| Checklist | What Testing cannot show: docs updated, nothing breaking for existing callers. Every box ticked |

Never walk the change file by file. GitHub already renders the file list, so a
bullet per file restates it.

An unticked box states what was not done, so delete the line instead of leaving
it empty. Where Changes Made can only repeat Summary, as on a one-line fix, it
comes out.

Two hundred and fifty words across all five sections is the ceiling.

## Merge Method Guide

| Method | When to use |
|---|---|
| `squash` | Feature branches, keeps the default branch history readable. The default |
| `merge` | When the individual branch commits are worth preserving |
| `rebase` | Linear history with no merge commit. Fails if the branch does not replay cleanly |

## Best Practices

- Title every PR as `<type>(<scope>): <prose summary>`, see Title Convention above
- Pass `commit_title` in the same form when merging, since the merge is refused without one
- Write PR bodies to the PR Body template above, and cut a section rather than padding it
- Link the issue with `Fixes #N.` under Related Issues, so merging closes it
- Assign every PR with `github_set_assignees` as you open it, so it has an owner from the start
- Let `github_merge_pr` gate on the checks itself, and reach for `force=True` only with the user's explicit agreement
- Use `draft=True` for work in progress, since a draft cannot be merged, and `github_set_pr_draft` to flip it once the work is ready
- Call `github_list_repo_labels` before labelling rather than guessing names, since GitHub creates a new label for a name that does not exist
- Label a PR through `github_create_pr` or `github_update_pr` rather than a shell, since both write the same labels the issue tools do
- Delete the head branch after merging
