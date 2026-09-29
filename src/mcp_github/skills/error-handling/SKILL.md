---
description: Read the error codes these tools raise and decide whether to retry, fix the call, or stop
---

# Error Handling

Every tool in this server fails through one small set of typed errors. The code in the message tells you which of the three responses is right: back off, correct the call, or stop and tell the user.

## Reading an Error

Failures arrive as a `ToolError` carrying a bracketed code:

```
[NOT_FOUND] HTTP 404: PR #4321: Resource not found GitHub said: Not Found
```

The code is written `[<CODE>] HTTP <status>: ` when a status is known and
`[<CODE>] ` when it is not. Read the code first, since the prose after it
varies with the call and is not stable enough to match on.

The code does not always start the message. A REST call that failed opens with
it, as above. An authentication failure, and anything a GraphQL-backed step
raised, arrive wrapped once more and open with the tool name instead:

```
Error calling tool 'github_get_pr_content': [AUTH_FAILED] HTTP 401: PR #1: Authentication failed. Check your GitHub token. GitHub said: Bad credentials
```

Search the message for the code rather than reading it off the front.

What follows the code has three parts. The call that failed comes first, such
as `PR #4321`. The server's own reading of the status comes next. Whatever
GitHub said about the refusal comes last, after `GitHub said: `, and a 422
appends its field-level errors as `<field>: <message>` after that. The last part
is absent when GitHub sent no body or sent one that is not JSON.

Report the `GitHub said` text to the user, since it names the cause where the
codes only name the class.

The GraphQL-backed tools label a failed step, so their messages read
`[GITHUB_API_ERROR] Failed to <action>: <original error>`. The action names the
step that failed, such as `fetch user activities` or `fetch status checks`.

Which codes survive that labelling is a question of transport. An error raised
inside the step itself, by the tool or by the GraphQL mapper, keeps its own code
where it is `NOT_FOUND` or `AUTH_FAILED`, since the labelling re-raises those
two untouched. `github_search_user` raises one against its own result for a name
nobody holds:

```
Error calling tool 'github_search_user': [NOT_FOUND] HTTP 404: User 'nobody' not found
```

An error the step met on a REST call is labelled even where it carries a code,
because the shared request helper strips the type off everything but an
authentication failure before the labelling sees it. The original code stays in
the text. `github_get_repo_stars_since` on a name nobody holds reports the same
missing account under a different code:

```
Error calling tool 'github_get_repo_stars_since': [GITHUB_API_ERROR] Failed to fetch repo stars: [NOT_FOUND] HTTP 404: repos for nobody: Resource not found GitHub said: Not Found
```

Read the innermost code in a message that carries two. A rate limit met inside
one of these steps arrives the same way whichever transport carried it, as
`RATE_LIMITED` inside `GITHUB_API_ERROR`.

## The Codes

| Code | Status | Means | Do |
|---|---|---|---|
| `AUTH_FAILED` | 401 | The token is missing, expired or revoked | Stop. Tell the user to re-authenticate or replace the token. Retrying cannot help |
| `AUTH_FAILED` | 401 | A GraphQL call needs a scope the token lacks, and the message names it | Stop. Tell the user to grant that scope and authorise again. Re-authenticating on its own changes nothing |
| `RATE_LIMITED` | 403 | A rate limit GitHub reported as a 403 is exhausted | Wait for the reset, then retry the same call unchanged |
| `NOT_FOUND` | 404 | The resource is absent, or the token cannot see it | Check the owner, repo and number. Do not retry unchanged |
| `VALIDATION_ERROR` | 422 | GitHub rejected the arguments | Fix the arguments. Retrying unchanged fails again |
| `GITHUB_API_ERROR` | 403, 429 or other | Permission denied, a secondary rate limit sent as a 429, or any status not listed above | Read the message. Permission problems need a token or approval change, not a retry |

Four of these are easy to misread.

**`AUTH_FAILED` covers two fixes, not one.** A revoked or expired token needs a
fresh authorisation. A token short of a scope needs that scope granted, and
authorising again without it fails in exactly the same way. The message
separates the two, since the scope form names the scope and the field that
wanted it. GraphQL Failures below covers that form.

**Permission denied shares a code with everything else.** A 403 that is not a
rate limit raises `GITHUB_API_ERROR`, not a code of its own, so the code alone
does not tell you a permission problem from an unexpected 500. Check the status
and the message text. It reads `Refused.` where GitHub named a cause, and
`Permission denied. Check your token permissions.` where GitHub sent no body,
which is the one case the token is the likeliest suspect. A refusal whose text
mentions the `workflow` scope takes a third form, appending guidance of its own,
because GitHub requires that scope for a write to a file under
`.github/workflows` and no permission on the repository substitutes for it.

