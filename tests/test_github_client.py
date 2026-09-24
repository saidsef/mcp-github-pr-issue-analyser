"""Tests for the GitHub client underneath every tool: the annotations each tool declares, the shared HTTP client, the ETag cache, error mapping and paging."""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, patch

import pytest
from fastmcp.exceptions import ToolError

from mcp_github.auth import MISSING_CREDENTIALS
from mcp_github.exceptions import (
    GitHubAPIError,
    GitHubAuthError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubValidationError,
)
from mcp_github.github_integration import CONNECT_TIMEOUT, TIMEOUT, GitHubIntegration, _timeout
from mcp_github.tool_annotations import GATED_SCOPES, PROJECT_SCOPES, WRITE_SCOPES, _write
from tests.support import mock_response

_READ = {
    "get_issue",
    "get_latest_sha",
    "get_pr_content",
    "get_pr_diff",
    "get_pr_linked_issues",
    "get_pr_status_checks",
    "get_project_fields",
    "get_release",
    "get_repo_stars_since",
    "get_repository_file",
    "get_skill",
    "get_user_activities",
    "list_milestones",
    "list_open_issues_prs",
    "list_pr_comments",
    "list_pr_reviews",
    "list_project_items",
    "list_releases",
    "list_repo_labels",
    "list_repos",
    "list_repository_tree",
    "list_skills",
    "list_tags",
    "search_issues_prs",
    "search_user",
}


_WRITE = {
    "add_inline_pr_comment",
    "add_pr_comments",
    "add_to_project",
    "create_issue",
    "create_milestone",
    "create_pr",
    "create_release",
    "create_tag",
    "merge_pr",
    "reply_to_review_comment",
    "set_issue_milestone",
    "set_pr_draft",
    "set_project_field",
    "update_assignees",
    "update_issue",
    "update_milestone",
    "update_pr",
    "update_pr_branch",
    "update_pr_comment",
    "update_release",
    "update_reviews",
}


_DESTRUCTIVE = {"delete_release", "delete_tag", "remove_from_project"}


_IDEMPOTENT = {
    "add_to_project",
    "set_issue_milestone",
    "set_pr_draft",
    "set_project_field",
    "update_assignees",
    "update_issue",
    "update_milestone",
    "update_pr",
    "update_pr_branch",
    "update_pr_comment",
    "update_release",
}


_TASKS = {"get_pr_linked_issues", "get_pr_status_checks", "get_repo_stars_since", "get_user_activities", "search_user"}


class TestAnnotations:
    """What each tool declares about itself, as one table, so a tool cannot move
    between classes or gain a hint without the change being visible here."""

    def test_the_table_names_every_tool(self, gi: GitHubIntegration):
        annotated = {
            name for name in dir(gi) if not name.startswith("_") and hasattr(getattr(gi, name), "_mcp_annotations")
        }

        assert annotated == _READ | _WRITE | _DESTRUCTIVE
        assert _IDEMPOTENT <= _WRITE
        assert _TASKS <= _READ

    @pytest.mark.parametrize("name", sorted(_READ | _WRITE | _DESTRUCTIVE))
    def test_each_tool_carries_the_hints_its_class_means(self, gi: GitHubIntegration, name: str):
        method = getattr(gi, name)
        ann = method._mcp_annotations

        assert ann.read_only_hint is (name in _READ)
        assert ann.destructive_hint is (name in _DESTRUCTIVE)
        assert ann.idempotent_hint is (name in _IDEMPOTENT)
        assert ann.open_world_hint is True
        assert method._mcp_task is (name in _TASKS)

    def test_a_tool_may_name_a_scope_beyond_its_class(self):
        def fn(): ...

        _write(idempotent=True, scopes=PROJECT_SCOPES)(fn)
        assert fn._mcp_scopes == WRITE_SCOPES + PROJECT_SCOPES

    def test_every_tool_scopes_match_its_class(self, gi: GitHubIntegration):
        """A read-only tool needs no scope, anything that changes state needs the
        write scopes, and every scope declared is one the server gates on. See #388."""
        seen = 0
        for name in dir(gi):
            if name.startswith("_"):
                continue
            method = getattr(gi, name)
            annotations = getattr(method, "_mcp_annotations", None)
            if annotations is None:
                continue
            seen += 1
            scopes = method._mcp_scopes
            assert set(scopes) <= set(GATED_SCOPES), name
            if annotations.read_only_hint:
                assert scopes == (), name
            else:
                assert set(WRITE_SCOPES) <= set(scopes), name
        assert seen > 0

    def test_the_board_tools_need_the_project_scope(self, gi: GitHubIntegration):
        """A board sits outside the repository it tracks, so repo does not reach it.
        See #351."""
        for name in ("add_to_project", "set_project_field", "remove_from_project"):
            assert set(PROJECT_SCOPES) <= set(getattr(gi, name)._mcp_scopes), name


