"""Tests for github_merge_pr: the checks it reads before merging, the head SHA it
sends with the merge, and the commit_title it requires. See #459."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastmcp.exceptions import ToolError

from mcp_github import github_integration
from mcp_github.exceptions import GitHubValidationError
from mcp_github.github_integration import GitHubIntegration

from .test_github_integration import _mock_response

TITLE = "feat(tools): enforce the merge preconditions (#459)"
HEAD = "e733b3622413f1fe9b3f2d4ce16a6051f2e2be6b"


def _checks(overall: str = "passing", head_sha: str | None = HEAD, truncated: bool = False) -> dict:
    return {
        "pr_number": 42,
        "head_sha": head_sha,
        "overall": overall,
        "check_runs": [],
        "commit_statuses": [],
        "truncated": truncated,
    }


def _reading(checks: dict):
    return patch.object(GitHubIntegration, "get_pr_status_checks", new_callable=AsyncMock, return_value=checks)


class TestCommitTitle:
    @pytest.mark.anyio
    async def test_missing_title_is_refused_before_github(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock()
        with _reading(_checks()), pytest.raises(GitHubValidationError) as excinfo:
            await gi.merge_pr("owner", "repo", 42)
        assert "commit_title" in str(excinfo.value)
        assert "github_get_skill('pr-management')" in str(excinfo.value)
        gi._http.request.assert_not_called()

    @pytest.mark.anyio
    async def test_blank_title_is_refused(self, gi: GitHubIntegration):
        with _reading(_checks()), pytest.raises(GitHubValidationError):
            await gi.merge_pr("owner", "repo", 42, commit_title="   ")

    @pytest.mark.anyio
    @pytest.mark.parametrize("title", ["Update README", "fix: empty diff", "[WIP] cache work", "Fix(tools): caps"])
    async def test_title_off_the_convention_is_refused(self, gi: GitHubIntegration, title: str):
        gi._http.request = AsyncMock()
        with _reading(_checks()), pytest.raises(GitHubValidationError) as excinfo:
            await gi.merge_pr("owner", "repo", 42, commit_title=title)
        assert repr(title) in str(excinfo.value)
        gi._http.request.assert_not_called()

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "title",
        [TITLE, "chore(deps): bump numpy from 2.5.2 to 2.5.3", "docs(docker/k8s): document the probes"],
    )
    async def test_title_on_the_convention_is_accepted(self, gi: GitHubIntegration, title: str):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data={"merged": True}))
        with _reading(_checks()):
            await gi.merge_pr("owner", "repo", 42, commit_title=title)
        assert gi._http.request.call_args.kwargs["json"]["commit_title"] == title

    @pytest.mark.anyio
    async def test_empty_pattern_checks_presence_only(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data={"merged": True}))
        with patch.object(github_integration, "MERGE_COMMIT_TITLE_PATTERN", ""), _reading(_checks()):
            await gi.merge_pr("owner", "repo", 42, commit_title="Update README")
        gi._http.request.assert_called_once()

    @pytest.mark.anyio
    async def test_title_is_checked_before_the_checks_are_read(self, gi: GitHubIntegration):
        with _reading(_checks()) as reading, pytest.raises(GitHubValidationError):
            await gi.merge_pr("owner", "repo", 42)
        reading.assert_not_called()


class TestCheckGate:
    @pytest.mark.anyio
    @pytest.mark.parametrize("overall", ["failing", "pending", "unknown"])
    async def test_checks_not_passing_are_refused(self, gi: GitHubIntegration, overall: str):
        gi._http.request = AsyncMock()
        with _reading(_checks(overall)), pytest.raises(GitHubValidationError) as excinfo:
            await gi.merge_pr("owner", "repo", 42, commit_title=TITLE)
        text = str(excinfo.value)
        assert overall in text
        assert "force=True" in text
        assert "github_get_skill('pr-management')" in text
        gi._http.request.assert_not_called()

    @pytest.mark.anyio
    async def test_truncated_read_is_refused(self, gi: GitHubIntegration):
        """A capped read comes back as unknown, which is not a pass."""
        with _reading(_checks("unknown", truncated=True)), pytest.raises(GitHubValidationError):
            await gi.merge_pr("owner", "repo", 42, commit_title=TITLE)

    @pytest.mark.anyio
    async def test_force_merges_over_failing_checks(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data={"merged": True}))
        with _reading(_checks("failing")):
            result = await gi.merge_pr("owner", "repo", 42, commit_title=TITLE, force=True)
        assert result == {"merged": True}

    @pytest.mark.anyio
    async def test_force_does_not_waive_the_title(self, gi: GitHubIntegration):
        with _reading(_checks("failing")), pytest.raises(GitHubValidationError):
            await gi.merge_pr("owner", "repo", 42, force=True)

    @pytest.mark.anyio
    async def test_checks_are_read_for_the_pr_being_merged(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data={"merged": True}))
        with _reading(_checks()) as reading:
            await gi.merge_pr("owner", "repo", 42, commit_title=TITLE)
        reading.assert_awaited_once_with("owner", "repo", 42)


class TestMergePayload:
    @pytest.mark.anyio
    async def test_head_sha_goes_with_the_merge(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data={"merged": True}))
        with _reading(_checks()):
            await gi.merge_pr("owner", "repo", 42, commit_title=TITLE)
        assert gi._http.request.call_args.kwargs["json"]["sha"] == HEAD

    @pytest.mark.anyio
    async def test_no_head_sha_sends_no_sha(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data={"merged": True}))
        with _reading(_checks(head_sha=None)):
            await gi.merge_pr("owner", "repo", 42, commit_title=TITLE, force=True)
        assert "sha" not in gi._http.request.call_args.kwargs["json"]

    @pytest.mark.anyio
    async def test_payload_carries_every_field(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data={"merged": True}))
        with _reading(_checks()):
            await gi.merge_pr(
                "owner",
                "repo",
                42,
                commit_title=TITLE,
                commit_message="Custom message",
                merge_method="rebase",
            )
        assert gi._http.request.call_args.kwargs["json"] == {
            "merge_method": "rebase",
            "commit_title": TITLE,
            "commit_message": "Custom message",
            "sha": HEAD,
        }

    @pytest.mark.anyio
    async def test_merges_without_ctx(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data={"merged": True}))
        with _reading(_checks()):
            result = await gi.merge_pr("owner", "repo", 42, commit_title=TITLE)
        assert result == {"merged": True}

    @pytest.mark.anyio
    async def test_merge_does_not_accept_ctx_kwarg(self, gi: GitHubIntegration):
        with pytest.raises(TypeError):
            await gi.merge_pr("owner", "repo", 42, commit_title=TITLE, ctx=object())  # type: ignore[call-arg]


class TestGitHubRefusals:
    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("status", "message"),
        [(405, "Pull Request is not mergeable"), (409, "Head branch was modified")],
    )
    async def test_refusal_carries_the_status_and_message(self, gi: GitHubIntegration, status: int, message: str):
        gi._http.request = AsyncMock(return_value=_mock_response(status_code=status, json_data={"message": message}))
        with _reading(_checks()), pytest.raises(ToolError) as excinfo:
            await gi.merge_pr("owner", "repo", 42, commit_title=TITLE)
        text = str(excinfo.value)
        assert message in text
        assert str(status) in text


class TestHeadSha:
    @pytest.mark.anyio
    async def test_status_checks_carry_the_head_commit(self, gi: GitHubIntegration):
        data = {
            "repository": {
                "pullRequest": {
                    "headRef": {"target": {"oid": HEAD, "checkSuites": {"nodes": []}, "status": None}}
                }
            }
        }
        with patch.object(GitHubIntegration, "_execute_graphql", new_callable=AsyncMock, return_value=data):
            result = await gi.get_pr_status_checks("owner", "repo", 42)
        assert result["head_sha"] == HEAD

    @pytest.mark.anyio
    async def test_missing_head_reads_as_none(self, gi: GitHubIntegration):
        data = {"repository": {"pullRequest": {"headRef": None}}}
        with patch.object(GitHubIntegration, "_execute_graphql", new_callable=AsyncMock, return_value=data):
            result = await gi.get_pr_status_checks("owner", "repo", 42)
        assert result["head_sha"] is None
        assert result["overall"] == "unknown"
