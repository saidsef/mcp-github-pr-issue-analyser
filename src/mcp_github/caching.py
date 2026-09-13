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

"""Caches read-only tool results in the store the replicas already share.

FastMCP's own caching middleware keys an entry by tool name, arguments and a hash
of the access token, and it expires that entry on a TTL alone. A generation marker
per caller is held beside the entries so that a write moves the caller's reads on
rather than waiting for the TTL. See #391.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from contextvars import ContextVar
from typing import Any, SupportsFloat, override
from uuid import uuid4

import mcp_types
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware.caching import (
    CallToolSettings,
    GetPromptSettings,
    ListPromptsSettings,
    ListResourcesSettings,
    ListToolsSettings,
    ReadResourceSettings,
    ResponseCachingMiddleware,
)
from fastmcp.server.middleware.middleware import CallNext, MiddlewareContext
from fastmcp.tools.base import ToolResult
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.wrappers.base import BaseWrapper
from prometheus_client import Counter

CACHE_LOOKUPS = Counter("mcp_tool_cache_lookups_total", "Cached tool result lookups", ["outcome"])

# The collection the markers live in, kept apart from the entries they scope and
# from the OAuth state the same store holds.
GENERATION_COLLECTION = "tools/call/generation"
GENERATION_FIELD = "id"
FIRST_GENERATION = "0"
ANONYMOUS_CALLER = "__anonymous__"

_generation: ContextVar[str] = ContextVar("_generation", default=FIRST_GENERATION)


def _caller_key() -> str:
    """A stable name for the caller's access token, so one caller's write cannot
    move another caller's reads on. Callers holding no token share one name."""
    token = get_access_token()
    return hashlib.sha256(token.token.encode()).hexdigest() if token is not None else ANONYMOUS_CALLER


class GenerationPrefix(BaseWrapper):
    """Prefixes every cache key with the caller's current generation marker.

    The middleware derives its own keys, so a marker in front of them is what
    strands the entries a write has made stale. See #391.
    """

    def __init__(self, key_value: AsyncKeyValue) -> None:
        self.key_value: AsyncKeyValue = key_value
        super().__init__()

    @override
    async def get(self, key: str, *, collection: str | None = None) -> dict[str, Any] | None:
        value = await self.key_value.get(key=f"{_generation.get()}:{key}", collection=collection)
        CACHE_LOOKUPS.labels(outcome="miss" if value is None else "hit").inc()
        return value

    @override
    async def put(
        self,
        key: str,
        value: Mapping[str, Any],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        await self.key_value.put(key=f"{_generation.get()}:{key}", value=value, collection=collection, ttl=ttl)


class ToolResultCache(ResponseCachingMiddleware):
    """Answers a repeated read-only tool call from the store rather than from GitHub.

    Only the tools named in cacheable are served from the cache. A call to a tool
    named in invalidating moves the caller's generation marker on, whether it
    succeeded or failed, since a failed write can still have changed the state.
    """

    def __init__(
        self,
        store: AsyncKeyValue,
        *,
        ttl: int,
        cacheable: Iterable[str],
        invalidating: Iterable[str],
    ) -> None:
        # Listing tools, reading a skill and getting a prompt are all answered from
        # this process, so caching them in a shared store would add a round trip.
        super().__init__(
            cache_storage=GenerationPrefix(store),
            call_tool_settings=CallToolSettings(ttl=ttl, included_tools=sorted(cacheable)),
            list_tools_settings=ListToolsSettings(enabled=False),
            list_resources_settings=ListResourcesSettings(enabled=False),
            list_prompts_settings=ListPromptsSettings(enabled=False),
            read_resource_settings=ReadResourceSettings(enabled=False),
            get_prompt_settings=GetPromptSettings(enabled=False),
        )
        self._store = store
        self._ttl = ttl
        self._cacheable = frozenset(cacheable)
        self._invalidating = frozenset(invalidating)

    @override
    async def on_call_tool(
        self,
        context: MiddlewareContext[mcp_types.CallToolRequestParams],
        call_next: CallNext[mcp_types.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        name = getattr(context.message, "name", "")
        if name in self._cacheable:
            _generation.set(await self._read_generation())
            return await super().on_call_tool(context, call_next)
        if name not in self._invalidating:
            return await call_next(context)
        try:
            return await call_next(context)
        finally:
            await self._next_generation()

    async def _read_generation(self) -> str:
        """The caller's current marker, or the first one where no write has landed."""
        entry = await self._store.get(key=_caller_key(), collection=GENERATION_COLLECTION)
        marker = entry.get(GENERATION_FIELD) if entry else None
        return marker if isinstance(marker, str) else FIRST_GENERATION

    async def _next_generation(self) -> None:
        """A marker outlives every entry written under the marker it replaced, so
        the fall back to the first generation cannot reach a live stale entry."""
        await self._store.put(
            key=_caller_key(),
            value={GENERATION_FIELD: uuid4().hex},
            collection=GENERATION_COLLECTION,
            ttl=self._ttl * 2,
        )
