"""Tests for GitHubIntegration — annotations, async HTTP, Context injection."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastmcp.exceptions import ToolError

from mcp_github.activity import ACTIVITY_SECTIONS, ACTIVITY_STAGES, MAX_REPO_PAGES
from mcp_github.exceptions import (
    GitHubAPIError,
    GitHubAuthError,
    GitHubNotFoundError,
    GitHubValidationError,
)
from mcp_github.github_integration import CONNECT_TIMEOUT, TIMEOUT, GitHubIntegration, _timeout
from mcp_github.graphql_client import handle_graphql_errors
from mcp_github.tool_annotations import _destructive, _read_only, _write

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(
    status_code: int = 200, json_data: dict | list | None = None, text: str = "", etag: str | None = None
) -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status_code
    r.is_success = status_code < 400
    r.json.return_value = json_data if json_data is not None else {}
    r.text = text
    r.reason_phrase = "OK"
    r.headers = {"ETag": etag} if etag else {}
    r.content = json.dumps(json_data).encode() if json_data is not None else text.encode()
    r.request = None
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
    """GitHubIntegration instance with a mocked HTTP client and test token."""
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
# Conditional reads
# ---------------------------------------------------------------------------


class TestEtagCache:
    """A repeated GET goes out conditionally and a 304 is free. See #317."""

    @pytest.mark.anyio
    async def test_first_get_sends_no_condition_and_remembers_the_etag(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data={"a": 1}, etag='"abc"'))
        await gi._request("GET", "https://api.github.com/x")
        assert "If-None-Match" not in gi._http.request.call_args.kwargs["headers"]
        assert len(gi._etags) == 1

    @pytest.mark.anyio
    async def test_repeat_get_sends_the_condition_and_serves_the_cached_body(self, gi: GitHubIntegration):
        responses = iter([
            _mock_response(json_data={"a": 1}, etag='"abc"'),
            _mock_response(status_code=304),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi._request("GET", "https://api.github.com/x")
        again = await gi._request("GET", "https://api.github.com/x")
        assert gi._http.request.call_args.kwargs["headers"]["If-None-Match"] == '"abc"'
        assert again.json() == {"a": 1}

    @pytest.mark.anyio
    async def test_different_params_do_not_share_an_entry(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data={"a": 1}, etag='"abc"'))
        await gi._request("GET", "https://api.github.com/x", params={"page": 1})
        await gi._request("GET", "https://api.github.com/x", params={"page": 2})
        assert len(gi._etags) == 2

    @pytest.mark.anyio
    async def test_a_write_is_never_cached(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data={"a": 1}, etag='"abc"'))
        await gi._request("POST", "https://api.github.com/x", json={})
        assert gi._etags == {}

    @pytest.mark.anyio
    async def test_the_cache_is_bounded(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data={"a": 1}, etag='"abc"'))
        with patch("mcp_github.github_integration.ETAG_CACHE_ENTRIES", 3):
            for i in range(10):
                await gi._request("GET", f"https://api.github.com/x{i}")
        assert len(gi._etags) == 3


# ---------------------------------------------------------------------------
# Timeouts
# ---------------------------------------------------------------------------


class TestTimeouts:
    """Connecting and reading are bounded separately. See #313."""

    def test_connect_and_read_budgets_are_distinct(self, gi: GitHubIntegration):
        with patch("mcp_github.github_integration.TIMEOUT", 30), patch(
            "mcp_github.github_integration.CONNECT_TIMEOUT", 3
        ):
            timeout = _timeout()
        assert timeout.connect == 3
        assert timeout.read == 30

    def test_the_shared_client_carries_both_budgets(self):
        with patch("mcp_github.github_integration.GITHUB_TOKEN", "test-token"):
            instance = GitHubIntegration()
        assert instance._http.timeout.connect == CONNECT_TIMEOUT
        assert instance._http.timeout.read == TIMEOUT


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

    @pytest.mark.anyio
    async def test_closing_the_shared_client_closes_graphql_too(self):
        # One client serves both, so there is nothing else left open. See #305.
        with patch("mcp_github.github_integration.GITHUB_TOKEN", "test-token"):
            instance = GitHubIntegration()
        await instance.aclose()
        assert instance._http.is_closed


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
# update_pr_description — reuses the PATCH response (no redundant GET)
# ---------------------------------------------------------------------------


class TestUpdatePrDescription:
    @pytest.mark.anyio
    async def test_reuses_patch_response_with_single_call(self, gi: GitHubIntegration):
        pr_payload = {
            "id": 1,
            "node_id": "PR_x",
            "title": "New title",
            "body": "New body",
            "user": _NOISE_USER,
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": "2026-07-02T00:00:00Z",
            "state": "open",
        }
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=pr_payload))
        result = await gi.update_pr_description("o", "r", 5, "New title", "New body")
        # A single PATCH — the old implementation issued a follow-up GET.
        gi._http.request.assert_awaited_once()
        assert gi._http.request.call_args.args[0] == "PATCH"
        assert gi._http.request.call_args.kwargs["json"] == {"title": "New title", "body": "New body"}
        assert result == {
            "title": "New title",
            "description": "New body",
            "author": "octocat",
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": "2026-07-02T00:00:00Z",
            "state": "open",
        }


# ---------------------------------------------------------------------------
# get_user_activities — Context progress ordering and completeness
# ---------------------------------------------------------------------------


class TestGetUserActivitiesContext:
    """Progress reporting is derived from ACTIVITY_SECTIONS, so these assert the
    relationship rather than hardcoded counts. See #298."""

    @pytest.mark.anyio
    async def test_no_ctx_runs_without_error(self, gi: GitHubIntegration):
        with patch.object(GitHubIntegration, "_execute_graphql", new_callable=AsyncMock, return_value=_EMPTY_CONTRIBUTIONS):
            result = await gi.get_user_activities("user1")
        assert result["username"] == "user1"
        assert result["commits"] == []

    @pytest.mark.anyio
    async def test_pre_call_info_fires_before_graphql(self, gi: GitHubIntegration):
        """ctx.info('Querying...') must appear before the GraphQL call."""
        order: list[str] = []

        async def fake_graphql(*args, **kwargs):
            order.append("graphql")
            return _EMPTY_CONTRIBUTIONS

        ctx = _mock_ctx()
        ctx.info.side_effect = lambda msg: order.append(f"info:{msg}")

        with patch.object(GitHubIntegration, "_execute_graphql", side_effect=fake_graphql):
            await gi.get_user_activities("user1", ctx=ctx)

        assert order[0].startswith("info:Querying")
        assert order[1] == "graphql"

    @pytest.mark.anyio
    async def test_progress_runs_from_zero_to_total(self, gi: GitHubIntegration):
        """One tick per section, plus the repo-stars stage, plus a final tick."""
        ctx = _mock_ctx()
        with patch.object(GitHubIntegration, "_execute_graphql", new_callable=AsyncMock, return_value=_EMPTY_CONTRIBUTIONS):
            await gi.get_user_activities("user1", ctx=ctx)
        progress = [c.kwargs["progress"] for c in ctx.report_progress.call_args_list]
        assert progress == list(range(ACTIVITY_STAGES + 1))

    @pytest.mark.anyio
    async def test_progress_total_matches_stage_count(self, gi: GitHubIntegration):
        ctx = _mock_ctx()
        with patch.object(GitHubIntegration, "_execute_graphql", new_callable=AsyncMock, return_value=_EMPTY_CONTRIBUTIONS):
            await gi.get_user_activities("user1", ctx=ctx)
        totals = {c.kwargs["total"] for c in ctx.report_progress.call_args_list}
        assert totals == {ACTIVITY_STAGES}

    @pytest.mark.anyio
    async def test_every_section_announces_itself(self, gi: GitHubIntegration):
        """The pre-call message, one per section, then repo stars."""
        ctx = _mock_ctx()
        with patch.object(GitHubIntegration, "_execute_graphql", new_callable=AsyncMock, return_value=_EMPTY_CONTRIBUTIONS):
            await gi.get_user_activities("user1", ctx=ctx)
        info_calls = [c.args[0] for c in ctx.info.call_args_list]
        assert len(info_calls) == ACTIVITY_STAGES + 1
        assert info_calls[1:-1] == [s.message for s in ACTIVITY_SECTIONS]
        assert "repo stars" in info_calls[-1].lower()

    @pytest.mark.anyio
    async def test_result_carries_a_key_per_section(self, gi: GitHubIntegration):
        """Every declared section must reach the result, so a new section cannot
        be added to the table and silently dropped from the payload."""
        with patch.object(GitHubIntegration, "_execute_graphql", new_callable=AsyncMock, return_value=_EMPTY_CONTRIBUTIONS):
            result = await gi.get_user_activities("user1")
        for section in ACTIVITY_SECTIONS:
            assert section.field in result


