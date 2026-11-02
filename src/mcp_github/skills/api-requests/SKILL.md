---
description: Reach a GitHub REST or GraphQL endpoint that no other tool covers, reading with github_api_read and changing with github_api_write
---

# API Requests

`github_api_read` and `github_api_write` send a request to any path under
`https://api.github.com`, or run a GraphQL document. They return GitHub's reply as
text. The other tools trim that reply and handle GitHub's quirks, and these two
do neither.

## When to use them

Use another tool wherever one covers the task:

| Task | Tool |
|---|---|
| Read a file or a directory | `github_get_repository_file`, `github_list_repository_tree` |
| List or delete branches | `github_list_branches`, `github_delete_branch` |
| Commit files to a branch | `github_commit_files` |
| Read workflow runs and job logs | `github_get_workflow_runs`, `github_get_job_log` |
| Label a pull request | `github_update_pr`, since GitHub keeps PR labels on the issues endpoint |
| List an owner's repositories | `github_list_repos`, which picks the user or organisation endpoint itself |

Reach for these two for the rest, such as collaborators, repository settings,
rulesets, webhooks, commit comparisons, or a GraphQL field no tool selects.

## Workflow

1. Look the endpoint up in the [REST reference](https://docs.github.com/en/rest) or the [GraphQL reference](https://docs.github.com/en/graphql)
2. Read with `github_api_read`
3. **Before any `github_api_write`, tell the user the method, the path and the body, and get an explicit yes in chat. The tool can make changes that no other tool here can undo**
4. Read the result back with `github_api_read` to confirm the change

## Paths

- Pass the path alone, relative to `https://api.github.com`, such as `repos/octocat/hello-world/collaborators`. A leading `/` is allowed
- Put query parameters in `params`. The tools refuse a path holding `?` or `#`
- The tools refuse a full URL, a `//` and a `..` segment
- A file path or a ref may carry slashes where GitHub accepts them, as in `repos/octocat/hello-world/contents/docs/README.md` or `repos/octocat/hello-world/compare/main...feature`

`github_api_read` refuses a GraphQL mutation, which belongs in `github_api_write`.
These refusals arrive as `VALIDATION_ERROR`, before any request is sent.

## Permissions

The server sends every path and leaves the decision to GitHub, so the token's
scopes or permissions are the only limit. `github_api_write` needs the `repo`
scope to appear in the tool list at all.

GitHub answers 404 rather than 403 for most private resources a classic or OAuth
token lacks the scope for. Where GitHub names the scopes it accepts and the token
holds none of them, the error says which scopes the token holds and which the
endpoint needs, as in:

```
Error calling tool 'github_api_read': [NOT_FOUND] HTTP 404: user/gpg_keys: Resource not found, or hidden from a token without the scope to see it. The token holds repo, workflow, and this endpoint needs one of admin:gpg_key, read:gpg_key, write:gpg_key. GitHub said: Not Found
```

Stop and tell the user which scope to grant. A fine-grained token gets the
permission it needs named on a 403 instead. A retry fails the same way until the
token changes.

## `github_api_read`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `path` | str | `""` | Path under `https://api.github.com`. Leave it empty with `graphql` |
| `params` | dict | `None` | Query parameters, such as `per_page` and `page` |
| `graphql` | str | `None` | A GraphQL query to run in place of a REST GET |
| `variables` | dict | `None` | Variables for the GraphQL query |
| `max_bytes` | int | `131072` | Cap on the body returned. `0` asks the size without reading the body |

## `github_api_write`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `method` | str | `POST` | `POST`, `PATCH`, `PUT` or `DELETE`. A GraphQL mutation goes as a POST whatever this says |
| `path` | str | `""` | Path under `https://api.github.com`. Leave it empty with `graphql` |
| `body` | dict or list | `None` | JSON request body |
| `graphql` | str | `None` | A GraphQL mutation to run in place of a REST call |
| `variables` | dict | `None` | Variables for the GraphQL mutation |
| `max_bytes` | int | `131072` | Cap on the body returned |

The tool sends each call once and never retries it, since a repeated POST can
create the same thing twice. After a timeout or an error, read the resource back
before sending the call again.

## Results

Both tools return `{"status", "body", "bytes_returned", "bytes_total", "truncated", "has_more"}`.

- `status` is the HTTP status, such as `201` for a created resource or `204` for a delete with an empty body
- `body` is GitHub's reply as text, which is JSON on nearly every route. A GraphQL call returns its `data` object
- `truncated` says `body` stopped at `max_bytes`, and `bytes_total` gives the full size. A cut body is not valid JSON, so ask again with a smaller `per_page` or a narrower GraphQL selection
- `has_more` comes from GitHub's `Link` header, so ask for the next `page` while it is true. A GraphQL connection pages by cursor through `pageInfo { hasNextPage endCursor }`, and `has_more` is always false for one

Errors carry the same codes as every other tool, per the `error-handling` skill.
