---
description: Create, update and list GitHub issues and open PRs, with label handling and duplicate checking
---

# Issue Management

Create issues, update existing ones, and list the open issues or PRs in a repo, org or user account.

## Prerequisites

- `repo_owner` and `repo_name` for the target repository
- GitHub token with `repo` write access

## Workflow

### Creating an Issue

1. Call `list_open_issues_prs` with `issue="issue"` and `filtering="repo"` to check for a duplicate
2. Call `create_issue` with a title, body and labels
3. The `mcp` label is appended automatically, so do not pass it yourself

### Updating an Issue

1. Call `update_issue` with the full replacement title, body, labels and state
2. Pass `state="closed"` to close a resolved issue

### Listing Issues and PRs

1. Call `list_open_issues_prs`, choosing `filtering` for the scope you want

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
| `title` | str | - | Replacement title, required |
| `body` | str | - | Replacement body in Markdown, required |
| `labels` | list[str] | `[]` | Replacement label set |
| `state` | str | `open` | `open` or `closed` |

Returns `IssueData`.

Every field is sent on every call, so this is a whole-issue replacement, not a
patch. Two consequences:

- Omitting `labels` sends `[]` and strips every existing label, including `mcp`. Unlike `create_issue`, this tool does not re-add `mcp`
- Passing a `title` or `body` you did not read first overwrites the current text

Read the issue first and pass back the fields you are not changing.

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

- Search for duplicates with `filtering="repo"` before creating an issue
- Title every issue as `<type>(<scope>): <prose summary>`, see Title Convention above
- Keep the type honest: `fix` for defects, `feat` for new behaviour, `chore` for maintenance
- Write bodies in Markdown, with steps to reproduce for a bug or acceptance criteria for a feature
- Read the issue before calling `update_issue` and pass back every field you are not changing
- Include `mcp` in the `labels` you send to `update_issue` if the issue should keep it
- Close issues with `state="closed"` rather than deleting them
- Reference the resolving PR in the body, e.g. `Resolved by #123`
