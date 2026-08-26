---
description: Read the error codes these tools raise and decide whether to retry, fix the call, or stop
---

# Error Handling

Every tool in this server fails through one small set of typed errors. The code in the message tells you which of the three responses is right: back off, correct the call, or stop and tell the user.

## Reading an Error

Failures arrive as a `ToolError` whose message opens with a bracketed code:

```
[NOT_FOUND] HTTP 404: PR #4321: Resource not found
```

The prefix is `[<CODE>] HTTP <status>: ` when a status is known and `[<CODE>] `
when it is not. Read the code first, since the prose after it varies with the
call and is not stable enough to match on.

The GraphQL-backed tools wrap failures once more, so their messages read
`[GITHUB_API_ERROR] Failed to <action>: <original error>`. The action names the
step that failed, such as `fetch user activities` or `fetch status checks`. A
missing resource is the exception and keeps its own `NOT_FOUND` code rather
than being re-wrapped.

## The Codes

| Code | Status | Means | Do |
|---|---|---|---|
| `AUTH_FAILED` | 401 | The token is missing, expired or revoked | Stop. Tell the user to re-authenticate. Retrying cannot help |
| `RATE_LIMITED` | 403 | GitHub's rate limit is exhausted | Wait for the reset, then retry the same call unchanged |
| `NOT_FOUND` | 404 | The resource is absent, or the token cannot see it | Check the owner, repo and number. Do not retry unchanged |
| `VALIDATION_ERROR` | 422 | GitHub rejected the arguments | Fix the arguments. Retrying unchanged fails again |
| `GITHUB_API_ERROR` | 403 or other | Permission denied, or any status not listed above | Read the message. Permission problems need a token or approval change, not a retry |

Two of these are easy to misread.

**Permission denied shares a code with everything else.** A 403 that is not a
rate limit raises `GITHUB_API_ERROR`, not a code of its own, so the code alone
does not tell you a permission problem from an unexpected 500. Check the status
and the message text, which begins `Permission denied. Check your token
permissions.`

**A 404 does not prove the thing is missing.** GitHub returns 404 rather than
403 for a resource the token cannot see, so a private repository the token
lacks scope for is indistinguishable from a typo in the name. Check the
spelling before concluding the resource does not exist.

## Rate Limits

`RATE_LIMITED` is the only code worth retrying on its own. The error carries a
`reset_timestamp` taken from GitHub's `X-RateLimit-Reset` header, which is Unix
epoch seconds and may be absent if GitHub omitted the header.

No tool retries or backs off internally. A failed call is simply raised, so any
waiting is yours to do. Do not retry in a tight loop, since every attempt spends
another request against a limit that is already exhausted.

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

Requests use a 5 second timeout by default, set by `GITHUB_API_TIMEOUT`. A
timeout surfaces as a `ToolError` carrying the underlying httpx message rather
than one of the codes above. Large diffs and busy status-check queries are the
usual causes, and the operator raises the value.

## Best Practices

- Match on the bracketed code, never on the prose after it
- Retry only `RATE_LIMITED`, and only after waiting for the reset
- Treat `AUTH_FAILED` as terminal and hand it back to the user
- Re-read the arguments on `VALIDATION_ERROR` rather than trying the call again
- On `NOT_FOUND`, check the spelling of owner, repo and number before reporting the resource as absent
- Report what failed and what the user needs to do, not the raw exception text
