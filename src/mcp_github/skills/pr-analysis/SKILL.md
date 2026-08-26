---
description: Analyse a GitHub pull request by fetching its metadata, diff, linked issues and CI status
---

# PR Analysis

Read-only inspection of a pull request: what it changes, what it closes, and whether CI is green.

## Prerequisites

- `repo_owner`, `repo_name` and `pr_number` for the target PR
- GitHub token with `repo` read access

## Workflow

1. **Fetch metadata** - call `get_pr_content` for title, description, author, state and timestamps
2. **Fetch the diff** - call `get_pr_diff` for the raw unified diff of every changed file
3. **Fetch linked issues** - call `get_pr_linked_issues` to see what merging will auto-close
4. **Fetch CI status** - call `get_pr_status_checks` to see whether the head commit is green
5. **Synthesise** - combine the four into a structured analysis

Steps 3 and 4 do not depend on steps 1 and 2, so issue them together.

## Tool Parameters

All four tools take the same three arguments.

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

Nothing else is returned. Draft status, base and head refs, labels and
`mergeable` are **not** available from this tool. `state` is `closed` for both
merged and abandoned PRs and does not distinguish the two.

To learn whether a PR is a draft, call `list_open_issues_prs` with
`filtering="repo"` and read `is_draft` on the matching entry.

### `get_pr_diff`

Returns the raw patch as a string in unified diff format. Each file section
starts with `diff --git a/... b/...`. It comes from the patch-diff host rather
than the REST API, so it is not paginated and carries no per-file metadata.

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

`overall` is derived, and `failing` and `pending` are authoritative. `passing`
is returned only when the full set of checks was read, so `truncated=True`
downgrades an otherwise-clean result to `unknown` rather than `passing`. Treat
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
- For diffs over 500 lines, analyse the high-risk files first: auth, config, dependency manifests
- Report `overall="unknown"` as unverified rather than treating it as a pass
- Use `get_pr_linked_issues` to check a PR actually closes what it claims to
- Do not assert draft or mergeable status from `get_pr_content`, which carries neither field
