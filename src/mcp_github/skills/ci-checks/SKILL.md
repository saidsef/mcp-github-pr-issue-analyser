---
description: Find out why a pull request's checks fail, read the failing job's log, run the job again or start a new run, follow it to its result, and get a Dependabot update merged
---

# CI Checks

Read the checks on a pull request, find what failed and why, start the run again
the way the failure needs, and follow it to its result.

## Prerequisites

- `repo_owner`, `repo_name` and the PR number, or the branch, tag or SHA whose runs you want
- GitHub token with `repo` read access to read checks, runs and logs
- Actions write on a fine-grained token, or `repo` on a classic one, to re-run a job or dispatch a workflow
- `repo` write access to push an empty commit

`github_get_pr_status_checks` and `github_get_workflow_runs` run as long-running
tasks. Neither reports progress while it works. `github_get_job_log` is a plain read.

## Workflow

### Finding out why a check failed

1. Call `github_get_pr_status_checks` and read `overall`. Report `unknown` as not verified, never as a pass
2. Pick the failing entries in `check_runs`. Only an entry whose `suite_app` is `GitHub Actions` has a job log behind it. For any other app, such as Codacy or CodeQL, report its `details_url`
3. Call `github_get_job_log` with that entry's `id`. The tool keeps the end of the log, where a job reports its failure
4. Read the cause from the log before you decide how to run the job again

### Running the checks again

Pick the action that fits the cause:

| Cause | Action |
|---|---|
| A flaky step or a runner fault | `github_rerun_job` with the job's `id`, which also re-runs the jobs that depend on it |
| A job that needs a secret, on a Dependabot PR | `github_create_empty_commit` with `pr_number`, because a re-run keeps Dependabot's read-only token |
| A workflow that runs only on `workflow_dispatch` | A dispatch, as in Dispatching a workflow below |
| A fault in the code | A fix committed with `github_commit_files`, per the `pr-management` skill |

Then follow the run:

1. Take the `workflow_run_id` that `github_rerun_job` or a dispatch returns, or the branch name after a commit
2. Call `github_get_workflow_runs` with `run_id`, or with `head` set to the branch, and `wait_seconds` for how long to wait
3. Read `timed_out`. `True` means a run had not finished, so call again rather than report the last `status` as final
4. Call `github_get_pr_status_checks` again for the new `overall`

### Dependabot updates

Dependabot opens each update from a branch in the same repository. GitHub runs the
`pull_request` workflows on that branch with a read-only token and without the
repository's Actions secrets, so a job that needs a secret fails on every update,
however sound the update is.

1. Find the open updates with `github_search_issues_prs` and `is:pr is:open author:app/dependabot repo:<owner>/<name>` in the query
2. Read each one's checks, as in Finding out why a check failed
3. Where a job failed for want of a secret, call `github_create_empty_commit` with `pr_number`. The commit starts a new run under the caller's name, and that run has the secrets. Keep `[dependabot skip]` in any message you pass, so Dependabot keeps rebasing the PR
4. Follow the run, as in Running the checks again
5. Merge per Merging a PR in the `pr-management` skill, with `merge_method` set to `squash`, an explicit `commit_title`, and the `head_sha` the checks were read on as `sha`

The merge follows the rule in `pr-management`. Where a user can answer, report
anything short of `passing` and ask, and "merge it anyway" is the answer. In an
unattended run, the request that started the run is the answer, so merge and report
the check state with the result. The loop never stops to refuse a merge.

## Tool Parameters

### `github_get_pr_status_checks`

| Parameter | Type | Description |
|---|---|---|
| `repo_owner` | str | GitHub organisation or username |
| `repo_name` | str | Repository name |
| `pr_number` | int | Pull request number |

Returns `StatusChecksResult`:

| Field | Type | Description |
|---|---|---|
| `pr_number` | int | The PR queried |
| `head_sha` | str \| None | The head commit the checks were read from, `None` where GitHub resolved no commit for the PR |
| `overall` | str | One of `passing`, `failing`, `pending`, `unknown` |
| `check_runs` | list | Check runs on the head commit |
| `commit_statuses` | list | Legacy commit statuses on the head commit |
| `truncated` | bool | `True` if the check-suite or check-run page caps were hit |

Each entry in `check_runs` carries `id`, `name`, `status`, `conclusion`,
`details_url` and `suite_app`, the last naming the app that owns the suite.
On a run whose `suite_app` is `GitHub Actions`, `id` is the job id
`github_get_job_log` and `github_rerun_job` take. A run from another app, such as
Codacy or CodeQL, carries an `id` with no job log behind it.
Each entry in `commit_statuses` carries `context`, `state`, `description` and
`target_url`.

`overall` is derived, and `failing` and `pending` are authoritative. `passing`
is returned only when the full set of checks was read, so `truncated=True`
downgrades an otherwise-clean result to `unknown` rather than `passing`. A head
commit with no checks and no statuses at all also reads `unknown`, with its
`head_sha` set. A `head_sha` of `None` means GitHub resolved no head commit, and
no checks were read. Treat `unknown` as "not verified", never as "fine".

The tool reads the head commit from the pull request, so a merged PR whose
branch was deleted still reports its checks.

