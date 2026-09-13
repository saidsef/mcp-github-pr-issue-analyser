"""Tests for accepting a tool name from before a rename. See #435."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, patch

import pytest
from fastmcp.exceptions import InsufficientScopeError
from fastmcp.server.auth import AccessToken
from fastmcp.server.context import reset_transport, set_transport
from prometheus_client import REGISTRY

from mcp_github import issues_pr_analyser as server
from mcp_github.issues_pr_analyser import PRIssueAnalyser


@contextmanager
def _deployment(*, accept_legacy: bool = True) -> Iterator[None]:
    """A stdio deployment holding a static token, with the window open or closed."""
    with ExitStack() as stack:
        stack.enter_context(patch("mcp_github.github_integration.GITHUB_TOKEN", "test-token"))
        stack.enter_context(patch.object(server, "MCP_ENABLE_REMOTE", False))
        stack.enter_context(patch.object(server, "ACCEPT_LEGACY_TOOL_NAMES", accept_legacy))
        stack.enter_context(patch("mcp_github.auth.REDIS_HOST_PORT", None))
        stack.enter_context(patch("mcp_github.auth.DYNAMODB_TABLE_ARN", None))
        yield


def _analyser(*, accept_legacy: bool = True) -> PRIssueAnalyser:
    with _deployment(accept_legacy=accept_legacy):
        return PRIssueAnalyser()


def _legacy_calls(requested: str) -> float:
    return REGISTRY.get_sample_value("mcp_legacy_tool_name_total", {"requested": requested}) or 0.0


class TestTheMap:
    @pytest.mark.anyio
    async def test_every_renamed_tool_answers_to_its_previous_name(self):
        analyser = _analyser()
        registered = {tool.name for tool in await analyser.mcp.list_tools(run_middleware=False)}

        assert analyser._legacy_names
        for previous, current in analyser._legacy_names.items():
            assert current in registered, previous
            assert previous != current

    @pytest.mark.anyio
    async def test_the_prefix_and_the_two_renames_are_both_covered(self):
        legacy = _analyser()._legacy_names

        assert legacy["create_issue"] == "github_create_issue"
        assert legacy["update_reviews"] == "github_submit_review"
        assert legacy["update_assignees"] == "github_set_assignees"

    def test_a_tool_that_was_removed_stays_removed(self):
        """update_pr_description went in #428 with no successor, so there is
        nothing to forward it to and it must not resolve."""
        assert "update_pr_description" not in _analyser()._legacy_names

    @pytest.mark.anyio
    async def test_no_previous_name_reaches_the_tool_listing(self):
        """The window costs nothing in the schema a client pays for on connect."""
        analyser = _analyser()
        listed = {tool.name for tool in await analyser.mcp.list_tools(run_middleware=False)}

        assert listed.isdisjoint(set(analyser._legacy_names))


class TestDispatch:
    @staticmethod
    def _served(analyser: PRIssueAnalyser) -> AsyncMock:
        """Answer the one HTTP call list_repo_labels makes."""
        response = AsyncMock()
        response.status_code = 200
        response.is_success = True
        response.json = list
        response.headers = {}
        response.content = b"[]"
        analyser.gi._http.request = AsyncMock(return_value=response)
        return analyser.gi._http.request

    @pytest.mark.anyio
    async def test_a_previous_name_reaches_the_tool_it_was_renamed_to(self):
        analyser = _analyser()
        self._served(analyser)
        result = await analyser.mcp.call_tool("list_repo_labels", {"repo_owner": "o", "repo_name": "r"})

        assert result.structured_content == {"count": 0, "has_more": False, "labels": []}

    @pytest.mark.anyio
    async def test_the_current_name_still_works(self):
        analyser = _analyser()
        self._served(analyser)
        result = await analyser.mcp.call_tool("github_list_repo_labels", {"repo_owner": "o", "repo_name": "r"})

        assert result.structured_content == {"count": 0, "has_more": False, "labels": []}

    @pytest.mark.anyio
    async def test_a_previous_name_is_counted_so_the_window_can_be_closed(self):
        analyser = _analyser()
        self._served(analyser)
        before = _legacy_calls("list_repo_labels")
        await analyser.mcp.call_tool("list_repo_labels", {"repo_owner": "o", "repo_name": "r"})

        assert _legacy_calls("list_repo_labels") == before + 1

    @pytest.mark.anyio
    async def test_the_current_name_is_not_counted(self):
        analyser = _analyser()
        self._served(analyser)
        before = _legacy_calls("list_repo_labels")
        await analyser.mcp.call_tool("github_list_repo_labels", {"repo_owner": "o", "repo_name": "r"})

        assert _legacy_calls("list_repo_labels") == before

    @pytest.mark.anyio
    async def test_a_name_belonging_to_no_tool_is_still_unknown(self):
        analyser = _analyser()
        with pytest.raises(Exception, match="nonesuch"):
            await analyser.mcp.call_tool("nonesuch", {})


@contextmanager
def _grant(scopes: list[str]) -> Iterator[None]:
    """A request over an HTTP transport carrying an OAuth grant with these scopes."""
    token = AccessToken(token="t", client_id="c", expires_at=None, scopes=scopes, claims={})
    transport = set_transport("streamable-http")
    try:
        with patch("fastmcp.server.middleware.authorization.get_access_token", return_value=token):
            yield
    finally:
        reset_transport(transport)


class TestTheScopeGateStillApplies:
    """The shim runs outermost so the gate resolves a tool that exists. That must
    not turn a previous name into a way around the gate."""

    _ISSUE = {"repo_owner": "o", "repo_name": "r", "title": "t", "body": "b", "labels": []}

    @pytest.mark.anyio
    async def test_a_previous_name_is_refused_without_the_scope(self):
        analyser = _analyser()
        with _grant(["read:org"]), pytest.raises(InsufficientScopeError):
            await analyser.mcp.call_tool("create_issue", self._ISSUE)

    @pytest.mark.anyio
    async def test_the_current_name_is_refused_the_same_way(self):
        analyser = _analyser()
        with _grant(["read:org"]), pytest.raises(InsufficientScopeError):
            await analyser.mcp.call_tool("github_create_issue", self._ISSUE)

    @pytest.mark.anyio
    async def test_a_previous_name_for_a_read_tool_needs_no_scope(self):
        """A read tool is ungated, so the window reaches it on any grant."""
        analyser = _analyser()
        response = AsyncMock()
        response.status_code, response.is_success = 200, True
        response.json, response.headers, response.content = list, {}, b"[]"
        analyser.gi._http.request = AsyncMock(return_value=response)
        with _grant(["read:org"]):
            result = await analyser.mcp.call_tool("list_repo_labels", {"repo_owner": "o", "repo_name": "r"})

        assert result.structured_content == {"count": 0, "has_more": False, "labels": []}


class TestClosingTheWindow:
    @pytest.mark.anyio
    async def test_a_previous_name_stops_resolving(self):
        analyser = _analyser(accept_legacy=False)
        with pytest.raises(Exception, match="list_repo_labels"):
            await analyser.mcp.call_tool("list_repo_labels", {"repo_owner": "o", "repo_name": "r"})

    @pytest.mark.anyio
    async def test_the_current_names_are_unaffected(self):
        analyser = _analyser(accept_legacy=False)
        listed = {tool.name for tool in await analyser.mcp.list_tools(run_middleware=False)}

        assert "github_list_repo_labels" in listed
        assert len(listed) > 40
