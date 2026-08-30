# Configuration

## Authentication modes

Three modes are supported. The active mode is selected automatically from environment variables.

| Mode | When active | Token used for API calls |
|------|-------------|--------------------------|
| **stdio** (default) | `MCP_ENABLE_REMOTE` unset or false | Server's `GITHUB_TOKEN`, no transport auth |
| **Static token** | `MCP_ENABLE_REMOTE` true, no `GITHUB_OAUTH_*` vars | Server's `GITHUB_TOKEN` for every caller |
| **GitHub OAuth2** | `MCP_ENABLE_REMOTE` true and all three `GITHUB_OAUTH_*` vars set | Each user's own `gho_*` token |

In static-token HTTP mode, clients send `Authorization: Bearer <GITHUB_TOKEN>`, compared against the server's `GITHUB_TOKEN` in constant time. Every caller shares one identity and one rate limit.

In OAuth2 mode the server registers clients dynamically, proxies the GitHub OAuth2 flow, and requests the `repo`, `read:org`, `user` and `project` scopes. Each caller's own token is used for API calls, so audit trails and rate limits follow the individual user.

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GITHUB_TOKEN` | Yes | GitHub PAT with `repo` scope, and the bearer token in static-token HTTP mode |
| `MCP_ENABLE_REMOTE` | No | `true`, `1`, `yes` or `on` enables HTTP mode, required for OAuth2. Anything else stays on stdio. Set to `true` in the published image |
| `GITHUB_OAUTH_CLIENT_ID` | OAuth2 only | GitHub OAuth App client ID |
| `GITHUB_OAUTH_CLIENT_SECRET` | OAuth2 only | GitHub OAuth App client secret |
| `GITHUB_OAUTH_BASE_URL` | OAuth2 only | Public base URL of the MCP server, used for the OAuth2 redirect |
| `JWT_SIGNING_KEY` | No | Signing key for issued JWTs. Derived from `GITHUB_OAUTH_CLIENT_SECRET` when unset, so rotating the client secret invalidates issued tokens |
| `REDIS_HOST_PORT` | No | Redis connection string. Accepts `host:port` or a full URI: `redis://[:password@]host:port[/db]` for plaintext, `rediss://...` for TLS |
| `REDIS_PASSWORD` | No | Redis AUTH password fallback, used when the password is not embedded in the URI |
| `DYNAMODB_TABLE_ARN` | No | ARN of the DynamoDB table holding OAuth token state, `arn:aws:dynamodb:<region>:<account>:table/<name>`. The region and table name are read from it. Takes precedence over `REDIS_HOST_PORT` |
| `PORT` | No, default `8081` | HTTP server port |
| `HOST` | No, default `localhost` | HTTP server bind address |
| `GITHUB_API_TIMEOUT` | No, default `5` | Seconds allowed for reading a GitHub API response. Raise this for large diffs and busy status-check queries |
| `GITHUB_API_CONNECT_TIMEOUT` | No, default `3` | Seconds allowed for opening the connection, separate from the read timeout |
| `GITHUB_DIFF_MAX_BYTES` | No, default `131072` | Default cap on the patch `get_pr_diff` returns. Callers can override it per call, and the reply carries the full size either way |
| `GITHUB_ETAG_CACHE_ENTRIES` | No, default `256` | How many read responses to keep for conditional requests. A repeat read is sent with `If-None-Match`, and GitHub charges no rate limit for a `304`. `0` sends every read unconditionally |
| `LOG_LEVEL` | No, default `WARNING` | Root log level, one of the standard Python names. Applied by the entry point only, not on import |

## Creating the GitHub OAuth App

1. Go to **Settings → Developer settings → OAuth Apps → New OAuth App**.
2. Set the Authorization callback URL to `<GITHUB_OAUTH_BASE_URL>/auth/callback`, for example `https://mcp.example.com/auth/callback`.
3. Copy the client ID and generate a client secret.
4. Set `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET` and `GITHUB_OAUTH_BASE_URL` on the server.