A refused 403 has several causes and each needs a different fix, so the code and
the status settle nothing on their own. `github_merge_pr` meets all of them.

| GitHub said | Cause | Do |
|---|---|---|
| `Resource protected by organization SAML enforcement` | The token is not authorised for that organisation | Authorise the token for the org under its SSO settings |
| `Resource not accessible by integration` | The App installation lacks the permission the call needs | Grant the permission on the installation |
| `<n> of <m> required status checks have not succeeded`, or a ruleset refusal | Branch protection is refusing this actor | Report it. No token change helps |
| `At least <n> approving review is required` | The PR is short of reviews | Report it. Retrying fails again |

**A 404 does not prove the thing is missing.** GitHub returns 404 rather than
403 for a resource the token cannot see, so a private repository the token
lacks scope for is indistinguishable from a typo in the name. Check the
spelling before concluding the resource does not exist.

**A rate limit does not always arrive as `RATE_LIMITED`.** Only the 403 form is
mapped to that code. GitHub sends the secondary limit as a 429 as well, and that
falls through to `GITHUB_API_ERROR` reading
`GitHub API error (<context>): 429 - Too Many Requests`. Treat a 429 as a rate
limit whatever code it carries.

## GraphQL Failures

The project board tools, `github_search_user`, `github_get_user_activities`,
`github_get_pr_linked_issues`, `github_get_pr_status_checks` and
`github_set_pr_draft` run on GitHub's GraphQL API, and Projects (v2) has no REST
equivalent at all. GraphQL answers HTTP 200 and puts the refusal in an `errors`
array, so the server reads the first entry and maps its `type` onto a code:

| GraphQL `type` | Code | Status the message shows |
|---|---|---|
| `NOT_FOUND`, or any type where the message contains `not found` | `NOT_FOUND` | 404 |
| `RATE_LIMITED` | `RATE_LIMITED` | 403 |
| `FORBIDDEN`, `UNAUTHORIZED` or `INSUFFICIENT_SCOPES` | `AUTH_FAILED` | 401 |
| anything else | `GITHUB_API_ERROR` | none, and the message opens `GraphQL error: ` |

The status in one of these messages is the code's own label. GitHub answered
200, so a GraphQL `HTTP 404` records the mapping and not a status GitHub sent.

The message test runs before the type test, so a `FORBIDDEN` whose message
contains `not found` arrives as `NOT_FOUND`. Read the message rather than
trusting the code to name the type behind it.

`INSUFFICIENT_SCOPES` is the one to watch, since it shares `AUTH_FAILED` with a
revoked authorisation and takes a different fix:

```
Error calling tool 'github_list_projects': [AUTH_FAILED] HTTP 401: The 'projectsV2' field requires ['read:project']. The token is missing a scope this query needs, rather than the authorisation itself. Projects (v2) needs 'read:project' to read and 'project' to write on a classic token, or Projects read and write on a fine-grained one.
```

Ask the user to grant the scope the message names and authorise again. A
revoked authorisation reads differently, ending
`please re-authenticate`, and a fresh OAuth flow is enough for that one.

`NOT_FOUND` and `AUTH_FAILED` reach the client as themselves. Every other
GraphQL code is labelled with the step that raised it, so a board read that hits
a rate limit arrives under two codes:

```
Error calling tool 'github_list_projects': [GITHUB_API_ERROR] Failed to list projects: [RATE_LIMITED] HTTP 403: API rate limit exceeded for user ID 1.
```

The text after `HTTP 403: ` is GitHub's own, so it varies with the account and
the limit that ran out.

## Statuses That Are Not Errors

Four calls name a status the server returns to the tool instead of raising, so
the tool decides what it means and the client sees the decision rather than the
status.

| Tool | Status | What the client gets |
|---|---|---|
| `github_list_repos` | 404 on the account lookup | `[NOT_FOUND] HTTP 404: No user or organisation named '<owner>'`, naming the account rather than the resource |
| `github_get_latest_sha` | 409 on the commit listing | `null`, since a repository with no commits has no SHA |
| `github_create_release` | 422 where the release exists | The release updated under `if_exists='update'`, or a `VALIDATION_ERROR` pointing at `github_update_release` under `if_exists='fail'` |
| `github_get_release`, `github_update_release`, `github_delete_release`, `github_delete_tag` | 404 on the release lookup | `[NOT_FOUND] HTTP 404: No release found for tag '<tag>' in <owner>/<repo>` for the first three, and clearance to proceed for `github_delete_tag` |

A status GitHub sent for any other reason still raises, so none of this is a
blanket suppression. A 422 on `github_create_release` that is not an existing
release reaches you as `VALIDATION_ERROR` in the usual way.

## Rate Limits

