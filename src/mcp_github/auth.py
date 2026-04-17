#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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

import hmac
import logging
import time
from os import getenv
from typing import Optional

from fastmcp.server.auth import TokenVerifier, AccessToken
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

GITHUB_OAUTH_CLIENT_ID = getenv("GITHUB_OAUTH_CLIENT_ID")
GITHUB_OAUTH_CLIENT_SECRET = getenv("GITHUB_OAUTH_CLIENT_SECRET")
GITHUB_OAUTH_BASE_URL = getenv("GITHUB_OAUTH_BASE_URL")


class APIKeyVerifier(TokenVerifier):
    """Verifies requests using a static GitHub personal access token."""

    def __init__(self, valid_api_keys: str):
        super().__init__()
        self.valid_api_keys = valid_api_keys

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        if hmac.compare_digest(token, self.valid_api_keys):
            return AccessToken(
                token=token,
                client_id="github_token",
                expires_at=None,  # API keys don't expire
                scopes=["api:read", "api:write"],
                claims={"authenticated": True},
            )
        return None


def get_oauth_verifier():
    """Return a PermissiveGitHubProvider instance for OAuth2 authentication.

    Requires GITHUB_OAUTH_CLIENT_ID, GITHUB_OAUTH_CLIENT_SECRET, and
    GITHUB_OAUTH_BASE_URL to be set.
    """
    from fastmcp.server.auth.providers.github import GitHubProvider

    if not all([GITHUB_OAUTH_CLIENT_ID, GITHUB_OAUTH_CLIENT_SECRET, GITHUB_OAUTH_BASE_URL]):
        raise ValueError(
            "GITHUB_OAUTH_CLIENT_ID, GITHUB_OAUTH_CLIENT_SECRET, and "
            "GITHUB_OAUTH_BASE_URL must all be set to use OAuth2 auth"
        )

    class _PermissiveGitHubProvider(GitHubProvider):
        """GitHubProvider that accepts the upstream client_id without prior DCR.

        Claude.ai and similar MCP clients skip Dynamic Client Registration and
        send the GitHub OAuth App's client_id directly in /authorize requests.
        On first use, this subclass auto-registers that client_id in the proxy's
        client store so the full OAuth flow can proceed normally.
        """

        async def get_client(self, client_id: str):
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

    return _PermissiveGitHubProvider(
        client_id=GITHUB_OAUTH_CLIENT_ID,
        client_secret=GITHUB_OAUTH_CLIENT_SECRET,
        base_url=GITHUB_OAUTH_BASE_URL,
        required_scopes=["repo", "read:org", "user"],
    )


def resolve_token(github_token: Optional[str], oauth_mode: bool) -> str:
    """Return the token to use for the current request.

    In OAuth2 mode, reads the authenticated user's token from FastMCP's
    per-request context. Falls back to the static github_token in all other
    cases (stdio mode or API-key mode).

    Raises:
        RuntimeError: In OAuth2 mode when no access token is available in the
            request context and no GITHUB_TOKEN fallback is configured.
    """
    if oauth_mode:
        from fastmcp.server.dependencies import get_access_token

        access_token = get_access_token()
        if access_token is not None:
            return access_token.token
        if not github_token:
            raise RuntimeError(
                "OAuth2 mode: no access token in request context and no GITHUB_TOKEN fallback"
            )
    return github_token or ""
