---
description: Analyse a GitHub pull request by fetching its metadata, diff, linked issues and CI status
---

# PR Analysis

Read-only inspection of a pull request: what it changes, what it closes, and whether CI is green.

## Prerequisites

- `repo_owner`, `repo_name` and `pr_number` for the target PR
- GitHub token with `repo` read access

`get_pr_linked_issues` and `get_pr_status_checks` run as long-running tasks.
Neither reports incremental progress, and `get_pr_status_checks` logs one
summary line once it has finished paging through the check suites.
`get_pr_content` and `get_pr_diff` are plain reads.

## Workflow

1. **Fetch metadata** - call `get_pr_content` for title, description, author, state and timestamps
2. **Fetch the diff** - call `get_pr_diff` for the raw unified diff of every changed file
3. **Read the surrounding code** - call `get_repository_file` on a file whose hunk does not say enough on its own
4. **Fetch linked issues** - call `get_pr_linked_issues` to see what merging will auto-close
5. **Fetch CI status** - call `get_pr_status_checks` to see whether the head commit is green
6. **Synthesise** - combine what you have into a structured analysis

Steps 4 and 5 do not depend on steps 1 and 2, so issue them together.

## Tool Parameters

`get_pr_content`, `get_pr_diff`, `get_pr_linked_issues` and
`get_pr_status_checks` take the same three arguments.

| Parameter | Type | Description |
|---|---|---|
| `repo_owner` | str | GitHub organisation or username |
| `repo_name` | str | Repository name |
| `pr_number` | int | Pull request number |

### `get_pr_content`

Returns `PRContent` with exactly these fields:

| Field | Type | Description |
|---|---|---|
| `title` | str | PR title |
| `description` | str \| None | PR body in Markdown |
| `author` | str | Login of the PR author |
| `created_at` | str | ISO 8601 timestamp |
| `updated_at` | str | ISO 8601 timestamp |
| `state` | str | `open` or `closed` |
| `head_sha` | str \| None | Newest commit on the PR branch |
| `head_ref` | str \| None | Branch the PR merges from |
| `base_ref` | str \| None | Branch the PR merges into |
| `requested_reviewers` | list[str] | Logins asked to review and yet to answer |
| `requested_teams` | list[str] | Team slugs asked to review |

Nothing else is returned. Draft status, labels and `mergeable` are **not**
available from this tool. `state` is `closed` for both merged and abandoned PRs
and does not distinguish the two.

`head_sha` is what `update_pr_branch` takes as `expected_head_sha`.

GitHub empties `requested_reviewers` once that reviewer answers, so an empty
list with no reviews means nobody was asked. Read the verdicts themselves with
`list_pr_reviews`.

To learn whether a PR is a draft, call `list_open_issues_prs` with
`filtering="repo"` and read `is_draft` on the matching entry. That tool returns
open PRs only, so read a closed or merged one through `search_issues_prs`, which
returns the same shape.

### `get_pr_diff`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `pr_number` | int | - | Pull request number |
| `max_bytes` | int | `131072` | Cap on the patch returned. `0` asks the size without reading the patch |

Returns:

| Field | Type | Description |
|---|---|---|
| `pr_number` | int | The PR queried |
| `patch` | str | Unified diff, each file section starting `diff --git a/... b/...` |
| `bytes_returned` | int | Size of `patch` |
| `bytes_total` | int | Size of the whole patch, whether or not it was cut |
| `truncated` | bool | `True` when `patch` is only the start of it |

The patch comes from the patch-diff host rather than the REST API, so it is not
paginated and carries no per-file metadata. `bytes_total` is the full size even
on a truncated reply, so `max_bytes=0` is a cheap way to decide whether to ask
for the patch at all. A cut lands on a byte boundary and any character split
across it is dropped, so the tail of a truncated patch can end mid-line.

The default is set by `GITHUB_DIFF_MAX_BYTES`.

### `get_repository_file`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `path` | str | - | File path from the repository root, e.g. `src/mcp_github/auth.py` |
| `ref` | str \| None | `None` | Branch, tag or SHA to read at. Omit for the default branch |
| `offset` | int | `0` | Byte to start the window at |
| `limit` | int | `131072` | Cap on the bytes returned. `0` asks the size without reading |