# ---------------------------------------------------------------------------
# get_user_activities — filtering, capping and date handling
# ---------------------------------------------------------------------------


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
    with patch.object(GitHubIntegration, "_execute_graphql", new_callable=AsyncMock, return_value=_FILTERABLE_CONTRIBUTIONS):
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
            return _EMPTY_CONTRIBUTIONS

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
                "contributionsCollection": {**_EMPTY_CONTRIBUTIONS["user"]["contributionsCollection"],
                                            "startedAt": "2025-01-01T00:00:00Z",
                                            "endedAt": "2025-09-09T00:00:00Z"},
                "repositories": {"nodes": []},
            }
        }
        with patch.object(GitHubIntegration, "_execute_graphql", new_callable=AsyncMock, return_value=payload):
            result = await gi.get_user_activities("user1", since="2025-02-02")
        assert result["date_range"] == {
            "since": "2025-02-02T00:00:00Z",
            "until": "2025-09-09T00:00:00Z",
        }


# ---------------------------------------------------------------------------
# get_repo_stars_since — new stars within a date window
# ---------------------------------------------------------------------------


class TestGetRepoStarsSince:
    @pytest.mark.anyio
    async def test_short_repo_listing_is_not_truncated(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[]))
        result = await gi.get_repo_stars_since("u", since="2090-01-01")
        assert result["truncated"] is False
        assert gi._http.request.call_count == 1

    @pytest.mark.anyio
    async def test_repo_listing_pages_past_the_first_hundred(self, gi: GitHubIntegration):
        # Two full pages then a short one: every repo is considered, and the
        # most-starred sits on the second page where a single call would miss it.
        page1 = [{"name": f"r{i}", "stargazers_count": 1, "html_url": "u", "description": None} for i in range(100)]
        page2 = [{"name": "popular", "stargazers_count": 999, "html_url": "u", "description": None}]
        sg = [{"starred_at": "2099-01-01T00:00:00Z", "user": {}}]
        responses = iter([_mock_response(json_data=page1), _mock_response(json_data=page2)] + [
            _mock_response(json_data=sg) for _ in range(30)
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        result = await gi.get_repo_stars_since("u", since="2090-01-01", max_repos=1)
        assert result["truncated"] is False
        assert result["repos"][0]["repo"] == "popular"

    @pytest.mark.anyio
    async def test_running_out_of_pages_is_reported(self, gi: GitHubIntegration):
        full = [{"name": f"r{i}", "stargazers_count": 0, "html_url": "u", "description": None} for i in range(100)]
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=full))
        result = await gi.get_repo_stars_since("u", since="2090-01-01")
        assert result["truncated"] is True
        assert gi._http.request.call_count == MAX_REPO_PAGES

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
    async def test_star_exactly_on_the_cutoff_counts(self, gi: GitHubIntegration):
        """The cutoff is inclusive, so a star stamped exactly at it is new."""
        repos_payload = [
            {"name": "edge", "stargazers_count": 2, "html_url": "https://github.com/u/edge", "description": None},
        ]
        sg = [
            {"starred_at": "2089-12-31T23:59:59Z", "user": {}},  # before
            {"starred_at": "2090-01-01T00:00:00Z", "user": {}},  # exactly on the cutoff
        ]
        responses = iter([_mock_response(json_data=repos_payload), _mock_response(json_data=sg)])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))

        result = await gi.get_repo_stars_since("u", since="2090-01-01")

        assert result["repos"][0]["new_stars"] == 1

    @pytest.mark.anyio
    async def test_walks_stargazer_pages_newest_first(self, gi: GitHubIntegration):
        """The walk starts at the last page and stops at the first page holding a
        star older than the cutoff, so early pages are never fetched."""
        repos_payload = [
            {"name": "big", "stargazers_count": 250, "html_url": "https://github.com/u/big", "description": None},
        ]
        pages = {
            3: [{"starred_at": "2099-01-01T00:00:00Z", "user": {}}] * 50,
            2: [{"starred_at": "2000-01-01T00:00:00Z", "user": {}}] * 99
            + [{"starred_at": "2099-01-01T00:00:00Z", "user": {}}],
        }
        requested: list[int] = []

        async def fake_request(method, url, **kw):
            if url.endswith("/repos"):
                return _mock_response(json_data=repos_payload)
            page = kw["params"]["page"]
            requested.append(page)
            return _mock_response(json_data=pages.get(page, []))

        gi._http.request = AsyncMock(side_effect=fake_request)

        result = await gi.get_repo_stars_since("u", since="2090-01-01")

        assert requested == [3, 2]  # page 1 never fetched
        assert result["repos"][0]["new_stars"] == 51

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
        with patch.object(GitHubIntegration, "_execute_graphql", new_callable=AsyncMock, return_value=_EMPTY_STATUS_CHECKS):
            result = await gi.get_pr_status_checks("owner", "repo", 1, ctx=None)
        assert "overall" in result
        assert "check_runs" in result

    @pytest.mark.anyio
    async def test_ctx_info_includes_suite_run_and_status_counts(self, gi: GitHubIntegration):
        ctx = _mock_ctx()
        with patch.object(GitHubIntegration, "_execute_graphql", new_callable=AsyncMock, return_value=_EMPTY_STATUS_CHECKS):
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
        with patch.object(GitHubIntegration, "_execute_graphql", new_callable=AsyncMock, return_value=data):
            await gi.get_pr_status_checks("owner", "repo", 1, ctx=ctx)
        msg = ctx.info.call_args[0][0]
        assert "5 check suites" in msg

    @pytest.mark.anyio
    async def test_overall_status_derived_correctly(self, gi: GitHubIntegration):
        with patch.object(GitHubIntegration, "_execute_graphql", new_callable=AsyncMock, return_value=_EMPTY_STATUS_CHECKS):
            result = await gi.get_pr_status_checks("owner", "repo", 1)
        assert result["overall"] == "unknown"


# ---------------------------------------------------------------------------
# get_latest_sha + create_tag — empty-repo contract
# ---------------------------------------------------------------------------


