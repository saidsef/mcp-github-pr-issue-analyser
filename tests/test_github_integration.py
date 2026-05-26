"""Tests for GitHubIntegration — annotations, async HTTP, Context injection."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import httpx
import pytest

from fastmcp.exceptions import ToolError
from mcp_github.exceptions import GitHubValidationError
from mcp_github.github_integration import (
    GitHubIntegration,
    _destructive,
    _read_only,
    _write,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(status_code: int = 200, json_data: dict | None = None, text: str = "") -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status_code
    r.is_success = status_code < 400
    r.json.return_value = json_data if json_data is not None else {}
    r.text = text
    r.reason_phrase = "OK"
    r.headers = {}
    return r


def _mock_ctx() -> AsyncMock:
    ctx = AsyncMock()
    ctx.info = AsyncMock()
    ctx.report_progress = AsyncMock()
    ctx.elicit = AsyncMock()
    return ctx


_EMPTY_CONTRIBUTIONS = {
    "user": {
        "contributionsCollection": {
            "commitContributionsByRepository": [],
            "pullRequestContributionsByRepository": [],
            "issueContributionsByRepository": [],
            "pullRequestReviewContributionsByRepository": [],
            "totalCommitContributions": 0,
            "totalPullRequestContributions": 0,
            "totalIssueContributions": 0,
            "totalPullRequestReviewContributions": 0,
        }
    }
}

_EMPTY_STATUS_CHECKS = {
    "repository": {
        "pullRequest": {
            "headRef": {
                "target": {
                    "checkSuites": {"nodes": [{"checkRuns": {"nodes": []}}]},
                    "status": None,
                }
            }
        }
    }
}


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def gi() -> GitHubIntegration:
    with patch("mcp_github.github_integration.GITHUB_TOKEN", "test-token"):
        instance = GitHubIntegration()
    instance._http = AsyncMock()
    return instance


# ---------------------------------------------------------------------------
# Annotation semantics
# ---------------------------------------------------------------------------


class TestAnnotations:
    def test_read_only_hints(self):
        def fn(): ...

        _read_only(fn)
        ann = fn._mcp_annotations
        assert ann.readOnlyHint is True
        assert ann.destructiveHint is False
        assert ann.idempotentHint is False
        assert fn._mcp_task is False

    def test_read_only_with_task(self):
        def fn(): ...

        _read_only(task=True)(fn)
        ann = fn._mcp_annotations
        assert ann.readOnlyHint is True
        assert fn._mcp_task is True

    def test_write_hints(self):
        def fn(): ...

        _write(fn)
        ann = fn._mcp_annotations
        assert ann.readOnlyHint is False
        assert ann.destructiveHint is False
        assert ann.idempotentHint is False
        assert fn._mcp_task is False

    def test_write_idempotent(self):
        def fn(): ...

        _write(idempotent=True)(fn)
        ann = fn._mcp_annotations
        assert ann.readOnlyHint is False
        assert ann.destructiveHint is False
        assert ann.idempotentHint is True

    def test_destructive_hints(self):
        def fn(): ...

        _destructive(fn)
        ann = fn._mcp_annotations
        assert ann.destructiveHint is True
        assert ann.readOnlyHint is False

    def test_idempotent_tools_annotated_correctly(self, gi: GitHubIntegration):
        for name in ("update_pr_description", "update_pr_branch", "update_issue", "update_assignees"):
            method = getattr(gi, name)
            ann = method._mcp_annotations
            assert ann.idempotentHint is True, f"{name} should have idempotentHint=True"
            assert ann.destructiveHint is False, f"{name} should not be destructive"

    def test_merge_pr_is_write_not_destructive(self, gi: GitHubIntegration):
        ann = gi.merge_pr._mcp_annotations
        assert ann.destructiveHint is False
        assert ann.readOnlyHint is False


# ---------------------------------------------------------------------------
# Connection pooling — single shared client
# ---------------------------------------------------------------------------


class TestConnectionPooling:
    @pytest.mark.anyio
    async def test_shared_client_not_recreated_per_request(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[{"sha": "abc"}]))
        with patch("httpx.AsyncClient") as mock_cls:
            await gi.get_latest_sha("owner", "repo")
            await gi.get_latest_sha("owner", "repo")
        mock_cls.assert_not_called()

    @pytest.mark.anyio
    async def test_same_client_instance_across_calls(self, gi: GitHubIntegration):
        client_before = gi._http
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[{"sha": "abc"}]))
        await gi.get_latest_sha("owner", "repo")
        assert gi._http is client_before


# ---------------------------------------------------------------------------
# aclose / async context manager
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# merge_pr — elicitation and dead-except removal
# ---------------------------------------------------------------------------


class TestMergePr:
    @pytest.mark.anyio
    async def test_merges_without_ctx(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data={"merged": True}))
        result = await gi.merge_pr("owner", "repo", 42)
        assert result == {"merged": True}

    @pytest.mark.anyio
    async def test_elicitation_accepted_proceeds_with_merge(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data={"merged": True}))
        ctx = _mock_ctx()
        ctx.elicit.return_value = MagicMock(action="accept")
        result = await gi.merge_pr("owner", "repo", 42, ctx=ctx)
        ctx.elicit.assert_called_once()
        assert "squash" in ctx.elicit.call_args[0][0]
        assert result == {"merged": True}

    @pytest.mark.anyio
    async def test_elicitation_declined_raises_validation_error(self, gi: GitHubIntegration):
        ctx = _mock_ctx()
        ctx.elicit.return_value = MagicMock(action="decline")
        with pytest.raises(GitHubValidationError, match="Merge cancelled"):
            await gi.merge_pr("owner", "repo", 42, ctx=ctx)
        gi._http.request.assert_not_called()

    @pytest.mark.anyio
    async def test_elicitation_cancel_raises_validation_error(self, gi: GitHubIntegration):
        ctx = _mock_ctx()
        ctx.elicit.return_value = MagicMock(action="cancel")
        with pytest.raises(GitHubValidationError):
            await gi.merge_pr("owner", "repo", 42, ctx=ctx)
        gi._http.request.assert_not_called()

    @pytest.mark.anyio
    async def test_http_error_propagates_as_tool_error(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(
            return_value=_mock_response(status_code=405, json_data={"message": "Not mergeable"})
        )
        with pytest.raises(ToolError):
            await gi.merge_pr("owner", "repo", 42)

    @pytest.mark.anyio
    async def test_merge_method_included_in_elicitation_prompt(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data={"merged": True}))
        ctx = _mock_ctx()
        ctx.elicit.return_value = MagicMock(action="accept")
        await gi.merge_pr("owner", "repo", 42, merge_method="rebase", ctx=ctx)
        prompt = ctx.elicit.call_args[0][0]
        assert "rebase" in prompt
        assert "42" in prompt


# ---------------------------------------------------------------------------
# get_user_activities — Context progress ordering and completeness
# ---------------------------------------------------------------------------


class TestGetUserActivitiesContext:
    @pytest.mark.anyio
    async def test_no_ctx_runs_without_error(self, gi: GitHubIntegration):
        with patch.object(asyncio, "to_thread", new_callable=AsyncMock, return_value=_EMPTY_CONTRIBUTIONS):
            result = await gi.get_user_activities("user1")
        assert result["username"] == "user1"
        assert result["commits"] == []

    @pytest.mark.anyio
    async def test_pre_call_info_fires_before_graphql(self, gi: GitHubIntegration):
        """ctx.info('Querying...') must appear before the asyncio.to_thread call."""
        order: list[str] = []

        async def fake_to_thread(fn, *args, **kwargs):
            order.append("graphql")
            return _EMPTY_CONTRIBUTIONS

        ctx = _mock_ctx()
        ctx.info.side_effect = lambda msg: order.append(f"info:{msg}")

        with patch.object(asyncio, "to_thread", side_effect=fake_to_thread):
            await gi.get_user_activities("user1", ctx=ctx)

        assert order[0].startswith("info:Querying")
        assert order[1] == "graphql"

    @pytest.mark.anyio
    async def test_progress_reported_five_times(self, gi: GitHubIntegration):
        ctx = _mock_ctx()
        with patch.object(asyncio, "to_thread", new_callable=AsyncMock, return_value=_EMPTY_CONTRIBUTIONS):
            await gi.get_user_activities("user1", ctx=ctx)
        assert ctx.report_progress.call_count == 5
        progress_values = [c.kwargs["progress"] for c in ctx.report_progress.call_args_list]
        assert progress_values == [0, 1, 2, 3, 4]

    @pytest.mark.anyio
    async def test_stage_info_messages_sent(self, gi: GitHubIntegration):
        ctx = _mock_ctx()
        with patch.object(asyncio, "to_thread", new_callable=AsyncMock, return_value=_EMPTY_CONTRIBUTIONS):
            await gi.get_user_activities("user1", ctx=ctx)
        info_calls = [c.args[0] for c in ctx.info.call_args_list]
        # pre-call + 4 stage messages
        assert len(info_calls) == 5
        assert any("commits" in m.lower() for m in info_calls)
        assert any("pull requests" in m.lower() for m in info_calls)
        assert any("issues" in m.lower() for m in info_calls)
        assert any("reviews" in m.lower() for m in info_calls)

    @pytest.mark.anyio
    async def test_progress_total_is_always_four(self, gi: GitHubIntegration):
        ctx = _mock_ctx()
        with patch.object(asyncio, "to_thread", new_callable=AsyncMock, return_value=_EMPTY_CONTRIBUTIONS):
            await gi.get_user_activities("user1", ctx=ctx)
        totals = {c.kwargs["total"] for c in ctx.report_progress.call_args_list}
        assert totals == {4}


# ---------------------------------------------------------------------------
# get_pr_status_checks — check_suites allocation is conditional on ctx
# ---------------------------------------------------------------------------


class TestGetPrStatusChecks:
    @pytest.mark.anyio
    async def test_no_ctx_returns_result_without_info_call(self, gi: GitHubIntegration):
        with patch.object(asyncio, "to_thread", new_callable=AsyncMock, return_value=_EMPTY_STATUS_CHECKS):
            result = await gi.get_pr_status_checks("owner", "repo", 1, ctx=None)
        assert "overall" in result
        assert "check_runs" in result

    @pytest.mark.anyio
    async def test_ctx_info_includes_suite_run_and_status_counts(self, gi: GitHubIntegration):
        ctx = _mock_ctx()
        with patch.object(asyncio, "to_thread", new_callable=AsyncMock, return_value=_EMPTY_STATUS_CHECKS):
            await gi.get_pr_status_checks("owner", "repo", 1, ctx=ctx)
        ctx.info.assert_called_once()
        msg = ctx.info.call_args[0][0]
        assert "check suites" in msg
        assert "runs" in msg
        assert "statuses" in msg

    @pytest.mark.anyio
    async def test_check_suites_not_evaluated_without_ctx(self, gi: GitHubIntegration):
        """Verify check_suites traversal only happens when ctx is provided."""
        data = {
            "repository": {
                "pullRequest": {
                    "headRef": {
                        "target": {
                            "checkSuites": {"nodes": [{"checkRuns": {"nodes": []}}] * 5},
                            "status": None,
                        }
                    }
                }
            }
        }
        ctx = _mock_ctx()
        with patch.object(asyncio, "to_thread", new_callable=AsyncMock, return_value=data):
            await gi.get_pr_status_checks("owner", "repo", 1, ctx=ctx)
        msg = ctx.info.call_args[0][0]
        assert "5 check suites" in msg

    @pytest.mark.anyio
    async def test_overall_status_derived_correctly(self, gi: GitHubIntegration):
        with patch.object(asyncio, "to_thread", new_callable=AsyncMock, return_value=_EMPTY_STATUS_CHECKS):
            result = await gi.get_pr_status_checks("owner", "repo", 1)
        assert result["overall"] == "unknown"