class TestConnectionPooling:
    @pytest.mark.anyio
    async def test_same_client_instance_across_calls(self, gi: GitHubIntegration):
        client_before = gi._http
        gi._http.request = AsyncMock(return_value=mock_response(json_data=[{"sha": "abc"}]))
        await gi.get_latest_sha("owner", "repo")
        assert gi._http is client_before


class TestEtagCache:
    """A repeated GET goes out conditionally and a 304 is free. See #317."""

    @pytest.mark.anyio
    async def test_first_get_sends_no_condition_and_remembers_the_etag(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data={"a": 1}, etag='"abc"'))
        await gi._request("GET", "https://api.github.com/x")
        assert "If-None-Match" not in gi._http.request.call_args.kwargs["headers"]
        assert len(gi._etags) == 1

    @pytest.mark.anyio
    async def test_repeat_get_sends_the_condition_and_serves_the_cached_body(self, gi: GitHubIntegration):
        responses = iter(
            [
                mock_response(json_data={"a": 1}, etag='"abc"'),
                mock_response(status_code=304),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi._request("GET", "https://api.github.com/x")
        again = await gi._request("GET", "https://api.github.com/x")
        assert gi._http.request.call_args.kwargs["headers"]["If-None-Match"] == '"abc"'
        assert again.json() == {"a": 1}

    @pytest.mark.anyio
    async def test_different_params_do_not_share_an_entry(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data={"a": 1}, etag='"abc"'))
        await gi._request("GET", "https://api.github.com/x", params={"page": 1})
        await gi._request("GET", "https://api.github.com/x", params={"page": 2})
        assert len(gi._etags) == 2

    @pytest.mark.anyio
    async def test_a_write_is_never_cached(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data={"a": 1}, etag='"abc"'))
        await gi._request("POST", "https://api.github.com/x", json={})
        assert gi._etags == {}

    @pytest.mark.anyio
    async def test_the_cache_is_bounded(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data={"a": 1}, etag='"abc"'))
        with patch("mcp_github.github_integration.ETAG_CACHE_ENTRIES", 3):
            for i in range(10):
                await gi._request("GET", f"https://api.github.com/x{i}")
        assert len(gi._etags) == 3


class TestTimeouts:
    """Connecting and reading are bounded separately. See #313."""

    def test_connect_and_read_budgets_are_distinct(self, gi: GitHubIntegration):
        with (
            patch("mcp_github.github_integration.TIMEOUT", 30),
            patch("mcp_github.github_integration.CONNECT_TIMEOUT", 3),
        ):
            timeout = _timeout()
        assert timeout.connect == 3
        assert timeout.read == 30

    def test_the_shared_client_carries_both_budgets(self):
        with patch("mcp_github.github_integration.GITHUB_TOKEN", "test-token"):
            instance = GitHubIntegration()
        assert instance._http.timeout.connect == CONNECT_TIMEOUT
        assert instance._http.timeout.read == TIMEOUT


