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

"""Decorators that tag methods for MCP tool registration.

Kept apart from github_integration so tool modules can import them without a
circular import back to the class they are mixed into.
"""

from __future__ import annotations

from typing import Any

from mcp.types import ToolAnnotations

# The scopes a grant needs before a tool that changes anything is listed or called.
# Read-only tools need none. See #388.
WRITE_SCOPES: tuple[str, ...] = ("repo",)

# A board sits outside the repository it tracks, so a tool that writes to one names
# this on top of the write scopes. See #351.
PROJECT_SCOPES: tuple[str, ...] = ("project",)

# Every scope the server gates on, one check each.
GATED_SCOPES: tuple[str, ...] = WRITE_SCOPES + PROJECT_SCOPES


def _annotate(*, ro: bool = False, destructive: bool = False) -> Any:
    def deco(
        fn: Any = None, *, task: bool = False, idempotent: bool = False, scopes: tuple[str, ...] = ()
    ) -> Any:
        def apply(f: Any) -> Any:
            f._mcp_annotations = ToolAnnotations(
                read_only_hint=ro, destructive_hint=destructive, idempotent_hint=idempotent
            )
            f._mcp_task = task
            f._mcp_scopes = scopes if ro else WRITE_SCOPES + scopes
            return f

        if fn is not None:
            return apply(fn)
        return apply

    return deco


_read_only = _annotate(ro=True)
_write = _annotate()
_destructive = _annotate(destructive=True)
