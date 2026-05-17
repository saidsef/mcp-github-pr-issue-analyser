#!/usr/bin/env python3

# /*
#  * Copyright Said Sef
#  *
#  * Licensed under the Apache License, Version 2.0 (the "License");
#  * you may not use this file except in compliance with the License.
#  * You may obtain a copy of the License at
#  *
#  *      https://www.apache.org/licenses/LICENSE-2.0
#  *
#  * Unless required by applicable law or agreed to in writing, software
#  * distributed under the License is distributed on an "AS IS" BASIS,
#  * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  * See the License for the specific language governing permissions and
#  * limitations under the License.
#  */

"""Authentication providers and token resolution for the MCP GitHub server."""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from os import getenv
from urllib.parse import urlparse

from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.auth.jwt_issuer import derive_jwt_key
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.dependencies import get_access_token
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.stores.memory import MemoryStore
from key_value.aio.stores.redis import RedisStore
from key_value.aio.wrappers.prefix_collections import PrefixCollectionsWrapper
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl
from redis.asyncio import Redis as AsyncRedis

GITHUB_OAUTH_CLIENT_ID = getenv("GITHUB_OAUTH_CLIENT_ID")
GITHUB_OAUTH_CLIENT_SECRET = getenv("GITHUB_OAUTH_CLIENT_SECRET")
GITHUB_OAUTH_BASE_URL = getenv("GITHUB_OAUTH_BASE_URL")
JWT_SIGNING_KEY = getenv("JWT_SIGNING_KEY")
REDIS_HOST_PORT = getenv("REDIS_HOST_PORT")
REDIS_PASSWORD = getenv("REDIS_PASSWORD")


class APIKeyVerifier(TokenVerifier):
    """Verifies requests using a static GitHub personal access token."""

    def __init__(self, valid_api_keys: str):
        super().__init__()
        self.valid_api_keys = valid_api_keys

    async def verify_token(self, token: str) -> AccessToken | None:
        if hmac.compare_digest(token, self.valid_api_keys):
            return AccessToken(
                token=token,
                client_id="github_token",
                expires_at=None,  # API keys don't expire
                scopes=["api:read", "api:write"],
                claims={"authenticated": True},
            )
        return None


class _PermissiveGitHubProvider(GitHubProvider):
    """GitHubProvider that accepts the upstream client_id without prior DCR.

    Claude.ai and similar MCP clients skip Dynamic Client Registration and
    send the GitHub OAuth App's client_id directly in /authorize requests.
    On first use, this subclass auto-registers that client_id in the proxy's
    client store so the full OAuth flow can proceed normally.
    """

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        client = await super().get_client(client_id)
        if client is not None:
            return client

        if client_id == self._upstream_client_id:
            logging.info(
                "Auto-registering upstream client_id %s (MCP client skipped DCR)",
                client_id,
            )
            await self.register_client(
                OAuthClientInformationFull(
                    client_id=client_id,
                    client_id_issued_at=int(time.time()),
                    redirect_uris=[AnyUrl("http://localhost")],
                    grant_types=["authorization_code", "refresh_token"],
                    response_types=["code"],
                )
            )
            return await self._client_store.get(key=client_id)

        return None


def _parse_redis_db(path: str) -> int:
    """Parse the database index from a Redis URI path component."""
    db_path = path.lstrip("/")
    if db_path and not db_path.isdigit():
        raise ValueError(f"Invalid Redis database in URI: {db_path!r} (must be a non-negative integer)")
    return int(db_path) if db_path else 0


def _build_redis_client(host_port: str) -> AsyncRedis:
    """Build an AsyncRedis client from a host:port string or Redis URI."""
    uri = host_port if "://" in host_port else f"redis://{host_port}"
    parsed = urlparse(uri)
    return AsyncRedis(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        db=_parse_redis_db(parsed.path),
        password=parsed.password or REDIS_PASSWORD or None,
        ssl=parsed.scheme == "rediss",
        decode_responses=True,
    )


