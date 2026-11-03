---
description: Find and call a GitHub REST or GraphQL endpoint that no other tool covers, searching with github_search_api_routes and sending with github_api_read, github_api_write or github_api_delete
---

# API Requests

Four tools reach any GitHub endpoint that the other tools do not cover.
`github_search_api_routes` finds the REST route for a task. The other three send
the request, one tool per kind of change, so a client can tell a read from a write
from a deletion before anything is sent. They return GitHub's reply as text. The
other tools trim that reply and handle GitHub's quirks, and these four do neither.

## When to use them

Use another tool wherever one covers the task:

| Task | Tool |
|---|---|
| Read a file | `github_get_repository_file` |
| Delete a branch | `github_delete_branch` |
| Commit files to a branch | `github_commit_files` |
| Read workflow runs and job logs | `github_get_workflow_runs`, `github_get_job_log` |
| Label a pull request | `github_update_pr`, since GitHub keeps PR labels on the issues endpoint |
| List an owner's repositories | `github_list_repos`, which picks the user or organisation endpoint itself |

Reach for these four for the rest, such as collaborators, repository settings,
rulesets, webhooks, commit comparisons, or a GraphQL field no tool selects.

## Which tool sends what

| Tool | Kind | REST | GraphQL | Scope |
|---|---|---|---|---|
| `github_search_api_routes` | read | Searches the route list and sends nothing | - | none |
| `github_api_read` | read | `GET` | A query | none |
| `github_api_write` | write | `POST`, `PATCH`, `PUT` | A mutation, unless it deletes or removes something | `repo` |
| `github_api_delete` | destructive | `DELETE` | A mutation that calls a `delete...` or `remove...` field | `repo` |

A grant without `repo` does not list `github_api_write` or `github_api_delete` at all.

Each request tool refuses a call that belongs to another, so its hint stays true.
`github_api_read` refuses any mutation, and `github_api_write` refuses a mutation
that calls a `delete...` or `remove...` field. Some `POST`, `PATCH` and `PUT` routes
still overwrite data, such as `PUT /repos/{owner}/{repo}/topics`, which replaces every
topic. Read the route's summary before calling it.

## Finding the endpoint

### REST

1. Call `github_search_api_routes` with two or three words for the task, such as `add collaborator`, `ruleset` or `webhook delivery`. A singular word matches its plural too
2. Each result gives the `method`, the `path` template, the `summary`, the `docs` page and the `tool` that sends it
3. Fill in the `{placeholders}` in the path. A `{path}`, `{ref}`, `{branch}` or `{basehead}` may hold slashes
4. For a request body, read the `docs` page where the client can fetch web pages. Otherwise send the call and read GitHub's 422 reply, which names each missing or invalid field

When nothing matches, try fewer words, a synonym such as `remove` for `delete` or `list` for `get`, or pass `method`.

### GraphQL

GitHub's GraphQL schema describes itself, and an introspection query is a read.
List the mutations through `github_api_read`, then read the input type of the one
that fits:

```graphql
query { __type(name: "Mutation") { fields { name } } }
```

```graphql
query { __type(name: "CreateIssueInput") { inputFields { name description type { name kind ofType { name } } } } }
```

`__type(name: "Repository")` lists the fields a query can select on a repository in
the same way.

## Workflow

1. Find the endpoint as above
2. Read the current state with `github_api_read`
3. **Before `github_api_write` or `github_api_delete`, tell the user the method, the path and the body, and get an explicit yes in chat.** A request that already names the change, such as "approve PR 12" or "assign octocat to issue 7", is that yes
4. **Before `github_api_delete`, say that the removal cannot be undone**
5. Read the result back with `github_api_read` to confirm the change

Where no user can answer, as in an unattended run, the request that started the run
is the answer. Make the call and report what changed.

## Paths

- Pass the path alone, relative to `https://api.github.com`, such as `repos/octocat/hello-world/collaborators`. A leading `/` is allowed
- Put query parameters in `params`. The tools refuse a path holding `?` or `#`
- The tools refuse a full URL, a `//` and a `..` segment
- A file path or a ref may carry slashes where GitHub accepts them, as in `repos/octocat/hello-world/contents/docs/README.md` or `repos/octocat/hello-world/compare/main...feature`

Every refusal arrives as `VALIDATION_ERROR`, before any request is sent.

## Permissions

The server sends every path and leaves the decision to GitHub, so the token's
scopes or permissions are the only limit.

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

## `github_search_api_routes`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | str | required | Words to find in a route's summary or path |
| `method` | str | `None` | Only routes sent with `GET`, `POST`, `PATCH`, `PUT` or `DELETE` |
| `limit` | int | `20` | Most routes to return |

Returns `{"total": int, "routes": [...]}`, each route carrying `method`, `path`,
`summary`, `docs` and `tool`. `total` counts every match, and `routes` holds the
first `limit` of them. The list comes from GitHub's OpenAPI description when this
server was built. A route GitHub has added since will not show up, though the
request tools still reach it.

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
| `method` | str | `POST` | `POST`, `PATCH` or `PUT`. A GraphQL mutation goes as a POST whatever this says |
| `path` | str | `""` | Path under `https://api.github.com`. Leave it empty with `graphql` |
| `body` | dict or list | `None` | JSON request body |
| `graphql` | str | `None` | A GraphQL mutation to run in place of a REST call |
| `variables` | dict | `None` | Variables for the GraphQL mutation |
| `max_bytes` | int | `131072` | Cap on the body returned |

## `github_api_delete`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `path` | str | `""` | Path under `https://api.github.com`. Leave it empty with `graphql` |
| `body` | dict or list | `None` | JSON request body, which a few routes require, such as deleting a file |
| `graphql` | str | `None` | A GraphQL mutation that deletes or removes something |
| `variables` | dict | `None` | Variables for the GraphQL mutation |
| `max_bytes` | int | `131072` | Cap on the body returned |

`github_api_write` and `github_api_delete` send each call once and never retry it,
since a repeated POST can create the same thing twice. After a timeout or an error,
read the resource back before sending the call again.

## Results

The three request tools return `{"status", "body", "bytes_returned", "bytes_total", "truncated", "has_more"}`.

- `status` is the HTTP status, such as `201` for a created resource or `204` for a delete with an empty body
- `body` is GitHub's reply as text, which is JSON on nearly every route. A GraphQL call returns its `data` object
- `truncated` says `body` stopped at `max_bytes`, and `bytes_total` gives the full size. A cut body is not valid JSON, so ask again with a smaller `per_page` or a narrower GraphQL selection
- `has_more` comes from GitHub's `Link` header, so ask for the next `page` while it is true. A GraphQL connection pages by cursor through `pageInfo { hasNextPage endCursor }`, and `has_more` is always false for one

Errors carry the same codes as every other tool, per the `error-handling` skill.
