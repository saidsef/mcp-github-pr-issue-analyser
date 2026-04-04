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

"""GraphQL client for GitHub API v4."""

from __future__ import annotations

import logging
import requests
from typing import Any, Optional

from .exceptions import (
    GitHubAPIError,
    GitHubAuthError,
    GitHubRateLimitError,
    GitHubNotFoundError,
)

logger = logging.getLogger(__name__)


class GraphQLClient:
    """Client for GitHub GraphQL API v4."""

    GRAPHQL_URL = "https://api.github.com/graphql"

    def __init__(self, token: str, timeout: int = 10):
        """
        Initialise the GraphQL client.

        Args:
            token: GitHub personal access token
            timeout: Request timeout in seconds
        """
        self.token = token
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
        )

    def execute_query(
        self,
        query: str,
        variables: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Execute a GraphQL query against the GitHub API.

        Args:
            query: GraphQL query string
            variables: Query variables dictionary

        Returns:
            dict: GraphQL response data

        Raises:
            GitHubAuthError: If authentication fails
            GitHubRateLimitError: If rate limit is exceeded
            GitHubNotFoundError: If resource not found
            GitHubAPIError: For other API errors
        """
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        try:
            logger.debug(f"Executing GraphQL query with variables: {variables}")
            response = self.session.post(
                self.GRAPHQL_URL,
                json=payload,
                timeout=self.timeout,
            )

            # Handle HTTP errors
            if response.status_code == 401:
                raise GitHubAuthError(
                    "Authentication failed. Check your GitHub token.",
                    response_body=response.json() if response.text else None,
                )
            elif response.status_code == 403:
                # Check if it's a rate limit error
                reset_header = response.headers.get("X-RateLimit-Reset")
                raise GitHubRateLimitError(
                    "GitHub API rate limit exceeded.",
                    response_body=response.json() if response.text else None,
                    reset_timestamp=int(reset_header) if reset_header else None,
                )
            elif response.status_code == 404:
                raise GitHubNotFoundError(
                    "Resource not found",
                    response_body=response.json() if response.text else None,
                )
            elif not response.ok:
                raise GitHubAPIError(
                    f"GraphQL request failed: {response.status_code} - {response.reason}",
                    status_code=response.status_code,
                    response_body=response.json() if response.text else None,
                )

            # Parse response
            data = response.json()

            # Handle GraphQL errors in the response body
            if "errors" in data:
                self._handle_graphql_errors(data["errors"])

            return data.get("data", {})

        except requests.RequestException as e:
            raise GitHubAPIError(f"GraphQL request failed: {e}") from e

    def _handle_graphql_errors(self, errors: list[dict[str, Any]]) -> None:
        """
        Handle GraphQL-specific errors from the response.

        Args:
            errors: List of GraphQL error dictionaries

        Raises:
            GitHubAPIError: With details from the first relevant error
        """
        if not errors:
            return

        # Get the first error for simplicity
        error = errors[0]
        error_message = error.get("message", "Unknown GraphQL error")
        error_type = error.get("type", "")

        logger.error(f"GraphQL error: {error_message} (type: {error_type})")

        # Map common GraphQL error patterns to specific exceptions
        if "NOT_FOUND" in error_type or "not found" in error_message.lower():
            raise GitHubNotFoundError(error_message)
        elif "RATE_LIMITED" in error_type:
            raise GitHubRateLimitError(error_message)
        elif "FORBIDDEN" in error_type:
            raise GitHubAuthError(error_message)

        raise GitHubAPIError(
            f"GraphQL error: {error_message}",
            response_body={"errors": errors},
        )
