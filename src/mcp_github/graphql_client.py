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

"""GraphQL client for GitHub API v4."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .exceptions import GitHubAPIError, GitHubAuthError, GitHubNotFoundError, GitHubRateLimitError

logger = logging.getLogger(__name__)


class GraphQLClient:
    """Client for GitHub GraphQL API v4."""

    GRAPHQL_URL = "https://api.github.com/graphql"

    def __init__(self, token: str, timeout: int = 10):
        """Initialise the GraphQL client."""
        self.token = token
        self.timeout = timeout
        self.client = httpx.Client(timeout=httpx.Timeout(self.timeout))
        self.client.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
        )

    def execute_query(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Execute a GraphQL query against the GitHub API."""
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        per_call_headers = {"Authorization": f"Bearer {token}"} if token else {}

        try:
            logger.debug(f"Executing GraphQL query with variables: {variables}")
            response = self.client.post(
                self.GRAPHQL_URL,
                json=payload,
                headers=per_call_headers,
            )

            body = response.json() if response.text else None
            match response.status_code:
                case 401:
                    raise GitHubAuthError(
                        "Authentication failed. Check your GitHub token."
                        " If using OAuth, the authorization may have been revoked -- please re-authenticate.",
                        response_body=body,
                    )
                case 403:
                    reset_header = response.headers.get("X-RateLimit-Reset")
                    raise GitHubRateLimitError(
                        "GitHub API rate limit exceeded.",
                        response_body=body,
                        reset_timestamp=int(reset_header) if reset_header else None,
                    )
                case 404:
                    raise GitHubNotFoundError("Resource not found", response_body=body)
                case _ if response.status_code >= 400:
                    raise GitHubAPIError(
                        f"GraphQL request failed: {response.status_code} - {response.reason_phrase}",
                        status_code=response.status_code,
                        response_body=body,
                    )

            data = response.json()
            if "errors" in data:
                self._handle_graphql_errors(data["errors"])

            return data.get("data", {})

        except httpx.HTTPError as e:
            raise GitHubAPIError(f"GraphQL request failed: {e}") from e

    def _handle_graphql_errors(self, errors: list[dict[str, Any]]) -> None:
        """Handle GraphQL-specific errors from the response."""
        if not errors:
            return

        error = errors[0]
        msg = error.get("message", "Unknown GraphQL error")
        err_type = error.get("type", "")

        logger.error(f"GraphQL error: {msg} (type: {err_type})")

        predicates = [
            ("NOT_FOUND" in err_type or "not found" in msg.lower(), GitHubNotFoundError),
            ("RATE_LIMITED" in err_type, GitHubRateLimitError),
            ("FORBIDDEN" in err_type or "UNAUTHORIZED" in err_type, GitHubAuthError),
        ]
        for predicate, exc_cls in predicates:
            if predicate:
                hint = (
                    " If using OAuth, the authorization may have been revoked -- please re-authenticate."
                    if exc_cls is GitHubAuthError
                    else ""
                )
                raise exc_cls(msg + hint, response_body={"errors": errors})

        raise GitHubAPIError(f"GraphQL error: {msg}", response_body={"errors": errors})
