---
description: Create, update, list and search GitHub issues and PRs, list a repository's labels, run milestones, and check for duplicates
---

# Issue Management

Create issues, update existing ones, list the open issues or PRs in a repo, org or user
account, search for the ones you cannot already name, and read the labels a repository
defines.

## Prerequisites

- `repo_owner` and `repo_name` for the target repository
- GitHub token with `repo` write access

## Workflow

### Creating an Issue

1. Call `list_open_issues_prs` with `issue="issue"` and `filtering="repo"` to check for a duplicate, or `search_issues_prs` to include closed ones
2. Call `list_repo_labels` to see the label names the repository defines
3. Call `create_issue` with a title, body and labels, plus a `milestone` title if it belongs to one
4. The `mcp` label is appended automatically, so do not pass it yourself

### Reading an Issue

1. Call `get_issue` when you have the number and need the body, labels, assignees or milestone
2. Read it back this way after `create_issue`, `update_issue` or `update_assignees` to confirm what landed

### Updating an Issue

1. Call `update_issue` with only the fields you are changing, the rest keep their current values
2. Pass `state="closed"` on its own to close a resolved issue

### Listing Issues and PRs

1. Call `list_open_issues_prs`, choosing `filtering` for the scope you want

### Finding an Issue or PR

1. Call `search_issues_prs` when you cannot already name the item, or need something closed
2. Narrow with qualifiers in the query itself, e.g. `repo:owner/name is:issue label:bug`

### Listing Labels

1. Call `list_repo_labels` for the repository
2. Page through with `page` if the repository defines more than `per_page` labels

### Running a Milestone

1. Call `list_milestones` to see what the repository already tracks
2. Call `create_milestone` for a new one, with a due date if the work has a deadline
3. Pass `milestone` to `create_issue` to file an issue as it is opened
4. Call `set_issue_milestone` to file or unfile an issue that already exists
5. Call `update_milestone` with `state="closed"` once the work has shipped

## Tool Parameters

### `create_issue`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `title` | str | - | Issue title, see Title Convention |
| `body` | str | - | Issue description in Markdown |
| `labels` | list[str] | - | Labels to apply. Required, pass `[]` for none |
| `milestone` | str | `""` | Milestone title to file it under. Omit for none |

Returns `IssueData` with `number`, `title`, `body`, `state`, `author`,
`labels`, `assignees`, `milestone`, `html_url`, `created_at`, `updated_at`.

`milestone` is a title, not a number, and the milestone has to exist already.

`labels` has no default and must be supplied. Whatever you pass, `mcp` is
appended, so `[]` yields `["mcp"]`. Setting labels needs push access on the
repository, and GitHub drops them silently rather than erroring when the token
lacks it, so read the returned `labels` back if they matter.

### `update_issue`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `issue_number` | int | - | Issue number |
| `title` | str | none | Replacement title. Omit to leave it alone |
| `body` | str | none | Replacement body in Markdown. Omit to leave it alone |
| `labels` | list[str] | none | Replacement label set. Omit to keep the current labels |
| `state` | str | none | `open` or `closed`. Omit to leave the state alone |

Returns `IssueData`.

Only the fields you supply are sent, so a call that passes `state="closed"` and
nothing else closes the issue and changes nothing else. A call that supplies
none of the four fields is rejected with a validation error.

Two things still overwrite rather than merge:

- `labels` replaces the whole set, so `[]` strips every label including `mcp`. Unlike `create_issue`, this tool does not re-add `mcp`
- A `title` or `body` you did not read first overwrites the current text

This tool cannot change the milestone. Use `set_issue_milestone`.

### `get_issue`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `issue_number` | int | - | Issue number |

Returns `IssueData`, the same shape `create_issue` and `update_issue` return.

This is the only read that gives you an issue's body and assignees. The two
listing tools return a trimmed search shape without either, and they go through
GitHub's search index, which lags behind a write by up to a minute. `get_issue`
reads the issue itself, so it sees a change straight away.

Open or closed makes no difference. A number belonging to a pull request is
rejected with a validation error, since GitHub serves both from this path and
the result would describe a PR as an issue. Use `get_pr_content` for those.

### `list_open_issues_prs`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | Organisation, username, or repo owner depending on `filtering` |
| `repo_name` | str | `""` | Repository name. Required when `filtering="repo"`, ignored otherwise |
| `issue` | str | `pr` | `pr` for pull requests, `issue` for issues |
| `filtering` | str | `involves` | One of `involves`, `user`, `org`, `repo` |
| `per_page` | int | `50` | Results per page, 1 to 100 |
| `page` | int | `1` | Page number |

Returns `{"total": int, "open_prs" | "open_issues": [...]}`, where the list key
follows the `issue` argument. Each entry carries `url`, `title`, `number`,
`state`, `created_at`, `updated_at`, `author`, `label_names` and `is_draft`.

Only open items are returned, since the search is hardcoded to `is:open`.

`is_draft` here is the one place the server exposes draft status, so use this
tool when a review or merge decision depends on it.

### `search_issues_prs`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | str | - | Terms and qualifiers, e.g. `rate limit repo:owner/name is:closed` |
| `per_page` | int | `50` | Results per page, 1 to 100 |
| `page` | int | `1` | Page number |

Returns `{"total": int, "incomplete_results": bool, "items": [...]}`, each item
in the same shape `list_open_issues_prs` returns.

The query is yours, so nothing is scoped for you. Without a `repo:` or `org:`
qualifier the search runs across all of GitHub. `total` counts every match, not
the page, so page through when it exceeds `per_page`.

