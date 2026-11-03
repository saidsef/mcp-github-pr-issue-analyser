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

1. Read the label names the repository defines, per Listing Labels in the `issue-management` skill
2. Write the title to the Title Convention and the body to the PR Body template below
3. Call `github_create_pr` with that title, body, head branch, base branch and labels
4. Assign whoever owns the work, per Assigning an Issue or PR in the `issue-management` skill
5. Ask the people and teams who should review it, as in Requesting reviewers below
6. Pass `draft=True` while the work is still in progress
7. The tool appends the `mcp` label to any `labels` you pass, so leave it out of the list. Omitting `labels` leaves the PR unlabelled

### Updating a PR

1. Call `github_update_pr` to change any subset of the title, body, state, base branch and labels. This is the one to reach for, since it leaves the fields you omit alone
2. Call `github_set_pr_draft` to mark a draft ready for review, or to put a PR back into draft
3. Change the assignees per Assigning an Issue or PR in the `issue-management` skill
4. Bring the branch up to date, as in Updating the branch below, when the base branch has moved on and the PR needs the latest upstream commits. GitHub refuses a conflicted branch, so resolve it as Resolving a conflicted PR describes
5. Call `github_create_empty_commit` with `pr_number` to start a new CI run under your name, such as on a Dependabot PR whose run failed on its read-only token
6. Call `github_commit_files` with the PR's `head_ref` to commit text files to its branch
7. Call `github_rerun_job` with a failed check run's `id` from `github_get_pr_status_checks` to run that job again, and the jobs that depend on it, without a commit. Pass the `workflow_run_id` it returns to `github_get_workflow_runs` to follow the run
8. Start a workflow with a `workflow_dispatch` trigger on a branch or tag, as in Dispatching a workflow below. Follow the run it starts the same way

### Resolving a conflicted PR

1. Call `github_get_pr_content` and keep `head_sha`, `head_ref`, `base_ref` and `changed_files`. A conflicted PR reads `mergeable: false`
2. Read the commit to merge in with `github_api_read` on `repos/{owner}/{repo}/commits` and `params={"per_page": 1, "sha": "<base_ref>"}`, and keep the first entry's `sha`
3. Call `github_get_pr_diff` for the files the PR changes
4. Read each of those files at `head_sha` and at the base commit with `github_get_repository_file`, and write the version that keeps both sides' changes
5. Call `github_commit_files` with `branch` set to `head_ref`, `parents` set to `head_sha` then the base commit, and every file from step 3 in `files` as it should read after the merge. A file the PR deletes goes in as `null`
6. A file left out of `files` takes the base branch's version, which drops the PR's change to it. `github_get_repository_file` returns no content for a binary file, so a PR that changes one cannot be resolved this way
7. Call `github_get_pr_content` again. `mergeable` reads `null` while GitHub recomputes it, then `true`. A `changed_files` count that differs from step 1 means a file was left out

### Closing a PR

1. Call `github_update_pr` with `state="closed"`. Reopen with `state="open"`
2. Closing is not merging. A closed PR keeps its branch and its comments
3. Call `github_delete_branch` to remove the head branch once the PR is merged or closed and nobody wants the branch any more. List what is left first with `github_api_read` on `repos/{owner}/{repo}/branches`, which returns each branch's `name`, `commit.sha` and `protected` flag

### Merging a PR

1. Call `github_get_pr_content` and keep its `head_sha`, then `github_get_pr_status_checks` for `overall`, per the `pr-analysis` skill, and the reviews for the verdicts, per Reading reviews in the `pr-review` skill
2. **Where a user can answer in the session, ask before sending the merge, and name anything that is not ready: a `failing`, `pending` or `unknown` result with the failing check names, or no approval. A yes, including "merge it anyway", is the answer, so merge and do not refuse**
3. Where nobody can answer, as in an unattended run, the request that started the run is the user's answer. Merge when it asked for a merge, and report the check and review state with the result rather than stopping to ask
4. Send the merge, as in Merging below, with an explicit `commit_title` and that `head_sha` as `sha`
5. The merge call checks nothing itself and cannot be undone. A refusal from GitHub, such as branch protection requiring checks or reviews, comes back as an error that no yes overrides, so report its `GitHub said` text per the `error-handling` skill
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
endpoint in a second call, and the returned `PRContent` carries the label set
after that write. A call that changes only `labels` reads the PR back afterwards,
which costs one more request.

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

### Requesting reviewers

Call `github_api_write` with `POST` on
`repos/{owner}/{repo}/pulls/{number}/requested_reviewers` and a body carrying
`reviewers`, a list of user logins, and `team_reviewers`, a list of team slugs in
the repository's organisation rather than display names. Withdraw a request with
`github_api_delete` on the same path and the same body. GitHub requires
`reviewers` on a withdrawal, so send `[]` when withdrawing teams alone.

