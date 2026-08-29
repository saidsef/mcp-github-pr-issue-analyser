---
description: Create, update, list and search GitHub issues and PRs, list a repository's labels, and check for duplicates
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
3. Call `create_issue` with a title, body and labels
4. The `mcp` label is appended automatically, so do not pass it yourself

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

## Tool Parameters

### `create_issue`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `title` | str | - | Issue title, see Title Convention |
| `body` | str | - | Issue description in Markdown |
| `labels` | list[str] | - | Labels to apply. Required, pass `[]` for none |

Returns `IssueData` with `number`, `title`, `body`, `state`, `author`,
`labels`, `html_url`, `created_at`, `updated_at`.

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
- Scope every `search_issues_prs` query with `repo:` or `org:`, or it searches all of GitHub
- Call `list_repo_labels` before writing labels rather than guessing names, since GitHub creates a new label for a name that does not exist
- Title every issue as `<type>(<scope>): <prose summary>`, see Title Convention above
- Keep the type honest: `fix` for defects, `feat` for new behaviour, `chore` for maintenance
- Write bodies in Markdown, with steps to reproduce for a bug or acceptance criteria for a feature
- Pass `update_issue` only the fields you are changing, and read the current text before replacing a `title` or `body`
- Include `mcp` in the `labels` you send to `update_issue`, since the list you send replaces the whole set
- Close issues with `state="closed"` rather than deleting them
- Reference the resolving PR in the body, e.g. `Resolved by #123`