class TestGetLatestShaAndCreateTag:
    @pytest.mark.anyio
    async def test_get_latest_sha_asks_for_one_commit(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[{"sha": "abc123"}]))
        assert await gi.get_latest_sha("owner", "repo") == "abc123"
        assert "per_page=1" in gi._http.request.call_args.args[1]

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
    async def test_create_tag_uses_the_sha_it_is_given(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data={"ref": "refs/tags/v1"}))
        await gi.create_tag("o", "r", "v1", sha="deadbee")
        # One call only: the latest-SHA lookup is skipped when a commit is named.
        assert gi._http.request.call_count == 1
        assert gi._http.request.call_args.kwargs["json"]["sha"] == "deadbee"

    @pytest.mark.anyio
    async def test_create_tag_without_a_message_is_a_plain_ref(self, gi: GitHubIntegration):
        responses = iter([
            _mock_response(json_data=[{"sha": "abc123"}]),
            _mock_response(json_data={"ref": "refs/tags/v1"}),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.create_tag("o", "r", "v1")
        assert gi._http.request.call_args.args[1].endswith("/git/refs")
        assert gi._http.request.call_args.kwargs["json"] == {"ref": "refs/tags/v1", "sha": "abc123"}

    @pytest.mark.anyio
    async def test_create_tag_with_a_message_creates_an_annotated_tag(self, gi: GitHubIntegration):
        responses = iter([
            _mock_response(json_data={"sha": "tagobj1"}),
            _mock_response(json_data={"ref": "refs/tags/v1"}),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.create_tag("o", "r", "v1", message="ship it", sha="deadbee")
        calls = gi._http.request.call_args_list
        assert calls[0].args[1].endswith("/git/tags")
        assert calls[0].kwargs["json"] == {
            "tag": "v1", "message": "ship it", "object": "deadbee", "type": "commit",
        }
        # The ref points at the tag object, not the commit, or the message is lost.
        assert calls[1].kwargs["json"]["sha"] == "tagobj1"

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
        with patch.object(GitHubIntegration, "_execute_graphql", new_callable=AsyncMock, side_effect=[page1, page2]) as p:
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
        suite_page = _status_page(
            [_suite_with_id("s1", [_run("a")], runs_has_next=True, app="Codacy Production")]
        )
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
        ctx = _mock_ctx()
        with patch.object(
            GitHubIntegration,
            "_execute_graphql",
            new_callable=AsyncMock,
            side_effect=[suite_page, *([infinite_runs] * 10)],
        ):
            await gi.get_pr_status_checks("owner", "repo", 1, ctx=ctx)
        msg = ctx.info.call_args[0][0]
        assert "truncated" in msg


# ---------------------------------------------------------------------------
# Response trimming — write tools return compact contracts, not raw payloads
# ---------------------------------------------------------------------------

_NOISE_USER = {
    "login": "octocat",
    "id": 1,
    "node_id": "MDQ6VXNlcjE=",
    "avatar_url": "https://avatars.githubusercontent.com/u/1",
    "url": "https://api.github.com/users/octocat",
    "html_url": "https://github.com/octocat",
    "gravatar_id": "",
    "type": "User",
    "site_admin": False,
    "followers_url": "https://api.github.com/users/octocat/followers",
}

_NOISE_REACTIONS = {
    "url": "https://api.github.com/repos/o/r/issues/comments/1/reactions",
    "total_count": 0,
    "+1": 0,
    "-1": 0,
    "laugh": 0,
    "confused": 0,
    "heart": 0,
    "hooray": 0,
    "rocket": 0,
    "eyes": 0,
}


def _issue_payload(**overrides) -> dict:
    payload = {
        "id": 999,
        "node_id": "I_abc",
        "url": "https://api.github.com/repos/o/r/issues/7",
        "repository_url": "https://api.github.com/repos/o/r",
        "number": 7,
        "title": "A bug",
        "body": "Details",
        "state": "open",
        "user": _NOISE_USER,
        "labels": [
            {"id": 1, "node_id": "L_1", "name": "bug", "color": "d73a4a", "default": True},
            {"id": 2, "node_id": "L_2", "name": "mcp", "color": "ededed", "default": False},
        ],
        "assignee": None,
        "assignees": [],
        "milestone": None,
        "locked": False,
        "comments": 0,
        "html_url": "https://github.com/o/r/issues/7",
        "created_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-07-02T00:00:00Z",
        "closed_at": None,
        "author_association": "OWNER",
        "reactions": _NOISE_REACTIONS,
        "timeline_url": "https://api.github.com/repos/o/r/issues/7/timeline",
    }
    payload.update(overrides)
    return payload


class TestResponseTrimming:
    @pytest.mark.anyio
    async def test_add_pr_comments_returns_trimmed_comment(self, gi: GitHubIntegration):
        payload = {
            "id": 11,
            "node_id": "IC_abc",
            "url": "https://api.github.com/repos/o/r/issues/comments/11",
            "html_url": "https://github.com/o/r/pull/5#issuecomment-11",
            "body": "hello",
            "user": _NOISE_USER,
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": "2026-07-01T00:00:00Z",
            "issue_url": "https://api.github.com/repos/o/r/issues/5",
            "author_association": "OWNER",
            "reactions": _NOISE_REACTIONS,
        }
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=payload))
        result = await gi.add_pr_comments("o", "r", 5, "hello")
        assert result == {
            "id": 11,
            "body": "hello",
            "author": "octocat",
            "html_url": "https://github.com/o/r/pull/5#issuecomment-11",
            "created_at": "2026-07-01T00:00:00Z",
        }

    @pytest.mark.anyio
    async def test_add_inline_pr_comment_returns_trimmed_comment(self, gi: GitHubIntegration):
        comment_payload = {
            "id": 22,
            "node_id": "PRRC_abc",
            "pull_request_review_id": 33,
            "diff_hunk": "@@ -1,3 +1,3 @@",
            "path": "app.py",
            "body": "fix this",
            "user": _NOISE_USER,
            "html_url": "https://github.com/o/r/pull/5#discussion_r22",
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": "2026-07-01T00:00:00Z",
            "_links": {"self": {"href": "https://api.github.com/x"}},
            "reactions": _NOISE_REACTIONS,
        }
        responses = iter([
            _mock_response(json_data={"head": {"sha": "abc123"}}),
            _mock_response(json_data=comment_payload),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        result = await gi.add_inline_pr_comment("o", "r", 5, "app.py", 3, "fix this")
        assert result == {
            "id": 22,
            "body": "fix this",
            "author": "octocat",
            "html_url": "https://github.com/o/r/pull/5#discussion_r22",
            "created_at": "2026-07-01T00:00:00Z",
        }
        post_kwargs = gi._http.request.call_args_list[1].kwargs
        assert post_kwargs["json"]["commit_id"] == "abc123"

    @pytest.mark.anyio
    async def test_create_issue_returns_trimmed_issue(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=_issue_payload()))
        result = await gi.create_issue("o", "r", "A bug", "Details", ["bug"])
        assert result == {
            "number": 7,
            "title": "A bug",
            "body": "Details",
            "state": "open",
            "author": "octocat",
            "labels": ["bug", "mcp"],
            "html_url": "https://github.com/o/r/issues/7",
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": "2026-07-02T00:00:00Z",
        }
        assert gi._http.request.call_args.kwargs["json"]["labels"] == ["bug", "mcp"]

    @pytest.mark.anyio
    async def test_update_issue_returns_trimmed_issue(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(
            return_value=_mock_response(json_data=_issue_payload(state="closed"))
        )
        result = await gi.update_issue("o", "r", 7, "A bug", "Details", state="closed")
        assert result["state"] == "closed"
        assert result["author"] == "octocat"
        assert set(result) == {
            "number", "title", "body", "state", "author", "labels", "html_url", "created_at", "updated_at",
        }

    @pytest.mark.anyio
    async def test_update_issue_sends_only_the_fields_supplied(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=_issue_payload(state="closed")))
        await gi.update_issue("o", "r", 7, state="closed")
        assert gi._http.request.call_args.kwargs["json"] == {"state": "closed"}

    @pytest.mark.anyio
    async def test_update_issue_keeps_labels_when_they_are_omitted(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=_issue_payload()))
        await gi.update_issue("o", "r", 7, title="A different title")
        assert "labels" not in gi._http.request.call_args.kwargs["json"]

    @pytest.mark.anyio
    async def test_update_issue_strips_labels_when_an_empty_list_is_explicit(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=_issue_payload()))
        await gi.update_issue("o", "r", 7, labels=[])
        assert gi._http.request.call_args.kwargs["json"] == {"labels": []}

    @pytest.mark.anyio
    async def test_update_issue_rejects_a_call_with_nothing_to_change(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock()
        with pytest.raises(GitHubValidationError):
            await gi.update_issue("o", "r", 7)
        gi._http.request.assert_not_called()

    @pytest.mark.anyio
    async def test_update_reviews_returns_trimmed_review(self, gi: GitHubIntegration):
        payload = {
            "id": 80,
            "node_id": "PRR_abc",
            "user": _NOISE_USER,
            "body": "LGTM",
            "state": "APPROVED",
            "html_url": "https://github.com/o/r/pull/5#pullrequestreview-80",
            "pull_request_url": "https://api.github.com/repos/o/r/pulls/5",
            "_links": {"html": {"href": "https://github.com/x"}},
            "submitted_at": "2026-07-01T00:00:00Z",
            "commit_id": "abc123",
            "author_association": "OWNER",
        }
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=payload))
        result = await gi.update_reviews("o", "r", 5, "APPROVE", "LGTM")
        assert result == {
            "id": 80,
            "state": "APPROVED",
            "body": "LGTM",
            "html_url": "https://github.com/o/r/pull/5#pullrequestreview-80",
            "submitted_at": "2026-07-01T00:00:00Z",
        }

    @pytest.mark.anyio
    async def test_update_assignees_all_applied(self, gi: GitHubIntegration):
        payload = _issue_payload(
            assignees=[{**_NOISE_USER, "login": "a"}, {**_NOISE_USER, "login": "b"}]
        )
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=payload))
        result = await gi.update_assignees("o", "r", 7, ["b", "a"])
        assert result == {
            "status": "ok",
            "assignees_requested": ["a", "b"],
            "assignees_applied": ["a", "b"],
            "issue_url": "https://github.com/o/r/issues/7",
        }

    @pytest.mark.anyio
    async def test_update_assignees_partial(self, gi: GitHubIntegration):
        payload = _issue_payload(assignees=[{**_NOISE_USER, "login": "a"}])
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=payload))
        result = await gi.update_assignees("o", "r", 7, ["a", "b"])
        assert result["status"] == "partial"
        assert result["assignees_applied"] == ["a"]
        assert "'b'" in result["message"]
        assert "issue" not in result

    @pytest.mark.anyio
    async def test_create_release_returns_trimmed_release(self, gi: GitHubIntegration):
        payload = {
            "id": 55,
            "node_id": "RE_abc",
            "url": "https://api.github.com/repos/o/r/releases/55",
            "assets_url": "https://api.github.com/repos/o/r/releases/55/assets",
            "upload_url": "https://uploads.github.com/repos/o/r/releases/55/assets{?name,label}",
            "html_url": "https://github.com/o/r/releases/tag/v1.0.0",
            "author": _NOISE_USER,
            "tag_name": "v1.0.0",
            "target_commitish": "main",
            "name": "v1.0.0",
            "draft": False,
            "prerelease": False,
            "created_at": "2026-07-01T00:00:00Z",
            "published_at": "2026-07-01T00:00:00Z",
            "assets": [],
            "tarball_url": "https://api.github.com/repos/o/r/tarball/v1.0.0",
            "zipball_url": "https://api.github.com/repos/o/r/zipball/v1.0.0",
            "body": "Generated notes",
        }
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=payload))
        result = await gi.create_release("o", "r", "v1.0.0", "v1.0.0", "notes")
        assert result == {
            "id": 55,
            "tag_name": "v1.0.0",
            "name": "v1.0.0",
            "html_url": "https://github.com/o/r/releases/tag/v1.0.0",
            "draft": False,
            "prerelease": False,
            "body": "Generated notes",
        }


