---
description: Manage the lifecycle of a GitHub PR - create, update the description, assign, request review, refresh the branch and merge
---

# PR Management

Open pull requests, keep them current, and merge them once they are ready.

## Prerequisites

- `repo_owner` and `repo_name` for the target repository
- The `head` branch must already be pushed before creating a PR
- GitHub token with `repo` write access
- The `workflow` scope on that token to merge a PR touching `.github/workflows`, which GitHub refuses without it
- Actions write on a fine-grained token to re-run a job or dispatch a workflow

## Workflow

### Opening a PR

1. Call `github_list_repo_labels` to see the label names the repository defines
2. Write the title to the Title Convention and the body to the PR Body template below
3. Call `github_create_pr` with that title, body, head branch, base branch and labels
4. Call `github_set_assignees` with whoever owns the work
5. Call `github_request_reviewers` with the people and teams who should review it
6. Pass `draft=True` while the work is still in progress
7. The tool appends the `mcp` label to any `labels` you pass, so leave it out of the list. Omitting `labels` leaves the PR unlabelled

### Updating a PR

1. Call `github_update_pr` to change any subset of the title, body, state, base branch and labels. This is the one to reach for, since it leaves the fields you omit alone
2. Call `github_set_pr_draft` to mark a draft ready for review, or to put a PR back into draft
3. Call `github_set_assignees` to set the assignees
4. Call `github_update_pr_branch` when the base branch has moved on and the PR needs the latest upstream commits
5. Call `github_create_empty_commit` with `pr_number` to start a new CI run under your name, such as on a Dependabot PR whose run failed on its read-only token
6. Call `github_rerun_job` with a failed check run's `id` from `github_get_pr_status_checks` to run that job again, and the jobs that depend on it, without a commit
7. Call `github_dispatch_workflow` to start a workflow with a `workflow_dispatch` trigger on a branch or tag, passing its inputs

### Closing a PR

1. Call `github_update_pr` with `state="closed"`. Reopen with `state="open"`
2. Closing is not merging. A closed PR keeps its branch and its comments

### Merging a PR

1. Call `github_get_pr_content` and keep its `head_sha`, then `github_get_pr_status_checks` for `overall`, per the `pr-analysis` skill, and `github_list_pr_reviews` for the verdicts, per the `pr-review` skill
2. **Where a user can answer in the session, ask before calling `github_merge_pr`, and name anything that is not ready: a `failing`, `pending` or `unknown` result with the failing check names, or no approval. A yes, including "merge it anyway", is the answer, so merge and do not refuse**
3. Where nobody can answer, as in an unattended run, the request that started the run is the user's answer. Merge when it asked for a merge, and report the check and review state with the result rather than stopping to ask
4. Call `github_merge_pr` with an explicit `commit_title`, and with that `head_sha` as `expected_head_sha`
5. `github_merge_pr` checks nothing itself and the merge cannot be undone. A refusal from GitHub, such as branch protection requiring checks or reviews, comes back as an error that no yes overrides, so report its `GitHub said` text per the `error-handling` skill
6. A 409 reading `Head branch was modified` means a push arrived after step 1 and nothing merged. Go back to step 1 for the new head, since the commits that were checked are no longer the ones that would merge

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
| `mcp_label` | bool | `True` | Append the `mcp` tracking label to `labels`. Pass `False` to opt out |

Returns `pr_url`, `pr_number`, `status` and `title`, plus `labels` whenever a
label set was passed.

The create endpoint takes no labels, so a set passed here is applied in a second
call against the issues endpoint, and `mcp` is appended as it is for
`github_create_issue` unless `mcp_label` is `False`. Read the returned `labels`
back, since writing them needs push access on the repository.

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
back with `github_get_issue` if they matter. A call that changes only `labels`
reads the PR back afterwards, which costs one more request.

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
| `assignees` | list[str] | GitHub usernames. Replaces the current set |

Returns `status`, `assignees_requested`, `assignees_applied` and `issue_url`.

The list replaces every assignee already there, and `[]` clears them all, so
include anyone who should stay. `status` reads `partial` where GitHub dropped a
login that is not a collaborator or does not exist, and `message` then names
the logins left out.

### `github_request_reviewers`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `pr_number` | int | - | Pull request number |
| `reviewers` | list[str] \| None | `None` | User logins, each a collaborator on the repository |
| `team_reviewers` | list[str] \| None | `None` | Team slugs in the repository's organisation, not display names |
| `remove` | bool | `False` | Withdraw the request from these reviewers rather than ask them |

Returns `status`, `requested_reviewers`, `requested_teams` and `pr_url`, read
off the PR GitHub answers with.