class TestLifecycle:
    @pytest.mark.anyio
    async def test_aclose_delegates_to_http_client(self, gi: GitHubIntegration):
        gi._http.aclose = AsyncMock()
        await gi.aclose()
        gi._http.aclose.assert_called_once()

    @pytest.mark.anyio
    async def test_async_context_manager_closes_on_exit(self, gi: GitHubIntegration):
        gi._http.aclose = AsyncMock()
        async with gi as g:
            assert g is gi
        gi._http.aclose.assert_called_once()

    @pytest.mark.anyio
    async def test_context_manager_closes_on_exception(self, gi: GitHubIntegration):
        gi._http.aclose = AsyncMock()
        with pytest.raises(RuntimeError):
            async with gi:
                raise RuntimeError("boom")
        gi._http.aclose.assert_called_once()

    @pytest.mark.anyio
    async def test_closing_the_shared_client_closes_graphql_too(self):
        with patch("mcp_github.github_integration.GITHUB_TOKEN", "test-token"):
            instance = GitHubIntegration()
        await instance.aclose()
        assert instance._http.is_closed


class TestAllowStatus:
    @pytest.mark.anyio
    async def test_allowed_status_is_returned_not_raised(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(status_code=404, json_data={}))
        response = await gi._request("GET", "https://api.github.com/x", allow_status=(404,))
        assert response.status_code == 404

    @pytest.mark.anyio
    async def test_an_unlisted_status_still_raises(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(status_code=404, json_data={}))
        with pytest.raises(ToolError):
            await gi._request("GET", "https://api.github.com/x", allow_status=(422,))


_SAML_403 = {
    "message": "Resource protected by organization SAML enforcement. You must grant your token access.",
    "documentation_url": "https://docs.github.com/rest",
}