Both calls answer with the pull request, so `requested_reviewers` and
`requested_teams` in the reply show what took effect. Check the names there,
since GitHub can leave off a reviewer the call asked for. A request adds to the
reviewers already asked and a withdrawal takes off only those named, so repeating
a call leaves the PR as it was. GitHub refuses the whole call with a
`VALIDATION_ERROR` when one login is not a collaborator. A fine-grained token
needs Pull requests write.

### Updating the branch

Call `github_api_write` with `PUT` on `repos/{owner}/{repo}/pulls/{number}/update-branch`
and `{"expected_head_sha": "<head_sha>"}`, using the `head_sha` from
`github_get_pr_content`, so a push from someone else is not raced. GitHub merges
the base branch into the head branch, adding a merge commit to the PR branch.

This does not resolve conflicts. A conflicting update fails, and Resolving a
conflicted PR above fixes the branch with `github_commit_files`.

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
by the caller and starts a new run under their name instead. GitHub signs it and
acts as its committer, as for a commit made in the web interface, so it shows as
Verified and passes a rule that requires signed commits.

`branch` and `pr_number` are mutually exclusive. A PR opened from a fork is refused,
since its head branch lives in the fork. GitHub refuses the commit when a push
arrives after the head is read, so nothing is overwritten.

Dependabot stops rebasing a PR once another commit is pushed to it, unless that
commit's message carries `[dependabot skip]`. The default message carries it, so
keep the marker in any message passed for a Dependabot PR.

### `github_commit_files`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `branch` | str | - | Branch to commit to, in this repository rather than a fork |
| `message` | str | - | Commit message. Its first line is the headline |
| `files` | dict[str, str \| None] | - | New text content by path from the repository root, or `null` to delete the path |
| `parents` | list[str] \| None | `None` | The head the files were read at, then for a merge commit the commit to merge in |
| `allow_default_branch` | bool | `False` | Commit to the default branch, which is refused otherwise |

Returns `commit_sha`, `parents`, `tree_sha`, `branch` and `html_url`.

Pass the head the files were read at as the first parent, and the tool refuses
the call when the branch has moved since. Without `parents` the tool commits on
whatever the head is when the call runs, which can overwrite a push you have not
read.

With one parent, GitHub creates the commit and signs it, as for
`github_create_empty_commit`. GitHub refuses the commit when a push arrives after
the head is read, so nothing is overwritten.

A second parent makes a merge commit. GitHub's signing mutation takes one parent
only, so the tool builds this commit through the Git Database API, and GitHub
leaves it unsigned under a personal access token or an OAuth token. A branch with
a rule that requires signed commits refuses it. The tree starts from the second
parent's tree, so `files` carries every file the branch changes, as it should
read after the merge. The branch moves only as a fast forward, so a push that
arrives after the head is read fails the call with a `VALIDATION_ERROR` and leaves
the branch where it was. A merge commit writes every file without the executable
bit.

The tool writes text only, as UTF-8, and refuses more than two parents. A PR
opened from a fork is out of reach, since its head branch lives in the fork.

### `github_rerun_job`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `job_id` | int | - | The check run's `id`, as `github_get_pr_status_checks` reports it |
| `enable_debug_logging` | bool | `False` | Run the job again with runner and step debug logging |

Returns `job_id`, the `workflow_run_id` of the run the job belongs to, and a
`status` of `requested`. Pass `workflow_run_id` to `github_get_workflow_runs` with
`wait_seconds` to follow the new attempt. GitHub moves the run to `queued` before
it answers, so a wait does not end on the previous attempt's conclusion. The tool
reads the job before it asks for the re-run, so a failed read leaves nothing
re-run.

GitHub re-runs every job that depends on this one as well, and each call starts
another attempt. Only a check run from GitHub Actions is a job, so pass the `id`
of one whose `suite_app` is `GitHub Actions`.

A re-run keeps the privileges of whoever started the first run, so a Dependabot
job that failed on its read-only token fails the same way again. Use
`github_create_empty_commit` for that case.

### Dispatching a workflow

Call `github_api_write` with `POST` on
`repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches`, where
`workflow_id` is the workflow's id or its file name such as `ci.yml`. The body
carries:

| Field | Value |
|---|---|
| `ref` | Branch or tag to run the workflow on |
| `inputs` | Values for the inputs the workflow declares, keyed by input name. Leave it out when there are none |
| `return_run_details` | `true`. Without it GitHub answers 204 and says nothing of the run |

The reply carries `workflow_run_id`, `run_url` and `html_url` for the run it
started. Pass `workflow_run_id` to `github_get_workflow_runs` to follow the run.

GitHub refuses a workflow with no `workflow_dispatch` trigger, and a call with
more than 25 inputs. An input left out takes the default the workflow file sets.

### Merging

Call `github_api_write` with `PUT` on `repos/{owner}/{repo}/pulls/{number}/merge` and
a body carrying:

