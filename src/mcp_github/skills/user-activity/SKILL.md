---
description: Look up a GitHub user's profile and retrieve their contribution activity with optional date and repository filtering
---

# User Activity

Retrieve a GitHub user's profile information and their contribution history (commits, PRs, issues, reviews).

## Prerequisites

- Target GitHub `username`
- GitHub token with `read:user` scope (GraphQL API)

## Workflow

1. **Look up the user profile** — call `search_user` to get bio, organisation membership, pinned repos, and follower counts
2. **Retrieve contributions** — call `get_user_activities` to get a detailed breakdown of their activity, optionally filtered by org, repo, or date range

## Tool Parameters

### `search_user`

| Parameter | Type | Description |
|---|---|---|
| `username` | str | GitHub username to look up |

Returns: `UserSearchResult` — login, name, bio, company, location, public repos count, followers/following, organisation memberships, pinned repositories.

### `get_user_activities`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `username` | str | — | GitHub username |
| `org` | str | `""` | Filter by organisation name (optional) |
| `repo` | str | `""` | Filter by repository name (optional) |
| `since` | str | `""` | Start date in ISO 8601 format: `YYYY-MM-DD` |
| `until` | str | `""` | End date in ISO 8601 format: `YYYY-MM-DD` |
| `max_results` | int | `50` | Maximum number of contribution entries to return |

Returns: `UserActivityResult` — lists of commits, pull requests, issues, PR reviews, and the user's repos with current star counts.

## Activity Types Returned

| Type | Fields |
|---|---|
| Commits | `commitCount`, `url`, `occurredAt` |
| Pull Requests | PR title, state, URL, creation date |
| Issues | Issue title, state, URL, creation date |
| PR Reviews | Review state (`APPROVED`, `CHANGES_REQUESTED`, `COMMENTED`), PR URL |
| Repo Stars | `repo`, `owner`, `url`, `description`, `star_count` |

## Answering "Which repos gained the most stars in the last N days?"

Use `get_repo_stars_since` — not `get_user_activities`. It paginates the GitHub stargazers REST endpoint (which carries `starred_at` timestamps) from the most recent page backwards to count stars received within the window.

```
get_repo_stars_since(username="saidsef", since="2024-04-01", top_n=5)
```

Returns repos sorted by `new_stars` (stars received since `since`) descending, with `total_stars` for context. `since` defaults to 30 days ago if omitted.

## Known Limitation: repo_stars in get_user_activities Is Cumulative

`repo_stars` in `get_user_activities` returns each repo's **current total star count**, not stars gained within `since`/`until`. Use `get_repo_stars_since` instead when the question is about a time window.

## Date Filtering

- Accepted formats: `YYYY-MM-DD` (e.g. `2024-01-01`) or full ISO 8601 (`2024-01-01T00:00:00Z`)
- Date-only values are automatically expanded: `since` becomes `T00:00:00Z`, `until` becomes `T23:59:59Z`
- `since` is inclusive (contributions on or after this date)
- `until` is inclusive (contributions on or before this date)
- Omit both to retrieve the most recent contributions up to `max_results`
- Date filtering applies to commits, PRs, issues, and reviews only — not to `repo_stars`

### `get_repo_stars_since`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `username` | str | — | GitHub username |
| `since` | str | 30 days ago | Start date: `YYYY-MM-DD` or ISO 8601 |
| `top_n` | int | `5` | Number of top repos to return |
| `max_repos` | int | `20` | Max repos to check (one REST call per repo) |

Returns: `RepoStarsSinceResult` — repos sorted by `new_stars` desc, each with `repo`, `owner`, `url`, `description`, `new_stars`, `total_stars`.

## Best Practices

- Call `search_user` first — it confirms the user exists and provides context before fetching activity
- Use `org` and `repo` filters together to scope activity to a specific project
- Set `max_results` conservatively (50–100) for broad date ranges to avoid large payloads
- Use date ranges when investigating contributions during a specific sprint or quarter
- PR review state `APPROVED` means the user approved that PR; `CHANGES_REQUESTED` means they blocked it
