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

"""Custom exceptions for MCP GitHub integration."""

from __future__ import annotations


class MCPGitHubError(Exception):
    """Base exception for MCP GitHub integration."""


class GitHubAPIError(MCPGitHubError):
    """GitHub API returned an error."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_body: dict | None = None,
        code: str = "GITHUB_API_ERROR",
    ):
        prefix = f"[{code}] HTTP {status_code}: " if status_code else f"[{code}] "
        super().__init__(f"{prefix}{message}")
        self.status_code = status_code
        self.response_body = response_body
        self.code = code


class GitHubAuthError(GitHubAPIError):
    def __init__(
        self, message: str = "Authentication failed. Check your GitHub token.", response_body: dict | None = None
    ):
        super().__init__(message, status_code=401, response_body=response_body, code="AUTH_FAILED")


class GitHubRateLimitError(GitHubAPIError):
    def __init__(
        self,
        message: str = "GitHub API rate limit exceeded.",
        response_body: dict | None = None,
        reset_timestamp: int | None = None,
    ):
        super().__init__(message, status_code=403, response_body=response_body, code="RATE_LIMITED")
        self.reset_timestamp = reset_timestamp


class GitHubNotFoundError(GitHubAPIError):
    def __init__(self, message: str, response_body: dict | None = None):
        super().__init__(message, status_code=404, response_body=response_body, code="NOT_FOUND")


class GitHubValidationError(GitHubAPIError):
    def __init__(self, message: str = "Validation failed.", response_body: dict | None = None):
        super().__init__(message, status_code=422, response_body=response_body, code="VALIDATION_ERROR")
