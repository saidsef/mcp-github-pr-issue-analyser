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

"""GraphQL helpers for GitHub API v4.

The transport lives on GitHubIntegration's async client. Only the GraphQL
specific error shape is here, since HTTP status mapping is shared with REST.
"""

from __future__ import annotations

import logging
from typing import Any

from .exceptions import GitHubAPIError, GitHubAuthError, GitHubNotFoundError, GitHubRateLimitError

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.github.com/graphql"


def handle_graphql_errors(errors: list[dict[str, Any]]) -> None:
    """Map the errors array a 200 response can carry onto our exceptions."""
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
