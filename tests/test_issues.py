"""Tests for issues, labels, search and milestones."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from mcp_github.exceptions import GitHubNotFoundError, GitHubValidationError
from mcp_github.github_integration import GitHubIntegration
from tests.support import NOISE_REACTIONS, NOISE_USER, issue_payload, milestone_payload, mock_response


class TestIssueTrimming:
    @pytest.mark.anyio
    async def test_get_issue_returns_trimmed_issue(self, gi: GitHubIntegration):
        payload = issue_payload(assignees=[{"login": "saidsef", "id": 1}])
        gi._http.request = AsyncMock(return_value=mock_response(json_data=payload))
        result = await gi.get_issue("o", "r", 7)
        assert result["number"] == 7
        assert result["body"] == "Details"
        assert result["assignees"] == ["saidsef"]
        assert gi._http.request.call_args.args[1] == "https://api.github.com/repos/o/r/issues/7"

    @pytest.mark.anyio
    async def test_get_issue_refuses_a_pull_request_number(self, gi: GitHubIntegration):
        payload = issue_payload(pull_request={"url": "https://api.github.com/repos/o/r/pulls/7"})
        gi._http.request = AsyncMock(return_value=mock_response(json_data=payload))
        with pytest.raises(GitHubValidationError, match="pull request"):
            await gi.get_issue("o", "r", 7)

    @pytest.mark.anyio
    async def test_create_issue_returns_trimmed_issue(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=issue_payload()))
        result = await gi.create_issue("o", "r", "A bug", "Details", ["bug"])
        assert result == {
            "number": 7,
            "title": "A bug",
            "body": "Details",
            "state": "open",
            "author": "octocat",
            "labels": ["bug", "mcp"],
            "assignees": [],
            "milestone": None,
            "html_url": "https://github.com/o/r/issues/7",
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": "2026-07-02T00:00:00Z",
        }
        assert gi._http.request.call_args.kwargs["json"]["labels"] == ["bug", "mcp"]

    @pytest.mark.anyio
    async def test_create_issue_without_labels_creates_an_unlabelled_issue(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=issue_payload(labels=[])))
        result = await gi.create_issue("o", "r", "A bug", "Details")
        assert "labels" not in gi._http.request.call_args.kwargs["json"]
        assert result["labels"] == []

    @pytest.mark.anyio
    async def test_create_issue_mcp_label_opt_out(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=issue_payload(labels=[{"name": "bug"}])))
        await gi.create_issue("o", "r", "A bug", "Details", ["bug"], mcp_label=False)
        assert gi._http.request.call_args.kwargs["json"]["labels"] == ["bug"]

    @pytest.mark.anyio
    async def test_create_issue_does_not_duplicate_a_caller_supplied_mcp_label(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=issue_payload(labels=[{"name": "mcp"}])))
        await gi.create_issue("o", "r", "A bug", "Details", ["mcp"])
        assert gi._http.request.call_args.kwargs["json"]["labels"] == ["mcp"]


class TestSearchIssuesPRs:
    @pytest.mark.anyio
    async def test_query_is_encoded_into_the_search_url(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data={"total_count": 0, "items": []}))
        await gi.search_issues_prs("rate limit repo:o/r is:closed")
        url = gi._http.request.call_args.args[1]
        assert "q=rate+limit+repo%3Ao%2Fr+is%3Aclosed" in url
        assert "advanced_search=true" in url

    @pytest.mark.anyio
    async def test_results_are_trimmed_to_the_listing_shape(self, gi: GitHubIntegration):
        payload = {
            "total_count": 1,
            "incomplete_results": False,
            "items": [
                {
                    "html_url": "https://github.com/o/r/issues/7",
                    "title": "Rate limits",
                    "number": 7,
                    "state": "closed",
                    "created_at": "2026-07-01T00:00:00Z",
                    "updated_at": "2026-07-02T00:00:00Z",
                    "user": NOISE_USER,
                    "labels": [{"name": "bug"}],
                    "body": "a very long body nobody asked for",
                    "reactions": NOISE_REACTIONS,
                }
            ],
        }
        gi._http.request = AsyncMock(return_value=mock_response(json_data=payload))
        result = await gi.search_issues_prs("rate limits")
        assert result["count"] == 1
        assert result["items"] == [
            {
                "url": "https://github.com/o/r/issues/7",
                "title": "Rate limits",
                "number": 7,
                "state": "closed",
                "created_at": "2026-07-01T00:00:00Z",
                "updated_at": "2026-07-02T00:00:00Z",
                "author": "octocat",
                "label_names": ["bug"],
                "is_draft": False,
            }
        ]

    @pytest.mark.anyio
    async def test_paging_params_sent_in_url(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data={"total_count": 0, "items": []}))
        await gi.search_issues_prs("x", per_page=10, page=3)
        url = gi._http.request.call_args.args[1]
        assert "per_page=10&page=3" in url

    @pytest.mark.anyio
    async def test_the_default_search_sends_no_search_type(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data={"total_count": 0, "items": []}))
        await gi.search_issues_prs("rate limit")
        url = gi._http.request.call_args.args[1]
        assert "search_type" not in url

    @pytest.mark.anyio
    async def test_hybrid_search_type_is_sent_in_url(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data={"total_count": 0, "items": []}))
        await gi.search_issues_prs("is:issue login fails", search_type="hybrid")
        url = gi._http.request.call_args.args[1]
        assert "advanced_search=true&search_type=hybrid" in url

    @pytest.mark.anyio
    async def test_the_result_names_the_search_that_ran_and_the_fallback_reason(self, gi: GitHubIntegration):
        payload = {
            "total_count": 0,
            "items": [],
            "search_type": "lexical",
            "lexical_fallback_reason": ["non_issue_target"],
        }
        gi._http.request = AsyncMock(return_value=mock_response(json_data=payload))
        result = await gi.search_issues_prs("is:pr login fails", search_type="semantic")
        assert result["search_type"] == "lexical"
        assert result["lexical_fallback_reason"] == ["non_issue_target"]

    @pytest.mark.anyio
    async def test_empty_query_is_rejected(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock()
        with pytest.raises(GitHubValidationError):
            await gi.search_issues_prs("   ")
        gi._http.request.assert_not_called()


class TestMilestones:
    @pytest.mark.anyio
    async def test_update_milestone_resolves_the_title_then_patches_by_number(self, gi: GitHubIntegration):
        responses = iter(
            [
                mock_response(json_data=[milestone_payload()]),
                mock_response(json_data=milestone_payload(state="closed")),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        result = await gi.update_milestone("o", "r", "v2.0", state="closed")
        patch_call = gi._http.request.call_args_list[1]
        assert patch_call.args[0] == "PATCH"
        assert patch_call.args[1].endswith("/milestones/3")
        assert patch_call.kwargs["json"] == {"state": "closed"}
        assert result["state"] == "closed"

    @pytest.mark.anyio
    async def test_update_milestone_renames_without_touching_the_rest(self, gi: GitHubIntegration):
        responses = iter(
            [
                mock_response(json_data=[milestone_payload()]),
                mock_response(json_data=milestone_payload(title="v2.1")),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.update_milestone("o", "r", "v2.0", new_title="v2.1")
        assert gi._http.request.call_args_list[1].kwargs["json"] == {"title": "v2.1"}

    @pytest.mark.anyio
    async def test_update_milestone_rejects_a_call_with_nothing_to_change(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock()
        with pytest.raises(GitHubValidationError, match="new_title, description, due_on or state"):
            await gi.update_milestone("o", "r", "v2.0")
        gi._http.request.assert_not_called()

    @pytest.mark.anyio
    async def test_an_unknown_title_names_it_in_the_error(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=[]))
        with pytest.raises(GitHubNotFoundError, match="No milestone titled 'v9.9'"):
            await gi.update_milestone("o", "r", "v9.9", state="closed")

    @pytest.mark.anyio
    async def test_the_lookup_reads_closed_milestones_too(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=[]))
        with pytest.raises(GitHubNotFoundError):
            await gi.update_milestone("o", "r", "v9.9", state="open")
        assert gi._http.request.call_args.kwargs["params"]["state"] == "all"

    @pytest.mark.anyio
    async def test_the_lookup_pages_past_the_first_hundred(self, gi: GitHubIntegration):
        first = [milestone_payload(number=n, title=f"m{n}") for n in range(100)]
        responses = iter(
            [
                mock_response(json_data=first),
                mock_response(json_data=[milestone_payload(number=101, title="v2.0")]),
                mock_response(json_data=milestone_payload(number=101, state="closed")),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.update_milestone("o", "r", "v2.0", state="closed")
        assert gi._http.request.call_args_list[1].kwargs["params"]["page"] == 2
        assert gi._http.request.call_args_list[2].args[1].endswith("/milestones/101")

    @pytest.mark.anyio
    async def test_create_issue_files_it_under_a_milestone(self, gi: GitHubIntegration):
        responses = iter(
            [
                mock_response(json_data=[milestone_payload()]),
                mock_response(json_data=issue_payload(milestone={"title": "v2.0"})),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        result = await gi.create_issue("o", "r", "A bug", "Details", ["bug"], milestone="v2.0")
        assert gi._http.request.call_args.kwargs["json"]["milestone"] == 3
        assert result["milestone"] == "v2.0"

    @pytest.mark.anyio
    async def test_create_issue_without_a_milestone_sends_none_and_looks_nothing_up(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=issue_payload()))
        result = await gi.create_issue("o", "r", "A bug", "Details", ["bug"])
        assert gi._http.request.call_count == 1
        assert "milestone" not in gi._http.request.call_args.kwargs["json"]
        assert result["milestone"] is None

    @pytest.mark.anyio
    async def test_set_issue_milestone_files_an_existing_issue(self, gi: GitHubIntegration):
        responses = iter(
            [
                mock_response(json_data=[milestone_payload()]),
                mock_response(json_data=issue_payload(milestone={"title": "v2.0"})),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        result = await gi.set_issue_milestone("o", "r", 7, "v2.0")
        assert gi._http.request.call_args.kwargs["json"] == {"milestone": 3}
        assert result["milestone"] == "v2.0"

    @pytest.mark.anyio
    async def test_clearing_sends_an_explicit_null(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=issue_payload()))
        result = await gi.set_issue_milestone("o", "r", 7)
        assert gi._http.request.call_args.kwargs["json"] == {"milestone": None}
        assert gi._http.request.call_count == 1
        assert result["milestone"] is None


class TestMilestoneSentinel:
    """An empty string and a null both mean no milestone, on both tools that take one."""

    @pytest.mark.anyio
    async def test_create_issue_reads_an_empty_string_the_same_way(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=issue_payload()))
        await gi.create_issue("o", "r", "A bug", "Details", ["bug"], milestone="")
        assert "milestone" not in gi._http.request.call_args.kwargs["json"]

    @pytest.mark.anyio
    async def test_set_issue_milestone_reads_an_empty_string_as_clear(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=issue_payload()))
        await gi.set_issue_milestone("o", "r", 7, "")
        assert gi._http.request.call_args.kwargs["json"] == {"milestone": None}

    @pytest.mark.anyio
    async def test_both_tools_declare_the_same_milestone_schema(self):
        import inspect as _inspect

        shapes = {
            name: _inspect.signature(getattr(GitHubIntegration, name)).parameters["milestone"]
            for name in ("create_issue", "set_issue_milestone")
        }
        assert len({str(p.annotation) for p in shapes.values()}) == 1, shapes
        assert {p.default for p in shapes.values()} == {None}
