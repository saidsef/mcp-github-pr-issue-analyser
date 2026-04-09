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

Returns: `UserActivityResult` — lists of commits, pull requests, issues, and PR reviews with counts and URLs.

## Activity Types Returned

| Type | Fields |
|---|---|
| Commits | `commitCount`, `url`, `occurredAt` |
| Pull Requests | PR title, state, URL, creation date |
| Issues | Issue title, state, URL, creation date |
| PR Reviews | Review state (`APPROVED`, `CHANGES_REQUESTED`, `COMMENTED`), PR URL |

## Date Filtering

- Use ISO 8601 format: `YYYY-MM-DD` (e.g. `2024-01-01`)
- `since` is inclusive (contributions on or after this date)
- `until` is inclusive (contributions on or before this date)
- Omit both to retrieve the most recent contributions up to `max_results`

## Best Practices

- Call `search_user` first — it confirms the user exists and provides context before fetching activity
- Use `org` and `repo` filters together to scope activity to a specific project
- Set `max_results` conservatively (50–100) for broad date ranges to avoid large payloads
- Use date ranges when investigating contributions during a specific sprint or quarter
- PR review state `APPROVED` means the user approved that PR; `CHANGES_REQUESTED` means they blocked it
