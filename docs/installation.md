# Installation

## Requirements

- Python 3.12+
- A GitHub personal access token with `repo` scope, **or** a GitHub OAuth App (client ID, secret and a public base URL)

## Run from source

```sh
git clone https://github.com/saidsef/mcp-github-pr-issue-analyser.git
cd mcp-github-pr-issue-analyser
```

### stdio mode

The default. The server talks over stdin and stdout, so it is launched by the client rather than run as a service.

```sh
export GITHUB_TOKEN="<github-token>"
uvx ./
```

### HTTP mode with a static token

```sh
export GITHUB_TOKEN="<github-token>"
export MCP_ENABLE_REMOTE=true
uvx ./
```

The endpoint is `http://localhost:8081/mcp`. Clients must send `Authorization: Bearer <GITHUB_TOKEN>`.

### HTTP mode with GitHub OAuth2

```sh
export GITHUB_TOKEN="<github-token>"
export MCP_ENABLE_REMOTE=true
export GITHUB_OAUTH_CLIENT_ID="<oauth-app-client-id>"
export GITHUB_OAUTH_CLIENT_SECRET="<oauth-app-client-secret>"
export GITHUB_OAUTH_BASE_URL="https://<your-public-host>"
uvx ./
```

Users authenticate through GitHub's OAuth flow and each user's own token is used for API calls. See [Configuration](./configuration.md) for the OAuth App setup.

## Docker

The published image sets `MCP_ENABLE_REMOTE=true`, so it always runs in HTTP mode. It runs as uid 10000 and carries no build tooling.

```sh
docker run -e GITHUB_TOKEN="<github-token>" \
  -p 8081:8081 \
  ghcr.io/saidsef/mcp-github-pr-issue-analyser:latest
```

With OAuth2 and a shared token store, Redis here:

```sh
docker run \
  -e GITHUB_TOKEN="<github-token>" \
  -e GITHUB_OAUTH_CLIENT_ID="<oauth-app-client-id>" \
  -e GITHUB_OAUTH_CLIENT_SECRET="<oauth-app-client-secret>" \
  -e GITHUB_OAUTH_BASE_URL="https://mcp.example.com" \
  -e REDIS_HOST_PORT="redis://redis:6379/0" \
  -p 8081:8081 \
  ghcr.io/saidsef/mcp-github-pr-issue-analyser:latest
```

Swap `REDIS_HOST_PORT` for `DYNAMODB_ARN` to keep the same state in DynamoDB instead. The container then needs AWS credentials, from `AWS_*` variables or a mounted role.

## Kubernetes

Manifests live under `deployment/`. Apply them with kustomize:

```sh
kubectl apply -k deployment/
```

The base expects two objects in the same namespace:

| Object | Kind | Keys |
|--------|------|------|
| `github-token` | Secret | `token` |
| `redis-config` | ConfigMap and Secret | `host-port`, `password` |
| `dynamodb-config` | ConfigMap | `arn` |

Every key above is optional except the token, so supply whichever store you want and leave the other object out. Without either the pod runs with the in-process store. For DynamoDB, annotate the `mcp-github` service account with a role that can reach the table.

The pod runs as a non-root user with a read-only root filesystem, drops all capabilities, and carries `prometheus.io/scrape` annotations. To run in OAuth2 mode, add the three `GITHUB_OAUTH_*` variables to the container spec and expose the service on the host named in `GITHUB_OAUTH_BASE_URL`.

## Next

Wire up a client with [Client configuration](./mcp-clients.md).
