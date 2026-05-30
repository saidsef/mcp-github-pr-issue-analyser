"""Tests for GitHubIntegration — annotations, async HTTP, Context injection."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import httpx
import pytest
from fastmcp.exceptions import ToolError

from mcp_github.exceptions import GitHubNotFoundError
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
        },
        "repositories": {"totalCount": 0, "nodes": []},
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
# merge_pr — request shape and GitHub error surfacing
# ---------------------------------------------------------------------------


class TestMergePr:
    @pytest.mark.anyio
    async def test_merges_without_ctx(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data={"merged": True}))
        result = await gi.merge_pr("owner", "repo", 42)
        assert result == {"merged": True}

    @pytest.mark.anyio
    async def test_http_error_propagates_as_tool_error_with_github_message(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(
            return_value=_mock_response(status_code=405, json_data={"message": "Not mergeable"})
        )
        with pytest.raises(ToolError) as excinfo:
            await gi.merge_pr("owner", "repo", 42)
        assert "Not mergeable" in str(excinfo.value)
        assert "405" in str(excinfo.value)

    @pytest.mark.anyio
    async def test_merge_405_includes_github_message(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(
            return_value=_mock_response(
                status_code=405, json_data={"message": "Pull Request is not mergeable"}
            )
        )
        with pytest.raises(ToolError) as excinfo:
            await gi.merge_pr("owner", "repo", 251)
        text = str(excinfo.value)
        assert "Pull Request is not mergeable" in text
        assert "405" in text

    @pytest.mark.anyio
    async def test_merge_409_includes_github_message(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(
            return_value=_mock_response(
                status_code=409, json_data={"message": "Head branch was modified"}
            )
        )
        with pytest.raises(ToolError) as excinfo:
            await gi.merge_pr("owner", "repo", 42)
        text = str(excinfo.value)
        assert "Head branch was modified" in text
        assert "409" in text

    @pytest.mark.anyio
    async def test_merge_does_not_accept_ctx_kwarg(self, gi: GitHubIntegration):
        with pytest.raises(TypeError):
            await gi.merge_pr("owner", "repo", 42, ctx=object())  # type: ignore[call-arg]

    @pytest.mark.anyio
    async def test_merge_payload_includes_optional_commit_fields(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data={"merged": True}))
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
    async def test_progress_reported_six_times(self, gi: GitHubIntegration):
        ctx = _mock_ctx()
        with patch.object(asyncio, "to_thread", new_callable=AsyncMock, return_value=_EMPTY_CONTRIBUTIONS):
            await gi.get_user_activities("user1", ctx=ctx)
        assert ctx.report_progress.call_count == 6
        progress_values = [c.kwargs["progress"] for c in ctx.report_progress.call_args_list]
        assert progress_values == [0, 1, 2, 3, 4, 5]

    @pytest.mark.anyio
    async def test_stage_info_messages_sent(self, gi: GitHubIntegration):
        ctx = _mock_ctx()
        with patch.object(asyncio, "to_thread", new_callable=AsyncMock, return_value=_EMPTY_CONTRIBUTIONS):
            await gi.get_user_activities("user1", ctx=ctx)
        info_calls = [c.args[0] for c in ctx.info.call_args_list]
        # pre-call + 5 stage messages
        assert len(info_calls) == 6
        assert any("commits" in m.lower() for m in info_calls)
        assert any("pull requests" in m.lower() for m in info_calls)
        assert any("issues" in m.lower() for m in info_calls)
        assert any("reviews" in m.lower() for m in info_calls)
        assert any("repo stars" in m.lower() for m in info_calls)

    @pytest.mark.anyio
    async def test_progress_total_is_always_five(self, gi: GitHubIntegration):
        ctx = _mock_ctx()
        with patch.object(asyncio, "to_thread", new_callable=AsyncMock, return_value=_EMPTY_CONTRIBUTIONS):
            await gi.get_user_activities("user1", ctx=ctx)
        totals = {c.kwargs["total"] for c in ctx.report_progress.call_args_list}
        assert totals == {5}


# ---------------------------------------------------------------------------
# get_repo_stars_since — new stars within a date window
# ---------------------------------------------------------------------------


class TestGetRepoStarsSince:
    @pytest.mark.anyio
    async def test_returns_repos_sorted_by_new_stars(self, gi: GitHubIntegration):
        repos_payload = [
            {"name": "repo-a", "stargazers_count": 10, "html_url": "https://github.com/u/repo-a", "description": None},
            {"name": "repo-b", "stargazers_count": 5, "html_url": "https://github.com/u/repo-b", "description": "B"},
        ]
        # GitHub returns stargazers oldest-first; reversed() gives newest-first
        # repo-a: 2 new stars (both after cutoff); repo-b: 1 new star (one before, one after)
        sg_a = [{"starred_at": "2099-01-01T00:00:00Z", "user": {}}, {"starred_at": "2099-01-02T00:00:00Z", "user": {}}]
        sg_b = [{"starred_at": "2000-01-01T00:00:00Z", "user": {}}, {"starred_at": "2099-01-01T00:00:00Z", "user": {}}]

        responses = iter([
            _mock_response(json_data=repos_payload),
            _mock_response(json_data=sg_a),
            _mock_response(json_data=sg_b),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))

        result = await gi.get_repo_stars_since("u", since="2090-01-01")

        assert result["username"] == "u"
        assert result["since"] == "2090-01-01T00:00:00Z"
        assert len(result["repos"]) == 2
        assert result["repos"][0]["repo"] == "repo-a"
        assert result["repos"][0]["new_stars"] == 2
        assert result["repos"][1]["repo"] == "repo-b"
        assert result["repos"][1]["new_stars"] == 1

    @pytest.mark.anyio
    async def test_excludes_repos_with_no_new_stars(self, gi: GitHubIntegration):
        repos_payload = [
            {"name": "old-repo", "stargazers_count": 3, "html_url": "https://github.com/u/old-repo", "description": None},
        ]
        sg_old = [{"starred_at": "2000-01-01T00:00:00Z", "user": {}}]  # before cutoff → no new stars

        responses = iter([
            _mock_response(json_data=repos_payload),
            _mock_response(json_data=sg_old),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))

        result = await gi.get_repo_stars_since("u", since="2090-01-01")

        assert result["repos"] == []

    @pytest.mark.anyio
    async def test_top_n_caps_results(self, gi: GitHubIntegration):
        repos_payload = [
            {"name": f"repo-{i}", "stargazers_count": 1, "html_url": f"https://github.com/u/repo-{i}", "description": None}
            for i in range(5)
        ]
        sg_new = [{"starred_at": "2099-06-01T00:00:00Z", "user": {}}]

        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: _mock_response(
            json_data=repos_payload if "repos" in str(a) or not kw.get("params") else sg_new
        ))
        # Simpler: just alternate — first call returns repos, rest return sg_new
        calls = iter(
            [_mock_response(json_data=repos_payload)]
            + [_mock_response(json_data=sg_new)] * 5
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(calls))

        result = await gi.get_repo_stars_since("u", since="2090-01-01", top_n=3)

        assert len(result["repos"]) == 3

    @pytest.mark.anyio
    async def test_default_since_is_30_days_ago(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[]))
        result = await gi.get_repo_stars_since("u")
        # since should be ~30 days ago — just check it's a valid ISO string
        assert result["since"].endswith("Z")
        assert len(result["since"]) == 20


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


# ---------------------------------------------------------------------------
# get_latest_sha + create_tag — empty-repo contract
# ---------------------------------------------------------------------------


class TestGetLatestShaAndCreateTag:
    @pytest.mark.anyio
    async def test_get_latest_sha_empty_repo_returns_none(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[]))
        result = await gi.get_latest_sha("owner", "empty-repo")
        assert result is None

    @pytest.mark.anyio
    async def test_get_latest_sha_returns_sha_when_commits_exist(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[{"sha": "abc123"}]))
        result = await gi.get_latest_sha("owner", "repo")
        assert result == "abc123"

    @pytest.mark.anyio
    async def test_create_tag_empty_repo_raises_github_not_found(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[]))
        with pytest.raises(GitHubNotFoundError, match="No commits found"):
            await gi.create_tag("owner", "empty-repo", "v1.0.0", "First tag")
        gi._http.request.assert_awaited_once()


# ---------------------------------------------------------------------------
# get_pr_status_checks — pagination + truncation
# ---------------------------------------------------------------------------


def _status_page(
    suites: list[dict],
    *,
    has_next: bool = False,
    end_cursor: str | None = None,
    status: dict | None = None,
) -> dict:
    return {
        "repository": {
            "pullRequest": {
                "headRef": {
                    "target": {
                        "checkSuites": {
                            "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
                            "nodes": suites,
                        },
                        "status": status,
                    }
                }
            }
        }
    }


def _suite(runs: list[dict], *, runs_has_next: bool = False, app: str = "GitHub Actions") -> dict:
    return {
        "app": {"name": app},
        "status": "COMPLETED",
        "conclusion": "SUCCESS",
        "checkRuns": {
            "pageInfo": {"hasNextPage": runs_has_next},
            "nodes": runs,
        },
    }


def _run(name: str, conclusion: str = "SUCCESS", status: str = "COMPLETED") -> dict:
    return {"name": name, "status": status, "conclusion": conclusion, "detailsUrl": f"https://x/{name}"}


def _suite_with_id(id_: str, runs: list[dict], *, runs_has_next: bool = False, app: str = "GitHub Actions") -> dict:
    suite = _suite(runs, runs_has_next=runs_has_next, app=app)
    suite["id"] = id_
    suite["checkRuns"]["pageInfo"]["endCursor"] = "runs-cursor" if runs_has_next else None
    return suite


def _runs_page(runs: list[dict], *, has_next: bool = False) -> dict:
    return {
        "node": {
            "checkRuns": {
                "pageInfo": {"hasNextPage": has_next, "endCursor": "next" if has_next else None},
                "nodes": runs,
            }
        }
    }


class TestStatusChecksPagination:
    @pytest.mark.anyio
    async def test_paginates_suites_until_complete(self, gi: GitHubIntegration):
        page1 = _status_page([_suite_with_id("s1", [_run("a")])], has_next=True, end_cursor="cursor-1")
        page2 = _status_page([_suite_with_id("s2", [_run("b")])], has_next=False)
        with patch.object(asyncio, "to_thread", new_callable=AsyncMock, side_effect=[page1, page2]) as p:
            result = await gi.get_pr_status_checks("owner", "repo", 1)
        assert p.await_count == 2
        assert {r["name"] for r in result["check_runs"]} == {"a", "b"}
        assert result["truncated"] is False
        assert result["overall"] == "passing"

    @pytest.mark.anyio
    async def test_truncated_when_suite_cap_hit(self, gi: GitHubIntegration):
        infinite_page = _status_page([_suite_with_id("s1", [_run("x")])], has_next=True, end_cursor="more")
        with patch.object(
            asyncio, "to_thread", new_callable=AsyncMock, side_effect=[infinite_page] * 10
        ) as p:
            result = await gi.get_pr_status_checks("owner", "repo", 1)
        assert p.await_count == 5
        assert result["truncated"] is True

    @pytest.mark.anyio
    async def test_drains_extra_runs_within_suite(self, gi: GitHubIntegration):
        suite_page = _status_page([_suite_with_id("s1", [_run("a")], runs_has_next=True)])
        extra_runs = _runs_page([_run("b"), _run("c")], has_next=False)
        with patch.object(
            asyncio, "to_thread", new_callable=AsyncMock, side_effect=[suite_page, extra_runs]
        ) as p:
            result = await gi.get_pr_status_checks("owner", "repo", 1)
        assert p.await_count == 2
        assert {r["name"] for r in result["check_runs"]} == {"a", "b", "c"}
        assert result["truncated"] is False
        assert result["overall"] == "passing"

    @pytest.mark.anyio
    async def test_truncated_when_run_cap_hit_per_suite(self, gi: GitHubIntegration):
        suite_page = _status_page([_suite_with_id("s1", [_run("a")], runs_has_next=True)])
        infinite_runs = _runs_page([_run("more")], has_next=True)
        with patch.object(
            asyncio,
            "to_thread",
            new_callable=AsyncMock,
            side_effect=[suite_page, *([infinite_runs] * 10)],
        ) as p:
            result = await gi.get_pr_status_checks("owner", "repo", 1)
        # 1 suite query + 5 run-pagination queries (MAX_STATUS_CHECKS_RUN_PAGES_PER_SUITE)
        assert p.await_count == 6
        assert result["truncated"] is True
        assert result["overall"] == "unknown"

    @pytest.mark.anyio
    async def test_truncated_keeps_failure_authoritative(self, gi: GitHubIntegration):
        suite_page = _status_page(
            [_suite_with_id("s1", [_run("failed", conclusion="FAILURE")], runs_has_next=True)]
        )
        infinite_runs = _runs_page([_run("more")], has_next=True)
        with patch.object(
            asyncio,
            "to_thread",
            new_callable=AsyncMock,
            side_effect=[suite_page, *([infinite_runs] * 10)],
        ):
            result = await gi.get_pr_status_checks("owner", "repo", 1)
        assert result["truncated"] is True
        assert result["overall"] == "failing"

    @pytest.mark.anyio
    async def test_drained_runs_inherit_suite_app(self, gi: GitHubIntegration):
        suite_page = _status_page(
            [_suite_with_id("s1", [_run("a")], runs_has_next=True, app="Codacy Production")]
        )
        extra_runs = _runs_page([_run("b")], has_next=False)
        with patch.object(
            asyncio, "to_thread", new_callable=AsyncMock, side_effect=[suite_page, extra_runs]
        ):
            result = await gi.get_pr_status_checks("owner", "repo", 1)
        assert all(r["suite_app"] == "Codacy Production" for r in result["check_runs"])

    @pytest.mark.anyio
    async def test_ctx_info_announces_truncation(self, gi: GitHubIntegration):
        suite_page = _status_page([_suite_with_id("s1", [_run("a")], runs_has_next=True)])
        infinite_runs = _runs_page([_run("x")], has_next=True)
        ctx = _mock_ctx()
        with patch.object(
            asyncio,
            "to_thread",
            new_callable=AsyncMock,
            side_effect=[suite_page, *([infinite_runs] * 10)],
        ):
            await gi.get_pr_status_checks("owner", "repo", 1, ctx=ctx)
        msg = ctx.info.call_args[0][0]
        assert "truncated" in msg
