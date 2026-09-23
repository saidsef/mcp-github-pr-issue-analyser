"""Tests for github_merge_pr: the commit_title it requires and the checks it reads
before merging. See #459."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastmcp.exceptions import ToolError

from mcp_github.exceptions import GitHubValidationError
from mcp_github.github_integration import GitHubIntegration

from .test_github_integration import _mock_response

TITLE = "feat(tools): enforce the merge preconditions (#459)"
SKILL = "github_get_skill('pr-management')"


def _checks(overall: str = "passing"):
    result = {"pr_number": 42, "overall": overall, "check_runs": [], "commit_statuses": [], "truncated": False}
    return patch.object(GitHubIntegration, "get_pr_status_checks", new_callable=AsyncMock, return_value=result)


class TestCommitTitle:
    @pytest.mark.anyio
    @pytest.mark.parametrize("title", [None, "", "Update README", "fix: empty diff", "[WIP] cache work", "Fix(tools): caps"])
    async def test_missing_or_off_convention_title_is_refused_before_github(self, gi: GitHubIntegration, title):
        gi._http.request = AsyncMock()
        with _checks() as reading, pytest.raises(GitHubValidationError) as excinfo:
            await gi.merge_pr("owner", "repo", 42, commit_title=title)
        assert "commit_title" in str(excinfo.value)
        assert SKILL in str(excinfo.value)
        reading.assert_not_called()
        gi._http.request.assert_not_called()

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "title",
        [TITLE, "chore(deps): bump numpy from 2.5.2 to 2.5.3", "docs(docker/k8s): document the probes"],
    )
    async def test_title_on_the_convention_is_sent(self, gi: GitHubIntegration, title: str):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data={"merged": True}))
        with _checks():
            await gi.merge_pr("owner", "repo", 42, commit_title=title)
        assert gi._http.request.call_args.kwargs["json"]["commit_title"] == title


class TestCheckGate:
    @pytest.mark.anyio
    @pytest.mark.parametrize("overall", ["failing", "pending", "unknown"])
    async def test_checks_not_passing_are_refused(self, gi: GitHubIntegration, overall: str):
        gi._http.request = AsyncMock()
        with _checks(overall), pytest.raises(GitHubValidationError) as excinfo:
            await gi.merge_pr("owner", "repo", 42, commit_title=TITLE)
        assert overall in str(excinfo.value)
        assert "force=True" in str(excinfo.value)
        assert SKILL in str(excinfo.value)
        gi._http.request.assert_not_called()

    @pytest.mark.anyio
    async def test_force_merges_over_checks_that_are_not_passing(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data={"merged": True}))
        with _checks("unknown"):
            result = await gi.merge_pr("owner", "repo", 42, commit_title=TITLE, force=True)
        assert result == {"merged": True}

    @pytest.mark.anyio
    async def test_force_does_not_waive_the_title(self, gi: GitHubIntegration):
        with _checks("unknown"), pytest.raises(GitHubValidationError):
            await gi.merge_pr("owner", "repo", 42, force=True)

    @pytest.mark.anyio
    async def test_checks_are_read_for_the_pr_being_merged(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data={"merged": True}))
        with _checks() as reading:
            result = await gi.merge_pr("owner", "repo", 42, commit_title=TITLE)
        reading.assert_awaited_once_with("owner", "repo", 42)
        assert result == {"merged": True}


class TestMergePayload:
    @pytest.mark.anyio
    async def test_payload_carries_every_field(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data={"merged": True}))
        with _checks():
            await gi.merge_pr("owner", "repo", 42, commit_title=TITLE, commit_message="Body", merge_method="rebase")
        assert gi._http.request.call_args.kwargs["json"] == {
            "merge_method": "rebase",
            "commit_title": TITLE,
            "commit_message": "Body",
        }

    @pytest.mark.anyio
    async def test_merge_does_not_accept_ctx_kwarg(self, gi: GitHubIntegration):
        with pytest.raises(TypeError):
            await gi.merge_pr("owner", "repo", 42, commit_title=TITLE, ctx=object())  # type: ignore[call-arg]

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("status", "message"),
        [(405, "Pull Request is not mergeable"), (409, "Head branch was modified")],
    )
    async def test_a_refusal_from_github_carries_its_status_and_message(self, gi: GitHubIntegration, status, message):
        gi._http.request = AsyncMock(return_value=_mock_response(status_code=status, json_data={"message": message}))
        with _checks(), pytest.raises(ToolError) as excinfo:
            await gi.merge_pr("owner", "repo", 42, commit_title=TITLE)
        assert message in str(excinfo.value)
        assert str(status) in str(excinfo.value)