# ---------------------------------------------------------------------------
# Repository labels
# ---------------------------------------------------------------------------


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
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=payload))
        result = await gi.list_repo_labels("o", "r")
        assert result == {
            "total": 2,
            "labels": [
                {"name": "bug", "description": "Something is not working", "color": "d73a4a"},
                {"name": "mcp", "description": None, "color": "ededed"},
            ],
        }

    @pytest.mark.anyio
    async def test_paging_params_sent_in_url(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[]))
        result = await gi.list_repo_labels("o", "r", per_page=100, page=2)
        url = gi._http.request.call_args.args[1]
        assert url == "https://api.github.com/repos/o/r/labels?per_page=100&page=2"
        assert result == {"total": 0, "labels": []}

    def test_is_read_only(self, gi: GitHubIntegration):
        assert gi.list_repo_labels._mcp_annotations.readOnlyHint is True


# ---------------------------------------------------------------------------
# get_pr_diff — size reporting and truncation (#314)
# ---------------------------------------------------------------------------


class TestGetPRDiff:
    @pytest.mark.anyio
    async def test_short_patch_comes_back_whole(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(text="diff --git a b\n"))
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
        gi._http.request = AsyncMock(return_value=_mock_response(text="x" * 100))
        result = await gi.get_pr_diff("o", "r", 5, max_bytes=10)
        assert result["patch"] == "x" * 10
        assert result["bytes_returned"] == 10
        assert result["bytes_total"] == 100
        assert result["truncated"] is True

    @pytest.mark.anyio
    async def test_zero_max_bytes_asks_the_size_alone(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(text="x" * 100))
        result = await gi.get_pr_diff("o", "r", 5, max_bytes=0)
        assert result["patch"] == ""
        assert result["bytes_total"] == 100
        assert result["truncated"] is True

    @pytest.mark.anyio
    async def test_a_split_character_is_dropped_not_mangled(self, gi: GitHubIntegration):
        # 'é' is two bytes, so a three-byte cut lands mid-character.
        gi._http.request = AsyncMock(return_value=_mock_response(text="abé"))
        result = await gi.get_pr_diff("o", "r", 5, max_bytes=3)
        assert result["patch"] == "ab"
        assert result["bytes_total"] == 4

    @pytest.mark.anyio
    async def test_negative_max_bytes_is_rejected(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock()
        with pytest.raises(GitHubValidationError):
            await gi.get_pr_diff("o", "r", 5, max_bytes=-1)
        gi._http.request.assert_not_called()


# ---------------------------------------------------------------------------
# search_issues_prs (#346)
# ---------------------------------------------------------------------------


class TestSearchIssuesPRs:
    @pytest.mark.anyio
    async def test_query_is_encoded_into_the_search_url(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data={"total_count": 0, "items": []}))
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
                    "user": _NOISE_USER,
                    "labels": [{"name": "bug"}],
                    "body": "a very long body nobody asked for",
                    "reactions": _NOISE_REACTIONS,
                }
            ],
        }
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=payload))
        result = await gi.search_issues_prs("rate limits")
        assert result["total"] == 1
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
        gi._http.request = AsyncMock(return_value=_mock_response(json_data={"total_count": 0, "items": []}))
        await gi.search_issues_prs("x", per_page=10, page=3)
        url = gi._http.request.call_args.args[1]
        assert "per_page=10&page=3" in url

    @pytest.mark.anyio
    async def test_empty_query_is_rejected(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock()
        with pytest.raises(GitHubValidationError):
            await gi.search_issues_prs("   ")
        gi._http.request.assert_not_called()

    def test_is_read_only(self, gi: GitHubIntegration):
        assert gi.search_issues_prs._mcp_annotations.readOnlyHint is True


# ---------------------------------------------------------------------------
# Releases and tags — read, update, delete (#347)
# ---------------------------------------------------------------------------


def _release_payload(**overrides) -> dict:
    payload = {
        "id": 55,
        "node_id": "RE_abc",
        "tag_name": "v1.0.0",
        "name": "v1.0.0",
        "html_url": "https://github.com/o/r/releases/tag/v1.0.0",
        "draft": False,
        "prerelease": False,
        "body": "notes",
        "author": _NOISE_USER,
        "assets": [],
    }
    payload.update(overrides)
    return payload


class TestReleasesAndTags:
    @pytest.mark.anyio
    async def test_get_release_by_tag(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=_release_payload()))
        result = await gi.get_release("o", "r", "v1.0.0")
        assert gi._http.request.call_args.args[1].endswith("/releases/tags/v1.0.0")
        assert result == {
            "id": 55,
            "tag_name": "v1.0.0",
            "name": "v1.0.0",
            "html_url": "https://github.com/o/r/releases/tag/v1.0.0",
            "draft": False,
            "prerelease": False,
            "body": "notes",
        }

    @pytest.mark.anyio
    async def test_get_release_without_a_tag_asks_for_the_latest(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=_release_payload()))
        await gi.get_release("o", "r")
        assert gi._http.request.call_args.args[1].endswith("/releases/latest")

    @pytest.mark.anyio
    async def test_missing_release_raises_not_found(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(status_code=404, json_data={}))
        with pytest.raises(GitHubNotFoundError, match="No release found for tag 'v9'"):
            await gi.get_release("o", "r", "v9")

    @pytest.mark.anyio
    async def test_list_releases_is_trimmed(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[_release_payload()]))
        result = await gi.list_releases("o", "r")
        assert result["total"] == 1
        assert set(result["releases"][0]) == {
            "id", "tag_name", "name", "html_url", "draft", "prerelease", "body",
        }

    @pytest.mark.anyio
    async def test_list_tags_keeps_name_and_sha(self, gi: GitHubIntegration):
        payload = [{"name": "v1.0.0", "zipball_url": "z", "commit": {"sha": "abc123", "url": "u"}}]
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=payload))
        result = await gi.list_tags("o", "r")
        assert result == {"total": 1, "tags": [{"name": "v1.0.0", "sha": "abc123"}]}

    @pytest.mark.anyio
    async def test_update_release_sends_only_the_fields_supplied(self, gi: GitHubIntegration):
        responses = iter([
            _mock_response(json_data=_release_payload()),
            _mock_response(json_data=_release_payload(body="corrected")),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.update_release("o", "r", "v1.0.0", body="corrected")
        patch_call = gi._http.request.call_args_list[1]
        assert patch_call.args[0] == "PATCH"
        assert patch_call.args[1].endswith("/releases/55")
        assert patch_call.kwargs["json"] == {"body": "corrected"}

    @pytest.mark.anyio
    async def test_update_release_can_clear_the_draft_flag(self, gi: GitHubIntegration):
        responses = iter([
            _mock_response(json_data=_release_payload(draft=True)),
            _mock_response(json_data=_release_payload()),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.update_release("o", "r", "v1.0.0", draft=False)
        assert gi._http.request.call_args_list[1].kwargs["json"] == {"draft": False}

    @pytest.mark.anyio
    async def test_update_release_rejects_a_call_with_nothing_to_change(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock()
        with pytest.raises(GitHubValidationError):
            await gi.update_release("o", "r", "v1.0.0")
        gi._http.request.assert_not_called()

    @pytest.mark.anyio
    async def test_create_release_updates_when_the_tag_already_has_one(self, gi: GitHubIntegration):
        responses = iter([
            _mock_response(status_code=422, json_data={"errors": [{"code": "already_exists"}]}),
            _mock_response(json_data=_release_payload()),
            _mock_response(json_data=_release_payload(body="second attempt")),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        result = await gi.create_release("o", "r", "v1.0.0", "v1.0.0", "second attempt")
        calls = gi._http.request.call_args_list
        assert calls[0].args[0] == "POST"
        assert calls[2].args[0] == "PATCH"
        assert calls[2].kwargs["json"]["body"] == "second attempt"
        assert result["body"] == "second attempt"

    @pytest.mark.anyio
    async def test_create_release_still_raises_on_other_validation_errors(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(
            return_value=_mock_response(status_code=422, json_data={"errors": [{"code": "invalid"}]})
        )
        with pytest.raises(GitHubValidationError):
            await gi.create_release("o", "r", "bad tag", "name", "notes")
        assert gi._http.request.call_count == 1

    @pytest.mark.anyio
    async def test_delete_release_leaves_the_tag_alone(self, gi: GitHubIntegration):
        responses = iter([
            _mock_response(json_data=_release_payload()),
            _mock_response(status_code=204),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        result = await gi.delete_release("o", "r", "v1.0.0")
        assert gi._http.request.call_count == 2
        assert gi._http.request.call_args.args[1].endswith("/releases/55")
        assert result["tag_deleted"] is False

    @pytest.mark.anyio
    async def test_delete_release_removes_the_tag_when_asked(self, gi: GitHubIntegration):
        responses = iter([
            _mock_response(json_data=_release_payload()),
            _mock_response(status_code=204),
            _mock_response(status_code=204),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        result = await gi.delete_release("o", "r", "v1.0.0", delete_tag=True)
        assert gi._http.request.call_args.args[1].endswith("/git/refs/tags/v1.0.0")
        assert result["tag_deleted"] is True

    @pytest.mark.anyio
    async def test_delete_tag_refuses_a_tag_a_release_points_at(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=_release_payload()))
        with pytest.raises(GitHubValidationError, match="force=True"):
            await gi.delete_tag("o", "r", "v1.0.0")
        # The lookup happened, the delete did not.
        assert gi._http.request.call_count == 1

    @pytest.mark.anyio
    async def test_delete_tag_proceeds_when_forced(self, gi: GitHubIntegration):
        responses = iter([
            _mock_response(json_data=_release_payload()),
            _mock_response(status_code=204),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        result = await gi.delete_tag("o", "r", "v1.0.0", force=True)
        assert gi._http.request.call_args.args[0] == "DELETE"
        assert result["release_still_published"] is True

    @pytest.mark.anyio
    async def test_delete_tag_without_a_release_needs_no_force(self, gi: GitHubIntegration):
        responses = iter([
            _mock_response(status_code=404, json_data={}),
            _mock_response(status_code=204),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        result = await gi.delete_tag("o", "r", "v0.1.0")
        assert result == {"status": "deleted", "tag_name": "v0.1.0", "release_still_published": False}

    def test_delete_tools_report_themselves_destructive(self, gi: GitHubIntegration):
        for name in ("delete_release", "delete_tag"):
            assert getattr(gi, name)._mcp_annotations.destructiveHint is True, name

    def test_read_tools_are_read_only(self, gi: GitHubIntegration):
        for name in ("list_releases", "get_release", "list_tags"):
            assert getattr(gi, name)._mcp_annotations.readOnlyHint is True, name


# ---------------------------------------------------------------------------
# update_pr and set_pr_draft (#348)
# ---------------------------------------------------------------------------


def _pr_payload(**overrides) -> dict:
    payload = {
        "title": "A change",
        "body": "Details",
        "user": _NOISE_USER,
        "created_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-07-02T00:00:00Z",
        "state": "open",
        "node_id": "PR_abc",
    }
    payload.update(overrides)
    return payload


class TestUpdatePR:
    @pytest.mark.anyio
    async def test_sends_only_the_fields_supplied(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=_pr_payload(state="closed")))
        await gi.update_pr("o", "r", 5, state="closed")
        assert gi._http.request.call_args.kwargs["json"] == {"state": "closed"}

    @pytest.mark.anyio
    async def test_title_changes_without_resending_the_body(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=_pr_payload()))
        await gi.update_pr("o", "r", 5, title="A better title")
        assert "body" not in gi._http.request.call_args.kwargs["json"]

    @pytest.mark.anyio
    async def test_base_can_be_retargeted(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=_pr_payload()))
        await gi.update_pr("o", "r", 5, base="develop")
        assert gi._http.request.call_args.kwargs["json"] == {"base": "develop"}

    @pytest.mark.anyio
    async def test_returns_the_trimmed_pr_content(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=_pr_payload(state="closed")))
        result = await gi.update_pr("o", "r", 5, state="closed")
        assert result == {
            "title": "A change",
            "description": "Details",
            "author": "octocat",
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": "2026-07-02T00:00:00Z",
            "state": "closed",
        }

    @pytest.mark.anyio
    async def test_rejects_a_call_with_nothing_to_change(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock()
        with pytest.raises(GitHubValidationError):
            await gi.update_pr("o", "r", 5)
        gi._http.request.assert_not_called()

    def test_is_idempotent_not_destructive(self, gi: GitHubIntegration):
        ann = gi.update_pr._mcp_annotations
        assert ann.idempotentHint is True
        assert ann.destructiveHint is False


class TestSetPRDraft:
    @pytest.mark.anyio
    async def test_ready_for_review_uses_the_mark_ready_mutation(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=_pr_payload()))
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
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=_pr_payload()))
        gi._execute_graphql = AsyncMock(
            return_value={"convertPullRequestToDraft": {"pullRequest": {"number": 5, "isDraft": True, "url": "u"}}}
        )
        result = await gi.set_pr_draft("o", "r", 5, draft=True)
        assert "convertPullRequestToDraft" in gi._execute_graphql.call_args.args[0]
        assert result["is_draft"] is True

    @pytest.mark.anyio
    async def test_missing_node_id_is_an_error(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data={"number": 5}))
        gi._execute_graphql = AsyncMock()
        with pytest.raises(ToolError, match="node id"):
            await gi.set_pr_draft("o", "r", 5, draft=False)
        gi._execute_graphql.assert_not_called()


# ---------------------------------------------------------------------------
# PR comments — list, edit, reply (#349)
# ---------------------------------------------------------------------------


class TestPRComments:
    @pytest.mark.anyio
    async def test_conversation_comments_come_from_the_issues_path(self, gi: GitHubIntegration):
        payload = [{
            "id": 11,
            "body": "hello",
            "user": _NOISE_USER,
            "html_url": "https://github.com/o/r/pull/5#issuecomment-11",
            "created_at": "2026-07-01T00:00:00Z",
        }]
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=payload))
        result = await gi.list_pr_comments("o", "r", 5)
        assert "/issues/5/comments" in gi._http.request.call_args.args[1]
        assert result["kind"] == "conversation"
        assert result["comments"] == [{
            "id": 11,
            "body": "hello",
            "author": "octocat",
            "html_url": "https://github.com/o/r/pull/5#issuecomment-11",
            "created_at": "2026-07-01T00:00:00Z",
        }]

    @pytest.mark.anyio
    async def test_inline_comments_carry_the_file_and_line(self, gi: GitHubIntegration):
        payload = [{
            "id": 22,
            "body": "fix this",
            "user": _NOISE_USER,
            "html_url": "https://github.com/o/r/pull/5#discussion_r22",
            "created_at": "2026-07-01T00:00:00Z",
            "path": "app.py",
            "line": 3,
            "in_reply_to_id": None,
            "diff_hunk": "@@ -1,3 +1,3 @@",
        }]
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=payload))
        result = await gi.list_pr_comments("o", "r", 5, kind="inline")
        assert "/pulls/5/comments" in gi._http.request.call_args.args[1]
        assert result["comments"][0]["path"] == "app.py"
        assert result["comments"][0]["line"] == 3
        assert "diff_hunk" not in result["comments"][0]

    @pytest.mark.anyio
    async def test_paging_params_sent_in_url(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[]))
        await gi.list_pr_comments("o", "r", 5, per_page=10, page=2)
        assert gi._http.request.call_args.args[1].endswith("?per_page=10&page=2")

    @pytest.mark.anyio
    async def test_editing_an_inline_comment_uses_the_pulls_id_space(self, gi: GitHubIntegration):
        payload = {
            "id": 22,
            "body": "corrected",
            "user": _NOISE_USER,
            "html_url": "https://github.com/o/r/pull/5#discussion_r22",
            "created_at": "2026-07-01T00:00:00Z",
        }
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=payload))
        result = await gi.update_pr_comment("o", "r", 22, "corrected", kind="inline")
        call = gi._http.request.call_args
        assert call.args[0] == "PATCH"
        assert call.args[1].endswith("/pulls/comments/22")
        assert result["body"] == "corrected"

    @pytest.mark.anyio
    async def test_editing_a_conversation_comment_uses_the_issues_id_space(self, gi: GitHubIntegration):
        payload = {
            "id": 11,
            "body": "corrected",
            "user": _NOISE_USER,
            "html_url": "https://github.com/o/r/pull/5#issuecomment-11",
            "created_at": "2026-07-01T00:00:00Z",
        }
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=payload))
        await gi.update_pr_comment("o", "r", 11, "corrected")
        assert gi._http.request.call_args.args[1].endswith("/issues/comments/11")

    @pytest.mark.anyio
    async def test_reply_posts_onto_the_existing_thread(self, gi: GitHubIntegration):
        payload = {
            "id": 23,
            "body": "agreed",
            "user": _NOISE_USER,
            "html_url": "https://github.com/o/r/pull/5#discussion_r23",
            "created_at": "2026-07-01T00:00:00Z",
            "path": "app.py",
            "line": 3,
            "in_reply_to_id": 22,
        }
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=payload))
        result = await gi.reply_to_review_comment("o", "r", 5, 22, "agreed")
        call = gi._http.request.call_args
        assert call.args[0] == "POST"
        assert call.args[1].endswith("/pulls/5/comments/22/replies")
        assert result["in_reply_to_id"] == 22

    @pytest.mark.anyio
    async def test_the_id_a_listing_returns_is_the_id_an_edit_takes(self, gi: GitHubIntegration):
        listing = [{
            "id": 22,
            "body": "fix this",
            "user": _NOISE_USER,
            "html_url": "https://github.com/o/r/pull/5#discussion_r22",
            "created_at": "2026-07-01T00:00:00Z",
            "path": "app.py",
            "line": 3,
        }]
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=listing))
        listed = await gi.list_pr_comments("o", "r", 5, kind="inline")
        comment_id = listed["comments"][0]["id"]
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=listing[0]))
        await gi.update_pr_comment("o", "r", comment_id, "corrected", kind="inline")
        assert gi._http.request.call_args.args[1].endswith("/pulls/comments/22")

    def test_listing_is_read_only(self, gi: GitHubIntegration):
        assert gi.list_pr_comments._mcp_annotations.readOnlyHint is True


