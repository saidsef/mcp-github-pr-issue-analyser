"""Tests for the cached tool results: what is served from the store, who it is
served to, and what a write does to it. See #391."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import mcp_types
import pytest
from fastmcp.server.middleware.middleware import MiddlewareContext
from fastmcp.tools.base import ToolResult
from key_value.aio.stores.memory import MemoryStore
from prometheus_client import REGISTRY

from mcp_github import auth
from mcp_github.caching import GENERATION_COLLECTION, ToolResultCache, _caller_key
from mcp_github.issues_pr_analyser import PRIssueAnalyser

READS = ["get_issue"]
WRITES = ["update_issue"]


@pytest.fixture(autouse=True)
def _reset_token_store():
    """A cache built through the server records the store it was given. See #391."""
    yield
    auth._token_store = auth._store_view = None


def _cache(store, ttl=300):
    return ToolResultCache(store, ttl=ttl, cacheable=READS, invalidating=WRITES)


def _context(name, **arguments):
    return MiddlewareContext(message=mcp_types.CallToolRequestParams(name=name, arguments=arguments))


@contextmanager
def _caller(token):
    """The access token the cache partitions its entries by. Both modules read it,
    so both are answered with the same caller."""
    access = SimpleNamespace(token=token) if token else None
    with (
        patch("mcp_github.caching.get_access_token", return_value=access),
        patch("fastmcp.server.middleware.caching.get_access_token", return_value=access),
    ):
        yield


class _Tool:
    """A tool body that counts the times it ran and answers with its current state."""

    def __init__(self, state="first"):
        self.state = state
        self.calls = 0

    async def __call__(self, _context):
        self.calls += 1
        return ToolResult(self.state)


async def _raises(_context):
    raise RuntimeError("boom")


def _sample(outcome):
    return REGISTRY.get_sample_value("mcp_tool_cache_lookups_total", {"outcome": outcome}) or 0.0


class TestReadsAreServedFromTheStore:
    """A repeat of the same read-only call does not reach GitHub."""

    @pytest.mark.anyio
    async def test_the_tool_runs_once_for_two_identical_calls(self):
        cache, tool = _cache(MemoryStore()), _Tool()
        with _caller("token-a"):
            first = await cache.on_call_tool(_context("get_issue", number=391), tool)
            second = await cache.on_call_tool(_context("get_issue", number=391), tool)

        assert tool.calls == 1
        assert first.content[0].text == second.content[0].text == "first"

    @pytest.mark.anyio
    async def test_different_arguments_are_different_entries(self):
        cache, tool = _cache(MemoryStore()), _Tool()
        with _caller("token-a"):
            await cache.on_call_tool(_context("get_issue", number=391), tool)
            await cache.on_call_tool(_context("get_issue", number=392), tool)

        assert tool.calls == 2

    @pytest.mark.anyio
    async def test_a_tool_that_is_not_read_only_is_never_served_from_the_store(self):
        cache, tool = _cache(MemoryStore()), _Tool()
        with _caller("token-a"):
            await cache.on_call_tool(_context("update_issue", number=391), tool)
            await cache.on_call_tool(_context("update_issue", number=391), tool)

        assert tool.calls == 2

    @pytest.mark.anyio
    async def test_a_tool_the_annotations_did_not_name_passes_straight_through(self):
        cache, tool = _cache(MemoryStore()), _Tool()
        with _caller("token-a"):
            await cache.on_call_tool(_context("github_pr_issue_analyser_ui"), tool)
            await cache.on_call_tool(_context("github_pr_issue_analyser_ui"), tool)

        assert tool.calls == 2


class TestEntriesAreNotShared:
    """An entry belongs to the token that filled it."""

    @pytest.mark.anyio
    async def test_another_token_is_not_served_the_entry(self):
        cache, tool = _cache(MemoryStore()), _Tool()
        with _caller("token-a"):
            await cache.on_call_tool(_context("get_issue", number=391), tool)
        with _caller("token-b"):
            await cache.on_call_tool(_context("get_issue", number=391), tool)

        assert tool.calls == 2

    @pytest.mark.anyio
    async def test_a_caller_holding_no_token_is_not_served_another_token_s_entry(self):
        cache, tool = _cache(MemoryStore()), _Tool()
        with _caller("token-a"):
            await cache.on_call_tool(_context("get_issue", number=391), tool)
        with _caller(None):
            await cache.on_call_tool(_context("get_issue", number=391), tool)

        assert tool.calls == 2


