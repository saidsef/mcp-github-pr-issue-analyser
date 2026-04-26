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

"""
Custom exceptions for MCP GitHub integration.

This module defines a hierarchy of exceptions for handling errors from
GitHub API and IP info services in a structured way.
"""

from __future__ import annotations


class MCPGitHubError(Exception):

    """Base exception for MCP GitHub integration."""

    def __init__(self, message: str, code: str = "ERROR"):
        super().__init__(message)
        self.message = message
        self.code = code

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class GitHubAPIError(MCPGitHubError):

    """GitHub API returned an error."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_body: dict | None = None,
        code: str = "GITHUB_API_ERROR",
    ):
        super().__init__(message, code)
        self.status_code = status_code
        self.response_body = response_body

    def __str__(self) -> str:
        if self.status_code:
            return f"[{self.code}] HTTP {self.status_code}: {self.message}"
        return super().__str__()


class GitHubAuthError(GitHubAPIError):

    """
    Authentication failed (401).

    Inherits from GitHubAPIError because a 401 response is still an HTTP API
    response -- it follows the same status_code + response_body pattern.
    """

    def __init__(
        self,
        message: str = "Authentication failed. Check your GitHub token.",
        response_body: dict | None = None,
    ):
        super().__init__(
            message, status_code=401, response_body=response_body, code="AUTH_FAILED"
        )


class GitHubRateLimitError(GitHubAPIError):

    """Rate limit exceeded (403)."""

    def __init__(
        self,
        message: str = "GitHub API rate limit exceeded.",
        response_body: dict | None = None,
        reset_timestamp: int | None = None,
    ):
        super().__init__(
            message, status_code=403, response_body=response_body, code="RATE_LIMITED"
        )
        self.reset_timestamp = reset_timestamp


class GitHubNotFoundError(GitHubAPIError):

    """Resource not found (404)."""

    def __init__(self, message: str, response_body: dict | None = None):
        """Initialize GitHubNotFoundError."""
        super().__init__(
            message, status_code=404, response_body=response_body, code="NOT_FOUND"
        )


class GitHubValidationError(GitHubAPIError):

    """Validation failed (422)."""

    def __init__(
        self, message: str = "Validation failed.", response_body: dict | None = None
    ):
        super().__init__(
            message,
            status_code=422,
            response_body=response_body,
            code="VALIDATION_ERROR",
        )


class IPInfoError(MCPGitHubError):

    """IP info service error."""

    def __init__(self, message: str, url: str | None = None):
        """Initialize IPInfoError."""
        super().__init__(message, code="IP_INFO_ERROR")
        self.url = url