`GITHUB_OAUTH_BASE_URL` must be the URL clients reach, not the internal bind address. A mismatch makes GitHub reject the redirect.

## Shared token store

OAuth client registrations and token state live in process by default. They are lost on restart and are not shared between replicas, so more than one replica needs a shared store.

Redis:

```sh
export REDIS_HOST_PORT="rediss://cache.example.com:6380/0"
export REDIS_PASSWORD="<password>"
```

DynamoDB:

```sh
export DYNAMODB_TABLE_ARN="arn:aws:dynamodb:eu-west-1:123456789012:table/mcp-github-oauth-state"
```

Set both and DynamoDB wins, with a warning in the log.

Keys are prefixed with a hash of `GITHUB_OAUTH_BASE_URL` when that is set, so several deployments can share one table or one Redis instance without colliding.

DynamoDB is billed per request and has no instance to run. Prefer Redis when you already run one, or when you are not on AWS.

### The DynamoDB table

The table is created at startup if it is missing: on-demand billing, `collection` as the partition key, `key` as the sort key, TTL on the `ttl` attribute. Replicas starting together race, and the losers wait for the winner's table. Create it yourself to withhold `dynamodb:CreateTable`.

The account in the ARN is checked against `sts:GetCallerIdentity` at startup, so an ARN naming another account fails rather than resolving to a same-named table in yours. The check is skipped, with a warning, when the identity cannot be read.

For DynamoDB Local or a private endpoint, use the AWS SDK's own `AWS_ENDPOINT_URL_DYNAMODB`.

Building the DynamoDB store logs `A configured store is unstable and may change in a backwards incompatible way`. That is `py-key-value-aio` marking its own API, not a misconfiguration. Allow it if you run Python with `-W error`.

### IAM policy for DynamoDB

Credentials come from the standard AWS chain: an IRSA-annotated service account, an instance profile or `AWS_*` variables. That identity needs the policy below, with `Resource` set to the table `DYNAMODB_TABLE_ARN` names.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "OAuthTokenStore",
      "Effect": "Allow",
      "Action": [
        "dynamodb:CreateTable",
        "dynamodb:DescribeTable",
        "dynamodb:DescribeTimeToLive",
        "dynamodb:UpdateTimeToLive",
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:DeleteItem"
      ],
      "Resource": "arn:aws:dynamodb:eu-west-1:123456789012:table/mcp-github-oauth-state"
    }
  ]
}
```

That is every DynamoDB call the server makes. There is no `Query`, `Scan`, `UpdateItem` or batch operation.

`dynamodb:CreateTable` is the only one you can drop, and only if you create the table yourself. `dynamodb:UpdateTimeToLive` is needed either way: startup reads the TTL status on every boot and enables TTL whenever it reads back `DISABLED`, so a table made by hand without TTL on the `ttl` attribute fails without it.

Startup makes the `CreateTable`, `Describe*` and `UpdateTimeToLive` calls, so a role missing one stops the rollout. The item actions are first reached at sign-in, so a role missing those deploys clean and fails on the first user. `sts:GetCallerIdentity` is also called, for the account check above, and needs no policy.

A table encrypted with a customer-managed KMS key also needs `kms:Encrypt`, `kms:Decrypt`, `kms:ReEncrypt*`, `kms:GenerateDataKey*`, `kms:DescribeKey` and `kms:CreateGrant` on that key, in the key policy as well as here. DynamoDB calls KMS as the caller, so a refusal surfaces as `AccessDeniedException` on `GetItem`. The default AWS-owned key needs none of this.

On EKS, annotate the service account in `deployment/base/sa.yml` with the role holding this policy:

```yaml
metadata:
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::123456789012:role/mcp-github
```

## Personal access token scopes

The PAT needs `repo` for private repositories. Reading org membership in user activity queries also needs `read:org`. The project board tools need `read:project` to read and `project` to write, which `repo` does not cover. A fine-grained token works if it grants read and write on pull requests, issues, contents and metadata for the repositories in scope, plus Projects read and write for the board tools.
