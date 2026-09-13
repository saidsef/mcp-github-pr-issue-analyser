"""Tests for telling a client its tool list is out of date. See #439."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, patch

import pytest
from fastmcp.exceptions import AuthorizationError, InsufficientScopeError, NotFoundError
from fastmcp.server.auth import AccessToken
from fastmcp.server.context import reset_transport, set_transport
from mcp.types import ToolListChangedNotification
from prometheus_client import REGISTRY

from mcp_github.issues_pr_analyser import PRIssueAnalyser

# A name from before the rename in #430. It is not registered and is not forwarded.
RETIRED = "create_issue"
CURRENT = "github_create_issue"
AN_ISSUE = {"repo_owner": "o", "repo_name": "r", "title": "t", "body": "b", "labels": []}


def _analyser() -> PRIssueAnalyser:
    with ExitStack() as stack:
        stack.enter_context(patch("mcp_github.github_integration.GITHUB_TOKEN", "test-token"))
        stack.enter_context(patch("mcp_github.issues_pr_analyser.MCP_ENABLE_REMOTE", False))
        stack.enter_context(patch("mcp_github.auth.REDIS_HOST_PORT", None))
        stack.enter_context(patch("mcp_github.auth.DYNAMODB_TABLE_ARN", None))
        return PRIssueAnalyser()


@contextmanager
def _sent() -> Iterator[AsyncMock]:
    """Captures what the server pushes to the client during a call."""
    with patch("fastmcp.server.context.Context.send_notification", new_callable=AsyncMock) as send:
        yield send


@contextmanager
def _over_stdio() -> Iterator[None]:
    """Stdio short-circuits the authorization middleware, so a missing tool
    surfaces as NotFoundError rather than the ambiguous authorization answer."""
    transport = set_transport("stdio")
    try:
        yield
    finally:
        reset_transport(transport)


@contextmanager
def _grant(scopes: list[str]) -> Iterator[None]:
    token = AccessToken(token="t", client_id="c", expires_at=None, scopes=scopes, claims={})
    transport = set_transport("streamable-http")
    try:
        with patch("fastmcp.server.middleware.authorization.get_access_token", return_value=token):
            yield
    finally:
        reset_transport(transport)


def _stale_count() -> float:
    return REGISTRY.get_sample_value("mcp_stale_tool_list_total") or 0.0


class TestARetiredName:
    @pytest.mark.anyio
    async def test_is_refused_rather_than_forwarded(self):
        """The whole point of #439. Until now this reached the renamed tool."""
        with _over_stdio(), pytest.raises(NotFoundError):
            await _analyser().mcp.call_tool(RETIRED, AN_ISSUE)

    @pytest.mark.anyio
    async def test_is_refused_over_http_too(self):
        """There the authorization middleware answers first, and will not say
        whether the name is absent or merely withheld."""
        with pytest.raises(AuthorizationError):
            await _analyser().mcp.call_tool(RETIRED, AN_ISSUE)

    @pytest.mark.anyio
    async def test_is_not_registered_either(self):
        analyser = _analyser()
        listed = {tool.name for tool in await analyser.mcp.list_tools(run_middleware=False)}

        assert RETIRED not in listed
        assert CURRENT in listed

    @pytest.mark.anyio
    async def test_tells_the_client_the_list_has_changed(self):
        analyser = _analyser()
        with _sent() as send, pytest.raises(AuthorizationError):
            await analyser.mcp.call_tool(RETIRED, AN_ISSUE)

        send.assert_awaited_once()
        assert isinstance(send.await_args.args[0], ToolListChangedNotification)

    @pytest.mark.anyio
    async def test_tells_the_client_over_stdio_as_well(self):
        analyser = _analyser()
        with _sent() as send, _over_stdio(), pytest.raises(NotFoundError):
            await analyser.mcp.call_tool(RETIRED, AN_ISSUE)

        send.assert_awaited_once()


class TestAnUnknownName:
    @pytest.mark.anyio
    async def test_is_answered_the_same_way(self):
        """A misspelling and a retired name are both names this server lacks."""
        analyser = _analyser()
        with _sent() as send, pytest.raises(AuthorizationError):
            await analyser.mcp.call_tool("nonesuch", {})

        send.assert_awaited_once()


class TestWhatDoesNotTriggerIt:
    @pytest.mark.anyio
    async def test_a_call_that_works_says_nothing(self):
        analyser = _analyser()
        response = AsyncMock()
        response.status_code, response.is_success = 200, True
        response.json, response.headers, response.content = list, {}, b"[]"
        analyser.gi._http.request = AsyncMock(return_value=response)
        with _sent() as send:
            await analyser.mcp.call_tool("github_list_repo_labels", {"repo_owner": "o", "repo_name": "r"})

        send.assert_not_awaited()

    @pytest.mark.anyio
    async def test_a_tool_withheld_by_the_scope_gate_is_not_a_stale_list(self):
        """The gate raises its own error, and the tool it withheld does exist.
        Reporting that as staleness would have every read-only grant asking for a
        refresh on every write tool."""
        analyser = _analyser()
        with _sent() as send, _grant(["read:org"]), pytest.raises(InsufficientScopeError):
            await analyser.mcp.call_tool(CURRENT, AN_ISSUE)

        send.assert_not_awaited()


class TestTheCounter:
    @pytest.mark.anyio
    async def test_counts_a_call_the_server_could_not_resolve(self):
        analyser = _analyser()
        before = _stale_count()
        with _sent(), pytest.raises(AuthorizationError):
            await analyser.mcp.call_tool("nonesuch", {})

        assert _stale_count() == before + 1

    def test_carries_no_label_a_caller_could_grow(self):
        """A label holding the name asked for would let a caller add a series per
        invented tool, which is what #304 fixed for the invocation counter."""
        metric = next(m for m in REGISTRY.collect() if m.name == "mcp_stale_tool_list")

        assert all(sample.labels == {} for sample in metric.samples)
