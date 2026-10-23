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

API = "https://api.github.com"
GRAPHQL_URL = f"{API}/graphql"


def _auth_hint(err_type: str) -> str:
    """A missing scope and a withdrawn authorisation both arrive as auth errors,
    and the fix for one is not the fix for the other. See #351."""
    if "INSUFFICIENT_SCOPES" in err_type:
        return (
            " The token is missing a scope this query needs, rather than the authorisation itself."
            " Projects (v2) needs 'read:project' to read and 'project' to write on a classic token,"
            " or Projects read and write on a fine-grained one."
        )
    return " If using OAuth, the authorization may have been revoked -- please re-authenticate."


def handle_graphql_errors(errors: list[dict[str, Any]], reset_timestamp: int | None = None) -> None:
    """Map the errors array a 200 response can carry onto our exceptions. The
    caller reads reset_timestamp off that response's headers. See #519."""
    if not errors:
        return

    error = errors[0]
    msg = error.get("message", "Unknown GraphQL error")
    err_type = error.get("type", "")
    body = {"errors": errors}

    logger.error(f"GraphQL error: {msg} (type: {err_type})")

    if "NOT_FOUND" in err_type or "not found" in msg.lower():
        raise GitHubNotFoundError(msg, response_body=body)
    if "RATE_LIMITED" in err_type:
        raise GitHubRateLimitError(msg, response_body=body, reset_timestamp=reset_timestamp)
    # A refusal reads as a REST 403 does, since a fresh login cannot lift it. See #519.
    if "FORBIDDEN" in err_type:
        raise GitHubAPIError(f"Refused. GitHub said: {msg}", status_code=403, response_body=body)
    if "UNAUTHORIZED" in err_type or "INSUFFICIENT_SCOPES" in err_type:
        raise GitHubAuthError(msg + _auth_hint(err_type), response_body=body)

    raise GitHubAPIError(f"GraphQL error: {msg}", response_body=body)