class TestErrorDetail:
    """GitHub explains a refusal in the response body, and the exception text is all
    the client sees, so the body has to survive into the message."""

    @pytest.mark.anyio
    async def test_a_permission_403_carries_githubs_own_message(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(
            return_value=mock_response(status_code=403, json_data=_SAML_403, text=json.dumps(_SAML_403))
        )
        with pytest.raises(ToolError, match="SAML enforcement"):
            await gi.merge_pr("owner", "repo", 42)

    @pytest.mark.anyio
    async def test_a_403_names_the_call_that_failed(self, gi: GitHubIntegration):
        body = {"message": "At least 1 approving review is required by reviewers with write access."}
        gi._http.request = AsyncMock(return_value=mock_response(status_code=403, json_data=body, text=json.dumps(body)))
        with pytest.raises(ToolError, match=r"PR #42 merge: Refused.*approving review"):
            await gi.merge_pr("owner", "repo", 42)

    @pytest.mark.anyio
    async def test_a_403_with_no_body_still_points_at_the_token(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(status_code=403, text=""))
        gi._http.request.return_value.json.side_effect = ValueError("not json")
        with pytest.raises(ToolError, match="Permission denied. Check your token permissions"):
            await gi.merge_pr("owner", "repo", 42)

    def test_a_permission_403_is_not_read_as_a_rate_limit(self, gi: GitHubIntegration):
        response = mock_response(status_code=403, json_data=_SAML_403, text=json.dumps(_SAML_403))
        with pytest.raises(GitHubAPIError) as caught:
            gi._handle_response_error(response, "PR #42 merge")
        assert not isinstance(caught.value, GitHubRateLimitError)
        assert caught.value.status_code == 403
        assert caught.value.response_body == _SAML_403

    def test_a_secondary_rate_limit_says_so_and_waits_the_retry_after(self, gi: GitHubIntegration):
        body = {"message": "You have exceeded a secondary rate limit. Please wait a few minutes."}
        response = mock_response(
            status_code=403,
            json_data=body,
            text=json.dumps(body),
            headers={"Retry-After": "60", "X-RateLimit-Reset": "1"},
        )
        with pytest.raises(GitHubRateLimitError) as caught:
            gi._handle_response_error(response, "issue #7")
        assert "secondary rate limit hit" in str(caught.value)
        assert caught.value.reset_timestamp == pytest.approx(int(time.time()) + 60, abs=5)

    def test_a_primary_rate_limit_uses_the_reset_header(self, gi: GitHubIntegration):
        body = {"message": "API rate limit exceeded for user ID 1."}
        response = mock_response(
            status_code=403, json_data=body, text=json.dumps(body), headers={"X-RateLimit-Reset": "1893456000"}
        )
        with pytest.raises(GitHubRateLimitError) as caught:
            gi._handle_response_error(response, "")
        assert caught.value.reset_timestamp == 1893456000

    def test_an_unusable_reset_header_is_dropped_rather_than_raising(self, gi: GitHubIntegration):
        body = {"message": "API rate limit exceeded."}
        response = mock_response(
            status_code=403, json_data=body, text=json.dumps(body), headers={"X-RateLimit-Reset": "Wed, 21 Oct 2026"}
        )
        with pytest.raises(GitHubRateLimitError) as caught:
            gi._handle_response_error(response, "")
        assert caught.value.reset_timestamp is None

    def test_a_422_carries_the_field_level_errors(self, gi: GitHubIntegration):
        body = {
            "message": "Validation Failed",
            "errors": [{"resource": "PullRequest", "field": "base", "message": "No commits between main and topic"}],
        }
        response = mock_response(status_code=422, json_data=body)
        with pytest.raises(GitHubValidationError, match="base: No commits between main and topic"):
            gi._handle_response_error(response, "create PR")

    def test_a_404_keeps_both_the_context_and_the_message(self, gi: GitHubIntegration):
        body = {"message": "Not Found"}
        response = mock_response(status_code=404, json_data=body)
        with pytest.raises(GitHubNotFoundError, match=r"PR #4321: Resource not found GitHub said: Not Found"):
            gi._handle_response_error(response, "PR #4321")

    def test_a_401_carries_githubs_own_message(self, gi: GitHubIntegration):
        body = {"message": "Bad credentials"}
        response = mock_response(status_code=401, json_data=body)
        with pytest.raises(GitHubAuthError, match="Bad credentials"):
            gi._handle_response_error(response, "issue #7")

    def test_an_unlisted_status_keeps_the_message_it_always_had(self, gi: GitHubIntegration):
        body = {"message": "Pull Request is not mergeable"}
        response = mock_response(status_code=405, json_data=body, reason_phrase="Method Not Allowed")
        with pytest.raises(
            GitHubAPIError, match=r"405 - Method Not Allowed GitHub said: Pull Request is not mergeable"
        ):
            gi._handle_response_error(response, "PR #42 merge")

    def test_a_body_that_is_not_json_leaves_the_message_alone(self, gi: GitHubIntegration):
        response = mock_response(status_code=403, text="<html>no</html>")
        response.json.side_effect = ValueError("not json")
        with pytest.raises(GitHubAPIError) as caught:
            gi._handle_response_error(response, "PR #42 merge")
        assert "GitHub said" not in str(caught.value)

    def test_an_error_repeating_the_top_level_message_is_not_said_twice(self, gi: GitHubIntegration):
        body = {"message": "Validation Failed", "errors": [{"message": "Validation Failed"}]}
        response = mock_response(status_code=422, json_data=body)
        with pytest.raises(GitHubValidationError) as caught:
            gi._handle_response_error(response, "")
        assert str(caught.value).count("Validation Failed") == 1

    def test_an_error_string_rather_than_an_object_still_reads(self, gi: GitHubIntegration):
        body = {"message": "Validation Failed", "errors": ["milestone does not exist"]}
        response = mock_response(status_code=422, json_data=body)
        with pytest.raises(GitHubValidationError, match="milestone does not exist"):
            gi._handle_response_error(response, "")


class TestMissingCredentials:
    """What a tool does when the server holds no GitHub credentials. See #386."""

    def _unconfigured(self) -> GitHubIntegration:
        with (
            patch("mcp_github.github_integration.GITHUB_TOKEN", None),
            patch("mcp_github.auth.GITHUB_OAUTH_CLIENT_ID", None),
            patch("mcp_github.auth.GITHUB_OAUTH_CLIENT_SECRET", None),
            patch("mcp_github.auth.GITHUB_OAUTH_BASE_URL", None),
        ):
            return GitHubIntegration()

    def test_building_the_integration_does_not_raise(self):
        assert self._unconfigured().credentials_configured is False

    def test_a_request_refuses_with_the_documented_code(self):
        with pytest.raises(GitHubAuthError) as raised:
            self._unconfigured()._get_headers()

        assert raised.value.status_code == 401
        assert "[AUTH_FAILED] HTTP 401" in str(raised.value)
        assert MISSING_CREDENTIALS in str(raised.value)
