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
Error calling tool 'get_pr_content': [AUTH_FAILED] HTTP 401: PR #1: Authentication failed. Check your GitHub token. GitHub said: Bad credentials
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

Two codes survive that labelling and arrive as themselves. `AUTH_FAILED` is
one. The other is `NOT_FOUND` where the tool raised it against its own result,
as `search_user` does for a name nobody holds:

```
Error calling tool 'search_user': [NOT_FOUND] HTTP 404: User 'nobody' not found
```

A failure the step met on a REST call is labelled like any other, and the
original code stays in the text. `get_repo_stars_since` on a name nobody holds
reports the same missing account under a different code:

```
Error calling tool 'get_repo_stars_since': [GITHUB_API_ERROR] Failed to fetch repo stars: [NOT_FOUND] HTTP 404: repos for nobody: Resource not found GitHub said: Not Found
```

Read the innermost code in a message that carries two. A rate limit met inside
one of these steps arrives the same way, as `RATE_LIMITED` inside
`GITHUB_API_ERROR`.

## The Codes

| Code | Status | Means | Do |
|---|---|---|---|
| `AUTH_FAILED` | 401 | The token is missing, expired or revoked | Stop. Tell the user to re-authenticate. Retrying cannot help |
| `RATE_LIMITED` | 403 | A rate limit GitHub reported as a 403 is exhausted | Wait for the reset, then retry the same call unchanged |
| `NOT_FOUND` | 404 | The resource is absent, or the token cannot see it | Check the owner, repo and number. Do not retry unchanged |
| `VALIDATION_ERROR` | 422 | GitHub rejected the arguments | Fix the arguments. Retrying unchanged fails again |
| `GITHUB_API_ERROR` | 403, 429 or other | Permission denied, a secondary rate limit sent as a 429, or any status not listed above | Read the message. Permission problems need a token or approval change, not a retry |

Three of these are easy to misread.

**Permission denied shares a code with everything else.** A 403 that is not a
rate limit raises `GITHUB_API_ERROR`, not a code of its own, so the code alone
does not tell you a permission problem from an unexpected 500. Check the status
and the message text. It reads `Refused.` where GitHub named a cause, and
`Permission denied. Check your token permissions.` where GitHub sent no body,
which is the one case the token is the likeliest suspect.

A refused 403 has several causes and each needs a different fix, so the code and
the status settle nothing on their own. `merge_pr` meets all of them.

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

## Rate Limits

A rate limit is the only failure worth retrying on its own, and there are two
of them. The primary limit is a budget of requests per hour, and its message
reads `GitHub API rate limit exceeded`. The secondary limit is a throttle on
writes made in quick succession, and its message reads `GitHub secondary rate
limit hit` where GitHub sent it as a 403 and `429 - Too Many Requests` where
GitHub sent it as a 429. A secondary limit clears in minutes, so the same call
succeeds later without any change.

Neither message says when the limit resets. The server reads `Retry-After` and
`X-RateLimit-Reset` off the response, and the value does not survive into the
`ToolError` the client receives. Wait a minute or so on a secondary limit, and
longer on a primary one, since the primary window is an hour.

No tool retries or backs off internally. A failed call is simply raised, so any
waiting is yours to do. Do not retry in a tight loop, since every attempt spends
another request against a limit that is already exhausted.

Reading the same thing twice is charged once. A repeated read goes out with
`If-None-Match`, GitHub answers `304 Not Modified`, and a 304 costs no rate
limit on an authenticated request, which is every request this server makes.
So a rate limit means real distinct reads rather than a loop re-reading one
thing.

Cost matters most in `get_repo_stars_since`, which makes one request per repo
inspected and then walks the stargazer pages of each. On an account with
popular repos this is the fastest way to reach the limit, so lower `max_repos`
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

## Timeouts

Reading a response is bounded by `GITHUB_API_TIMEOUT`, 5 seconds by default.
Opening the connection is bounded separately by `GITHUB_API_CONNECT_TIMEOUT`,
3 seconds by default. Either surfaces as a `ToolError` carrying the underlying
httpx message rather than one of the codes above.

Large diffs and busy status-check queries are the usual causes, and the
operator raises `GITHUB_API_TIMEOUT` for them. That no longer changes how long
the server waits on a host it cannot reach.

## Best Practices

- Match on the bracketed code, never on the prose after it, and search the whole message for it rather than reading it off the front
- Read the innermost code where a message carries two, since the outer one names the step and the inner one names the failure
- Retry only a rate limit, whether it came through as `RATE_LIMITED` or as a 429 under `GITHUB_API_ERROR`, and wait before retrying
- Treat `AUTH_FAILED` as terminal and hand it back to the user
- Re-read the arguments on `VALIDATION_ERROR` rather than trying the call again
- On `NOT_FOUND`, check the spelling of owner, repo and number before reporting the resource as absent
- Report what failed and what the user needs to do, quoting the `GitHub said` text and not the whole exception