### `github_get_job_log`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `job_id` | int | - | The `id` of a GitHub Actions check run from `github_get_pr_status_checks` |
| `max_bytes` | int | `131072` | Cap on the log returned, kept from the end. `0` asks the size without reading the log |

Returns `JobLogResult`:

| Field | Type | Description |
|---|---|---|
| `job_id` | int | The job queried |
| `log` | str | The plain-text log, or its end where it was cut |
| `bytes_returned` | int | Size of `log` |
| `bytes_total` | int | Size of the whole log, whether or not it was cut |
| `truncated` | bool | `True` when `log` is only the end of it |

A job reports its failure last, so a cut keeps the end of the log where
`github_get_pr_diff` keeps the start. A cut falls on a byte boundary and any
character split across it is dropped, so a truncated log can start mid-line.
Re-read with a higher `max_bytes` where the end does not show the cause.

GitHub answers the log request with a redirect to a storage host, and the link
expires after one minute. The tool follows it at once and sends no token to
that host. A private repository needs `repo` on a classic token, or Actions
read on a fine-grained one.

`GITHUB_DIFF_MAX_BYTES` sets the default, and `github_get_pr_diff` reads it as
well.

### `github_get_workflow_runs`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `run_id` | int \| None | `None` | A run's id, such as the `workflow_run_id` that a workflow dispatch or `github_rerun_job` returns |
| `head` | str \| None | `None` | Branch, tag or SHA whose commit to read every run of, in place of `run_id` |
| `wait_seconds` | int | `0` | Seconds to wait for every run to complete, capped by the server |

Pass `run_id` or `head`, not both. The tool resolves `head` to one commit when
the call starts, so a push during the wait does not move it.

Returns `WorkflowRunsResult`:

| Field | Type | Description |
|---|---|---|
| `runs` | list | The run `run_id` names, or every run on the commit `head` resolves to |
| `truncated` | bool | `True` when GitHub holds more than 100 runs for the commit, or more than 100 jobs for a run |
| `timed_out` | bool | `True` when a run had not completed by the time the tool returned |

Each entry in `runs` carries `id`, `name`, `event`, `head_sha`, `run_attempt`,
`status`, `conclusion`, `html_url` and `jobs`. Each job carries `id`, `name`,
`status`, `conclusion`, `started_at`, `completed_at`, `html_url` and `steps`, and
each step carries `number`, `name`, `status` and `conclusion`. A job's `id` is the
id that `github_get_job_log` and `github_rerun_job` take. The jobs belong to the
run's newest attempt, so a re-run reads its new jobs.

With `wait_seconds` above 0 the tool polls every 10 seconds until every run has
completed or the wait ends. The server caps the wait 60 seconds under the task
worker's `FASTMCP_DOCKET_REDELIVERY_TIMEOUT`, so a task ends before the worker
hands it to another. The cap is 240 seconds with the worker's default. An inline
call blocks for the whole wait, so ask for one only when the call runs as a task,
or keep it under the client's own timeout. A call that a timeout cuts off can run
again, because the tool only reads.

`timed_out` is `True` whenever a run had not completed when the tool returned,
including a call with `wait_seconds=0`, so the last `status` never reads as final.
Right after a push, a lookup by `head` can come back empty, and the wait keeps
polling until GitHub creates the runs. A run that a `workflow_run` trigger starts
after the others finish is not in the result.

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
Dependabot PR is a read-only token without Actions secrets. The caller authors the
empty commit, so it starts a new run under the caller's name instead. GitHub signs
it and acts as its committer, as for a commit made in the web interface, so it
shows as Verified and passes a rule that requires signed commits.

`branch` and `pr_number` are mutually exclusive. A PR opened from a fork is refused,
since its head branch lives in the fork. GitHub refuses the commit when a push
arrives after the head is read, so nothing is overwritten.

Dependabot stops rebasing a PR once another commit is pushed to it, unless that
commit's message carries `[dependabot skip]`. The default message carries it, so
keep the marker in any message passed for a Dependabot PR.

### Dispatching a workflow

Call `github_api_write` with `POST` on
`repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches`, where
`workflow_id` is the workflow's id or its file name such as `ci.yml`. The body
carries:

| Field | Value |
|---|---|
| `ref` | Branch or tag to run the workflow on |
| `inputs` | Values for the inputs the workflow declares, keyed by input name. Leave it out when there are none |
| `return_run_details` | `true`. Without it GitHub answers 204 and does not report the run |

The reply carries `workflow_run_id`, `run_url` and `html_url` for the run it
started. Pass `workflow_run_id` to `github_get_workflow_runs` to follow the run.

GitHub refuses a workflow without a `workflow_dispatch` trigger, and a call with
more than 25 inputs. An input left out takes the default the workflow file sets.

## Best Practices

- Read the log before re-running a job, since a re-run of a real fault fails the same way and spends runner minutes
- Re-run the failed job with `github_rerun_job`, which also re-runs what depends on it, rather than the whole workflow
- Use `github_create_empty_commit` for a Dependabot job that failed on its read-only token, since a re-run keeps that token
- Ask for `wait_seconds` only when the call runs as a task, or keep it under the client's own timeout
- Check `suite_app` before reading a log, since only a GitHub Actions run has one
