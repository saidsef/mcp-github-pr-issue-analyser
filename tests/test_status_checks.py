"""Tests for the CI status of a pull request's head commit."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from mcp_github.github_integration import GitHubIntegration
from tests.support import EMPTY_STATUS_CHECKS, mock_ctx


class TestGetPrStatusChecks:
    @pytest.mark.anyio
    async def test_no_ctx_returns_result_without_info_call(self, gi: GitHubIntegration):
        with patch.object(
            GitHubIntegration, "_execute_graphql", new_callable=AsyncMock, return_value=EMPTY_STATUS_CHECKS
        ):
            result = await gi.get_pr_status_checks("owner", "repo", 1, ctx=None)
        assert "overall" in result
        assert "check_runs" in result

    @pytest.mark.anyio
    async def test_ctx_info_counts_the_suites_runs_and_statuses(self, gi: GitHubIntegration):
        data = _status_page([_suite([_run("a"), _run("b")])] * 5)
        ctx = mock_ctx()
        with patch.object(GitHubIntegration, "_execute_graphql", new_callable=AsyncMock, return_value=data):
            await gi.get_pr_status_checks("owner", "repo", 1, ctx=ctx)
        ctx.info.assert_called_once()
        assert ctx.info.call_args[0][0] == "Found 5 check suites, 10 runs, 0 legacy statuses"

    @pytest.mark.anyio
    async def test_overall_status_derived_correctly(self, gi: GitHubIntegration):
        with patch.object(
            GitHubIntegration, "_execute_graphql", new_callable=AsyncMock, return_value=EMPTY_STATUS_CHECKS
        ):
            result = await gi.get_pr_status_checks("owner", "repo", 1)
        assert result["overall"] == "unknown"


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
        with patch.object(
            GitHubIntegration, "_execute_graphql", new_callable=AsyncMock, side_effect=[page1, page2]
        ) as p:
            result = await gi.get_pr_status_checks("owner", "repo", 1)
        assert p.await_count == 2
        assert {r["name"] for r in result["check_runs"]} == {"a", "b"}
        assert result["truncated"] is False
        assert result["overall"] == "passing"

    @pytest.mark.anyio
    async def test_truncated_when_suite_cap_hit(self, gi: GitHubIntegration):
        infinite_page = _status_page([_suite_with_id("s1", [_run("x")])], has_next=True, end_cursor="more")
        with patch.object(
            GitHubIntegration, "_execute_graphql", new_callable=AsyncMock, side_effect=[infinite_page] * 10
        ) as p:
            result = await gi.get_pr_status_checks("owner", "repo", 1)
        assert p.await_count == 5
        assert result["truncated"] is True

    @pytest.mark.anyio
    async def test_drains_extra_runs_within_suite(self, gi: GitHubIntegration):
        suite_page = _status_page([_suite_with_id("s1", [_run("a")], runs_has_next=True)])
        extra_runs = _runs_page([_run("b"), _run("c")], has_next=False)
        with patch.object(
            GitHubIntegration, "_execute_graphql", new_callable=AsyncMock, side_effect=[suite_page, extra_runs]
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
            GitHubIntegration,
            "_execute_graphql",
            new_callable=AsyncMock,
            side_effect=[suite_page, *([infinite_runs] * 10)],
        ) as p:
            result = await gi.get_pr_status_checks("owner", "repo", 1)
        assert p.await_count == 6
        assert result["truncated"] is True
        assert result["overall"] == "unknown"

    @pytest.mark.anyio
    async def test_truncated_keeps_failure_authoritative(self, gi: GitHubIntegration):
        suite_page = _status_page([_suite_with_id("s1", [_run("failed", conclusion="FAILURE")], runs_has_next=True)])
        infinite_runs = _runs_page([_run("more")], has_next=True)
        with patch.object(
            GitHubIntegration,
            "_execute_graphql",
            new_callable=AsyncMock,
            side_effect=[suite_page, *([infinite_runs] * 10)],
        ):
            result = await gi.get_pr_status_checks("owner", "repo", 1)
        assert result["truncated"] is True
        assert result["overall"] == "failing"

    @pytest.mark.anyio
    async def test_drained_runs_inherit_suite_app(self, gi: GitHubIntegration):
        suite_page = _status_page([_suite_with_id("s1", [_run("a")], runs_has_next=True, app="Codacy Production")])
        extra_runs = _runs_page([_run("b")], has_next=False)
        with patch.object(
            GitHubIntegration, "_execute_graphql", new_callable=AsyncMock, side_effect=[suite_page, extra_runs]
        ):
            result = await gi.get_pr_status_checks("owner", "repo", 1)
        assert all(r["suite_app"] == "Codacy Production" for r in result["check_runs"])

    @pytest.mark.anyio
    async def test_ctx_info_announces_truncation(self, gi: GitHubIntegration):
        suite_page = _status_page([_suite_with_id("s1", [_run("a")], runs_has_next=True)])
        infinite_runs = _runs_page([_run("x")], has_next=True)
        ctx = mock_ctx()
        with patch.object(
            GitHubIntegration,
            "_execute_graphql",
            new_callable=AsyncMock,
            side_effect=[suite_page, *([infinite_runs] * 10)],
        ):
            await gi.get_pr_status_checks("owner", "repo", 1, ctx=ctx)
        msg = ctx.info.call_args[0][0]
        assert "truncated" in msg