A rate limit is the only failure worth retrying on its own, and there are two
of them. The primary limit is a budget of requests per hour, and its message
reads `GitHub API rate limit exceeded`. The secondary limit is a throttle on
writes made in quick succession, and its message reads `GitHub secondary rate
limit hit` where GitHub sent it as a 403 and `429 - Too Many Requests` where
GitHub sent it as a 429. A secondary limit clears in minutes, so the same call
succeeds later without any change.

All three wordings are the server's own and it writes them for a REST status
only. A GraphQL rate limit carries GitHub's message and neither phrase, so
recognise that one by the `RATE_LIMITED` code inside the step label rather than
by the prose. GraphQL keeps a separate budget from REST, which is why a board
call can be refused while a REST read on the same token still works.

Neither message says when the limit resets. The server reads `Retry-After` and
`X-RateLimit-Reset` off the response, and the value does not survive into the
`ToolError` the client receives. Wait a minute or so on a secondary limit, and
longer on a primary one, since the primary window is an hour.

No tool retries or backs off internally. A failed call is simply raised, so any
waiting is yours to do. Do not retry in a tight loop, since every attempt spends
another request against a limit that is already exhausted.

Reading the same thing twice is charged once, under conditions. A repeated read
goes out with `If-None-Match`, GitHub answers `304 Not Modified`, and a 304
costs no rate limit on an authenticated request, which is every request this
server makes. The server caches the tag for GET requests alone, holds at most
`GITHUB_ETAG_CACHE_ENTRIES` of them, 256 by default, and evicts the oldest once
that cap is reached. Setting the variable to 0 turns the cache off and every
read is charged again. A write is never conditional, and neither is a GraphQL
query, which goes out without touching the cache at all.

Within those limits a rate limit means real distinct reads rather than a loop
re-reading one thing. Paging through a long listing is distinct reads, and a
long enough run of them evicts the entries an earlier read left behind.

Cost matters most in `github_get_repo_stars_since`, which reads the repo listing
and then the weekly star history of each repo it inspects. One page of history
covers 30 weeks, so the bill follows `max_repos` and the length of the window
rather than how popular the repos are. Lower `max_repos`, or shorten the window,
before retrying it rather than repeating the same call.

## OAuth Mode

The server runs on a static `GITHUB_TOKEN` or on OAuth, and the mode changes
what two of the errors mean. In OAuth mode the messages say so.

`AUTH_FAILED` gains a line about the authorisation having been revoked, and the
fix is a fresh OAuth flow rather than a new token.

`NOT_FOUND` and the permission-denied form of `GITHUB_API_ERROR` gain a line
about organisation approval. A private organisation repository stays invisible
until an org admin approves the OAuth App under Org Settings, Third-party
access, OAuth App access policy. Surface that to the user, since no retry and
no change of arguments will fix it.

The `workflow` scope guidance takes the place of that line rather than joining
it, so a 403 mentioning the scope reads the same in either mode and says
nothing about org approval. Read the scope guidance as the answer where it
appears, and do not go looking for a missing approval behind it.

## Timeouts

Reading a response is bounded by `GITHUB_API_TIMEOUT`, 5 seconds by default.
Opening the connection is bounded separately by `GITHUB_API_CONNECT_TIMEOUT`,
3 seconds by default. A REST call that hits either surfaces as a `ToolError`
carrying the underlying httpx message rather than one of the codes above.

A GraphQL call takes a different path. The GraphQL step codes the failure as
`GITHUB_API_ERROR` and the step label codes it again, so the message carries the
code twice:

```
[GITHUB_API_ERROR] Failed to <action>: [GITHUB_API_ERROR] GraphQL request failed: <httpx message>
```

The repeated code marks a transport failure rather than an API refusal, so read
the httpx message at the end for the cause.

Large diffs and busy status-check queries are the usual causes, and the
operator raises `GITHUB_API_TIMEOUT` for them. That no longer changes how long
the server waits on a host it cannot reach.

## Best Practices

- Match on the bracketed code, never on the prose after it, and search the whole message for it rather than reading it off the front
- Read the innermost code where a message carries two, since the outer one names the step and the inner one names the failure
- Retry only a rate limit, whether it came through as `RATE_LIMITED` or as a 429 under `GITHUB_API_ERROR`, and wait before retrying
- Treat `AUTH_FAILED` as terminal and hand it back to the user, naming the scope where the message names one, since a fresh login without it fails identically
- Read a status on a GraphQL code as the code's own label, since GitHub answered 200 and sent no status of its own
- Re-read the arguments on `VALIDATION_ERROR` rather than trying the call again
- On `NOT_FOUND`, check the spelling of owner, repo and number before reporting the resource as absent
- Report what failed and what the user needs to do, quoting the `GitHub said` text and not the whole exception
