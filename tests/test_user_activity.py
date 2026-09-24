"""Tests for a user's contribution history."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from mcp_github.activity import ACTIVITY_SECTIONS, ACTIVITY_STAGES
from mcp_github.github_integration import GitHubIntegration
from tests.support import EMPTY_CONTRIBUTIONS, mock_ctx


class TestGetUserActivitiesContext:
    """Progress reporting is derived from ACTIVITY_SECTIONS, so these assert the
    relationship rather than hardcoded counts. See #298."""

    @pytest.mark.anyio
    async def test_no_ctx_runs_without_error(self, gi: GitHubIntegration):
        with patch.object(
            GitHubIntegration, "_execute_graphql", new_callable=AsyncMock, return_value=EMPTY_CONTRIBUTIONS
        ):
            result = await gi.get_user_activities("user1")
        assert result["username"] == "user1"
        assert result["commits"] == []

    @pytest.mark.anyio
    async def test_pre_call_info_fires_before_graphql(self, gi: GitHubIntegration):
        """ctx.info('Querying...') must appear before the GraphQL call."""
        order: list[str] = []

        async def fake_graphql(*args, **kwargs):
            order.append("graphql")
            return EMPTY_CONTRIBUTIONS

        ctx = mock_ctx()
        ctx.info.side_effect = lambda msg: order.append(f"info:{msg}")

        with patch.object(GitHubIntegration, "_execute_graphql", side_effect=fake_graphql):
            await gi.get_user_activities("user1", ctx=ctx)

        assert order[0].startswith("info:Querying")
        assert order[1] == "graphql"

    @pytest.mark.anyio
    async def test_progress_runs_from_zero_to_total(self, gi: GitHubIntegration):
        """One tick per section, plus the repo-stars stage, plus a final tick, every
        one reporting the same total."""
        ctx = mock_ctx()
        with patch.object(
            GitHubIntegration, "_execute_graphql", new_callable=AsyncMock, return_value=EMPTY_CONTRIBUTIONS
        ):
            await gi.get_user_activities("user1", ctx=ctx)
        ticks = ctx.report_progress.call_args_list
        assert [c.kwargs["progress"] for c in ticks] == list(range(ACTIVITY_STAGES + 1))
        assert {c.kwargs["total"] for c in ticks} == {ACTIVITY_STAGES}

    @pytest.mark.anyio
    async def test_every_section_announces_itself(self, gi: GitHubIntegration):
        """The pre-call message, one per section, then repo stars."""
        ctx = mock_ctx()
        with patch.object(
            GitHubIntegration, "_execute_graphql", new_callable=AsyncMock, return_value=EMPTY_CONTRIBUTIONS
        ):
            await gi.get_user_activities("user1", ctx=ctx)
        info_calls = [c.args[0] for c in ctx.info.call_args_list]
        assert len(info_calls) == ACTIVITY_STAGES + 1
        assert info_calls[1:-1] == [s.message for s in ACTIVITY_SECTIONS]
        assert "repo stars" in info_calls[-1].lower()

    @pytest.mark.anyio
    async def test_result_carries_a_key_per_section(self, gi: GitHubIntegration):
        """Every declared section must reach the result, so a new section cannot
        be added to the table and silently dropped from the payload."""
        with patch.object(
            GitHubIntegration, "_execute_graphql", new_callable=AsyncMock, return_value=EMPTY_CONTRIBUTIONS
        ):
            result = await gi.get_user_activities("user1")
        for section in ACTIVITY_SECTIONS:
            assert section.field in result


def _repo_block(owner: str, name: str, nodes: list[dict]) -> dict:
    return {
        "repository": {"name": name, "owner": {"login": owner}},
        "contributions": {"nodes": nodes},
    }


def _commit_node(n: int) -> dict:
    return {"occurredAt": f"2025-03-0{n}T10:00:00Z", "commitCount": n, "url": f"https://c/{n}"}


_FILTERABLE_CONTRIBUTIONS = {
    "user": {
        "contributionsCollection": {
            "totalCommitContributions": 99,
            "totalPullRequestContributions": 0,
            "totalIssueContributions": 0,
            "totalPullRequestReviewContributions": 0,
            "commitContributionsByRepository": [
                _repo_block("acme", "widget", [_commit_node(1), _commit_node(2)]),
                _repo_block("beta", "widget", [_commit_node(3)]),
                _repo_block("beta", "gadget", [_commit_node(4)]),
            ],
            "pullRequestContributionsByRepository": [],
            "issueContributionsByRepository": [],
            "pullRequestReviewContributionsByRepository": [],
        },
        "repositories": {
            "nodes": [
                {
                    "name": "widget",
                    "owner": {"login": "acme"},
                    "url": "https://github.com/acme/widget",
                    "description": "W",
                    "stargazerCount": 10,
                },
                {
                    "name": "gadget",
                    "owner": {"login": "beta"},
                    "url": "https://github.com/beta/gadget",
                    "description": None,
                    "stargazerCount": 5,
                },
            ]
        },
    }
}


async def _activities(gi: GitHubIntegration, **kwargs):
    with patch.object(
        GitHubIntegration, "_execute_graphql", new_callable=AsyncMock, return_value=_FILTERABLE_CONTRIBUTIONS
    ):
        return await gi.get_user_activities("user1", **kwargs)


