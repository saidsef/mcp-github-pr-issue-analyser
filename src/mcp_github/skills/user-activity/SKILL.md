---
description: Find a user's or organisation's repositories, and look up a profile, contribution history, and which repos gained stars recently
---

# User Activity

Find the repositories an account owns, then retrieve a user's profile, their contributions
(commits, PRs, issues, reviews), and star growth per repository.

## Prerequisites

- The target GitHub `username`, or the owner whose repositories you want
- GitHub token with `read:user` scope, since most of these tools use the GraphQL API

`search_user`, `get_user_activities` and `get_repo_stars_since` run as long-running
tasks and report progress while they work. `list_repos` is a plain read.

## Workflow

1. **Find the repository** - call `list_repos` when you do not already know the name
2. **Look up the profile** - call `search_user` to confirm the user exists and get context
3. **Retrieve contributions** - call `get_user_activities`, optionally filtered by org, repo or date range
4. **Measure star growth** - call `get_repo_stars_since` when the question is about stars in a time window

## `list_repos`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `owner` | str | `""` | User or organisation. Omit for the caller's own |
| `sort` | str | `updated` | One of `updated`, `pushed`, `created`, `full_name` |
| `per_page` | int | `30` | Results per page, 1 to 100 |
| `page` | int | `1` | Page number |

Returns `{"total": int, "repos": [...]}`, each entry carrying `name`, `owner`,
`description`, `default_branch`, `private`, `fork`, `archived`, `pushed_at`
and `html_url`. `total` counts the page returned, not everything the owner has.

You do not say whether the owner is a person or an organisation, because the
tool reads the account type and picks the endpoint itself. That matters:
GitHub's organisation endpoint returns 404 for a person, and its user endpoint
accepts an organisation but returns only its public repositories.

Omitting `owner` is the only way to see private repositories you own. Passing
your own username instead reads the public listing and quietly leaves them out.
An organisation's private and internal repositories do appear, for a token
entitled to see them.

An owner that is neither a user nor an organisation fails with a not-found
error naming it.

## `search_user`

| Parameter | Type | Description |
|---|---|---|
| `username` | str | GitHub username to look up |

Returns `UserSearchResult`: `login`, `name`, `email`, `company`, `location`,
`bio`, `url`, `avatar_url`, `created_at`, `updated_at`, `followers`,
`following`, `public_repos`, `recent_repos`, `organizations`.

`recent_repos` is the 10 most recently updated public repositories, not the
user's pinned ones. Public repositories only.

## `get_user_activities`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `username` | str | - | GitHub username |
| `org` | str | `""` | Keep only contributions in repos owned by this org or user |
| `repo` | str | `""` | Keep only contributions in repos with this name |
| `since` | str | `""` | Start date, `YYYY-MM-DD` or full ISO 8601 |
| `until` | str | `""` | End date, `YYYY-MM-DD` or full ISO 8601 |
| `max_results` | int | `50` | Cap applied to each section separately |

Returns `UserActivityResult`: `username`, `date_range`, `total_contributions`,
`commits`, `pull_requests`, `issues`, `reviews`, `repo_stars`.

| Section | Fields per entry |
|---|---|
| `commits` | `repo`, `owner`, `commit_count`, `url`, `date` |
| `pull_requests` | `repo`, `owner`, `number`, `title`, `state`, `url`, `created`, `merged` |
| `issues` | `repo`, `owner`, `number`, `title`, `state`, `url`, `created` |
| `reviews` | `repo`, `owner`, `pr_number`, `pr_title`, `pr_url`, `review_state`, `review_url`, `date` |
| `repo_stars` | `repo`, `owner`, `url`, `description`, `star_count` |

`review_state` is `APPROVED`, `CHANGES_REQUESTED` or `COMMENTED`. The `date` on
a commit or review entry is when the contribution occurred, whereas `created`
on a pull request or issue is when that item was opened.

Four things about this tool are easy to get wrong:

- **`max_results` is per section.** It caps commits, pull requests, issues, reviews and repo_stars independently, so `max_results=50` can return up to 250 entries
- **`total_contributions` is not filtered.** It reports the account-wide totals for the period, so it will exceed the length of the returned lists whenever `org`, `repo` or `max_results` trims them. Do not present it as a count of the listed items
- **`org` and `repo` do not touch `repo_stars`**, which always lists the user's own top public repos by star count
- **The window cannot exceed one year.** GitHub's contributions API rejects a `since`/`until` range longer than that. Split a longer question into per-year calls

### Date filtering

- Accepts `YYYY-MM-DD` or full ISO 8601 such as `2024-01-01T00:00:00Z`
- A date-only value is expanded: `since` gains `T00:00:00Z`, `until` gains `T23:59:59Z`
- Both bounds are inclusive
- Omit both to get the most recent contributions up to `max_results`
- Filtering applies to commits, PRs, issues and reviews, never to `repo_stars`

## `get_repo_stars_since`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `username` | str | - | GitHub username |
| `since` | str | 30 days ago | Start date, `YYYY-MM-DD` or ISO 8601 |
| `top_n` | int | `5` | Number of repos to return |
| `max_repos` | int | `20` | Maximum repos to inspect |

Returns `RepoStarsSinceResult`: `username`, the normalised `since` cutoff,
`repos` sorted by `new_stars` descending, each with `repo`, `owner`, `url`,
`description`, `new_stars` and `total_stars`, and `truncated`.

`truncated` is `True` when the account has more public repos than the listing
could read, 500 being the ceiling. The answer is then built on a subset and may
miss a repo that gained stars, so say so rather than reporting it as complete.

This is the tool for any question of the form "which repos gained the most
stars recently".

```
get_repo_stars_since(username="saidsef", since="2024-04-01", top_n=5)
```

How it picks and counts:

- Reads the user's public repos, up to 5 pages of 100, drops any with zero stars, then inspects the `max_repos` with the highest total star count
- For each, it walks the stargazers endpoint backwards from the last page and stops at the first star older than the cutoff
- Repos that gained no stars in the window are omitted rather than returned with `new_stars: 0`

Cost scales with how popular the repos are, since a repo with 5,000 stars can
need many pages before the walk stops. Keep `max_repos` low on accounts with
large repositories.

## Star Counts: Which Tool to Use

`repo_stars` in `get_user_activities` is each repo's **current cumulative
total** and ignores `since` and `until`. GitHub does not expose per-period
deltas there. Use `get_repo_stars_since` whenever the question is about a time
window, and `get_user_activities` only for a snapshot of where a user stands
today.

## Best Practices

- Call `list_repos` rather than guessing a repository name, and omit `owner` when you want your own private ones
- Call `search_user` first, since it confirms the user exists and gives context before the heavier calls
- Combine `org` and `repo` to scope activity to one project
- Keep `max_results` at 50 to 100 for wide date ranges, remembering the cap is per section
- Use date ranges when investigating a specific sprint or quarter, and split anything over a year
- Quote `total_contributions` as the account-wide figure, not as a count of the entries listed
- A review state of `APPROVED` means the user approved that PR, and `CHANGES_REQUESTED` means they blocked it