def build_token_store() -> AsyncKeyValue:
    """
    Return a token store for OAuth state.

    When REDIS_HOST_PORT is set, returns a RedisStore whose collection names are
    prefixed with a 12-char SHA-256 hash of GITHUB_OAUTH_BASE_URL. Two server
    instances sharing the same Redis instance will have fully isolated keyspaces
    provided their base URLs differ.

    When REDIS_HOST_PORT is unset, returns an in-process MemoryStore. No tokens
    are written to disk in either case. Sessions are lost on server restart in
    MemoryStore mode.

    REDIS_HOST_PORT accepts either a bare host:port or a full URI:
      redis://[:<password>@]<host>:<port>[/<db>]   — plaintext
      rediss://[:<password>@]<host>:<port>[/<db>]  — TLS
    REDIS_PASSWORD is used as a fallback when not embedded in the URI.
    The database defaults to 0 when not specified in the URI.

    """
    if REDIS_HOST_PORT:
        store: AsyncKeyValue = RedisStore(client=_build_redis_client(REDIS_HOST_PORT))
        if GITHUB_OAUTH_BASE_URL:
            prefix = hashlib.sha256(GITHUB_OAUTH_BASE_URL.encode()).hexdigest()[:12]
            return PrefixCollectionsWrapper(store, prefix=prefix)
        return store
    return MemoryStore()


def _derive_jwt_signing_key() -> bytes:
    """
    Return a stable JWT signing key.

    Priority:
    1. ``JWT_SIGNING_KEY`` env var (explicit override).
    2. Deterministic derivation from ``GITHUB_OAUTH_CLIENT_SECRET``
       (automatic — all pods with the same secret share the same key).

    When the automatic path is used, rotating the GitHub OAuth App
    secret invalidates all stored sessions and forces clients to
    re-authenticate.

    """
    if JWT_SIGNING_KEY:
        return derive_jwt_key(
            low_entropy_material=JWT_SIGNING_KEY,
            salt="fastmcp-jwt-signing-key",
        )
    return derive_jwt_key(
        high_entropy_material=GITHUB_OAUTH_CLIENT_SECRET,  # type: ignore[arg-type]
        salt="fastmcp-jwt-signing-key",
    )


def get_oauth_verifier() -> _PermissiveGitHubProvider:
    """Return a PermissiveGitHubProvider instance for OAuth2 authentication.

    Requires GITHUB_OAUTH_CLIENT_ID, GITHUB_OAUTH_CLIENT_SECRET, and
    GITHUB_OAUTH_BASE_URL to be set.

    JWT_SIGNING_KEY is optional. When omitted, a stable key is derived
    automatically from GITHUB_OAUTH_CLIENT_SECRET so all pods generate
    the same signing key without requiring an additional env var.

    """
    if not all((GITHUB_OAUTH_CLIENT_ID, GITHUB_OAUTH_CLIENT_SECRET, GITHUB_OAUTH_BASE_URL)):
        raise ValueError(
            "GITHUB_OAUTH_CLIENT_ID, GITHUB_OAUTH_CLIENT_SECRET, and "
            "GITHUB_OAUTH_BASE_URL must all be set to use OAuth2 auth"
        )

    # Validate types after check (pyright doesn't narrow through all() check)
    return _PermissiveGitHubProvider(
        client_id=GITHUB_OAUTH_CLIENT_ID,  # type: ignore[arg-type]
        client_secret=GITHUB_OAUTH_CLIENT_SECRET,  # type: ignore[arg-type]
        base_url=GITHUB_OAUTH_BASE_URL,  # type: ignore[arg-type]
        jwt_signing_key=_derive_jwt_signing_key(),
        required_scopes=["repo", "read:org", "user"],
        client_storage=build_token_store(),
    )


def resolve_token(github_token: str | None, oauth_mode: bool) -> str:
    """
    Return the token to use for the current request.

    In OAuth2 mode, reads the authenticated user's token from FastMCP's
    per-request context. Falls back to the static github_token in all other
    cases (stdio mode or API-key mode).

    Raises
    ------
    RuntimeError
        In OAuth2 mode when no access token is available in
        the request context and no GITHUB_TOKEN fallback is configured.

    """
    if oauth_mode:
        access_token = get_access_token()
        if access_token is not None:
            return access_token.token
        if not github_token:
            raise RuntimeError("OAuth2 mode: no access token in request context and no GITHUB_TOKEN fallback")
    return github_token or ""
