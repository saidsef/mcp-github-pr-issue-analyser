# Tools

The server registers every public method on the GitHub integration that carries an MCP annotation. Read tools make no changes, write tools do, and destructive tools remove something that cannot be brought back. A few long-running read tools are registered as tasks, so the client can poll rather than block.

## Pagination

Every REST list tool takes `per_page` (default 50, maximum 100) and `page`, and returns `count` with `has_more`. `count` is the number of items in the reply, never the size of the result set. `has_more` comes from GitHub's `Link` header, so paging stops on a fact rather than on a guess from a full page.

`total` appears only where GitHub reports a genuine count: `github_list_open_issues_prs` and `github_search_issues_prs` report the matches found, and `github_list_project_items` reports the cards on the board. A tool without `total` cannot answer how many there are without paging to the end.

`github_list_project_items` reads a GraphQL connection, which pages by cursor, so it takes `after` and returns `next_cursor` in place of `page`.

## Pull requests

| Tool | Kind | Description |
|------|------|-------------|
| `github_get_pr_diff` | read | Retrieve the diff or patch for a PR, capped at `max_bytes` and reporting the full size |
| `github_get_pr_content` | read | PR title, description, author, timestamps, state, head SHA, the branches either side and who was asked to review |
| `github_get_pr_linked_issues` | read, task | Issues that auto-close when the PR merges, via GraphQL `closingIssuesReferences` |
| `github_get_pr_status_checks` | read, task | Check run conclusions and legacy commit status for the PR's HEAD commit |
| `github_create_pr` | write | Open a PR with title, body, head and base branch, a draft option and labels |
| `github_update_pr` | write | Change any subset of a PR's title, body, state, base branch and labels |
| `github_set_pr_draft` | write | Mark a draft ready for review, or return a PR to draft, via GraphQL |
| `github_update_pr_branch` | write | Update the PR branch with the latest base branch |
| `github_merge_pr` | write | Merge using the merge, squash or rebase method, once the checks pass and with a `commit_title` |
| `github_add_pr_comments` | write | Post a general comment on the PR thread |
| `github_add_inline_pr_comment` | write | Comment on a line or a range in a PR's files, either side of the diff |
| `github_list_pr_comments` | read | Conversation or inline comments already on a PR, inline ones with their file, line and side |
| `github_update_pr_comment` | write | Rewrite a comment already posted |
| `github_reply_to_review_comment` | write | Reply on an existing review thread |
| `github_list_pr_reviews` | read | Reviews submitted on a PR, each with its author, verdict and timestamp |
| `github_submit_review` | write | Approve, request changes, or comment as a review |
| `github_set_assignees` | write | Assign or update users on a PR or issue |

## Issues, labels and milestones

| Tool | Kind | Description |
|------|------|-------------|
| `github_get_issue` | read | One issue by number, with its body, labels, assignees and milestone |
| `github_create_issue` | write | Open an issue with title, body, labels and an optional milestone |
| `github_update_issue` | write | Update any subset of an existing issue's title, body, labels or state |
| `github_list_open_issues_prs` | read | List open PRs or issues for a user or organisation |
| `github_search_issues_prs` | read | Free-text and qualifier search across issues and PRs, closed ones included |
| `github_list_repo_labels` | read | Name, description and colour of every label a repository defines |
| `github_list_milestones` | read | Open, closed or all milestones, with the count of issues in each |
| `github_create_milestone` | write | Open a milestone with a description and a due date |
| `github_update_milestone` | write | Rename a milestone, move its due date, or close it |
| `github_set_issue_milestone` | write | File an issue under a milestone, or take it off one |

## Repository contents

| Tool | Kind | Description |
|------|------|-------------|
| `github_get_repository_file` | read | One file at a branch, tag or SHA, windowed by `offset` and `limit` |
| `github_list_repository_tree` | read | Entries of a directory, one level deep or every level |

## Tags and releases

| Tool | Kind | Description |
|------|------|-------------|
| `github_get_latest_sha` | read | The newest commit SHA on a branch, tag or SHA, defaulting to the default branch |
| `github_create_tag` | write | Tag a commit, a named one or the latest, annotated when given a message |
| `github_create_release` | write | Publish a release with a changelog, refusing a tag that already has one unless `if_exists="update"` |
| `github_list_releases` | read | A repository's releases, newest first |
| `github_get_release` | read | One release, by tag or the latest published |
| `github_update_release` | write | Change a published release's title, notes, draft or prerelease state |
| `github_list_tags` | read | A repository's tags and the commit each points at |
| `github_delete_release` | destructive | Remove a release, keeping its tag unless asked otherwise, which skips the `github_delete_tag` force check |
| `github_delete_tag` | destructive | Remove a tag, refused while a release points at it unless forced |

## Project boards

Projects (v2) has no REST surface, so every tool here goes through GraphQL. The token needs a Projects grant separate from `repo`: `read:project` to read, `project` to write.

| Tool | Kind | Description |
|------|------|-------------|
| `github_get_project_fields` | read | A board's fields and the options each single-select one accepts |
| `github_list_project_items` | read | What is on a board, each card with its field values |
| `github_add_to_project` | write | Put an issue or pull request on a board |
| `github_set_project_field` | write | Set a single-select field such as Status, by field and option name |
| `github_remove_from_project` | destructive | Take a card off a board, leaving the issue open |

## Users and activity

| Tool | Kind | Description |
|------|------|-------------|
| `github_list_repos` | read | Repositories for a user, an organisation, or the caller, private ones included |
| `github_search_user` | read, task | Fetch a user's profile via GraphQL |
| `github_get_user_activities` | read, task | Commit, PR, issue and review contributions, filtered by org, repo or date |
| `github_get_repo_stars_since` | read, task | Repositories owned by a user that gained the most stars since a given date, with a `truncated` flag when the repo listing or a repo's star history was cut short |

## Interactive UI

| Tool | Kind | Description |
|------|------|-------------|
| `choose` | - | Ask the user to pick from a set of options |
| `github_pr_issue_analyser_ui` | - | Render results as a generated UI panel |
| `github_search_prefab_components` | - | Look up the UI components available to that panel |

## Skills

Workflow guidance ships with the server. `github_list_skills` and `github_get_skill` reach it with tool support alone, and the same content is served as MCP resources under the `skill://` URI scheme for clients that read resources. [Configuration](./configuration.md#skills) covers which path a given client gets.

Every tool above names the skill that documents it, at the end of its own description and in its `_meta`, so the guidance is reachable without listing the skills first.

| Tool | Kind | Description |
|------|------|-------------|
| `github_list_skills` | read | Every bundled skill, with its name, description and `skill://` URI |
| `github_get_skill` | read | One skill in full, by the name `github_list_skills` reports |

| Resource | Covers |
|----------|--------|
| `skill://pr-analysis/SKILL.md` | Fetch a PR's metadata, diff, linked issues and CI status |
| `skill://pr-review/SKILL.md` | Post inline comments and submit review decisions |
| `skill://pr-management/SKILL.md` | Create, update, assign, refresh and merge PRs |
| `skill://issue-management/SKILL.md` | Create, update, list and search issues and PRs, list labels, and run milestones |
| `skill://release-management/SKILL.md` | Tag commits, publish releases, and correct or withdraw what is published |
| `skill://project-boards/SKILL.md` | Place issues on a project board, set their fields, and read a board |
| `skill://user-activity/SKILL.md` | Find repositories, and look up user profiles, contributions and star growth |
| `skill://error-handling/SKILL.md` | Read the error codes and decide whether to retry |
| `skill://interactive-ui/SKILL.md` | Ask the user to choose, or render data as a UI panel |
