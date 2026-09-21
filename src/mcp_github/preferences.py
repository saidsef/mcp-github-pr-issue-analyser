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

"""Per-user tool preferences, and the middleware that applies them. See #451."""

from __future__ import annotations

import logging
import time
from typing import Any

from fastmcp.exceptions import NotFoundError
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware, MiddlewareContext

from .auth import get_token_store

# Plain rather than encrypted, so rotating the signing key signs an admin out
# without also wiping what they chose. See #451.
USERS_COLLECTION = "admin-users"

AUDIT_COLLECTION = "admin-audit"

# Kept to a bounded list because the store has no UpdateItem, so the whole value is
# rewritten each time and an unbounded one would grow without limit.
AUDIT_LIMIT = 50
AUDIT_SECONDS = 90 * 24 * 60 * 60

# The shape stored per user, so a later field can be added without mistaking an
# old record for a corrupt one.
RECORD_VERSION = 1

logger = logging.getLogger(__name__)


def current_subject() -> str | None:
    """The GitHub user id behind the request, or None where there is no grant.

    GitHubTokenVerifier resolves the account while it validates the token, so the
    id is already on the request and costs no second call to GitHub."""
    token = get_access_token()
    if token is None:
        return None
    subject = getattr(token, "subject", None) or (token.claims or {}).get("sub")
    return str(subject) if subject else None


async def read_record(subject: str) -> dict[str, Any]:
    """The user's stored record, or an empty one where they have saved nothing."""
    record = await get_token_store().get(key=subject, collection=USERS_COLLECTION)
    return record or {"disabled": [], "epoch": 0, "version": RECORD_VERSION}


async def read_disabled(subject: str) -> set[str]:
    """The tool names this user has turned off."""
    record = await read_record(subject)
    disabled = record.get("disabled") or []
    return {str(name) for name in disabled}


async def write_disabled(subject: str, disabled: set[str]) -> None:
    """Replace the user's disabled set, leaving the rest of the record alone."""
    record = await read_record(subject)
    record["disabled"] = sorted(disabled)
    record["version"] = RECORD_VERSION
    await get_token_store().put(key=subject, value=record, collection=USERS_COLLECTION)


async def bump_epoch(subject: str) -> int:
    """Raise the session epoch, which refuses every session issued before now.

    Signing out everywhere has to invalidate sessions the server cannot list, so the
    sessions carry the epoch they were issued under and this moves the floor."""
    record = await read_record(subject)
    record["epoch"] = int(record.get("epoch", 0)) + 1
    record["version"] = RECORD_VERSION
    await get_token_store().put(key=subject, value=record, collection=USERS_COLLECTION)
    return record["epoch"]


async def record_action(subject: str, action: str, detail: str = "") -> None:
    """Append to the user's own trail of what the admin page did for them.

    Read-modify-write without UpdateItem, so two writes at once can drop an entry.
    Losing one line of a trail nobody acts on is cheaper than the locking that would
    prevent it. See #451."""
    store = get_token_store()
    held = await store.get(key=subject, collection=AUDIT_COLLECTION) or {}
    entries = list(held.get("entries") or [])
    entries.append({"at": int(time.time()), "action": action, "detail": detail})
    await store.put(
        key=subject,
        value={"entries": entries[-AUDIT_LIMIT:], "version": RECORD_VERSION},
        collection=AUDIT_COLLECTION,
        ttl=AUDIT_SECONDS,
    )


async def read_actions(subject: str) -> list[dict[str, Any]]:
    """The user's recorded actions, oldest first."""
    held = await get_token_store().get(key=subject, collection=AUDIT_COLLECTION)
    return list((held or {}).get("entries") or [])


class ToolPreferences(Middleware):
    """Hides the tools a user turned off, and refuses them when called.

    Filtering the list alone is not enough. A client reads the list when it connects
    and caches it, so a tool dropped from a later list is still reachable by name
    until that client reconnects. See #451.
    """

    async def _disabled(self) -> set[str]:
        subject = current_subject()
        if subject is None:
            return set()
        try:
            return await read_disabled(subject)
        except Exception as reason:
            # A store that cannot be read must not take the tools down with it.
            logger.warning("Could not read tool preferences, serving every tool: %s", reason)
            return set()

    async def on_list_tools(self, context: MiddlewareContext, call_next: Any) -> Any:
        tools = await call_next(context)
        disabled = await self._disabled()
        if not disabled:
            return tools
        return [tool for tool in tools if tool.name not in disabled]

    async def on_call_tool(self, context: MiddlewareContext, call_next: Any) -> Any:
        name = getattr(context.message, "name", "")
        if name and name in await self._disabled():
            # The same answer a retired name gets, so StaleToolList asks the client
            # to re-read and the refusal needs no wording of its own.
            raise NotFoundError(f"Unknown tool: {name}")
        return await call_next(context)
