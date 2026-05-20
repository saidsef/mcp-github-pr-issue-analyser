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
                expires_at=None,
                scopes=["api:read", "api:write"],
                claims={"authenticated": True},
            )
        return None


def _build_redis_client(host_port: str) -> AsyncRedis:
    """Build an AsyncRedis client from a host:port string or Redis URI."""
    uri = host_port if "://" in host_port else f"redis://{host_port}"
    parsed = urlparse(uri)
    db_path = parsed.path.lstrip("/")
    if db_path and not db_path.isdigit():
        raise ValueError(f"Invalid Redis database in URI: {db_path!r} (must be a non-negative integer)")
    return AsyncRedis(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        db=int(db_path) if db_path else 0,
        password=parsed.password or REDIS_PASSWORD or None,
        ssl=parsed.scheme == "rediss",
        decode_responses=True,
    )


def build_token_store() -> AsyncKeyValue:
    """Return a token store for OAuth state. MemoryStore by default; RedisStore when REDIS_HOST_PORT is set."""
    if REDIS_HOST_PORT:
        store: AsyncKeyValue = RedisStore(client=_build_redis_client(REDIS_HOST_PORT))
        if GITHUB_OAUTH_BASE_URL:
            prefix = hashlib.sha256(GITHUB_OAUTH_BASE_URL.encode()).hexdigest()[:12]
            return PrefixCollectionsWrapper(store, prefix=prefix)
        return store
    return MemoryStore()


def _derive_jwt_signing_key() -> bytes:
    """Return a stable JWT signing key from JWT_SIGNING_KEY or GITHUB_OAUTH_CLIENT_SECRET."""
    if JWT_SIGNING_KEY:
        return derive_jwt_key(low_entropy_material=JWT_SIGNING_KEY, salt="fastmcp-jwt-signing-key")
    return derive_jwt_key(high_entropy_material=GITHUB_OAUTH_CLIENT_SECRET, salt="fastmcp-jwt-signing-key")  # type: ignore[arg-type]


def get_oauth_verifier() -> GitHubProvider:
    """Return a GitHubProvider instance for OAuth2 authentication."""
    if not all((GITHUB_OAUTH_CLIENT_ID, GITHUB_OAUTH_CLIENT_SECRET, GITHUB_OAUTH_BASE_URL)):
        raise ValueError(
            "GITHUB_OAUTH_CLIENT_ID, GITHUB_OAUTH_CLIENT_SECRET, and GITHUB_OAUTH_BASE_URL must all be set"
        )

    return GitHubProvider(
        client_id=GITHUB_OAUTH_CLIENT_ID,  # type: ignore[arg-type]
        client_secret=GITHUB_OAUTH_CLIENT_SECRET,  # type: ignore[arg-type]
        base_url=GITHUB_OAUTH_BASE_URL,  # type: ignore[arg-type]
        jwt_signing_key=_derive_jwt_signing_key(),
        required_scopes=["repo", "read:org", "user"],
        client_storage=build_token_store(),
    )


def resolve_token(github_token: str | None, oauth_mode: bool) -> str:
    """Return the token for the current request."""
    if oauth_mode:
        access_token = get_access_token()
        if access_token is not None:
            return access_token.token
        if not github_token:
            raise RuntimeError("OAuth2 mode: no access token in request context and no GITHUB_TOKEN fallback")
    return github_token or ""