class TestGetUserActivitiesFiltering:
    @pytest.mark.anyio
    async def test_unfiltered_returns_every_contribution(self, gi: GitHubIntegration):
        result = await _activities(gi)
        assert [c["commit_count"] for c in result["commits"]] == [1, 2, 3, 4]

    @pytest.mark.anyio
    async def test_org_filter_keeps_only_that_owner(self, gi: GitHubIntegration):
        result = await _activities(gi, org="beta")
        assert {c["owner"] for c in result["commits"]} == {"beta"}
        assert len(result["commits"]) == 2

    @pytest.mark.anyio
    async def test_repo_filter_spans_owners(self, gi: GitHubIntegration):
        """'widget' exists under two owners, so both must come back."""
        result = await _activities(gi, repo="widget")
        assert {c["owner"] for c in result["commits"]} == {"acme", "beta"}
        assert len(result["commits"]) == 3

    @pytest.mark.anyio
    async def test_org_and_repo_combine(self, gi: GitHubIntegration):
        result = await _activities(gi, org="beta", repo="widget")
        assert len(result["commits"]) == 1
        assert result["commits"][0]["commit_count"] == 3

    @pytest.mark.anyio
    async def test_filters_are_case_insensitive(self, gi: GitHubIntegration):
        assert await _activities(gi, org="BETA") == await _activities(gi, org="beta")
        assert await _activities(gi, repo="WIDGET") == await _activities(gi, repo="widget")

    @pytest.mark.anyio
    async def test_no_match_yields_empty_section(self, gi: GitHubIntegration):
        result = await _activities(gi, org="nobody")
        assert result["commits"] == []

    @pytest.mark.anyio
    async def test_repo_stars_ignores_org_and_repo_filters(self, gi: GitHubIntegration):
        """Documented behaviour: repo_stars is the user's own top repos regardless."""
        result = await _activities(gi, org="nobody", repo="nothing")
        assert len(result["repo_stars"]) == 2

    @pytest.mark.anyio
    async def test_max_results_caps_each_section_separately(self, gi: GitHubIntegration):
        result = await _activities(gi, max_results=1)
        assert len(result["commits"]) == 1
        assert len(result["repo_stars"]) == 1

    @pytest.mark.anyio
    async def test_totals_are_account_wide_not_filtered(self, gi: GitHubIntegration):
        """total_contributions reports the period total, not the listed count."""
        result = await _activities(gi, org="nobody")
        assert result["commits"] == []
        assert result["total_contributions"]["commits"] == 99

    @pytest.mark.anyio
    async def test_repo_stars_total_sums_all_repos(self, gi: GitHubIntegration):
        result = await _activities(gi)
        assert result["total_contributions"]["repo_stars"] == 15

    @pytest.mark.anyio
    async def test_repo_stars_total_ignores_max_results(self, gi: GitHubIntegration):
        """The total sums every repo even when the listing is capped to one."""
        result = await _activities(gi, max_results=1)
        assert len(result["repo_stars"]) == 1
        assert result["total_contributions"]["repo_stars"] == 15

    @pytest.mark.anyio
    async def test_entries_lead_with_repo_and_owner(self, gi: GitHubIntegration):
        """The repo and owner keys are merged in front of the mapper output, so
        they must stay the first two keys of every entry."""
        result = await _activities(gi)
        assert list(result["commits"][0]) == ["repo", "owner", "commit_count", "url", "date"]
        assert list(result["repo_stars"][0]) == ["repo", "owner", "url", "description", "star_count"]


class TestGetUserActivitiesDates:
    async def _variables(self, gi: GitHubIntegration, **kwargs) -> dict:
        captured: dict = {}

        async def fake_graphql(query, variables, *, token=None):
            captured.update(variables)
            return EMPTY_CONTRIBUTIONS

        with patch.object(GitHubIntegration, "_execute_graphql", side_effect=fake_graphql):
            await gi.get_user_activities("user1", **kwargs)
        return captured

    @pytest.mark.anyio
    async def test_no_dates_sends_no_bounds(self, gi: GitHubIntegration):
        assert await self._variables(gi) == {"username": "user1"}

    @pytest.mark.anyio
    async def test_date_only_expands_to_day_bounds(self, gi: GitHubIntegration):
        variables = await self._variables(gi, since="2025-01-01", until="2025-12-31")
        assert variables["since"] == "2025-01-01T00:00:00Z"
        assert variables["until"] == "2025-12-31T23:59:59Z"

    @pytest.mark.anyio
    async def test_full_iso_is_passed_through(self, gi: GitHubIntegration):
        variables = await self._variables(gi, since="2025-01-01T01:02:03Z", until="2025-06-01T04:05:06Z")
        assert variables["since"] == "2025-01-01T01:02:03Z"
        assert variables["until"] == "2025-06-01T04:05:06Z"

    @pytest.mark.anyio
    async def test_date_range_absent_when_no_dates_given(self, gi: GitHubIntegration):
        result = await _activities(gi)
        assert result["date_range"] is None

    @pytest.mark.anyio
    async def test_date_range_reports_normalised_bounds(self, gi: GitHubIntegration):
        result = await _activities(gi, since="2025-01-01", until="2025-12-31")
        assert result["date_range"] == {
            "since": "2025-01-01T00:00:00Z",
            "until": "2025-12-31T23:59:59Z",
        }

    @pytest.mark.anyio
    async def test_one_sided_range_falls_back_to_collection_bounds(self, gi: GitHubIntegration):
        """With only 'since' given, 'until' comes from the collection's endedAt."""
        payload = {
            "user": {
                "contributionsCollection": {
                    **EMPTY_CONTRIBUTIONS["user"]["contributionsCollection"],
                    "startedAt": "2025-01-01T00:00:00Z",
                    "endedAt": "2025-09-09T00:00:00Z",
                },
                "repositories": {"nodes": []},
            }
        }
        with patch.object(GitHubIntegration, "_execute_graphql", new_callable=AsyncMock, return_value=payload):
            result = await gi.get_user_activities("user1", since="2025-02-02")
        assert result["date_range"] == {
            "since": "2025-02-02T00:00:00Z",
            "until": "2025-09-09T00:00:00Z",
        }
