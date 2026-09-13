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

"""Decorators that tag methods for MCP tool registration, and the confirmation
a destructive one puts to the caller before it acts.

Kept apart from github_integration so tool modules can import them without a
circular import back to the class they are mixed into.
"""

from __future__ import annotations

from typing import Any

import mcp_types
from fastmcp import Context
from fastmcp.exceptions import ToolError
from fastmcp.server.elicitation import AcceptedElicitation
from mcp.shared.exceptions import MCPError
from mcp.types import ToolAnnotations
from mcp_types.version import MODERN_PROTOCOL_VERSIONS


def _annotate(*, ro: bool = False, destructive: bool = False) -> Any:
    def deco(fn: Any = None, *, task: bool = False, idempotent: bool = False) -> Any:
        def apply(f: Any) -> Any:
            f._mcp_annotations = ToolAnnotations(
                read_only_hint=ro, destructive_hint=destructive, idempotent_hint=idempotent
            )
            f._mcp_task = task
            return f

        if fn is not None:
            return apply(fn)
        return apply

    return deco


_read_only = _annotate(ro=True)
_write = _annotate()
_destructive = _annotate(destructive=True)

# destructive_hint is a hint the client is free to ignore, so the three tools
# that remove something ask for themselves rather than trusting it. See #390.
_UNCONFIRMABLE = (
    "A removal is made only once it has been confirmed, and this client cannot answer "
    "a confirmation request."
)

#: Key shared by the question a guard round asks and the answer it comes back with.
_CONFIRM_KEY = "confirm_removal"

#: MCP elicitation takes a flat object of primitives, so the answer is one boolean.
_CONFIRM_SCHEMA: mcp_types.ElicitRequestedSchema = {
    "type": "object",
    "properties": {"value": {"type": "boolean", "title": "Confirm"}},
    "required": ["value"],
}


def _asks_on_the_next_round(ctx: Context) -> bool:
    """True where the connection carries no server-initiated request.

    The 2026-07-28 era dropped the back-channel elicit() needs and replaced it
    with the guard round, where the tool returns the question and the client
    calls again with the answer.
    """
    rc = ctx.request_context
    return rc is not None and rc.protocol_version in MODERN_PROTOCOL_VERSIONS


def _answer_from_an_earlier_round(ctx: Context) -> bool | None:
    """The answer a guard round carried back, or None where nothing was asked yet."""
    answer = (ctx.input_responses or {}).get(_CONFIRM_KEY)
    if not isinstance(answer, mcp_types.ElicitResult):
        return None
    return answer.action == "accept" and (answer.content or {}).get("value") is True


async def _confirm_removal(ctx: Context | None, target: str) -> mcp_types.InputRequiredResult | bool:
    """Put the removal of target to the caller, naming what is about to go.

    Returns True where the answer was yes and False where it was no. A connection
    with no back-channel gets an InputRequiredResult instead, which the tool
    returns for the client to answer before it calls again. A caller that cannot
    be asked at all raises, because a question nobody saw is not an answer.
    See #390.
    """
    if ctx is None:
        raise ToolError(f"{_UNCONFIRMABLE} The {target} was left in place.")
    answered = _answer_from_an_earlier_round(ctx)
    if answered is not None:
        return answered
    message = f"Remove {target}? This cannot be undone."
    if _asks_on_the_next_round(ctx):
        return mcp_types.InputRequiredResult(
            input_requests={
                _CONFIRM_KEY: mcp_types.ElicitRequest(
                    params=mcp_types.ElicitRequestFormParams(message=message, requested_schema=_CONFIRM_SCHEMA)
                )
            }
        )
    try:
        answer = await ctx.elicit(message, bool, response_title="Confirm")
    except (MCPError, ToolError, RuntimeError) as e:
        raise ToolError(f"{_UNCONFIRMABLE} The {target} was left in place. The ask failed with: {e}") from e
    return isinstance(answer, AcceptedElicitation) and answer.data is True
