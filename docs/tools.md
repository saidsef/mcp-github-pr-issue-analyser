# Tools

The server registers every public method on the GitHub integration that carries an MCP annotation. Read-only tools make no changes; write tools do; destructive tools remove something that cannot be brought back. A few long-running read tools are registered as tasks, so the client can poll rather than block.

## Pull requests

| Tool | Kind | Description |
|------|------|-------------|
| `get_pr_diff` | read | Retrieve the diff or patch for a PR, capped at `max_bytes` and reporting the full size |
| `get_pr_content` | read | PR title, description, author, timestamps and state |
| `get_pr_linked_issues` | read, task | Issues that auto-close when the PR merges, via GraphQL `closingIssuesReferences` |
| `get_pr_status_checks` | read, task | Check run conclusions and legacy commit status for the PR's HEAD commit |
| `create_pr` | write | Open a PR with title, body, head and base branch, and a draft option |
| `update_pr` | write | Change any subset of a PR's title, body, state and base branch |
| `update_pr_description` | write | Change the title and body of a PR together |
| `set_pr_draft` | write | Mark a draft ready for review, or return a PR to draft, via GraphQL |
| `update_pr_branch` | write | Update the PR branch with the latest base branch |
| `merge_pr` | write | Merge using the merge, squash or rebase method |
| `add_pr_comments` | write | Post a general comment on the PR thread |
| `add_inline_pr_comment` | write | Comment on specific lines of a PR's files |
| `list_pr_comments` | read | Conversation or inline comments already on a PR, inline ones with their file and line |
| `update_pr_comment` | write | Rewrite a comment already posted |
| `reply_to_review_comment` | write | Reply on an existing review thread |
| `update_reviews` | write | Approve, request changes, or comment as a review |
| `update_assignees` | write | Assign or update users on a PR or issue |

## Issues and labels

| Tool | Kind | Description |
|------|------|-------------|
| `create_issue` | write | Open an issue with title, body and labels |
| `update_issue` | write | Update any subset of an existing issue's title, body, labels or state |
| `list_open_issues_prs` | read | List open PRs or issues for a user or organisation |
| `search_issues_prs` | read | Free-text and qualifier search across issues and PRs, closed ones included |
| `list_repo_labels` | read | Name, description and colour of every label a repository defines |

## Tags and releases

| Tool | Kind | Description |
|------|------|-------------|
| `get_latest_sha` | read | The latest commit SHA on a repository's default branch |
| `create_tag` | write | Tag a commit, a named one or the latest, annotated when given a message |
| `create_release` | write | Publish a release with a changelog, updating one that already exists for the tag |
| `list_releases` | read | A repository's releases, newest first |
| `get_release` | read | One release, by tag or the latest published |
| `update_release` | write | Change a published release's title, notes, draft or prerelease state |
| `list_tags` | read | A repository's tags and the commit each points at |
| `delete_release` | destructive | Remove a release, keeping its tag unless asked otherwise |
| `delete_tag` | destructive | Remove a tag, refused while a release points at it unless forced |

## Users and activity

| Tool | Kind | Description |
|------|------|-------------|
| `search_user` | read, task | Fetch a user's profile via GraphQL |
| `get_user_activities` | read, task | Commit, PR, issue and review contributions, filtered by org, repo or date |
| `get_repo_stars_since` | read, task | Repositories owned by a user that gained the most stars since a given date, with a `truncated` flag when the repo listing was cut short |

## Interactive UI

| Tool | Kind | Description |
|------|------|-------------|
| `choose` | - | Ask the user to pick from a set of options |
| `github_pr_issue_analyser_ui` | - | Render results as a generated UI panel |
| `search_prefab_components` | - | Look up the UI components available to that panel |

## Skills

Workflow guidance ships with the server as MCP resources under the `skill://` URI scheme. Clients that support skills load them on demand, so the model gets the procedure for a task rather than a bare tool list.

| Resource | Covers |
|----------|--------|
| `skill://pr-analysis/SKILL.md` | Fetch a PR's metadata, diff, linked issues and CI status |
| `skill://pr-review/SKILL.md` | Post inline comments and submit review decisions |
| `skill://pr-management/SKILL.md` | Create, update, assign, refresh and merge PRs |
| `skill://issue-management/SKILL.md` | Create, update, list and search issues and PRs, and list labels |
| `skill://release-management/SKILL.md` | Tag commits, publish releases, and correct or withdraw what is published |
| `skill://user-activity/SKILL.md` | Look up user profiles, contributions and star growth |
| `skill://error-handling/SKILL.md` | Read the error codes and decide whether to retry |
| `skill://interactive-ui/SKILL.md` | Ask the user to choose, or render data as a UI panel |
