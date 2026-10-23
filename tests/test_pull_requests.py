"""Tests for reading, updating, labelling, drafting and merging pull requests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastmcp.exceptions import ToolError

from mcp_github.exceptions import GitHubAPIError, GitHubValidationError
from mcp_github.github_integration import GitHubIntegration
from tests.support import CREATED_PR, label_payload, mock_response, pr_payload


class TestMergePr:
    @pytest.mark.anyio
    async def test_merges_without_ctx(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data={"merged": True}))
        result = await gi.merge_pr("owner", "repo", 42)
        assert result == {"merged": True}

    @pytest.mark.anyio
    async def test_merge_405_includes_github_message(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(
            return_value=mock_response(status_code=405, json_data={"message": "Pull Request is not mergeable"})
        )
        with pytest.raises(ToolError) as excinfo:
            await gi.merge_pr("owner", "repo", 251)
        text = str(excinfo.value)
        assert "Pull Request is not mergeable" in text
        assert "405" in text

    @pytest.mark.anyio
    async def test_merge_409_includes_github_message(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(
            return_value=mock_response(status_code=409, json_data={"message": "Head branch was modified"})
        )
        with pytest.raises(ToolError) as excinfo:
            await gi.merge_pr("owner", "repo", 42)
        text = str(excinfo.value)
        assert "Head branch was modified" in text
        assert "409" in text

    @pytest.mark.anyio
    async def test_merge_payload_includes_optional_commit_fields(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data={"merged": True}))
        await gi.merge_pr(
            "owner",
            "repo",
            42,
            commit_title="Custom title",
            commit_message="Custom message",
            merge_method="rebase",
        )
        kwargs = gi._http.request.call_args.kwargs
        payload = kwargs["json"]
        assert payload == {
            "merge_method": "rebase",
            "commit_title": "Custom title",
            "commit_message": "Custom message",
        }


class TestGetPRContent:
    _ON_BRANCH = {"head": {"sha": "9341d65", "ref": "feature/x"}, "base": {"ref": "main"}}

    @pytest.mark.anyio
    async def test_reports_the_head_sha_and_refs(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=pr_payload(**self._ON_BRANCH)))
        result = await gi.get_pr_content("o", "r", 5)
        assert gi._http.request.call_args.args[1] == "https://api.github.com/repos/o/r/pulls/5"
        assert result["head_sha"] == "9341d65"
        assert result["head_ref"] == "feature/x"
        assert result["base_ref"] == "main"

    @pytest.mark.anyio
    async def test_the_head_sha_reaches_update_pr_branch(self, gi: GitHubIntegration):
        """The guard pr-management recommends had no source until get_pr_content
        carried the head SHA."""
        responses = iter(
            [
                mock_response(json_data=pr_payload(**self._ON_BRANCH)),
                mock_response(json_data={"message": "Updating pull request branch."}),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        content = await gi.get_pr_content("o", "r", 5)
        await gi.update_pr_branch("o", "r", 5, expected_head_sha=content["head_sha"])
        assert gi._http.request.call_args.kwargs["json"] == {"expected_head_sha": "9341d65"}

    @pytest.mark.anyio
    async def test_reports_who_was_asked_to_review(self, gi: GitHubIntegration):
        """Distinguishes nobody having reviewed from nobody having been asked."""
        payload = pr_payload(
            **self._ON_BRANCH,
            requested_reviewers=[{"login": "octocat"}, {"login": "hubot"}],
            requested_teams=[{"slug": "platform"}],
        )
        gi._http.request = AsyncMock(return_value=mock_response(json_data=payload))
        result = await gi.get_pr_content("o", "r", 5)
        assert result["requested_reviewers"] == ["octocat", "hubot"]
        assert result["requested_teams"] == ["platform"]

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("overrides", "expected"),
        [
            ({"draft": True}, ("open", True, False)),
            ({}, ("open", False, False)),
            ({"state": "closed", "merged": True}, ("closed", False, True)),
            ({"state": "closed"}, ("closed", False, False)),
        ],
        ids=["draft", "open", "merged", "abandoned"],
    )
    async def test_one_read_tells_the_pr_states_apart(self, gi: GitHubIntegration, overrides, expected):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=pr_payload(**overrides)))
        result = await gi.get_pr_content("o", "r", 5)
        gi._http.request.assert_awaited_once()
        assert (result["state"], result["is_draft"], result["merged"]) == expected

    @pytest.mark.anyio
    async def test_reports_mergeable_as_github_sends_it_with_the_size(self, gi: GitHubIntegration):
        """GitHub sends null while it is still working mergeability out."""
        payload = pr_payload(
            mergeable=None, mergeable_state="unknown", commits=3, additions=40, deletions=5, changed_files=4
        )
        gi._http.request = AsyncMock(return_value=mock_response(json_data=payload))
        result = await gi.get_pr_content("o", "r", 5)
        gi._http.request.assert_awaited_once()
        assert result["mergeable"] is None
        assert result["mergeable_state"] == "unknown"
        assert (result["commits"], result["additions"], result["deletions"], result["changed_files"]) == (3, 40, 5, 4)

    @pytest.mark.anyio
    async def test_a_pr_nobody_was_asked_to_review_reports_empty(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=pr_payload(head={}, base={})))
        result = await gi.get_pr_content("o", "r", 5)
        assert result["head_sha"] is None
        assert result["requested_reviewers"] == []
        assert result["requested_teams"] == []


class TestGetPRDiff:
    @pytest.mark.anyio
    async def test_short_patch_comes_back_whole(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(text="diff --git a b\n"))
        result = await gi.get_pr_diff("o", "r", 5)
        assert result == {
            "pr_number": 5,
            "patch": "diff --git a b\n",
            "bytes_returned": 15,
            "bytes_total": 15,
            "truncated": False,
        }

    @pytest.mark.anyio
    async def test_long_patch_is_cut_and_says_so(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(text="x" * 100))
        result = await gi.get_pr_diff("o", "r", 5, max_bytes=10)
        assert result["patch"] == "x" * 10
        assert result["bytes_returned"] == 10
        assert result["bytes_total"] == 100
        assert result["truncated"] is True

    @pytest.mark.anyio
    async def test_zero_max_bytes_asks_the_size_alone(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(text="x" * 100))
        result = await gi.get_pr_diff("o", "r", 5, max_bytes=0)
        assert result["patch"] == ""
        assert result["bytes_total"] == 100
        assert result["truncated"] is True

    @pytest.mark.anyio
    async def test_a_split_character_is_dropped_not_mangled(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(text="abé"))
        result = await gi.get_pr_diff("o", "r", 5, max_bytes=3)
        assert result["patch"] == "ab"
        assert result["bytes_total"] == 4

    @pytest.mark.anyio
    async def test_negative_max_bytes_is_rejected(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock()
        with pytest.raises(GitHubValidationError):
            await gi.get_pr_diff("o", "r", 5, max_bytes=-1)
        gi._http.request.assert_not_called()


class TestUpdatePR:
    @pytest.mark.anyio
    async def test_sends_only_the_fields_supplied(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=pr_payload(state="closed")))
        await gi.update_pr("o", "r", 5, state="closed")
        assert gi._http.request.call_args.kwargs["json"] == {"state": "closed"}

    @pytest.mark.anyio
    async def test_title_changes_without_resending_the_body(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=pr_payload()))
        await gi.update_pr("o", "r", 5, title="A better title")
        assert "body" not in gi._http.request.call_args.kwargs["json"]

    @pytest.mark.anyio
    async def test_base_can_be_retargeted(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=pr_payload()))
        await gi.update_pr("o", "r", 5, base="develop")
        assert gi._http.request.call_args.kwargs["json"] == {"base": "develop"}

    @pytest.mark.anyio
    async def test_returns_the_trimmed_pr_content(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=pr_payload(state="closed")))
        result = await gi.update_pr("o", "r", 5, state="closed")
        assert result == {
            "title": "A change",
            "description": "Details",
            "author": "octocat",
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": "2026-07-02T00:00:00Z",
            "state": "closed",
            "is_draft": False,
            "merged": False,
            "mergeable": True,
            "mergeable_state": "clean",
            "head_sha": None,
            "head_ref": None,
            "base_ref": None,
            "requested_reviewers": [],
            "requested_teams": [],
            "commits": 1,
            "additions": 10,
            "deletions": 2,
            "changed_files": 1,
        }

    @pytest.mark.anyio
    async def test_rejects_a_call_with_nothing_to_change(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock()
        with pytest.raises(GitHubValidationError, match="title, body, state, base or labels"):
            await gi.update_pr("o", "r", 5)
        gi._http.request.assert_not_called()


class TestPRLabels:
    @pytest.mark.anyio
    async def test_update_pr_sends_labels_to_the_issues_endpoint(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=label_payload("bug")))
        await gi.update_pr("o", "r", 5, labels=["bug"])
        label_call = gi._http.request.call_args_list[0]
        assert label_call.args == ("PATCH", "https://api.github.com/repos/o/r/issues/5")
        assert label_call.kwargs["json"] == {"labels": ["bug"]}

    @pytest.mark.anyio
    async def test_update_pr_labels_alone_still_returns_pr_content(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=label_payload("bug")))
        result = await gi.update_pr("o", "r", 5, labels=["bug"])
        assert gi._http.request.call_count == 2
        assert gi._http.request.call_args.args == ("GET", "https://api.github.com/repos/o/r/pulls/5")
        assert result["title"] == "A change"
        assert result["state"] == "open"

    @pytest.mark.anyio
    async def test_update_pr_strips_every_label_for_an_empty_list(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=label_payload()))
        await gi.update_pr("o", "r", 5, labels=[])
        assert gi._http.request.call_args_list[0].kwargs["json"] == {"labels": []}

    @pytest.mark.anyio
    async def test_update_pr_sends_the_labels_after_the_other_fields(self, gi: GitHubIntegration):
        responses = iter(
            [
                mock_response(json_data=pr_payload(title="A better title")),
                mock_response(json_data=label_payload("bug")),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.update_pr("o", "r", 5, title="A better title", labels=["bug"])
        calls = gi._http.request.call_args_list
        assert calls[0].args[1].endswith("/pulls/5")
        assert calls[0].kwargs["json"] == {"title": "A better title"}
        assert calls[1].args[1].endswith("/issues/5")

    @pytest.mark.anyio
    async def test_create_pr_applies_labels_and_appends_mcp(self, gi: GitHubIntegration):
        responses = iter(
            [
                mock_response(json_data=CREATED_PR),
                mock_response(json_data=label_payload("bug", "mcp")),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        result = await gi.create_pr("o", "r", "A change", "Details", "feat", "main", labels=["bug"])
        calls = gi._http.request.call_args_list
        assert calls[1].args == ("PATCH", "https://api.github.com/repos/o/r/issues/7")
        assert calls[1].kwargs["json"] == {"labels": ["bug", "mcp"]}
        assert result["labels"] == ["bug", "mcp"]

    @pytest.mark.anyio
    async def test_create_pr_labels_an_empty_list_as_mcp_alone(self, gi: GitHubIntegration):
        responses = iter(
            [
                mock_response(json_data=CREATED_PR),
                mock_response(json_data=label_payload("mcp")),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.create_pr("o", "r", "A change", "Details", "feat", "main", labels=[])
        assert gi._http.request.call_args.kwargs["json"] == {"labels": ["mcp"]}

    @pytest.mark.anyio
    async def test_create_pr_leaves_the_pr_unlabelled_when_labels_are_omitted(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=CREATED_PR))
        result = await gi.create_pr("o", "r", "A change", "Details", "feat", "main")
        assert gi._http.request.call_count == 1
        assert result == {
            "pr_url": "https://github.com/o/r/pull/7",
            "pr_number": 7,
            "status": "open",
            "title": "A change",
        }

    @pytest.mark.anyio
    async def test_create_pr_mcp_label_opt_out(self, gi: GitHubIntegration):
        responses = iter(
            [
                mock_response(json_data=CREATED_PR),
                mock_response(json_data=label_payload("bug")),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        result = await gi.create_pr("o", "r", "A change", "Details", "feat", "main", labels=["bug"], mcp_label=False)
        assert gi._http.request.call_args_list[1].kwargs["json"] == {"labels": ["bug"]}
        assert result["labels"] == ["bug"]


class TestSetPRDraft:
    @pytest.mark.anyio
    async def test_ready_for_review_uses_the_mark_ready_mutation(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=pr_payload()))
        gi._execute_graphql = AsyncMock(
            return_value={"markPullRequestReadyForReview": {"pullRequest": {"number": 5, "isDraft": False, "url": "u"}}}
        )
        result = await gi.set_pr_draft("o", "r", 5, draft=False)
        query, variables = gi._execute_graphql.call_args.args
        assert "markPullRequestReadyForReview" in query
        assert variables == {"pullRequestId": "PR_abc"}
        assert result == {"pr_number": 5, "is_draft": False, "url": "u"}

    @pytest.mark.anyio
    async def test_back_to_draft_uses_the_convert_mutation(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=pr_payload()))
        gi._execute_graphql = AsyncMock(
            return_value={"convertPullRequestToDraft": {"pullRequest": {"number": 5, "isDraft": True, "url": "u"}}}
        )
        result = await gi.set_pr_draft("o", "r", 5, draft=True)
        assert "convertPullRequestToDraft" in gi._execute_graphql.call_args.args[0]
        assert result["is_draft"] is True

    @pytest.mark.anyio
    async def test_missing_node_id_raises_the_api_code(self, gi: GitHubIntegration):
        """The PR fetch succeeded and the payload came back short, so the failure is a
        GitHub API error rather than a validation one. See #475."""
        gi._http.request = AsyncMock(return_value=mock_response(json_data={"number": 5}))
        gi._execute_graphql = AsyncMock()
        with pytest.raises(GitHubAPIError, match=r"\[GITHUB_API_ERROR\] Could not retrieve the node id for PR #5"):
            await gi.set_pr_draft("o", "r", 5, draft=False)
        gi._execute_graphql.assert_not_called()