Useful qualifiers: `repo:owner/name`, `org:name`, `is:issue`, `is:pr`,
`is:open`, `is:closed`, `is:merged`, `author:login`, `assignee:login`,
`label:"needs triage"`, `created:>2026-01-01`, `updated:<2026-06-01`,
`in:title`. Search is rate limited separately at 30 requests a minute.

### `list_milestones`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `state` | str | `open` | One of `open`, `closed`, `all` |
| `per_page` | int | `50` | Results per page, 1 to 100 |
| `page` | int | `1` | Page number |

Returns `{"total": int, "state": str, "milestones": [...]}`. Each milestone
carries `number`, `title`, `description`, `state`, `due_on`, `open_issues`,
`closed_issues` and `html_url`, so the issue counts tell you what is left
without listing the issues themselves.

### `create_milestone`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `title` | str | - | Milestone title, e.g. `v2.0` |
| `description` | str | `""` | What the milestone covers |
| `due_on` | str \| None | `None` | Due date as ISO 8601, e.g. `2026-12-31T23:59:59Z` |
| `state` | str | `open` | `open` or `closed` |

Titles are unique within a repository, so reusing one fails rather than
returning the existing milestone. Call `list_milestones` first if you are not
sure whether it is already there.

GitHub keeps only the date part of `due_on` and returns it as midnight UTC, so
`2026-12-31T23:59:59Z` reads back as `2026-12-31T00:00:00Z`. The milestone is
still due on that day.

### `update_milestone`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `title` | str | - | Title of the milestone to change |
| `new_title` | str \| None | `None` | Replacement title |
| `description` | str \| None | `None` | Replacement description |
| `due_on` | str \| None | `None` | Replacement due date as ISO 8601 |
| `state` | str \| None | `None` | `closed` to close it, `open` to reopen |

Only the fields you pass are sent, so closing a milestone leaves its title and
due date alone. A call supplying nothing to change is rejected. The `title`
argument identifies the milestone and `new_title` renames it, so passing
`title` alone changes nothing.

### `set_issue_milestone`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `issue_number` | int | - | Issue number |
| `milestone` | str \| None | `None` | Milestone title to file it under. Omit to take it off |

Returns `IssueData`, whose `milestone` field reads back the title so you can
confirm it landed. This is a separate tool rather than an argument on
`update_issue` because clearing a milestone means sending an explicit null,
and `update_issue` drops every argument left unset.

Milestones are addressed by title here and by number in the GitHub API, so a
title that matches nothing fails with a not-found error naming it. The lookup
covers closed milestones as well as open ones.

### `list_repo_labels`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `per_page` | int | `50` | Results per page, 1 to 100 |
| `page` | int | `1` | Page number |

Returns `{"total": int, "labels": [{"name", "description", "color"}]}`, where
`total` counts the labels on the page returned, not the repository total.
`description` is `null` for a label that has none.

Returns every label the repository defines, not only those in use. Reading
labels needs no more access than reading the repository.

## Filtering Guide

`filtering` selects the GitHub search qualifier, which changes what
`repo_owner` means.

| Filter | Qualifier | Returns |
|---|---|---|
| `involves` | `involves:<repo_owner>` | Items that user authored, is assigned, is mentioned in, or reviewed, across all of GitHub |
| `user` | `user:<repo_owner>` | Items in repositories owned by that user |
| `org` | `org:<repo_owner>` | Items in repositories belonging to that organisation |
| `repo` | `repo:<repo_owner>/<repo_name>` | Items in one repository. `repo_name` is required and the call fails without it |

Use `repo` for a duplicate check and `involves` for "what is on my plate".

## Title Convention

Issue titles share one shape with PR titles and commit subjects:

```
<type>(<scope>): <short prose summary>
```

| Part | Rules |
|---|---|
| `<type>` | One of `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `perf`, `ci`, `build` |
| `<scope>` | Lowercase area touched: `auth`, `tools`, `deps`, `cache`, `skills`, `readme`, `k8s`. Use `/` for a compound scope such as `docker/k8s` |
| Summary | Prose, not a slug. Lowercase start, imperative mood, no trailing full stop, roughly 72 characters or fewer |

Examples:

- `fix(cache): redis client leaks connections after reconnect`
- `feat(auth): support GitHub App installation tokens`
- `chore(deps): bump fastmcp-slim to 3.4.7`
- `docs(readme): document the skill:// resource URIs`

Avoid bare titles such as `Update README`, bracketed prefixes such as
`[BUG] cache broken`, a kebab-case slug where prose belongs such as
`fix(cache): redis-connection-leak`, and a type with no scope such as
`fix: cache broken`.

## Best Practices

- Search for duplicates before creating an issue: `list_open_issues_prs` with `filtering="repo"` for the open ones, `search_issues_prs` when a closed one would also count
- Read a known issue with `get_issue` rather than searching for it, since search omits the body and lags a write
- Scope every `search_issues_prs` query with `repo:` or `org:`, or it searches all of GitHub
- Call `list_repo_labels` before writing labels rather than guessing names, since GitHub creates a new label for a name that does not exist
- Title every issue as `<type>(<scope>): <prose summary>`, see Title Convention above
- Keep the type honest: `fix` for defects, `feat` for new behaviour, `chore` for maintenance
- Write bodies in Markdown, with steps to reproduce for a bug or acceptance criteria for a feature
- Pass `update_issue` only the fields you are changing, and read the current text before replacing a `title` or `body`
- Include `mcp` in the `labels` you send to `update_issue`, since the list you send replaces the whole set
- Close issues with `state="closed"` rather than deleting them
- File an issue under a milestone as you create it, since `create_issue` takes the title directly
- Read `open_issues` from `list_milestones` to see what a milestone has left, rather than listing and counting issues
- Reference the resolving PR in the body, e.g. `Resolved by #123`