A request adds to the reviewers already asked and a removal takes off only those
named, so repeating a call leaves the PR as it was. GitHub refuses the whole call
with a `VALIDATION_ERROR` when one login is not a collaborator, and the error
names the logins and teams the call asked for. `status` reads `partial` where the
PR leaves off a reviewer the call asked for, or still lists one it withdrew, and
`message` then names them. A fine-grained token needs Pull requests write.

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

### `github_create_empty_commit`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `branch` | str \| None | `None` | Branch to commit to. Omit it and `pr_number` for the default branch |
| `pr_number` | int \| None | `None` | Pull request whose head branch to commit to, in place of `branch` |
| `message` | str | `chore: trigger CI [dependabot skip]` | Commit message |
| `expected_head_sha` | str \| None | `None` | Refuse unless the head still matches this SHA |
| `allow_default_branch` | bool | `False` | Commit to the default branch, which is refused otherwise |

Returns `commit_sha`, `parent_sha`, `parent_tree_sha`, `tree_sha`, `branch` and
`html_url`. The commit reuses its parent's tree, so `tree_sha` equal to
`parent_tree_sha` shows it changed no file.

A re-run keeps the privileges of whoever started the first run, which for a
Dependabot PR is a read-only token with no Actions secrets. This commit is authored
by the caller and starts a new run under their name instead.

`branch` and `pr_number` are mutually exclusive. A PR opened from a fork is refused,
since its head branch lives in the fork. The ref only fast-forwards, so a push that
arrives after the head is read fails rather than being overwritten.

Dependabot stops rebasing a PR once another commit is pushed to it, unless that
commit's message carries `[dependabot skip]`. The default message carries it, so
keep the marker in any message passed for a Dependabot PR.

### `github_rerun_job`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `job_id` | int | - | The check run's `id`, as `github_get_pr_status_checks` reports it |
| `enable_debug_logging` | bool | `False` | Run the job again with runner and step debug logging |

Returns `job_id` and a `status` of `requested`. GitHub answers with no body, so
read the new attempt's result from `github_get_pr_status_checks`.

GitHub re-runs every job that depends on this one as well, and each call starts
another attempt. Only a check run from GitHub Actions is a job, so pass the `id`
of one whose `suite_app` is `GitHub Actions`.

A re-run keeps the privileges of whoever started the first run, so a Dependabot
job that failed on its read-only token fails the same way again. Use
`github_create_empty_commit` for that case.

### `github_dispatch_workflow`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `workflow_id` | int \| str | - | The workflow's id, or its file name such as `ci.yml` |
| `ref` | str | - | Branch or tag to run the workflow on |
| `inputs` | dict \| None | `None` | Values for the inputs the workflow declares, keyed by input name |

Returns `workflow_run_id`, `run_url` and `html_url` for the run it started.

GitHub refuses a workflow with no `workflow_dispatch` trigger. It takes at most
25 inputs, so a call passing more is refused before it reaches GitHub, and an
input left out takes the default the workflow file sets.

### `github_merge_pr`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `pr_number` | int | - | Pull request number |
| `commit_title` | str \| None | `None` | Merge commit title, GitHub generates one if omitted |
| `commit_message` | str \| None | `None` | Merge commit body |
| `merge_method` | str | `squash` | One of `merge`, `squash`, `rebase` |
| `expected_head_sha` | str \| None | `None` | Refuse unless the head still matches this SHA |

Read `head_sha` from `github_get_pr_content` and pass it as `expected_head_sha`,
so the merge takes the commit that was checked. GitHub refuses with a 409 once
the head has moved, and the error carries GitHub's `Head branch was modified`
message. A call without it merges whatever the head is when GitHub receives it.

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
`commit_title` in `github_merge_pr`. Always set `commit_title` explicitly when
squashing. Without it GitHub follows the repository's setting, and the default
takes a single-commit PR's subject from that commit rather than from the PR
title. Append the PR reference, e.g.
`feat(auth): support GitHub App tokens (#123)`.

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
| Related Issues | `Fixes #N.` and nothing else, which closes the issue when the PR merges into the default branch. `Refs #N.` where the PR leaves part of the issue undone |
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
- Pass `commit_title` in the same form when merging so the branch history stays parseable
- Write PR bodies to the PR Body template above, and cut a section rather than padding it
- Link the issue with `Fixes #N.` under Related Issues, so merging closes it
- Assign every PR with `github_set_assignees` as you open it, so it has an owner from the start
- Read `github_get_pr_status_checks` before merging and tell the user about anything but `passing`, since `unknown` is not a pass. The user's yes, or the request that started an unattended run, is enough to merge
- Use `draft=True` for work in progress, since a draft cannot be merged, and `github_set_pr_draft` to flip it once the work is ready
- Call `github_list_repo_labels` before labelling rather than guessing names, since GitHub creates a new label for a name that does not exist
- Label a PR through `github_create_pr` or `github_update_pr` rather than a shell, since both write the same labels the issue tools do
- Report that the head branch is still in place after a merge, since no tool here deletes a branch