# ---------------------------------------------------------------------------
# _request allow_status
# ---------------------------------------------------------------------------


class TestAllowStatus:
    @pytest.mark.anyio
    async def test_allowed_status_is_returned_not_raised(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(status_code=404, json_data={}))
        response = await gi._request("GET", "https://api.github.com/x", allow_status=(404,))
        assert response.status_code == 404

    @pytest.mark.anyio
    async def test_an_unlisted_status_still_raises(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(status_code=404, json_data={}))
        with pytest.raises(ToolError):
            await gi._request("GET", "https://api.github.com/x", allow_status=(422,))


# ---------------------------------------------------------------------------
# Project boards (#351)
# ---------------------------------------------------------------------------

# An owner whose projectV2 resolved to nothing, which is what a wrong number and
# a token that cannot see Projects both look like.
_NO_PROJECT: dict = {"repositoryOwner": {}}


def _project() -> dict:
    """A board with one plain field and one single select. Built per call so one
    test cannot mutate what the next one reads."""
    return {
        "id": "PVT_1",
        "number": 4,
        "title": "Backlog",
        "url": "https://github.com/users/o/projects/4",
        "fields": {
            "nodes": [
                {"id": "F_title", "name": "Title", "dataType": "TITLE"},
                {
                    "id": "F_status",
                    "name": "Status",
                    "dataType": "SINGLE_SELECT",
                    "options": [{"id": "opt_todo", "name": "Todo"}, {"id": "opt_doing", "name": "In Progress"}],
                },
                {},
            ]
        },
    }


