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

    @pytest.mark.anyio
    async def test_update_issue_returns_trimmed_issue(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=issue_payload(state="closed")))
        result = await gi.update_issue("o", "r", 7, "A bug", "Details", state="closed")
        assert result["state"] == "closed"
        assert result["author"] == "octocat"
        assert set(result) == {
            "number",
            "title",
            "body",
            "state",
            "author",
            "labels",
            "assignees",
            "milestone",
            "html_url",
            "created_at",
            "updated_at",
        }

    @pytest.mark.anyio
    async def test_update_issue_sends_only_the_fields_supplied(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=issue_payload(state="closed")))
        await gi.update_issue("o", "r", 7, state="closed")
        assert gi._http.request.call_args.kwargs["json"] == {"state": "closed"}

    @pytest.mark.anyio
    async def test_update_issue_keeps_labels_when_they_are_omitted(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=issue_payload()))
        await gi.update_issue("o", "r", 7, title="A different title")
        assert "labels" not in gi._http.request.call_args.kwargs["json"]

    @pytest.mark.anyio
    async def test_update_issue_strips_labels_when_an_empty_list_is_explicit(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=issue_payload()))
        await gi.update_issue("o", "r", 7, labels=[])
        assert gi._http.request.call_args.kwargs["json"] == {"labels": []}

    @pytest.mark.anyio
    async def test_update_issue_rejects_a_call_with_nothing_to_change(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock()
        with pytest.raises(GitHubValidationError):
            await gi.update_issue("o", "r", 7)
        gi._http.request.assert_not_called()

    @pytest.mark.anyio
    async def test_update_assignees_all_applied(self, gi: GitHubIntegration):
        payload = issue_payload(assignees=[{**NOISE_USER, "login": "a"}, {**NOISE_USER, "login": "b"}])
        gi._http.request = AsyncMock(return_value=mock_response(json_data=payload))
        result = await gi.update_assignees("o", "r", 7, ["b", "a"])
        assert result == {
            "status": "ok",
            "assignees_requested": ["a", "b"],
            "assignees_applied": ["a", "b"],
            "issue_url": "https://github.com/o/r/issues/7",
        }

    @pytest.mark.anyio
    async def test_update_assignees_partial(self, gi: GitHubIntegration):
        payload = issue_payload(assignees=[{**NOISE_USER, "login": "a"}])
        gi._http.request = AsyncMock(return_value=mock_response(json_data=payload))
        result = await gi.update_assignees("o", "r", 7, ["a", "b"])
        assert result["status"] == "partial"
        assert result["assignees_applied"] == ["a"]
        assert "'b'" in result["message"]
        assert "issue" not in result


class TestListRepoLabels:
    @pytest.mark.anyio
    async def test_returns_trimmed_labels_with_total(self, gi: GitHubIntegration):
        payload = [
            {
                "id": 1,
                "node_id": "LA_abc",
                "url": "https://api.github.com/repos/o/r/labels/bug",
                "name": "bug",
                "description": "Something is not working",
                "color": "d73a4a",
                "default": True,
            },
            {
                "id": 2,
                "node_id": "LA_def",
                "url": "https://api.github.com/repos/o/r/labels/mcp",
                "name": "mcp",
                "description": None,
                "color": "ededed",
                "default": False,
            },
        ]
        gi._http.request = AsyncMock(return_value=mock_response(json_data=payload))
        result = await gi.list_repo_labels("o", "r")
        assert result == {
            "count": 2,
            "has_more": False,
            "labels": [
                {"name": "bug", "description": "Something is not working", "color": "d73a4a"},
                {"name": "mcp", "description": None, "color": "ededed"},
            ],
        }

    @pytest.mark.anyio
    async def test_paging_goes_out_as_params(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=[]))
        result = await gi.list_repo_labels("o", "r", per_page=100, page=2)
        call = gi._http.request.call_args
        assert call.args[1] == "https://api.github.com/repos/o/r/labels"
        assert call.kwargs["params"] == {"per_page": 100, "page": 2}
        assert result == {"count": 0, "has_more": False, "labels": []}


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
    async def test_empty_query_is_rejected(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock()
        with pytest.raises(GitHubValidationError):
            await gi.search_issues_prs("   ")
        gi._http.request.assert_not_called()


class TestMilestones:
    @pytest.mark.anyio
    async def test_list_milestones_is_trimmed_and_carries_issue_counts(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=[milestone_payload()]))
        result = await gi.list_milestones("o", "r")
        assert result["count"] == 1
        assert result["state"] == "open"
        assert result["milestones"][0] == {
            "number": 3,
            "title": "v2.0",
            "description": "the next one",
            "state": "open",
            "due_on": "2026-12-31T23:59:59Z",
            "open_issues": 4,
            "closed_issues": 9,
            "html_url": "https://github.com/o/r/milestone/3",
        }

    @pytest.mark.anyio
    async def test_list_milestones_can_ask_for_closed_ones(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=[]))
        await gi.list_milestones("o", "r", state="closed", per_page=10, page=2)
        params = gi._http.request.call_args.kwargs["params"]
        assert params == {"state": "closed", "per_page": 10, "page": 2}

    @pytest.mark.anyio
    async def test_create_milestone_sends_a_due_date_only_when_given(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=milestone_payload()))
        await gi.create_milestone("o", "r", "v2.0", "the next one")
        assert "due_on" not in gi._http.request.call_args.kwargs["json"]
        await gi.create_milestone("o", "r", "v2.0", due_on="2026-12-31T23:59:59Z")
        assert gi._http.request.call_args.kwargs["json"]["due_on"] == "2026-12-31T23:59:59Z"

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