Returns:

| Field | Type | Description |
|---|---|---|
| `path` | str | The file read |
| `ref` | str \| None | The ref asked for, `None` where the default branch was used |
| `content` | str | The window, empty when the file is binary |
| `binary` | bool | `True` where the window holds a NUL byte |
| `bytes_returned` | int | Size of the window |
| `bytes_total` | int | Size of the whole file |
| `truncated` | bool | `True` when the window stops short of the end |
| `next_offset` | int \| None | Where to carry on, `None` once the end is reached |

A hunk shows a few lines either side of a change, which is often not enough to
judge whether the change is right. Read the file to see what surrounds it.

Pass `next_offset` back as `offset` to walk a long file. A cut lands on a byte
boundary and any character split across it is dropped, so a window can end
mid-line. A binary file returns no content rather than mangled text.

A directory is an error naming `list_repository_tree`. The default is set by
`GITHUB_FILE_MAX_BYTES`.

### `list_repository_tree`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `path` | str | `""` | Subdirectory to list. Omit for the repository root |
| `ref` | str \| None | `None` | Branch, tag or SHA to list at. Omit for the default branch |
| `recursive` | bool | `False` | Descend into every subdirectory rather than one level |

Returns `path`, `ref`, `total`, `entries` and `truncated`. Each entry carries
`path`, `mode`, `type`, `size` and `sha`. `type` is `blob` for a file, `tree`
for a directory and `commit` for a submodule.

`truncated` is `True` where the tree exceeded GitHub's cap of 100,000 entries or
7 MB. Paging does not widen it, so list a subdirectory at a time instead.

### `get_pr_linked_issues`

Returns `LinkedIssuesResult`:

| Field | Type | Description |
|---|---|---|
| `pr_number` | int | The PR queried |
| `linked_issues` | list | One entry per issue with `number`, `title`, `state`, `url`, `created_at`, `labels` |

Only issues GitHub will auto-close on merge are returned, meaning those written
into the PR body with a closing keyword such as `Fixes #42`. An issue merely
mentioned as `#42` is not included.

### `get_pr_status_checks`

Returns `StatusChecksResult`:

| Field | Type | Description |
|---|---|---|
| `pr_number` | int | The PR queried |
| `overall` | str | One of `passing`, `failing`, `pending`, `unknown` |
| `check_runs` | list | Check runs on the head commit |
| `commit_statuses` | list | Legacy commit statuses on the head commit |
| `truncated` | bool | `True` if the check-suite or check-run page caps were hit |

Each entry in `check_runs` carries `name`, `status`, `conclusion`,
`details_url` and `suite_app`, the last naming the app that owns the suite.
Each entry in `commit_statuses` carries `context`, `state`, `description` and
`target_url`.

`overall` is derived, and `failing` and `pending` are authoritative. `passing`
is returned only when the full set of checks was read, so `truncated=True`
downgrades an otherwise-clean result to `unknown` rather than `passing`. A head
commit with no checks and no statuses at all also reads `unknown`. Treat
`unknown` as "not verified", never as "fine".

## Analysis Output Structure

Cover:

- **What**: short summary of the change
- **Why**: inferred from the description and linked issues
- **Scope**: files changed, lines added and removed
- **CI**: the `overall` value, naming any failing check
- **Risk areas**: large diffs, auth and config changes, dependency bumps
- **Missing items**: tests, docs, changelog entries

## Best Practices

- Call `get_pr_content` before `get_pr_diff`, since the metadata gives context for reading the diff
- On a PR that looks large, call `get_pr_diff` with `max_bytes=0` first and decide from `bytes_total`
- Re-read a `truncated=True` patch with a higher `max_bytes` rather than analysing half of it
- For diffs over 500 lines, analyse the high-risk files first: auth, config, dependency manifests
- Report `overall="unknown"` as unverified rather than treating it as a pass
- Use `get_pr_linked_issues` to check a PR actually closes what it claims to
- Do not assert draft or mergeable status from `get_pr_content`, which carries neither field