class TestCreateEmptyCommit:
    _HOME = {"full_name": "o/r", "default_branch": "main"}
    _ON_PR = {"head": {"ref": "dependabot-pip-x", "sha": "p1", "repo": _HOME}, "base": {"ref": "main", "repo": _HOME}}

    @pytest.mark.anyio
    async def test_commits_the_parent_tree_onto_the_pr_head(self, gi: GitHubIntegration):
        responses = iter(
            [
                mock_response(json_data=pr_payload(**self._ON_PR)),
                mock_response(json_data={"sha": "p1", "tree": {"sha": "t1"}}),
                mock_response(
                    json_data={"sha": "c1", "tree": {"sha": "t1"}, "html_url": "https://github.com/o/r/commit/c1"}
                ),
                mock_response(json_data={"ref": "refs/heads/dependabot-pip-x", "object": {"sha": "c1"}}),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        result = await gi.create_empty_commit("o", "r", pr_number=5)
        calls = gi._http.request.call_args_list
        assert calls[2].kwargs["json"] == {
            "message": "chore: trigger CI [dependabot skip]",
            "tree": "t1",
            "parents": ["p1"],
        }
        assert calls[3].args == ("PATCH", "https://api.github.com/repos/o/r/git/refs/heads/dependabot-pip-x")
        assert calls[3].kwargs["json"] == {"sha": "c1", "force": False}
        assert result == {
            "commit_sha": "c1",
            "parent_sha": "p1",
            "parent_tree_sha": "t1",
            "tree_sha": "t1",
            "branch": "dependabot-pip-x",
            "html_url": "https://github.com/o/r/commit/c1",
        }

    @pytest.mark.anyio
    async def test_an_empty_repository_is_a_coded_error(self, gi: GitHubIntegration):
        responses = iter(
            [
                mock_response(json_data={"default_branch": "main"}),
                mock_response(
                    status_code=409, json_data={"message": "Git Repository is empty."}, reason_phrase="Conflict"
                ),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        with pytest.raises(ToolError, match=r"\[GITHUB_API_ERROR\] HTTP 409.*Git Repository is empty"):
            await gi.create_empty_commit("o", "r", branch="feature")
        assert gi._http.request.call_count == 2

    @pytest.mark.anyio
    async def test_a_head_that_moved_is_refused(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=pr_payload(**self._ON_PR)))
        with pytest.raises(GitHubValidationError, match="'dependabot-pip-x' is at p1, not p0"):
            await gi.create_empty_commit("o", "r", pr_number=5, expected_head_sha="p0")
        assert gi._http.request.call_count == 1

    @pytest.mark.anyio
    async def test_the_default_branch_needs_the_opt_in(self, gi: GitHubIntegration):
        responses = iter(
            [mock_response(json_data={"default_branch": "main"}), mock_response(json_data={"object": {"sha": "p1"}})]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        with pytest.raises(GitHubValidationError, match="allow_default_branch=True"):
            await gi.create_empty_commit("o", "r")
        assert gi._http.request.call_count == 2


class TestRequestReviewers:
    _URL = "https://api.github.com/repos/o/r/pulls/5/requested_reviewers"

    @pytest.mark.anyio
    async def test_a_request_posts_both_lists(self, gi: GitHubIntegration):
        payload = pr_payload(requested_reviewers=[{"login": "octocat"}], requested_teams=[{"slug": "platform"}])
        gi._http.request = AsyncMock(return_value=mock_response(status_code=201, json_data=payload))
        result = await gi.request_reviewers("o", "r", 5, reviewers=["octocat"], team_reviewers=["platform"])
        assert gi._http.request.call_args.args == ("POST", self._URL)
        assert gi._http.request.call_args.kwargs["json"] == {"reviewers": ["octocat"], "team_reviewers": ["platform"]}
        assert result["status"] == "ok"
        assert result["requested_reviewers"] == ["octocat"]
        assert result["requested_teams"] == ["platform"]

    @pytest.mark.anyio
    async def test_a_team_only_removal_sends_an_empty_reviewers_list(self, gi: GitHubIntegration):
        """The removal endpoint requires reviewers even when only teams are named."""
        gi._http.request = AsyncMock(
            return_value=mock_response(json_data=pr_payload(requested_reviewers=[], requested_teams=[]))
        )
        result = await gi.request_reviewers("o", "r", 5, team_reviewers=["platform"], remove=True)
        assert gi._http.request.call_args.args == ("DELETE", self._URL)
        assert gi._http.request.call_args.kwargs["json"] == {"reviewers": [], "team_reviewers": ["platform"]}
        assert result["status"] == "ok"

    @pytest.mark.anyio
    async def test_a_reviewer_missing_from_the_reply_is_named(self, gi: GitHubIntegration):
        payload = pr_payload(requested_reviewers=[{"login": "octocat"}], requested_teams=[])
        gi._http.request = AsyncMock(return_value=mock_response(status_code=201, json_data=payload))
        result = await gi.request_reviewers("o", "r", 5, reviewers=["octocat"], team_reviewers=["platform"])
        assert result["status"] == "partial"
        assert "'platform'" in result["message"]
        assert "octocat" not in result["message"]

    @pytest.mark.anyio
    async def test_a_422_names_the_logins_asked_for(self, gi: GitHubIntegration):
        refusal = {"message": "Reviews may only be requested from collaborators."}
        gi._http.request = AsyncMock(return_value=mock_response(status_code=422, json_data=refusal))
        with pytest.raises(
            ToolError, match=r"request review from octocat, stranger on PR #5: .*GitHub said: Reviews may only"
        ):
            await gi.request_reviewers("o", "r", 5, reviewers=["octocat", "stranger"])
