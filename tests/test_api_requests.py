"""Tests for github_api_read and github_api_write, which reach any documented route. See #543."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from mcp_github.api_routes import is_mutation, route_label
from mcp_github.exceptions import GitHubValidationError
from mcp_github.github_integration import GitHubIntegration
from tests.support import mock_response

_NEXT = {"Link": '<https://api.github.com/repositories/1/collaborators?page=2>; rel="next"'}


class TestReaching:
    @pytest.mark.anyio
    async def test_a_read_reaches_a_route_no_other_tool_wraps(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=[{"login": "a"}], headers=_NEXT))
        result = await gi.api_read("/repos/o/r/collaborators", params={"per_page": 1})

        method, url = gi._http.request.call_args.args
        assert (method, url) == ("GET", "https://api.github.com/repos/o/r/collaborators")
        assert gi._http.request.call_args.kwargs["params"] == {"per_page": 1}
        assert json.loads(result["body"]) == [{"login": "a"}]
        assert result["has_more"] is True

    @pytest.mark.parametrize("path", ["repos/o/r/contents/docs/guide/README.md", "repos/o/r/compare/main...feature/x"])
    @pytest.mark.anyio
    async def test_a_parameter_github_marks_multi_segment_takes_slashes(self, gi: GitHubIntegration, path: str):
        gi._http.request = AsyncMock(return_value=mock_response(json_data={}))
        await gi.api_read(path)

        assert gi._http.request.call_args.args[1] == f"https://api.github.com/{path}"

    @pytest.mark.anyio
    async def test_a_write_sends_its_method_and_body_once(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(status_code=201, json_data={"name": "bug"}))
        result = await gi.api_write("POST", "repos/o/r/labels", body={"name": "bug"})

        gi._http.request.assert_awaited_once()
        assert gi._http.request.call_args.args[0] == "POST"
        assert gi._http.request.call_args.kwargs["json"] == {"name": "bug"}
        assert result["status"] == 201

    @pytest.mark.anyio
    async def test_a_graphql_query_returns_its_data(self, gi: GitHubIntegration):
        gi._http.post = AsyncMock(return_value=mock_response(json_data={"data": {"viewer": {"login": "a"}}}))
        result = await gi.api_read(graphql="query { viewer { login } }")

        assert json.loads(result["body"]) == {"viewer": {"login": "a"}}
        assert result["has_more"] is False

    @pytest.mark.anyio
    async def test_a_graphql_mutation_goes_through_the_write_tool(self, gi: GitHubIntegration):
        gi._http.post = AsyncMock(return_value=mock_response(json_data={"data": {"addStar": {}}}))
        await gi.api_write(graphql='mutation { addStar(input: {starrableId: "x"}) { clientMutationId } }')

        assert gi._http.post.call_args.kwargs["json"]["query"].startswith("mutation")


class TestPaths:
    """A path is refused only where httpx would send something other than it says,
    and the refusal comes before a request."""

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "https://evil.example/repos/o/r"),
            ("GET", "//evil.example/repos/o/r"),
            ("GET", "repos/o/r/issues/../../../user"),
            ("GET", "repos/o/r/%2e%2e/x"),
            ("GET", "repos/o/r?per_page=1"),
            ("DELETE", "repos/o/r#/labels/bug"),
        ],
    )
    @pytest.mark.anyio
    async def test_a_path_httpx_would_send_elsewhere(self, gi: GitHubIntegration, method: str, path: str):
        gi._http.request = AsyncMock()
        call = gi.api_read(path) if method == "GET" else gi.api_write(method, path)
        with pytest.raises(GitHubValidationError):
            await call
        gi._http.request.assert_not_called()

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("DELETE", "repos/o/r"),
            ("DELETE", "orgs/acme"),
            ("POST", "user/keys"),
            ("PUT", "repos/o/r/actions/secrets/TOKEN"),
            ("GET", "repos/o/r/not-in-the-description"),
        ],
    )
    @pytest.mark.anyio
    async def test_every_path_is_left_to_github(self, gi: GitHubIntegration, method: str, path: str):
        """GitHub's permission check decides what the token may do. See #543."""
        gi._http.request = AsyncMock(return_value=mock_response(status_code=204))
        await (gi.api_read(path) if method == "GET" else gi.api_write(method, path))

        assert gi._http.request.call_args.args == (method, f"https://api.github.com/{path}")

    @pytest.mark.anyio
    async def test_the_read_tool_refuses_a_mutation(self, gi: GitHubIntegration):
        gi._http.post = AsyncMock()
        with pytest.raises(GitHubValidationError, match="github_api_write"):
            await gi.api_read(graphql="mutation { addStar(input: {}) { clientMutationId } }")
        gi._http.post.assert_not_called()

    @pytest.mark.parametrize(
        ("document", "expected"),
        [
            ("query { viewer { login } }", False),
            ('query { search(query: "mutation testing", type: REPOSITORY, first: 1) { repositoryCount } }', False),
            ("# a mutation would go here\nquery { viewer { login } }", False),
            ('query { a(x: """ \\""" """) { b } } mutation { c } # """', True),
            ("mutation{addStar(input:{}){clientMutationId}}", True),
        ],
    )
    def test_a_mutation_is_found_outside_strings_and_comments(self, document: str, expected: bool):
        assert is_mutation(document) is expected


class TestCapping:
    @pytest.mark.anyio
    async def test_an_oversized_body_reports_its_full_size(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(text="x" * 10))
        result = await gi.api_read("repos/o/r", max_bytes=4)

        assert result["body"] == "xxxx"
        assert (result["bytes_returned"], result["bytes_total"], result["truncated"]) == (4, 10, True)

    @pytest.mark.anyio
    async def test_a_negative_cap_is_refused_before_a_write(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock()
        with pytest.raises(GitHubValidationError):
            await gi.api_write("POST", "repos/o/r/labels", max_bytes=-1)
        gi._http.request.assert_not_called()


class TestRouteLabel:
    def test_a_literal_segment_wins_over_a_parameter(self):
        assert route_label("GET", "repos/o/r/pulls/comments", None) == ("GET", "/repos/{owner}/{repo}/pulls/comments")

    def test_a_path_matching_no_route_shares_one_label(self):
        assert route_label("GET", "anything/at/all", None) == ("unmatched", "unmatched")
        assert route_label("BREW", "repos/o/r", None) == ("unmatched", "unmatched")