| Field | Value |
|---|---|
| `merge_method` | `squash`, `merge` or `rebase`. GitHub merges with a merge commit when this is left out, so always send it |
| `commit_title` | Merge commit title, per the Title Convention below |
| `commit_message` | Merge commit body, optional |
| `sha` | The `head_sha` from `github_get_pr_content` |

`sha` makes the merge take the commit that was checked. GitHub refuses with a 409
once the head has moved, and the error carries GitHub's `Head branch was modified`
message. A call without it merges whatever the head is when GitHub receives it.

### `github_delete_branch`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `branch` | str | - | Branch to delete |
| `expected_head_sha` | str \| None | `None` | Refuse unless the head still matches this SHA |
| `force` | bool | `False` | Delete even though an open PR comes from the branch |

Returns `status`, `branch` and `closed_prs`, the numbers of the open PRs that
`force` closed by deleting their head branch.

Destructive. GitHub can restore a deleted branch from its PR page.
`github_api_write` with `POST` on `repos/{owner}/{repo}/git/refs` and
`{"ref": "refs/heads/<branch>", "sha": "<head_sha>"}` recreates it, given a head SHA
read before the delete, since the tool does not return one. The default branch is refused, and `force` does not lift that. A branch
that an open PR comes from is refused unless `force=True`, because GitHub closes
the PR when its head branch goes. A protection rule that forbids deleting the
branch comes back as GitHub's own refusal, per the `error-handling` skill.

The tool compares `expected_head_sha` with the head before it deletes, since
GitHub's delete takes no precondition. A push that arrives between the check and
the delete is not caught.

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
`commit_title` on a merge. Always set `commit_title` explicitly when
squashing. Without it GitHub follows the repository's setting, and the default
takes a single-commit PR's subject from that commit rather than from the PR
title. Append the PR reference, e.g.
`feat(auth): support GitHub App tokens (#123)`.

The summary names the action and what it acts on, and stops there. It names that
thing so a reader who has not seen the code can tell which one changed, never by how
it was chosen: `refactor(tools): remove 22 tools that generic API tools already cover`,
not `refactor(tools): remove tools that only send one GitHub request`. It says
what was done, with the verb a colleague would use, and never passes a verdict on
the old version: `docs(skills): update skills and docs to match current tools`, not
`docs(skills): correct stale and contradictory text`. It drops "the", "a" and "an"
wherever it reads the same without them, as a headline does. What the
change displaces belongs in the body, where there is room to say why, so
`docs(site): publish documentation on Read the Docs` beats the same line
with `instead of one long README` on the end. A title that leads with the fault
reads the wrong way round: `fix(api): retry failed token refreshes`, not
`fix(api): token refresh never retried`. A version being released never appears
in the title, though versions being moved do, as in `chore(deps): bump ruff from
0.16.7 to 0.16.8`.

Examples:

- `fix(tools): handle empty diff response from compare endpoint`
- `feat(cache): add Redis-backed PR diff cache`
- `chore(deps): bump fastmcp-slim to 3.4.7, refresh lock`

Avoid bare titles such as `Update README`, bracketed prefixes such as
`[WIP] cache work`, a kebab-case slug where prose belongs such as
`fix(tools): empty-diff-handling`, and a type with no scope such as
`fix: empty diff`.

## PR Body

Five `##` sections, in this order, with nothing above the first or after the
last. Every section stays, filled with what is true at the time, and never with
planned work.

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
it empty. Tick at least one box a reviewer can check, such as the branch being
up to date with its base. On a one-line fix, Changes Made says
in one bullet what the fix changes.

Two hundred and fifty words across all five sections is the ceiling.

## Merge Method Guide

| Method | When to use |
|---|---|
| `squash` | Feature branches, keeps the default branch history readable. Send it unless there is a reason not to |
| `merge` | When the individual branch commits are worth preserving |
| `rebase` | Linear history with no merge commit. Fails if the branch does not replay cleanly |

## Best Practices

- Title every PR as `<type>(<scope>): <prose summary>`, see Title Convention above
- Pass `commit_title` in the same form when merging so the branch history stays parseable
- Write PR bodies to the PR Body template above, and fill every section with what is true rather than padding it
- Link the issue with `Fixes #N.` under Related Issues, so merging closes it
- Assign every PR as you open it, so it has an owner from the start
- Read `github_get_pr_status_checks` before merging and tell the user about anything but `passing`, since `unknown` is not a pass. The user's yes, or the request that started an unattended run, is enough to merge
- Use `draft=True` for work in progress, since a draft cannot be merged, and `github_set_pr_draft` to flip it once the work is ready
- Read the repository's labels before labelling rather than guessing names, since GitHub creates a new label for a name that does not exist
- Label a PR through `github_create_pr` or `github_update_pr` rather than a shell, since both write the same labels the issue tools do
- Delete the head branch with `github_delete_branch` after a merge where the repository does not delete it on merge. Where a user can answer, ask before deleting the branch of a PR closed without merging, since it may hold work someone still wants. In an unattended run, the request that started the run is the answer
