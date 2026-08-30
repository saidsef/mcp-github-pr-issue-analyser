# Configuration

## Authentication modes

Two modes are supported. The active mode is selected automatically from environment variables.

| Mode | When active | Token used for API calls |
|------|-------------|--------------------------|
| **stdio** (default) | `MCP_ENABLE_REMOTE` unset or false | Server's `GITHUB_TOKEN`; no transport auth |
| **Static token** | `MCP_ENABLE_REMOTE` true, no `GITHUB_OAUTH_*` vars | Server's `GITHUB_TOKEN` for every caller |
| **GitHub OAuth2** | `MCP_ENABLE_REMOTE` true and all three `GITHUB_OAUTH_*` vars set | Each user's own `gho_*` token |

In static-token HTTP mode, clients must send `Authorization: Bearer <GITHUB_TOKEN>`. The value is compared against the server's `GITHUB_TOKEN` with a constant-time comparison, so every caller shares one identity and one rate limit.

In OAuth2 mode the server registers clients dynamically, proxies the GitHub OAuth2 flow, and requests the `repo`, `read:org`, `user` and `project` scopes. Each caller's own token is used for API calls, so audit trails and rate limits follow the individual user.

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GITHUB_TOKEN` | Yes | GitHub PAT with `repo` scope; also the bearer token in static-token HTTP mode |
| `MCP_ENABLE_REMOTE` | No | `true`, `1`, `yes` or `on` enables HTTP mode, required for OAuth2. Anything else, including `false` and unset, stays on stdio. Already set to `true` in the published image |
| `GITHUB_OAUTH_CLIENT_ID` | OAuth2 only | GitHub OAuth App client ID |
| `GITHUB_OAUTH_CLIENT_SECRET` | OAuth2 only | GitHub OAuth App client secret |
| `GITHUB_OAUTH_BASE_URL` | OAuth2 only | Public base URL of the MCP server, used for the OAuth2 redirect |
| `JWT_SIGNING_KEY` | No | Signing key for issued JWTs. Derived from `GITHUB_OAUTH_CLIENT_SECRET` when unset, so set it if you want tokens to survive a client-secret rotation |
| `REDIS_HOST_PORT` | No | Redis connection string. Accepts `host:port` or a full URI: `redis://[:password@]host:port[/db]` for plaintext, `rediss://...` for TLS. When set, OAuth token state is stored in Redis rather than in process |
| `REDIS_PASSWORD` | No | Redis AUTH password fallback, used when the password is not embedded in the URI |
| `DYNAMODB_TABLE_NAME` | No | DynamoDB table holding OAuth token state. When set it takes precedence over `REDIS_HOST_PORT`. The table is created on first use if it does not exist |
| `DYNAMODB_REGION` | No | AWS region for that table. Falls back to `AWS_REGION`, then to whatever the AWS credential chain resolves |
| `DYNAMODB_ENDPOINT_URL` | No | Override the DynamoDB endpoint, for DynamoDB Local or a VPC endpoint |
| `PORT` | No, default `8081` | HTTP server port |
| `HOST` | No, default `localhost` | HTTP server bind address |
| `GITHUB_API_TIMEOUT` | No, default `5` | Seconds allowed for reading a GitHub API response. Raise this for large diffs and busy status-check queries |
| `GITHUB_API_CONNECT_TIMEOUT` | No, default `3` | Seconds allowed for opening the connection. Kept separate so raising the read timeout does not also make an unreachable host hang for that long |
| `GITHUB_DIFF_MAX_BYTES` | No, default `131072` | Default cap on the patch `get_pr_diff` returns. The reply carries the full size either way, so a caller can ask again for more. Callers can override it per call |
| `GITHUB_ETAG_CACHE_ENTRIES` | No, default `256` | How many read responses to keep for conditional requests. A repeat read is sent with `If-None-Match`, and GitHub charges no rate limit for the `304` that comes back. Set to `0` to send every read unconditionally |
| `LOG_LEVEL` | No, default `WARNING` | Root log level, one of the standard Python names. Applied by the entry point only, so importing the package as a library leaves your own logging setup alone |

## Creating the GitHub OAuth App

1. Go to **Settings → Developer settings → OAuth Apps → New OAuth App**.
2. Set the Authorization callback URL to `<GITHUB_OAUTH_BASE_URL>/auth/callback`, for example `https://mcp.example.com/auth/callback`.
3. Copy the client ID and generate a client secret.
4. Set `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET` and `GITHUB_OAUTH_BASE_URL` on the server.

`GITHUB_OAUTH_BASE_URL` must be the URL clients reach, not the internal bind address. A mismatch makes GitHub reject the redirect.

## Shared token store

Without one, OAuth client registrations and token state live in process. They are lost on restart and are not shared between replicas, so any deployment with more than one replica needs a shared store. There are two, and you pick by setting a variable.

Redis:

```sh
export REDIS_HOST_PORT="rediss://cache.example.com:6380/0"
export REDIS_PASSWORD="<password>"
```

DynamoDB:

```sh
export DYNAMODB_TABLE_NAME="mcp-github-oauth-state"
export DYNAMODB_REGION="eu-west-1"
```

Set both and DynamoDB wins, with a warning in the log.

Keys are prefixed with a hash of `GITHUB_OAUTH_BASE_URL` when that is set, so several deployments can share one table or one Redis instance without colliding.

### Which one

Redis needs an instance to size, run and pay for by the hour. DynamoDB has none of that, is billed per request, and is the cheaper choice for the handful of small keys this holds. Prefer Redis when you already run one, or when you are not on AWS.

### The DynamoDB table

The table is created on first use, on-demand billing, partitioned on `collection` with `key` as the sort key, and TTL enabled on the `ttl` attribute so expired tokens are removed without a sweep. Create it yourself if you would rather the running role could not.

Credentials come from the standard AWS chain, so an IRSA-annotated service account, an instance profile or `AWS_*` variables all work. On the table the server needs `dynamodb:GetItem`, `dynamodb:PutItem`, `dynamodb:DeleteItem`, `dynamodb:DescribeTable` and `dynamodb:DescribeTimeToLive`. Leaving the table to be created adds `dynamodb:CreateTable` and `dynamodb:UpdateTimeToLive`.

## Personal access token scopes

The PAT needs `repo` for private repositories. Reading org membership in user activity queries also needs `read:org`. The project board tools need `read:project` to read and `project` to write, which `repo` does not cover. A fine-grained token works if it grants read and write on pull requests, issues, contents and metadata for the repositories in scope, plus Projects read and write for the board tools.