class TestAWriteMovesTheReadsOn:
    """A read after a write reports the new state rather than waiting for the TTL."""

    @pytest.mark.anyio
    async def test_the_read_after_a_write_reports_the_new_state(self):
        cache, tool = _cache(MemoryStore()), _Tool()
        with _caller("token-a"):
            await cache.on_call_tool(_context("get_issue", number=391), tool)
            tool.state = "second"
            await cache.on_call_tool(_context("update_issue", number=391), tool)
            after = await cache.on_call_tool(_context("get_issue", number=391), tool)

        assert after.content[0].text == "second"

    @pytest.mark.anyio
    async def test_a_write_that_failed_moves_the_reads_on_too(self):
        cache, tool = _cache(MemoryStore()), _Tool()
        with _caller("token-a"):
            await cache.on_call_tool(_context("get_issue", number=391), tool)
            with pytest.raises(RuntimeError):
                await cache.on_call_tool(_context("update_issue", number=391), _raises)
            await cache.on_call_tool(_context("get_issue", number=391), tool)

        assert tool.calls == 2

    @pytest.mark.anyio
    async def test_a_write_leaves_another_caller_s_entries_alone(self):
        store = MemoryStore()
        cache, tool = _cache(store), _Tool()
        with _caller("token-a"):
            await cache.on_call_tool(_context("get_issue", number=391), tool)
        with _caller("token-b"):
            await cache.on_call_tool(_context("update_issue", number=391), tool)
        with _caller("token-a"):
            await cache.on_call_tool(_context("get_issue", number=391), tool)

        assert tool.calls == 2

    @pytest.mark.anyio
    async def test_the_marker_outlives_the_entries_it_scopes(self):
        store = MemoryStore()
        cache, tool = _cache(store, ttl=300), _Tool()
        with _caller("token-a"):
            await cache.on_call_tool(_context("update_issue", number=391), tool)
            _, ttl = await store.ttl(key=_caller_key(), collection=GENERATION_COLLECTION)

        assert ttl is not None and ttl > 300


class TestTheStoreIsWhatIsShared:
    """The entries outlive the process the call was answered in."""

    @pytest.mark.anyio
    async def test_a_second_replica_is_served_the_first_one_s_entry(self):
        store = MemoryStore()
        tool = _Tool()
        with _caller("token-a"):
            await _cache(store).on_call_tool(_context("get_issue", number=391), tool)
            restarted = await _cache(store).on_call_tool(_context("get_issue", number=391), tool)

        assert tool.calls == 1
        assert restarted.content[0].text == "first"

    @pytest.mark.anyio
    async def test_a_replica_with_its_own_store_starts_cold(self):
        tool = _Tool()
        with _caller("token-a"):
            await _cache(MemoryStore()).on_call_tool(_context("get_issue", number=391), tool)
            await _cache(MemoryStore()).on_call_tool(_context("get_issue", number=391), tool)

        assert tool.calls == 2


class TestLookupsAreCounted:
    """The hit and miss counts reported to Prometheus."""

    @pytest.mark.anyio
    async def test_a_miss_then_a_hit_is_counted_as_one_of_each(self):
        cache, tool = _cache(MemoryStore()), _Tool()
        hits, misses = _sample("hit"), _sample("miss")
        with _caller("token-a"):
            await cache.on_call_tool(_context("get_issue", number=391), tool)
            await cache.on_call_tool(_context("get_issue", number=391), tool)

        assert _sample("miss") == misses + 1
        assert _sample("hit") == hits + 1


class TestRegistration:
    """The switch that turns the cache on, and the tools it is given."""

    def _analyser(self, enabled):
        with (
            patch("mcp_github.github_integration.GITHUB_TOKEN", "test-token"),
            patch("mcp_github.issues_pr_analyser.MCP_ENABLE_CACHE", enabled),
        ):
            return PRIssueAnalyser()

    def _registered(self, analyser):
        return [mw for mw in analyser.mcp.middleware if isinstance(mw, ToolResultCache)]

    def test_nothing_is_cached_unless_the_cache_is_switched_on(self):
        assert self._registered(self._analyser(False)) == []

    def test_switching_it_on_registers_one_cache(self):
        assert len(self._registered(self._analyser(True))) == 1

    def test_only_the_read_only_tools_are_cacheable(self):
        cache = self._registered(self._analyser(True))[0]
        assert {"get_pr_diff", "get_issue", "list_repos"} <= cache._cacheable
        assert {"create_issue", "merge_pr", "delete_tag"} <= cache._invalidating
        assert not cache._cacheable & cache._invalidating

    def test_the_metrics_middleware_still_sees_a_call_the_cache_answers(self):
        analyser = self._analyser(True)
        names = [type(mw).__name__ for mw in analyser.mcp.middleware]
        # The chain runs outermost first, so the counters come before the cache.
        assert names.index("MetricsMiddleware") < names.index("ToolResultCache")