def _owner(project: dict | None = None) -> dict:
    """A repositoryOwner payload, as either inline fragment resolves into one shape."""
    return {"repositoryOwner": {"projectV2": project if project is not None else _project()}}


def _issue_node(items: list[dict] | None = None) -> dict:
    return {
        "repository": {
            "issueOrPullRequest": {
                "id": "I_1",
                "number": 12,
                "title": "A bug",
                "url": "https://github.com/o/r/issues/12",
                "projectItems": {"nodes": items if items is not None else []},
            }
        }
    }


class TestProjectResolution:
    @pytest.mark.anyio
    async def test_a_project_is_looked_up_by_owner_and_number(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(return_value=_owner())
        result = await gi.get_project_fields("o", 4)
        query, variables = gi._execute_graphql.call_args.args
        assert "repositoryOwner" in query
        assert variables == {"owner": "o", "number": 4}
        assert result["title"] == "Backlog"

    @pytest.mark.anyio
    async def test_a_missing_project_names_what_was_not_found(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(return_value=_NO_PROJECT)
        with pytest.raises(GitHubNotFoundError, match="No project #4 for 'o'"):
            await gi.get_project_fields("o", 4)

    @pytest.mark.anyio
    async def test_an_invisible_project_points_at_the_scope(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(return_value=_NO_PROJECT)
        with pytest.raises(GitHubNotFoundError, match="read:project"):
            await gi.get_project_fields("o", 4)

    @pytest.mark.anyio
    async def test_fields_list_their_options(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(return_value=_owner())
        fields = (await gi.get_project_fields("o", 4))["fields"]
        assert [f["name"] for f in fields] == ["Title", "Status"]
        assert fields[1]["options"] == ["Todo", "In Progress"]
        assert fields[0]["options"] == []

    def test_read_tools_are_read_only(self, gi: GitHubIntegration):
        for name in ("get_project_fields", "list_project_items"):
            assert getattr(gi, name)._mcp_annotations.readOnlyHint is True, name


class TestAddToProject:
    @pytest.mark.anyio
    async def test_the_content_node_id_is_what_is_added(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(
            side_effect=[_owner(), _issue_node(), {"addProjectV2ItemById": {"item": {"id": "PVTI_9"}}}]
        )
        result = await gi.add_to_project("o", 4, "o", "r", 12)
        assert gi._execute_graphql.call_args.args[1] == {"projectId": "PVT_1", "contentId": "I_1"}
        assert result["item_id"] == "PVTI_9"
        assert result["project_title"] == "Backlog"

    @pytest.mark.anyio
    async def test_a_missing_issue_is_named(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(side_effect=[_owner(), {"repository": {"issueOrPullRequest": None}}])
        with pytest.raises(GitHubNotFoundError, match="#12 in o/r"):
            await gi.add_to_project("o", 4, "o", "r", 12)

    @pytest.mark.anyio
    async def test_a_mutation_returning_no_item_is_an_error(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(side_effect=[_owner(), _issue_node(), {"addProjectV2ItemById": {}}])
        with pytest.raises(GitHubAPIError, match="no item id"):
            await gi.add_to_project("o", 4, "o", "r", 12)

    def test_is_idempotent_not_destructive(self, gi: GitHubIntegration):
        ann = gi.add_to_project._mcp_annotations
        assert ann.idempotentHint is True
        assert ann.destructiveHint is False


class TestSetProjectField:
    @pytest.mark.anyio
    async def test_field_and_option_names_resolve_to_ids(self, gi: GitHubIntegration):
        on_board = [{"id": "PVTI_9", "project": {"id": "PVT_1", "number": 4}}]
        gi._execute_graphql = AsyncMock(side_effect=[_owner(), _issue_node(on_board), {}])
        result = await gi.set_project_field("o", 4, "o", "r", 12, "Status", "In Progress")
        assert gi._execute_graphql.call_args.args[1] == {
            "projectId": "PVT_1",
            "itemId": "PVTI_9",
            "fieldId": "F_status",
            "optionId": "opt_doing",
        }
        assert result["option"] == "In Progress"

    @pytest.mark.anyio
    async def test_names_are_matched_regardless_of_case(self, gi: GitHubIntegration):
        on_board = [{"id": "PVTI_9", "project": {"id": "PVT_1", "number": 4}}]
        gi._execute_graphql = AsyncMock(side_effect=[_owner(), _issue_node(on_board), {}])
        await gi.set_project_field("o", 4, "o", "r", 12, "status", "in progress")
        assert gi._execute_graphql.call_args.args[1]["optionId"] == "opt_doing"

    @pytest.mark.anyio
    async def test_an_issue_not_on_the_board_is_added_first(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(
            side_effect=[_owner(), _issue_node(), {"addProjectV2ItemById": {"item": {"id": "PVTI_new"}}}, {}]
        )
        result = await gi.set_project_field("o", 4, "o", "r", 12, "Status", "Todo")
        assert "addProjectV2ItemById" in gi._execute_graphql.call_args_list[2].args[0]
        assert result["item_id"] == "PVTI_new"

    @pytest.mark.anyio
    async def test_an_item_already_on_the_board_is_not_added_again(self, gi: GitHubIntegration):
        on_board = [{"id": "PVTI_9", "project": {"id": "PVT_1", "number": 4}}]
        gi._execute_graphql = AsyncMock(side_effect=[_owner(), _issue_node(on_board), {}])
        await gi.set_project_field("o", 4, "o", "r", 12, "Status", "Todo")
        assert all("addProjectV2ItemById" not in c.args[0] for c in gi._execute_graphql.call_args_list)

    @pytest.mark.anyio
    async def test_an_item_on_another_board_does_not_count(self, gi: GitHubIntegration):
        elsewhere = [{"id": "PVTI_other", "project": {"id": "PVT_2", "number": 7}}]
        gi._execute_graphql = AsyncMock(
            side_effect=[_owner(), _issue_node(elsewhere), {"addProjectV2ItemById": {"item": {"id": "PVTI_new"}}}, {}]
        )
        result = await gi.set_project_field("o", 4, "o", "r", 12, "Status", "Todo")
        assert result["item_id"] == "PVTI_new"

    @pytest.mark.anyio
    async def test_an_unknown_field_lists_the_ones_there(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(return_value=_owner())
        with pytest.raises(GitHubNotFoundError, match="No field named 'Stage'.*Status, Title"):
            await gi.set_project_field("o", 4, "o", "r", 12, "Stage", "Todo")

    @pytest.mark.anyio
    async def test_an_unknown_option_lists_the_ones_there(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(return_value=_owner())
        with pytest.raises(GitHubNotFoundError, match="No option named 'Blocked'.*Todo, In Progress"):
            await gi.set_project_field("o", 4, "o", "r", 12, "Status", "Blocked")

    @pytest.mark.anyio
    async def test_a_field_that_is_not_a_select_is_rejected(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(return_value=_owner())
        with pytest.raises(GitHubValidationError, match="TITLE field"):
            await gi.set_project_field("o", 4, "o", "r", 12, "Title", "Todo")

    @pytest.mark.anyio
    async def test_a_bad_name_fails_before_the_issue_is_looked_up(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(return_value=_owner())
        with pytest.raises(GitHubNotFoundError):
            await gi.set_project_field("o", 4, "o", "r", 12, "Stage", "Todo")
        assert gi._execute_graphql.call_count == 1


class TestRemoveFromProject:
    @pytest.mark.anyio
    async def test_the_item_on_that_board_is_the_one_deleted(self, gi: GitHubIntegration):
        on_board = [
            {"id": "PVTI_other", "project": {"id": "PVT_2", "number": 7}},
            {"id": "PVTI_9", "project": {"id": "PVT_1", "number": 4}},
        ]
        gi._execute_graphql = AsyncMock(
            side_effect=[_owner(), _issue_node(on_board), {"deleteProjectV2Item": {"deletedItemId": "PVTI_9"}}]
        )
        result = await gi.remove_from_project("o", 4, "o", "r", 12)
        assert gi._execute_graphql.call_args.args[1] == {"projectId": "PVT_1", "itemId": "PVTI_9"}
        assert result == {"status": "removed", "item_id": "PVTI_9", "project_number": 4, "issue_number": 12}

    @pytest.mark.anyio
    async def test_an_issue_not_on_the_board_is_not_added_to_delete_it(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(side_effect=[_owner(), _issue_node()])
        with pytest.raises(GitHubNotFoundError, match="is not on project #4"):
            await gi.remove_from_project("o", 4, "o", "r", 12)
        assert gi._execute_graphql.call_count == 2

    def test_is_destructive(self, gi: GitHubIntegration):
        assert gi.remove_from_project._mcp_annotations.destructiveHint is True


class TestListProjectItems:
    @staticmethod
    def _items(nodes: list[dict], has_next: bool = False) -> dict:
        return _owner({
            "id": "PVT_1",
            "number": 4,
            "title": "Backlog",
            "items": {
                "totalCount": len(nodes),
                "pageInfo": {"hasNextPage": has_next, "endCursor": "cur"},
                "nodes": nodes,
            },
        })

    @pytest.mark.anyio
    async def test_an_item_reports_its_content_and_field_values(self, gi: GitHubIntegration):
        node = {
            "id": "PVTI_9",
            "type": "ISSUE",
            "content": {
                "number": 12,
                "title": "A bug",
                "state": "OPEN",
                "url": "https://github.com/o/r/issues/12",
                "repository": {"nameWithOwner": "o/r"},
            },
            "fieldValues": {
                "nodes": [
                    {},
                    {"name": "In Progress", "field": {"name": "Status"}},
                    {"number": 3.0, "field": {"name": "Size"}},
                    {"text": "note", "field": {"name": "Notes"}},
                ]
            },
        }
        gi._execute_graphql = AsyncMock(return_value=self._items([node]))
        result = await gi.list_project_items("o", 4)
        assert result["items"] == [{
            "item_id": "PVTI_9",
            "type": "ISSUE",
            "number": 12,
            "title": "A bug",
            "state": "OPEN",
            "url": "https://github.com/o/r/issues/12",
            "repository": "o/r",
            "fields": {"Status": "In Progress", "Size": 3.0, "Notes": "note"},
        }]

    @pytest.mark.anyio
    async def test_paging_arguments_reach_the_query(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(return_value=self._items([]))
        await gi.list_project_items("o", 4, per_page=10, after="prev")
        assert gi._execute_graphql.call_args.args[1] == {"owner": "o", "number": 4, "first": 10, "after": "prev"}

    @pytest.mark.anyio
    async def test_a_cursor_comes_back_only_when_there_is_another_page(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(return_value=self._items([], has_next=True))
        assert (await gi.list_project_items("o", 4))["next_cursor"] == "cur"
        gi._execute_graphql = AsyncMock(return_value=self._items([]))
        assert (await gi.list_project_items("o", 4))["next_cursor"] is None

    @pytest.mark.anyio
    async def test_a_draft_item_carries_a_title_and_no_number(self, gi: GitHubIntegration):
        node = {"id": "PVTI_d", "type": "DRAFT_ISSUE", "content": {"title": "Think about it"}}
        gi._execute_graphql = AsyncMock(return_value=self._items([node]))
        item = (await gi.list_project_items("o", 4))["items"][0]
        assert item["title"] == "Think about it"
        assert item["number"] is None
        assert item["fields"] == {}


class TestGraphQLScopeErrors:
    def test_a_missing_scope_is_an_auth_error_naming_the_scope(self):
        errors = [{
            "type": "INSUFFICIENT_SCOPES",
            "message": "The 'id' field requires one of the following scopes: ['read:project'].",
        }]
        with pytest.raises(GitHubAuthError, match="read:project"):
            handle_graphql_errors(errors)

    def test_a_scope_error_does_not_advise_re_authenticating(self):
        errors = [{"type": "INSUFFICIENT_SCOPES", "message": "Missing scope."}]
        with pytest.raises(GitHubAuthError) as caught:
            handle_graphql_errors(errors)
        assert "re-authenticate" not in str(caught.value)

    def test_a_forbidden_error_still_advises_re_authenticating(self):
        with pytest.raises(GitHubAuthError, match="re-authenticate"):
            handle_graphql_errors([{"type": "FORBIDDEN", "message": "Nope."}])

    @pytest.mark.anyio
    async def test_guard_lets_an_auth_error_keep_its_class(self, gi: GitHubIntegration):
        with pytest.raises(GitHubAuthError):  # noqa: PT012 - the guard is what is under test
            async with gi._guard("do a thing"):
                raise GitHubAuthError("Missing scope.")


# ---------------------------------------------------------------------------
# list_repos (#354)
# ---------------------------------------------------------------------------


def _repo_payload(**overrides) -> dict:
    payload = {
        "name": "toolbox",
        "full_name": "acme/toolbox",
        "owner": _NOISE_USER,
        "description": "a repo",
        "default_branch": "main",
        "private": False,
        "fork": False,
        "archived": False,
        "pushed_at": "2026-08-01T00:00:00Z",
        "html_url": "https://github.com/acme/toolbox",
        "stargazers_count": 12,
        "watchers_count": 12,
        "permissions": {"admin": True, "push": True, "pull": True},
    }
    payload.update(overrides)
    return payload


class TestListRepos:
    @pytest.mark.anyio
    async def test_a_person_uses_the_users_endpoint(self, gi: GitHubIntegration):
        responses = iter([
            _mock_response(json_data={"login": "someone", "type": "User"}),
            _mock_response(json_data=[_repo_payload()]),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.list_repos("someone")
        assert gi._http.request.call_args.args[1] == "https://api.github.com/users/someone/repos"

    @pytest.mark.anyio
    async def test_an_organisation_uses_the_orgs_endpoint(self, gi: GitHubIntegration):
        responses = iter([
            _mock_response(json_data={"login": "acme", "type": "Organization"}),
            _mock_response(json_data=[_repo_payload()]),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.list_repos("acme")
        # /users/acme/repos would answer, but only with the public ones.
        assert gi._http.request.call_args.args[1] == "https://api.github.com/orgs/acme/repos"

    @pytest.mark.anyio
    async def test_no_owner_reads_the_callers_own(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[_repo_payload(private=True)]))
        result = await gi.list_repos()
        # No account lookup, since there is no owner to classify.
        assert gi._http.request.call_count == 1
        assert gi._http.request.call_args.args[1] == "https://api.github.com/user/repos"
        assert result["repos"][0]["private"] is True

    @pytest.mark.anyio
    async def test_results_are_trimmed(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[_repo_payload()]))
        result = await gi.list_repos()
        assert result == {
            "total": 1,
            "repos": [{
                "name": "toolbox",
                "owner": "octocat",
                "description": "a repo",
                "default_branch": "main",
                "private": False,
                "fork": False,
                "archived": False,
                "pushed_at": "2026-08-01T00:00:00Z",
                "html_url": "https://github.com/acme/toolbox",
            }],
        }

    @pytest.mark.anyio
    async def test_sort_and_paging_go_out_as_params(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[]))
        await gi.list_repos(sort="full_name", per_page=100, page=3)
        assert gi._http.request.call_args.kwargs["params"] == {"sort": "full_name", "per_page": 100, "page": 3}

    @pytest.mark.anyio
    async def test_an_owner_that_is_neither_names_itself_in_the_error(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(status_code=404, json_data={}))
        with pytest.raises(GitHubNotFoundError, match="No user or organisation named 'nope'"):
            await gi.list_repos("nope")
        # The listing was never attempted.
        assert gi._http.request.call_count == 1

    def test_is_read_only(self, gi: GitHubIntegration):
        assert gi.list_repos._mcp_annotations.readOnlyHint is True
