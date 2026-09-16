"""Tests for GitHubIntegration — annotations, async HTTP, Context injection."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastmcp.exceptions import ToolError

from mcp_github.activity import ACTIVITY_SECTIONS, ACTIVITY_STAGES, MAX_HISTORY_PAGES, MAX_REPO_PAGES
from mcp_github.auth import MISSING_CREDENTIALS
from mcp_github.exceptions import (
    GitHubAPIError,
    GitHubAuthError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubValidationError,
)
from mcp_github.github_integration import CONNECT_TIMEOUT, TIMEOUT, GitHubIntegration, _timeout
from mcp_github.graphql_client import handle_graphql_errors
from mcp_github.tool_annotations import (
    GATED_SCOPES,
    PROJECT_SCOPES,
    WRITE_SCOPES,
    _destructive,
    _read_only,
    _write,
)

# Helpers


def _mock_response(
    status_code: int = 200,
    json_data: dict | list | None = None,
    text: str = "",
    etag: str | None = None,
    headers: dict[str, str] | None = None,
    reason_phrase: str = "OK",
) -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status_code
    r.is_success = status_code < 400
    r.json.return_value = json_data if json_data is not None else {}
    r.text = text
    r.reason_phrase = reason_phrase
    r.headers = {"ETag": etag} if etag else {}
    r.headers.update(headers or {})
    r.content = json.dumps(json_data).encode() if json_data is not None else text.encode()
    r.request = None
    return r


def _week(sunday: str, days: list[int]) -> dict:
    """One entry of a stargazers/history page. `sunday` is the UTC Sunday the week
    opens on, and `days` holds that week's star counts from the Sunday onwards."""
    return {
        "week": int(datetime.fromisoformat(sunday + "T00:00:00+00:00").timestamp()),
        "total": sum(days),
        "days": days,
    }


def _weeks_back(newest_sunday: str, count: int, per_day: int) -> list[dict]:
    """A run of consecutive history weeks, newest first, as GitHub returns them."""
    newest = datetime.fromisoformat(newest_sunday + "T00:00:00+00:00")
    return [_week((newest - timedelta(weeks=i)).strftime("%Y-%m-%d"), [per_day] * 7) for i in range(count)]


# Older than every cutoff the star tests use, so it ends the walk.
_OLD_WEEK = _week("2000-01-02", [0] * 7)


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


# Fixture


@pytest.fixture
def gi() -> GitHubIntegration:
    """GitHubIntegration instance with a mocked HTTP client and test token."""
    with patch("mcp_github.github_integration.GITHUB_TOKEN", "test-token"):
        instance = GitHubIntegration()
    instance._http = AsyncMock()
    return instance


# Annotation semantics


class TestAnnotations:
    def test_read_only_hints(self):
        def fn(): ...

        _read_only(fn)
        ann = fn._mcp_annotations
        assert ann.read_only_hint is True
        assert ann.destructive_hint is False
        assert ann.idempotent_hint is False
        assert fn._mcp_task is False
        assert fn._mcp_scopes == ()

    def test_read_only_with_task(self):
        def fn(): ...

        _read_only(task=True)(fn)
        ann = fn._mcp_annotations
        assert ann.read_only_hint is True
        assert fn._mcp_task is True
        assert fn._mcp_scopes == ()

    def test_write_hints(self):
        def fn(): ...

        _write(fn)
        ann = fn._mcp_annotations
        assert ann.read_only_hint is False
        assert ann.destructive_hint is False
        assert ann.idempotent_hint is False
        assert fn._mcp_task is False
        assert fn._mcp_scopes == WRITE_SCOPES

    def test_write_idempotent(self):
        def fn(): ...

        _write(idempotent=True)(fn)
        ann = fn._mcp_annotations
        assert ann.read_only_hint is False
        assert ann.destructive_hint is False
        assert ann.idempotent_hint is True

    def test_destructive_hints(self):
        def fn(): ...

        _destructive(fn)
        ann = fn._mcp_annotations
        assert ann.destructive_hint is True
        assert ann.read_only_hint is False
        assert fn._mcp_scopes == WRITE_SCOPES

    def test_a_tool_may_name_a_scope_beyond_its_class(self):
        def fn(): ...

        _write(idempotent=True, scopes=PROJECT_SCOPES)(fn)
        assert fn._mcp_scopes == WRITE_SCOPES + PROJECT_SCOPES

    def test_every_tool_scopes_match_its_class(self, gi: GitHubIntegration):
        """A read-only tool needs no scope, anything that changes state needs the
        write scopes, and every scope declared is one the server gates on. See #388."""
        seen = 0
        for name in dir(gi):
            if name.startswith("_"):
                continue
            method = getattr(gi, name)
            annotations = getattr(method, "_mcp_annotations", None)
            if annotations is None:
                continue
            seen += 1
            scopes = method._mcp_scopes
            assert set(scopes) <= set(GATED_SCOPES), name
            if annotations.read_only_hint:
                assert scopes == (), name
            else:
                assert set(WRITE_SCOPES) <= set(scopes), name
        assert seen > 0

    def test_the_board_tools_need_the_project_scope(self, gi: GitHubIntegration):
        """A board sits outside the repository it tracks, so repo does not reach it.
        See #351."""
        for name in ("add_to_project", "set_project_field", "remove_from_project"):
            assert set(PROJECT_SCOPES) <= set(getattr(gi, name)._mcp_scopes), name

    def test_idempotent_tools_annotated_correctly(self, gi: GitHubIntegration):
        for name in ("update_pr", "update_pr_branch", "update_issue", "update_assignees"):
            method = getattr(gi, name)
            ann = method._mcp_annotations
            assert ann.idempotent_hint is True, f"{name} should have idempotent_hint=True"
            assert ann.destructive_hint is False, f"{name} should not be destructive"

    def test_merge_pr_is_write_not_destructive(self, gi: GitHubIntegration):
        ann = gi.merge_pr._mcp_annotations
        assert ann.destructive_hint is False
        assert ann.read_only_hint is False


# Connection pooling — single shared client


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


# Conditional reads


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


# Timeouts


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


# aclose / async context manager


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


# merge_pr — request shape and GitHub error surfacing


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


# update_pr — reuses the PATCH response (no redundant GET). See #399.


class TestUpdatePrTitleAndBody:
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
        result = await gi.update_pr("o", "r", 5, title="New title", body="New body")
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
            "head_sha": None,
            "head_ref": None,
            "base_ref": None,
            "requested_reviewers": [],
            "requested_teams": [],
        }


# get_pr_content head SHA — the source update_pr_branch needs. See #411.


class TestPRHeadSha:
    @pytest.mark.anyio
    async def test_get_pr_content_reports_the_head_sha_and_refs(self, gi: GitHubIntegration):
        payload = {
            "title": "A change",
            "body": "Details",
            "user": _NOISE_USER,
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": "2026-07-02T00:00:00Z",
            "state": "open",
            "head": {"sha": "9341d65", "ref": "feature/x"},
            "base": {"ref": "main"},
        }
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=payload))
        result = await gi.get_pr_content("o", "r", 5)
        assert result["head_sha"] == "9341d65"
        assert result["head_ref"] == "feature/x"
        assert result["base_ref"] == "main"

    @pytest.mark.anyio
    async def test_the_head_sha_reaches_update_pr_branch(self, gi: GitHubIntegration):
        """The guard pr-management recommends had no source until get_pr_content
        carried the head SHA."""
        pr = {
            "title": "A change",
            "body": "Details",
            "user": _NOISE_USER,
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": "2026-07-02T00:00:00Z",
            "state": "open",
            "head": {"sha": "9341d65", "ref": "feature/x"},
            "base": {"ref": "main"},
        }
        responses = iter([
            _mock_response(json_data=pr),
            _mock_response(json_data={"message": "Updating pull request branch."}),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        content = await gi.get_pr_content("o", "r", 5)
        await gi.update_pr_branch("o", "r", 5, expected_head_sha=content["head_sha"])
        assert gi._http.request.call_args.kwargs["json"] == {"expected_head_sha": "9341d65"}


# get_user_activities — Context progress ordering and completeness


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


# get_user_activities — filtering, capping and date handling


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


# get_repo_stars_since — new stars within a date window


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
        history = [_week("2090-01-01", [1, 0, 0, 0, 0, 0, 0]), _OLD_WEEK]
        responses = iter([_mock_response(json_data=page1), _mock_response(json_data=page2)] + [
            _mock_response(json_data=history) for _ in range(30)
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
        # repo-a: 2 new stars in the cutoff week; repo-b: 1 new, plus one the week
        # before the cutoff that must not count.
        history_a = [_week("2090-01-01", [1, 1, 0, 0, 0, 0, 0]), _OLD_WEEK]
        history_b = [
            _week("2090-01-01", [1, 0, 0, 0, 0, 0, 0]),
            _week("2089-12-25", [1, 0, 0, 0, 0, 0, 0]),
            _OLD_WEEK,
        ]

        responses = iter([
            _mock_response(json_data=repos_payload),
            _mock_response(json_data=history_a),
            _mock_response(json_data=history_b),
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
    async def test_day_exactly_on_the_cutoff_counts(self, gi: GitHubIntegration):
        """The cutoff is inclusive, so the day it names is new."""
        repos_payload = [
            {"name": "edge", "stargazers_count": 2, "html_url": "https://github.com/u/edge", "description": None},
        ]
        # Sunday and Monday sit before the cutoff, Tuesday lands exactly on it.
        history = [_week("2090-01-01", [0, 0, 1, 0, 0, 0, 0]), _OLD_WEEK]
        responses = iter([_mock_response(json_data=repos_payload), _mock_response(json_data=history)])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))

        result = await gi.get_repo_stars_since("u", since="2090-01-03")

        assert result["repos"][0]["new_stars"] == 1

    @pytest.mark.anyio
    async def test_week_straddling_the_cutoff_counts_only_later_days(self, gi: GitHubIntegration):
        """The week holding the cutoff contributes its days from the cutoff onwards,
        never its whole total. See #398."""
        repos_payload = [
            {"name": "straddle", "stargazers_count": 20, "html_url": "https://github.com/u/s", "description": None},
        ]
        # Ten stars fall on the Sunday and Monday before the cutoff, six on or after.
        history = [_week("2090-01-01", [5, 5, 1, 2, 3, 0, 0]), _OLD_WEEK]
        responses = iter([_mock_response(json_data=repos_payload), _mock_response(json_data=history)])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))

        result = await gi.get_repo_stars_since("u", since="2090-01-03")

        assert result["repos"][0]["new_stars"] == 6

    @pytest.mark.anyio
    async def test_stops_before_reading_older_history_pages(self, gi: GitHubIntegration):
        """The walk ends at the first week wholly older than the cutoff, so a repo
        with years of history still costs one request for a recent window."""
        repos_payload = [
            {"name": "big", "stargazers_count": 250, "html_url": "https://github.com/u/big", "description": None},
        ]
        page1 = [_week("2090-01-01", [3] * 7), _week("2089-12-25", [4] * 7)]
        requested: list[int] = []

        async def fake_request(method, url, **kw):
            if url.endswith("/repos"):
                return _mock_response(json_data=repos_payload)
            requested.append(kw["params"]["page"])
            return _mock_response(json_data=page1 if kw["params"]["page"] == 1 else [])

        gi._http.request = AsyncMock(side_effect=fake_request)

        result = await gi.get_repo_stars_since("u", since="2090-01-01")

        assert requested == [1]  # page 2 never fetched
        assert result["repos"][0]["new_stars"] == 21

    @pytest.mark.anyio
    async def test_counts_across_more_than_one_history_page(self, gi: GitHubIntegration):
        """A window longer than the 30 weeks one page holds carries on into the next
        page, so the count covers the whole window rather than the first page."""
        repos_payload = [
            {"name": "long", "stargazers_count": 400, "html_url": "https://github.com/u/long", "description": None},
        ]
        pages = {
            1: _weeks_back("2090-01-01", 30, 1),
            2: _weeks_back("2089-06-05", 30, 1),
        }
        requested: list[int] = []

        async def fake_request(method, url, **kw):
            if url.endswith("/repos"):
                return _mock_response(json_data=repos_payload)
            requested.append(kw["params"]["page"])
            return _mock_response(json_data=pages.get(kw["params"]["page"], []))

        gi._http.request = AsyncMock(side_effect=fake_request)

        result = await gi.get_repo_stars_since("u", since="2089-06-05")

        assert requested == [1, 2]
        # 30 whole weeks on page 1, then the single cutoff week on page 2.
        assert result["repos"][0]["new_stars"] == 217
        assert result["truncated"] is False

    @pytest.mark.anyio
    async def test_history_page_cap_marks_the_result_truncated(self, gi: GitHubIntegration):
        """Every page comes back newer than the cutoff, so the walk runs out of pages
        before it finishes and has to report the count as short."""
        repos_payload = [
            {"name": "ancient", "stargazers_count": 9000, "html_url": "https://github.com/u/a", "description": None},
        ]
        page = _weeks_back("2090-01-01", 30, 1)
        requested: list[int] = []

        async def fake_request(method, url, **kw):
            if url.endswith("/repos"):
                return _mock_response(json_data=repos_payload)
            requested.append(kw["params"]["page"])
            return _mock_response(json_data=page)

        gi._http.request = AsyncMock(side_effect=fake_request)

        result = await gi.get_repo_stars_since("u", since="2000-01-01")

        assert requested == list(range(1, MAX_HISTORY_PAGES + 1))
        assert result["truncated"] is True

    @pytest.mark.anyio
    async def test_reads_the_star_history_endpoint(self, gi: GitHubIntegration):
        """The plain stargazers endpoint answers 404 on any repo the token neither
        admins nor collaborates on, so the URL is part of the contract. See #398."""
        repos_payload = [
            {"name": "r", "stargazers_count": 4, "html_url": "https://github.com/u/r", "description": None},
        ]
        history = [_week("2090-01-01", [1, 0, 0, 0, 0, 0, 0]), _OLD_WEEK]
        responses = iter([_mock_response(json_data=repos_payload), _mock_response(json_data=history)])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))

        await gi.get_repo_stars_since("u", since="2090-01-01")

        assert gi._http.request.call_args.args[1] == "https://api.github.com/repos/u/r/stargazers/history"

    @pytest.mark.anyio
    async def test_excludes_repos_with_no_new_stars(self, gi: GitHubIntegration):
        repos_payload = [
            {"name": "old-repo", "stargazers_count": 3, "html_url": "https://github.com/u/old-repo", "description": None},
        ]
        history_old = [_OLD_WEEK]  # every week before the cutoff → no new stars

        responses = iter([
            _mock_response(json_data=repos_payload),
            _mock_response(json_data=history_old),
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
        history_new = [_week("2090-01-01", [1, 0, 0, 0, 0, 0, 0]), _OLD_WEEK]
        calls = iter(
            [_mock_response(json_data=repos_payload)]
            + [_mock_response(json_data=history_new)] * 5
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


# get_pr_status_checks — check_suites allocation is conditional on ctx


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


# get_latest_sha + create_tag — empty-repo contract


class TestGetLatestShaAndCreateTag:
    @pytest.mark.anyio
    async def test_get_latest_sha_asks_for_one_commit(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[{"sha": "abc123"}]))
        assert await gi.get_latest_sha("owner", "repo") == "abc123"
        assert "per_page=1" in gi._http.request.call_args.args[1]

    @pytest.mark.anyio
    async def test_get_latest_sha_empty_repo_returns_none(self, gi: GitHubIntegration):
        """GitHub answers an empty repository with 409, not an empty list, so the
        documented no-commits contract only holds by reading that status. See #411."""
        gi._http.request = AsyncMock(
            return_value=_mock_response(status_code=409, json_data={"message": "Git Repository is empty."})
        )
        result = await gi.get_latest_sha("owner", "empty-repo")
        assert result is None

    @pytest.mark.anyio
    async def test_get_latest_sha_reads_the_default_branch_when_no_ref_is_given(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[{"sha": "abc123"}]))
        await gi.get_latest_sha("owner", "repo")
        assert "sha=" not in gi._http.request.call_args.args[1]

    @pytest.mark.anyio
    async def test_get_latest_sha_reads_a_named_branch(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[{"sha": "branchsha"}]))
        assert await gi.get_latest_sha("owner", "repo", ref="feature/x") == "branchsha"
        assert "sha=feature%2Fx" in gi._http.request.call_args.args[1]

    @pytest.mark.anyio
    async def test_get_latest_sha_reads_a_tag(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[{"sha": "tagsha"}]))
        assert await gi.get_latest_sha("owner", "repo", ref="v1.2.3") == "tagsha"
        assert "sha=v1.2.3" in gi._http.request.call_args.args[1]

    @pytest.mark.anyio
    async def test_get_latest_sha_rejects_an_unknown_ref(self, gi: GitHubIntegration):
        """A ref GitHub cannot resolve is a 404, which is a failure rather than
        the no-commits answer."""
        gi._http.request = AsyncMock(
            return_value=_mock_response(status_code=404, json_data={"message": "No commit found for SHA: nope"})
        )
        with pytest.raises(ToolError, match="nope"):
            await gi.get_latest_sha("owner", "repo", ref="nope")

    @pytest.mark.anyio
    async def test_create_tag_refuses_an_empty_repository(self, gi: GitHubIntegration):
        """The guard was unreachable while an empty repository raised instead of
        answering None."""
        gi._http.request = AsyncMock(
            return_value=_mock_response(status_code=409, json_data={"message": "Git Repository is empty."})
        )
        with pytest.raises(GitHubNotFoundError, match="No commits found"):
            await gi.create_tag("owner", "empty-repo", "v1")

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


# get_pr_status_checks — pagination + truncation


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


# Response trimming — write tools return compact contracts, not raw payloads

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
    async def test_get_issue_returns_trimmed_issue(self, gi: GitHubIntegration):
        payload = _issue_payload(assignees=[{"login": "saidsef", "id": 1}])
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=payload))
        result = await gi.get_issue("o", "r", 7)
        assert result["number"] == 7
        assert result["body"] == "Details"
        assert result["assignees"] == ["saidsef"]
        assert gi._http.request.call_args.args[1] == "https://api.github.com/repos/o/r/issues/7"

    @pytest.mark.anyio
    async def test_get_issue_refuses_a_pull_request_number(self, gi: GitHubIntegration):
        payload = _issue_payload(pull_request={"url": "https://api.github.com/repos/o/r/pulls/7"})
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=payload))
        with pytest.raises(GitHubValidationError, match="pull request"):
            await gi.get_issue("o", "r", 7)

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
            "assignees": [],
            "milestone": None,
            "html_url": "https://github.com/o/r/issues/7",
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": "2026-07-02T00:00:00Z",
        }
        assert gi._http.request.call_args.kwargs["json"]["labels"] == ["bug", "mcp"]

    @pytest.mark.anyio
    async def test_create_issue_without_labels_creates_an_unlabelled_issue(
        self, gi: GitHubIntegration
    ):
        # #402: labels must be omittable, and omitting must not append anything.
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=_issue_payload(labels=[])))
        result = await gi.create_issue("o", "r", "A bug", "Details")
        assert "labels" not in gi._http.request.call_args.kwargs["json"]
        assert result["labels"] == []

    @pytest.mark.anyio
    async def test_create_issue_mcp_label_opt_out(self, gi: GitHubIntegration):
        # #402: the 'mcp' append is opt-out.
        gi._http.request = AsyncMock(
            return_value=_mock_response(json_data=_issue_payload(labels=[{"name": "bug"}]))
        )
        await gi.create_issue("o", "r", "A bug", "Details", ["bug"], mcp_label=False)
        assert gi._http.request.call_args.kwargs["json"]["labels"] == ["bug"]

    @pytest.mark.anyio
    async def test_create_issue_does_not_duplicate_a_caller_supplied_mcp_label(
        self, gi: GitHubIntegration
    ):
        gi._http.request = AsyncMock(
            return_value=_mock_response(json_data=_issue_payload(labels=[{"name": "mcp"}]))
        )
        await gi.create_issue("o", "r", "A bug", "Details", ["mcp"])
        assert gi._http.request.call_args.kwargs["json"]["labels"] == ["mcp"]

    @pytest.mark.anyio
    async def test_create_pr_mcp_label_opt_out(self, gi: GitHubIntegration):
        responses = iter([
            _mock_response(json_data=_CREATED_PR),
            _mock_response(json_data=_label_payload("bug")),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        result = await gi.create_pr(
            "o", "r", "A change", "Details", "feat", "main", labels=["bug"], mcp_label=False
        )
        assert gi._http.request.call_args_list[1].kwargs["json"] == {"labels": ["bug"]}
        assert result["labels"] == ["bug"]

    @pytest.mark.anyio
    async def test_update_issue_returns_trimmed_issue(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(
            return_value=_mock_response(json_data=_issue_payload(state="closed"))
        )
        result = await gi.update_issue("o", "r", 7, "A bug", "Details", state="closed")
        assert result["state"] == "closed"
        assert result["author"] == "octocat"
        assert set(result) == {
            "number", "title", "body", "state", "author", "labels", "assignees",
            "milestone", "html_url", "created_at", "updated_at",
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
            "updated": False,
        }


# Repository labels


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
            "count": 2,
            "has_more": False,
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
        assert result == {"count": 0, "has_more": False, "labels": []}

    def test_is_read_only(self, gi: GitHubIntegration):
        assert gi.list_repo_labels._mcp_annotations.read_only_hint is True


# get_repository_file + list_repository_tree — repository reads. See #409.


def _raw(content: bytes) -> MagicMock:
    r = _mock_response(text="")
    r.content = content
    r.headers = {"content-type": "application/vnd.github.raw; charset=utf-8"}
    return r


class TestGetRepositoryFile:
    @pytest.mark.anyio
    async def test_reads_a_text_file_whole(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_raw(b"print('hi')\n"))
        result = await gi.get_repository_file("o", "r", "app.py")
        assert result["content"] == "print('hi')\n"
        assert result["binary"] is False
        assert result["truncated"] is False
        assert result["next_offset"] is None

    @pytest.mark.anyio
    async def test_asks_for_the_raw_media_type(self, gi: GitHubIntegration):
        """The base64 body stops carrying content over 1 MB. Raw does not."""
        gi._http.request = AsyncMock(return_value=_raw(b"x"))
        await gi.get_repository_file("o", "r", "app.py")
        assert gi._http.request.call_args.kwargs["headers"]["Accept"] == "application/vnd.github.raw"

    @pytest.mark.anyio
    async def test_a_ref_is_sent_and_a_leading_slash_is_dropped(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_raw(b"x"))
        await gi.get_repository_file("o", "r", "/src/app.py", ref="feature/x")
        url = gi._http.request.call_args.args[1]
        assert url.endswith("/contents/src/app.py?ref=feature%2Fx")

    @pytest.mark.anyio
    async def test_a_window_says_where_to_carry_on(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_raw(b"abcdefghij"))
        result = await gi.get_repository_file("o", "r", "app.py", offset=2, limit=3)
        assert result["content"] == "cde"
        assert result["bytes_returned"] == 3
        assert result["bytes_total"] == 10
        assert result["truncated"] is True
        assert result["next_offset"] == 5

    @pytest.mark.anyio
    async def test_the_last_window_is_not_truncated(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_raw(b"abcdefghij"))
        result = await gi.get_repository_file("o", "r", "app.py", offset=5, limit=5)
        assert result["truncated"] is False
        assert result["next_offset"] is None

    @pytest.mark.anyio
    async def test_a_zero_limit_reports_the_size_without_the_content(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_raw(b"abcdefghij"))
        result = await gi.get_repository_file("o", "r", "app.py", limit=0)
        assert result["content"] == ""
        assert result["bytes_total"] == 10
        assert result["truncated"] is True

    @pytest.mark.anyio
    async def test_a_binary_file_is_named_rather_than_decoded(self, gi: GitHubIntegration):
        """Decoding a blob with a NUL in it returns mangled text under a name
        that promises the file."""
        gi._http.request = AsyncMock(return_value=_raw(b"\x89PNG\r\n\x1a\n\x00\x00"))
        result = await gi.get_repository_file("o", "r", "logo.png")
        assert result["binary"] is True
        assert result["content"] == ""
        assert result["bytes_total"] == 10

    @pytest.mark.anyio
    async def test_a_directory_points_at_the_tree_tool(self, gi: GitHubIntegration):
        """A directory answers as JSON however the Accept header is set."""
        listing = _mock_response(json_data=[{"name": "app.py", "type": "file"}])
        listing.headers = {"content-type": "application/json; charset=utf-8"}
        gi._http.request = AsyncMock(return_value=listing)
        with pytest.raises(GitHubValidationError, match="list_repository_tree"):
            await gi.get_repository_file("o", "r", "src")

    @pytest.mark.anyio
    async def test_a_missing_path_is_not_found(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(
            return_value=_mock_response(status_code=404, json_data={"message": "Not Found"})
        )
        with pytest.raises(ToolError, match="nope.txt"):
            await gi.get_repository_file("o", "r", "nope.txt")

    @pytest.mark.anyio
    async def test_a_bad_ref_is_not_found(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(
            return_value=_mock_response(status_code=404, json_data={"message": "No commit found for the ref nope"})
        )
        with pytest.raises(ToolError, match="nope"):
            await gi.get_repository_file("o", "r", "README.md", ref="nope")

    @pytest.mark.anyio
    async def test_a_negative_window_is_refused(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_raw(b"x"))
        with pytest.raises(GitHubValidationError, match="negative"):
            await gi.get_repository_file("o", "r", "app.py", offset=-1)
        gi._http.request.assert_not_awaited()

    def test_is_read_only(self, gi: GitHubIntegration):
        assert gi.get_repository_file._mcp_annotations.read_only_hint is True


class TestListRepositoryTree:
    @staticmethod
    def _tree(**kw) -> MagicMock:
        return _mock_response(json_data={
            "sha": "t1",
            "tree": [{"path": "app.py", "mode": "100644", "type": "blob", "size": 12, "sha": "b1"}],
            "truncated": False,
            **kw,
        })

    @pytest.mark.anyio
    async def test_lists_the_root_at_head_by_default(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=self._tree())
        result = await gi.list_repository_tree("o", "r")
        assert gi._http.request.call_args.args[1].endswith("/git/trees/HEAD")
        assert result["total"] == 1
        assert result["entries"][0] == {
            "path": "app.py", "mode": "100644", "type": "blob", "size": 12, "sha": "b1"
        }

    @pytest.mark.anyio
    async def test_a_subdirectory_uses_the_ref_colon_path_form(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=self._tree())
        await gi.list_repository_tree("o", "r", path="src", ref="main")
        assert gi._http.request.call_args.args[1].endswith("/git/trees/main%3Asrc")

    @pytest.mark.anyio
    async def test_recursive_is_omitted_rather_than_sent_as_false(self, gi: GitHubIntegration):
        """GitHub reads any value of recursive as on, "false" and "0" included,
        so sending it at all would recurse."""
        gi._http.request = AsyncMock(return_value=self._tree())
        await gi.list_repository_tree("o", "r", recursive=False)
        assert "recursive" not in gi._http.request.call_args.args[1]

    @pytest.mark.anyio
    async def test_recursive_asks_for_every_level(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=self._tree())
        await gi.list_repository_tree("o", "r", recursive=True)
        assert "recursive=1" in gi._http.request.call_args.args[1]

    @pytest.mark.anyio
    async def test_a_capped_tree_says_so(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=self._tree(truncated=True))
        assert (await gi.list_repository_tree("o", "r"))["truncated"] is True

    @pytest.mark.anyio
    async def test_a_bad_ref_is_not_found(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(
            return_value=_mock_response(status_code=404, json_data={"message": "Not Found"})
        )
        with pytest.raises(ToolError, match="nope"):
            await gi.list_repository_tree("o", "r", ref="nope")

    def test_is_read_only(self, gi: GitHubIntegration):
        assert gi.list_repository_tree._mcp_annotations.read_only_hint is True


# add_inline_pr_comment — side and multi-line ranges. See #410.


def _inline_responses(**overrides):
    """A head-SHA read followed by the created review comment."""
    comment = {
        "id": 22,
        "path": "app.py",
        "body": "fix this",
        "user": _NOISE_USER,
        "html_url": "https://github.com/o/r/pull/5#discussion_r22",
        "created_at": "2026-07-01T00:00:00Z",
        **overrides,
    }
    return iter([_mock_response(json_data={"head": {"sha": "abc123"}}), _mock_response(json_data=comment)])


class TestInlineCommentPlacement:
    @staticmethod
    def _posted(gi: GitHubIntegration) -> dict:
        return gi._http.request.call_args_list[1].kwargs["json"]

    @pytest.mark.anyio
    async def test_an_added_line_defaults_to_the_right_side(self, gi: GitHubIntegration):
        responses = _inline_responses()
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.add_inline_pr_comment("o", "r", 5, "app.py", 3, "fix this")
        posted = self._posted(gi)
        assert posted["side"] == "RIGHT"
        assert posted["line"] == 3
        assert "start_line" not in posted

    @pytest.mark.anyio
    async def test_a_deleted_line_is_reachable_on_the_left_side(self, gi: GitHubIntegration):
        responses = _inline_responses()
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.add_inline_pr_comment("o", "r", 5, "app.py", 7, "why drop this?", side="LEFT")
        assert self._posted(gi)["side"] == "LEFT"

    @pytest.mark.anyio
    async def test_a_context_line_stays_on_the_right_side(self, gi: GitHubIntegration):
        """An unchanged line shown for context lives on RIGHT, same as an addition."""
        responses = _inline_responses()
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.add_inline_pr_comment("o", "r", 5, "app.py", 11, "context", side="RIGHT")
        assert self._posted(gi)["side"] == "RIGHT"

    @pytest.mark.anyio
    async def test_a_range_sends_start_line_and_matches_the_side(self, gi: GitHubIntegration):
        responses = _inline_responses()
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.add_inline_pr_comment("o", "r", 5, "app.py", 9, "this block", start_line=4)
        posted = self._posted(gi)
        assert posted["start_line"] == 4
        assert posted["line"] == 9
        assert posted["start_side"] == "RIGHT"

    @pytest.mark.anyio
    async def test_a_range_can_start_on_the_other_side(self, gi: GitHubIntegration):
        responses = _inline_responses()
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.add_inline_pr_comment("o", "r", 5, "app.py", 9, "spans", start_line=4, start_side="LEFT")
        assert self._posted(gi)["start_side"] == "LEFT"

    @pytest.mark.anyio
    async def test_a_backwards_range_is_refused_before_github_sees_it(self, gi: GitHubIntegration):
        responses = _inline_responses()
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        with pytest.raises(GitHubValidationError, match="must come before"):
            await gi.add_inline_pr_comment("o", "r", 5, "app.py", 4, "backwards", start_line=9)
        gi._http.request.assert_not_awaited()

    @pytest.mark.anyio
    async def test_a_line_outside_the_diff_names_the_path_and_line(self, gi: GitHubIntegration):
        """GitHub rejects a line outside every hunk. The message has to say which
        line, since the caller picked it from a diff it read separately."""
        responses = iter([
            _mock_response(json_data={"head": {"sha": "abc123"}}),
            _mock_response(
                status_code=422,
                json_data={
                    "message": "Validation Failed",
                    "errors": [{"resource": "PullRequestReviewComment", "field": "line", "code": "invalid"}],
                },
                reason_phrase="Unprocessable Entity",
            ),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        with pytest.raises(ToolError, match=r"app\.py:999"):
            await gi.add_inline_pr_comment("o", "r", 5, "app.py", 999, "out of hunk")

    @pytest.mark.anyio
    async def test_a_failed_range_names_both_ends(self, gi: GitHubIntegration):
        responses = iter([
            _mock_response(json_data={"head": {"sha": "abc123"}}),
            _mock_response(status_code=422, json_data={"message": "Validation Failed"}),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        with pytest.raises(ToolError, match=r"app\.py:4-9"):
            await gi.add_inline_pr_comment("o", "r", 5, "app.py", 9, "range", start_line=4)

    @pytest.mark.anyio
    async def test_the_read_side_reports_where_a_comment_sits(self, gi: GitHubIntegration):
        """list_pr_comments has to carry the fields the write side can now set,
        or a second review cannot tell what the first said about a range."""
        gi._http.request = AsyncMock(
            return_value=_mock_response(
                json_data=[{
                    "id": 1,
                    "body": "b",
                    "user": _NOISE_USER,
                    "html_url": "https://github.com/o/r/pull/5#discussion_r1",
                    "created_at": "2026-07-01T00:00:00Z",
                    "path": "app.py",
                    "line": 9,
                    "side": "RIGHT",
                    "start_line": 4,
                    "start_side": "RIGHT",
                }]
            )
        )
        comment = (await gi.list_pr_comments("o", "r", 5, kind="inline"))["comments"][0]
        assert comment["side"] == "RIGHT"
        assert comment["start_line"] == 4
        assert comment["start_side"] == "RIGHT"


# get_pr_diff — size reporting and truncation (#314)


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


# search_issues_prs (#346)


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
        assert gi.search_issues_prs._mcp_annotations.read_only_hint is True


# Releases and tags — read, update, delete (#347)


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
        assert result["count"] == 1
        assert set(result["releases"][0]) == {
            "id", "tag_name", "name", "html_url", "draft", "prerelease", "body",
        }

    @pytest.mark.anyio
    async def test_list_tags_keeps_name_and_sha(self, gi: GitHubIntegration):
        payload = [{"name": "v1.0.0", "zipball_url": "z", "commit": {"sha": "abc123", "url": "u"}}]
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=payload))
        result = await gi.list_tags("o", "r")
        assert result == {"count": 1, "has_more": False, "tags": [{"name": "v1.0.0", "sha": "abc123"}]}

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
    async def test_create_release_refuses_a_tag_that_already_has_one(self, gi: GitHubIntegration):
        """A retry after a timeout used to land here and replace the published
        notes without saying so. See #401."""
        gi._http.request = AsyncMock(
            return_value=_mock_response(status_code=422, json_data={"errors": [{"code": "already_exists"}]})
        )
        with pytest.raises(GitHubValidationError, match="update_release"):
            await gi.create_release("o", "r", "v1.0.0", "v1.0.0", "second attempt")
        # The POST went out and nothing followed it.
        assert gi._http.request.call_count == 1

    @pytest.mark.anyio
    async def test_create_release_updates_when_asked_to(self, gi: GitHubIntegration):
        responses = iter([
            _mock_response(status_code=422, json_data={"errors": [{"code": "already_exists"}]}),
            _mock_response(json_data=_release_payload()),
            _mock_response(json_data=_release_payload(body="second attempt")),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        result = await gi.create_release(
            "o", "r", "v1.0.0", "v1.0.0", "second attempt", if_exists="update"
        )
        calls = gi._http.request.call_args_list
        assert calls[0].args[0] == "POST"
        assert calls[2].args[0] == "PATCH"
        assert calls[2].kwargs["json"]["body"] == "second attempt"
        assert result["body"] == "second attempt"
        assert result["updated"] is True

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
            assert getattr(gi, name)._mcp_annotations.destructive_hint is True, name

    def test_read_tools_are_read_only(self, gi: GitHubIntegration):
        for name in ("list_releases", "get_release", "list_tags"):
            assert getattr(gi, name)._mcp_annotations.read_only_hint is True, name


# update_pr and set_pr_draft (#348)


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
            "head_sha": None,
            "head_ref": None,
            "base_ref": None,
            "requested_reviewers": [],
            "requested_teams": [],
        }

    @pytest.mark.anyio
    async def test_rejects_a_call_with_nothing_to_change(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock()
        with pytest.raises(GitHubValidationError):
            await gi.update_pr("o", "r", 5)
        gi._http.request.assert_not_called()

    def test_is_idempotent_not_destructive(self, gi: GitHubIntegration):
        ann = gi.update_pr._mcp_annotations
        assert ann.idempotent_hint is True
        assert ann.destructive_hint is False


_CREATED_PR = {"html_url": "https://github.com/o/r/pull/7", "number": 7, "state": "open", "title": "A change"}


def _label_payload(*names: str) -> dict:
    return _pr_payload(labels=[{"name": name} for name in names])


class TestPRLabels:
    @pytest.mark.anyio
    async def test_update_pr_sends_labels_to_the_issues_endpoint(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=_label_payload("bug")))
        await gi.update_pr("o", "r", 5, labels=["bug"])
        method, url = gi._http.request.call_args.args
        assert (method, url) == ("PATCH", "https://api.github.com/repos/o/r/issues/5")
        assert gi._http.request.call_args.kwargs["json"] == {"labels": ["bug"]}

    @pytest.mark.anyio
    async def test_update_pr_labels_alone_still_returns_pr_content(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=_label_payload("bug")))
        result = await gi.update_pr("o", "r", 5, labels=["bug"])
        assert gi._http.request.call_count == 1
        assert result["title"] == "A change"
        assert result["state"] == "open"

    @pytest.mark.anyio
    async def test_update_pr_strips_every_label_for_an_empty_list(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=_label_payload()))
        await gi.update_pr("o", "r", 5, labels=[])
        assert gi._http.request.call_args.kwargs["json"] == {"labels": []}

    @pytest.mark.anyio
    async def test_update_pr_sends_the_labels_after_the_other_fields(self, gi: GitHubIntegration):
        responses = iter([
            _mock_response(json_data=_pr_payload(title="A better title")),
            _mock_response(json_data=_label_payload("bug")),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.update_pr("o", "r", 5, title="A better title", labels=["bug"])
        calls = gi._http.request.call_args_list
        assert calls[0].args[1].endswith("/pulls/5")
        assert calls[0].kwargs["json"] == {"title": "A better title"}
        assert calls[1].args[1].endswith("/issues/5")

    @pytest.mark.anyio
    async def test_create_pr_applies_labels_and_appends_mcp(self, gi: GitHubIntegration):
        responses = iter([
            _mock_response(json_data=_CREATED_PR),
            _mock_response(json_data=_label_payload("bug", "mcp")),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        result = await gi.create_pr("o", "r", "A change", "Details", "feat", "main", labels=["bug"])
        calls = gi._http.request.call_args_list
        assert calls[1].args == ("PATCH", "https://api.github.com/repos/o/r/issues/7")
        assert calls[1].kwargs["json"] == {"labels": ["bug", "mcp"]}
        assert result["labels"] == ["bug", "mcp"]

    @pytest.mark.anyio
    async def test_create_pr_labels_an_empty_list_as_mcp_alone(self, gi: GitHubIntegration):
        responses = iter([
            _mock_response(json_data=_CREATED_PR),
            _mock_response(json_data=_label_payload("mcp")),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.create_pr("o", "r", "A change", "Details", "feat", "main", labels=[])
        assert gi._http.request.call_args.kwargs["json"] == {"labels": ["mcp"]}

    @pytest.mark.anyio
    async def test_create_pr_leaves_the_pr_unlabelled_when_labels_are_omitted(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=_CREATED_PR))
        result = await gi.create_pr("o", "r", "A change", "Details", "feat", "main")
        assert gi._http.request.call_count == 1
        assert result == {
            "pr_url": "https://github.com/o/r/pull/7",
            "pr_number": 7,
            "status": "open",
            "title": "A change",
        }


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


# PR comments — list, edit, reply (#349)


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
        assert gi.list_pr_comments._mcp_annotations.read_only_hint is True


# _request allow_status


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


# Project boards (#351)

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
            assert getattr(gi, name)._mcp_annotations.read_only_hint is True, name


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
        assert ann.idempotent_hint is True
        assert ann.destructive_hint is False


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
        assert gi.remove_from_project._mcp_annotations.destructive_hint is True


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


# Milestones (#350)


def _milestone_payload(**overrides) -> dict:
    payload = {
        "number": 3,
        "title": "v2.0",
        "description": "the next one",
        "state": "open",
        "due_on": "2026-12-31T23:59:59Z",
        "open_issues": 4,
        "closed_issues": 9,
        "html_url": "https://github.com/o/r/milestone/3",
        "node_id": "MI_abc",
        "creator": _NOISE_USER,
    }
    payload.update(overrides)
    return payload


class TestMilestones:
    @pytest.mark.anyio
    async def test_list_milestones_is_trimmed_and_carries_issue_counts(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[_milestone_payload()]))
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
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[]))
        await gi.list_milestones("o", "r", state="closed", per_page=10, page=2)
        params = gi._http.request.call_args.kwargs["params"]
        assert params == {"state": "closed", "per_page": 10, "page": 2}

    @pytest.mark.anyio
    async def test_create_milestone_sends_a_due_date_only_when_given(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=_milestone_payload()))
        await gi.create_milestone("o", "r", "v2.0", "the next one")
        assert "due_on" not in gi._http.request.call_args.kwargs["json"]
        await gi.create_milestone("o", "r", "v2.0", due_on="2026-12-31T23:59:59Z")
        assert gi._http.request.call_args.kwargs["json"]["due_on"] == "2026-12-31T23:59:59Z"

    @pytest.mark.anyio
    async def test_update_milestone_resolves_the_title_then_patches_by_number(self, gi: GitHubIntegration):
        responses = iter([
            _mock_response(json_data=[_milestone_payload()]),
            _mock_response(json_data=_milestone_payload(state="closed")),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        result = await gi.update_milestone("o", "r", "v2.0", state="closed")
        patch_call = gi._http.request.call_args_list[1]
        assert patch_call.args[0] == "PATCH"
        assert patch_call.args[1].endswith("/milestones/3")
        assert patch_call.kwargs["json"] == {"state": "closed"}
        assert result["state"] == "closed"

    @pytest.mark.anyio
    async def test_update_milestone_renames_without_touching_the_rest(self, gi: GitHubIntegration):
        responses = iter([
            _mock_response(json_data=[_milestone_payload()]),
            _mock_response(json_data=_milestone_payload(title="v2.1")),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.update_milestone("o", "r", "v2.0", new_title="v2.1")
        assert gi._http.request.call_args_list[1].kwargs["json"] == {"title": "v2.1"}

    @pytest.mark.anyio
    async def test_update_milestone_rejects_a_call_with_nothing_to_change(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock()
        with pytest.raises(GitHubValidationError):
            await gi.update_milestone("o", "r", "v2.0")
        gi._http.request.assert_not_called()

    @pytest.mark.anyio
    async def test_an_unknown_title_names_it_in_the_error(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[]))
        with pytest.raises(GitHubNotFoundError, match="No milestone titled 'v9.9'"):
            await gi.update_milestone("o", "r", "v9.9", state="closed")

    @pytest.mark.anyio
    async def test_the_lookup_reads_closed_milestones_too(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[]))
        with pytest.raises(GitHubNotFoundError):
            await gi.update_milestone("o", "r", "v9.9", state="open")
        assert gi._http.request.call_args.kwargs["params"]["state"] == "all"

    @pytest.mark.anyio
    async def test_the_lookup_pages_past_the_first_hundred(self, gi: GitHubIntegration):
        first = [_milestone_payload(number=n, title=f"m{n}") for n in range(100)]
        responses = iter([
            _mock_response(json_data=first),
            _mock_response(json_data=[_milestone_payload(number=101, title="v2.0")]),
            _mock_response(json_data=_milestone_payload(number=101, state="closed")),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.update_milestone("o", "r", "v2.0", state="closed")
        assert gi._http.request.call_args_list[1].kwargs["params"]["page"] == 2
        assert gi._http.request.call_args_list[2].args[1].endswith("/milestones/101")

    @pytest.mark.anyio
    async def test_create_issue_files_it_under_a_milestone(self, gi: GitHubIntegration):
        responses = iter([
            _mock_response(json_data=[_milestone_payload()]),
            _mock_response(json_data=_issue_payload(milestone={"title": "v2.0"})),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        result = await gi.create_issue("o", "r", "A bug", "Details", ["bug"], milestone="v2.0")
        assert gi._http.request.call_args.kwargs["json"]["milestone"] == 3
        assert result["milestone"] == "v2.0"

    @pytest.mark.anyio
    async def test_create_issue_without_a_milestone_sends_none_and_looks_nothing_up(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=_issue_payload()))
        result = await gi.create_issue("o", "r", "A bug", "Details", ["bug"])
        assert gi._http.request.call_count == 1
        assert "milestone" not in gi._http.request.call_args.kwargs["json"]
        assert result["milestone"] is None

    @pytest.mark.anyio
    async def test_set_issue_milestone_files_an_existing_issue(self, gi: GitHubIntegration):
        responses = iter([
            _mock_response(json_data=[_milestone_payload()]),
            _mock_response(json_data=_issue_payload(milestone={"title": "v2.0"})),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        result = await gi.set_issue_milestone("o", "r", 7, "v2.0")
        assert gi._http.request.call_args.kwargs["json"] == {"milestone": 3}
        assert result["milestone"] == "v2.0"

    @pytest.mark.anyio
    async def test_clearing_sends_an_explicit_null(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=_issue_payload()))
        result = await gi.set_issue_milestone("o", "r", 7)
        # An omitted key would leave the milestone in place, so null has to go up.
        assert gi._http.request.call_args.kwargs["json"] == {"milestone": None}
        assert gi._http.request.call_count == 1
        assert result["milestone"] is None

    def test_annotations(self, gi: GitHubIntegration):
        assert gi.list_milestones._mcp_annotations.read_only_hint is True
        assert gi.create_milestone._mcp_annotations.destructive_hint is False
        for name in ("update_milestone", "set_issue_milestone"):
            assert getattr(gi, name)._mcp_annotations.idempotent_hint is True, name


# list_repos (#354)


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
            "count": 1,
            "has_more": False,
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
        assert gi.list_repos._mcp_annotations.read_only_hint is True


# Error detail


_SAML_403 = {
    "message": "Resource protected by organization SAML enforcement. You must grant your token access.",
    "documentation_url": "https://docs.github.com/rest",
}


class TestErrorDetail:
    """GitHub explains a refusal in the response body, and the exception text is all
    the client sees, so the body has to survive into the message."""

    @pytest.mark.anyio
    async def test_a_permission_403_carries_githubs_own_message(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(
            return_value=_mock_response(status_code=403, json_data=_SAML_403, text=json.dumps(_SAML_403))
        )
        with pytest.raises(ToolError, match="SAML enforcement"):
            await gi.merge_pr("owner", "repo", 42)

    @pytest.mark.anyio
    async def test_a_403_names_the_call_that_failed(self, gi: GitHubIntegration):
        body = {"message": "At least 1 approving review is required by reviewers with write access."}
        gi._http.request = AsyncMock(
            return_value=_mock_response(status_code=403, json_data=body, text=json.dumps(body))
        )
        with pytest.raises(ToolError, match=r"PR #42 merge: Refused.*approving review"):
            await gi.merge_pr("owner", "repo", 42)

    @pytest.mark.anyio
    async def test_a_403_with_no_body_still_points_at_the_token(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(status_code=403, text=""))
        gi._http.request.return_value.json.side_effect = ValueError("not json")
        with pytest.raises(ToolError, match="Permission denied. Check your token permissions"):
            await gi.merge_pr("owner", "repo", 42)

    def test_a_permission_403_is_not_read_as_a_rate_limit(self, gi: GitHubIntegration):
        response = _mock_response(status_code=403, json_data=_SAML_403, text=json.dumps(_SAML_403))
        with pytest.raises(GitHubAPIError) as caught:
            gi._handle_response_error(response, "PR #42 merge")
        assert not isinstance(caught.value, GitHubRateLimitError)
        assert caught.value.status_code == 403
        assert caught.value.response_body == _SAML_403

    def test_a_secondary_rate_limit_says_so_and_waits_the_retry_after(self, gi: GitHubIntegration):
        body = {"message": "You have exceeded a secondary rate limit. Please wait a few minutes."}
        response = _mock_response(
            status_code=403,
            json_data=body,
            text=json.dumps(body),
            # The primary window is unrelated and long past, so Retry-After is the only usable wait.
            headers={"Retry-After": "60", "X-RateLimit-Reset": "1"},
        )
        with pytest.raises(GitHubRateLimitError) as caught:
            gi._handle_response_error(response, "issue #7")
        assert "secondary rate limit hit" in str(caught.value)
        assert caught.value.reset_timestamp == pytest.approx(int(time.time()) + 60, abs=5)

    def test_a_primary_rate_limit_uses_the_reset_header(self, gi: GitHubIntegration):
        body = {"message": "API rate limit exceeded for user ID 1."}
        response = _mock_response(
            status_code=403, json_data=body, text=json.dumps(body), headers={"X-RateLimit-Reset": "1893456000"}
        )
        with pytest.raises(GitHubRateLimitError) as caught:
            gi._handle_response_error(response, "")
        assert caught.value.reset_timestamp == 1893456000

    def test_an_unusable_reset_header_is_dropped_rather_than_raising(self, gi: GitHubIntegration):
        body = {"message": "API rate limit exceeded."}
        response = _mock_response(
            status_code=403, json_data=body, text=json.dumps(body), headers={"X-RateLimit-Reset": "Wed, 21 Oct 2026"}
        )
        with pytest.raises(GitHubRateLimitError) as caught:
            gi._handle_response_error(response, "")
        assert caught.value.reset_timestamp is None

    def test_a_422_carries_the_field_level_errors(self, gi: GitHubIntegration):
        body = {
            "message": "Validation Failed",
            "errors": [{"resource": "PullRequest", "field": "base", "message": "No commits between main and topic"}],
        }
        response = _mock_response(status_code=422, json_data=body)
        with pytest.raises(GitHubValidationError, match="base: No commits between main and topic"):
            gi._handle_response_error(response, "create PR")

    def test_a_404_keeps_both_the_context_and_the_message(self, gi: GitHubIntegration):
        body = {"message": "Not Found"}
        response = _mock_response(status_code=404, json_data=body)
        with pytest.raises(GitHubNotFoundError, match=r"PR #4321: Resource not found GitHub said: Not Found"):
            gi._handle_response_error(response, "PR #4321")

    def test_a_401_carries_githubs_own_message(self, gi: GitHubIntegration):
        body = {"message": "Bad credentials"}
        response = _mock_response(status_code=401, json_data=body)
        with pytest.raises(GitHubAuthError, match="Bad credentials"):
            gi._handle_response_error(response, "issue #7")

    def test_an_unlisted_status_keeps_the_message_it_always_had(self, gi: GitHubIntegration):
        body = {"message": "Pull Request is not mergeable"}
        response = _mock_response(status_code=405, json_data=body, reason_phrase="Method Not Allowed")
        with pytest.raises(
            GitHubAPIError, match=r"405 - Method Not Allowed GitHub said: Pull Request is not mergeable"
        ):
            gi._handle_response_error(response, "PR #42 merge")

    def test_a_body_that_is_not_json_leaves_the_message_alone(self, gi: GitHubIntegration):
        response = _mock_response(status_code=403, text="<html>no</html>")
        response.json.side_effect = ValueError("not json")
        with pytest.raises(GitHubAPIError) as caught:
            gi._handle_response_error(response, "PR #42 merge")
        assert "GitHub said" not in str(caught.value)

    def test_an_error_repeating_the_top_level_message_is_not_said_twice(self, gi: GitHubIntegration):
        body = {"message": "Validation Failed", "errors": [{"message": "Validation Failed"}]}
        response = _mock_response(status_code=422, json_data=body)
        with pytest.raises(GitHubValidationError) as caught:
            gi._handle_response_error(response, "")
        assert str(caught.value).count("Validation Failed") == 1

    def test_an_error_string_rather_than_an_object_still_reads(self, gi: GitHubIntegration):
        body = {"message": "Validation Failed", "errors": ["milestone does not exist"]}
        response = _mock_response(status_code=422, json_data=body)
        with pytest.raises(GitHubValidationError, match="milestone does not exist"):
            gi._handle_response_error(response, "")


class TestMissingCredentials:
    """What a tool does when the server holds no GitHub credentials. See #386."""

    def _unconfigured(self) -> GitHubIntegration:
        with (
            patch("mcp_github.github_integration.GITHUB_TOKEN", None),
            patch("mcp_github.auth.GITHUB_OAUTH_CLIENT_ID", None),
            patch("mcp_github.auth.GITHUB_OAUTH_CLIENT_SECRET", None),
            patch("mcp_github.auth.GITHUB_OAUTH_BASE_URL", None),
        ):
            return GitHubIntegration()

    def test_building_the_integration_does_not_raise(self):
        assert self._unconfigured().credentials_configured is False

    def test_a_request_refuses_with_the_documented_code(self):
        with pytest.raises(GitHubAuthError) as raised:
            self._unconfigured()._get_headers()

        assert raised.value.status_code == 401
        assert "[AUTH_FAILED] HTTP 401" in str(raised.value)
        assert MISSING_CREDENTIALS in str(raised.value)


# list_pr_reviews — the read counterpart to update_reviews. See #408.


def _review_payload(**overrides) -> dict:
    return {
        "id": 80,
        "node_id": "PRR_abc",
        "user": _NOISE_USER,
        "body": "LGTM",
        "state": "APPROVED",
        "html_url": "https://github.com/o/r/pull/5#pullrequestreview-80",
        "pull_request_url": "https://api.github.com/repos/o/r/pulls/5",
        "author_association": "OWNER",
        "_links": {"html": {"href": "https://github.com/x"}},
        "submitted_at": "2026-07-01T00:00:00Z",
        "commit_id": "abc123",
        **overrides,
    }


class TestListPRReviews:
    @pytest.mark.anyio
    async def test_an_approval_carries_its_author_and_verdict(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[_review_payload()]))
        result = await gi.list_pr_reviews("o", "r", 5)
        assert result["reviews"] == [{
            "id": 80,
            "author": "octocat",
            "state": "APPROVED",
            "body": "LGTM",
            "html_url": "https://github.com/o/r/pull/5#pullrequestreview-80",
            "submitted_at": "2026-07-01T00:00:00Z",
            "commit_id": "abc123",
        }]

    @pytest.mark.anyio
    @pytest.mark.parametrize("state", ["APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED"])
    async def test_every_verdict_comes_back_as_given(self, gi: GitHubIntegration, state: str):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[_review_payload(state=state)]))
        assert (await gi.list_pr_reviews("o", "r", 5))["reviews"][0]["state"] == state

    @pytest.mark.anyio
    async def test_an_unreviewed_pr_returns_nothing_rather_than_failing(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[]))
        result = await gi.list_pr_reviews("o", "r", 5)
        assert result["reviews"] == []
        assert result["count"] == 0

    @pytest.mark.anyio
    async def test_a_pending_review_is_told_apart_by_a_missing_timestamp(self, gi: GitHubIntegration):
        """GitHub omits submitted_at on a review that was written but not sent."""
        payload = _review_payload(state="PENDING")
        del payload["submitted_at"]
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[payload]))
        assert (await gi.list_pr_reviews("o", "r", 5))["reviews"][0]["submitted_at"] is None

    @pytest.mark.anyio
    async def test_the_noise_is_trimmed_away(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[_review_payload()]))
        review = (await gi.list_pr_reviews("o", "r", 5))["reviews"][0]
        for noise in ("node_id", "_links", "pull_request_url", "author_association", "user"):
            assert noise not in review

    @pytest.mark.anyio
    async def test_paging_goes_out_in_the_url(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[]))
        await gi.list_pr_reviews("o", "r", 5, per_page=100, page=2)
        url = gi._http.request.call_args.args[1]
        assert url.endswith("/pulls/5/reviews?per_page=100&page=2")

    def test_is_read_only(self, gi: GitHubIntegration):
        assert gi.list_pr_reviews._mcp_annotations.read_only_hint is True


class TestRequestedReviewers:
    @pytest.mark.anyio
    async def test_get_pr_content_reports_who_was_asked(self, gi: GitHubIntegration):
        """Distinguishes nobody having reviewed from nobody having been asked."""
        payload = {
            "title": "A change", "body": "Details", "user": _NOISE_USER,
            "created_at": "2026-07-01T00:00:00Z", "updated_at": "2026-07-02T00:00:00Z",
            "state": "open", "head": {"sha": "s", "ref": "f"}, "base": {"ref": "main"},
            "requested_reviewers": [{"login": "octocat"}, {"login": "hubot"}],
            "requested_teams": [{"slug": "platform"}],
        }
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=payload))
        result = await gi.get_pr_content("o", "r", 5)
        assert result["requested_reviewers"] == ["octocat", "hubot"]
        assert result["requested_teams"] == ["platform"]

    @pytest.mark.anyio
    async def test_a_pr_nobody_was_asked_to_review_reports_empty(self, gi: GitHubIntegration):
        payload = {
            "title": "A change", "body": "Details", "user": _NOISE_USER,
            "created_at": "2026-07-01T00:00:00Z", "updated_at": "2026-07-02T00:00:00Z",
            "state": "open", "head": {}, "base": {},
        }
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=payload))
        result = await gi.get_pr_content("o", "r", 5)
        assert result["requested_reviewers"] == []
        assert result["requested_teams"] == []


# delete_release(delete_tag=True) against a released tag. See #404.


class TestDeleteReleaseWithTag:
    @pytest.mark.anyio
    async def test_the_release_goes_before_the_tag(self, gi: GitHubIntegration):
        """The whole reason this path needs no force flag. Reverse the order and
        a failed tag delete would strand a release naming a ref nobody can fetch."""
        responses = iter([
            _mock_response(json_data=_release_payload()),
            _mock_response(status_code=204),
            _mock_response(status_code=204),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.delete_release("o", "r", "v1.0.0", delete_tag=True)

        methods_and_urls = [(c.args[0], c.args[1]) for c in gi._http.request.call_args_list]
        assert len(methods_and_urls) == 3
        assert methods_and_urls[0][0] == "GET"
        assert methods_and_urls[1] == ("DELETE", "https://api.github.com/repos/o/r/releases/55")
        assert methods_and_urls[2][0] == "DELETE"
        assert methods_and_urls[2][1].endswith("/git/refs/tags/v1.0.0")

    @pytest.mark.anyio
    async def test_a_failed_release_delete_leaves_the_tag_alone(self, gi: GitHubIntegration):
        responses = iter([
            _mock_response(json_data=_release_payload()),
            _mock_response(status_code=500, reason_phrase="Server Error"),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        with pytest.raises(ToolError):
            await gi.delete_release("o", "r", "v1.0.0", delete_tag=True)
        assert gi._http.request.call_count == 2

    @pytest.mark.anyio
    async def test_the_same_ref_is_refused_through_delete_tag(self, gi: GitHubIntegration):
        """The asymmetry the description now explains: one path deletes this ref
        freely, the other refuses it."""
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=_release_payload()))
        with pytest.raises(GitHubValidationError, match="force=True"):
            await gi.delete_tag("o", "r", "v1.0.0")
        assert gi._http.request.call_count == 1


# milestone sentinel — one encoding across both tools. See #403.


class TestMilestoneSentinel:
    @pytest.mark.anyio
    async def test_create_issue_accepts_null_for_no_milestone(self, gi: GitHubIntegration):
        """Passing null used to be a hard validation error here, while it was the
        documented way to say the same thing on set_issue_milestone."""
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=_issue_payload()))
        await gi.create_issue("o", "r", "A bug", "Details", ["bug"], milestone=None)
        assert gi._http.request.call_count == 1
        assert "milestone" not in gi._http.request.call_args.kwargs["json"]

    @pytest.mark.anyio
    async def test_create_issue_reads_an_empty_string_the_same_way(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=_issue_payload()))
        await gi.create_issue("o", "r", "A bug", "Details", ["bug"], milestone="")
        assert "milestone" not in gi._http.request.call_args.kwargs["json"]

    @pytest.mark.anyio
    async def test_set_issue_milestone_reads_an_empty_string_as_clear(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=_issue_payload()))
        await gi.set_issue_milestone("o", "r", 7, "")
        assert gi._http.request.call_args.kwargs["json"] == {"milestone": None}

    @staticmethod
    def _lookup_then_write(gi: GitHubIntegration) -> None:
        """A milestone lookup followed by the issue write it feeds."""
        responses = iter([
            _mock_response(json_data=[{"title": "v2.0", "number": 3}]),
            _mock_response(json_data=_issue_payload()),
        ])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))

    @pytest.mark.anyio
    async def test_create_issue_resolves_a_title_to_a_number(self, gi: GitHubIntegration):
        self._lookup_then_write(gi)
        await gi.create_issue("o", "r", "A bug", "Details", ["bug"], milestone="v2.0")
        assert gi._http.request.call_args.kwargs["json"]["milestone"] == 3

    @pytest.mark.anyio
    async def test_set_issue_milestone_resolves_a_title_to_a_number(self, gi: GitHubIntegration):
        self._lookup_then_write(gi)
        await gi.set_issue_milestone("o", "r", 7, "v2.0")
        assert gi._http.request.call_args.kwargs["json"]["milestone"] == 3

    @pytest.mark.anyio
    async def test_both_tools_declare_the_same_milestone_schema(self):
        import inspect as _inspect

        shapes = {
            name: _inspect.signature(getattr(GitHubIntegration, name)).parameters["milestone"]
            for name in ("create_issue", "set_issue_milestone")
        }
        assert len({str(p.annotation) for p in shapes.values()}) == 1, shapes
        assert {p.default for p in shapes.values()} == {None}


# Pagination metadata across the list tools. See #405.

_NEXT_LINK = '<https://api.github.com/repositories/1/tags?page=2>; rel="next", ' '<...?page=83>; rel="last"'


class TestPaginationMetadata:
    @pytest.mark.anyio
    async def test_has_more_reads_the_link_header(self, gi: GitHubIntegration):
        """A full page is not the signal. A set dividing exactly by per_page would
        loop forever on that guess, so the header decides."""
        gi._http.request = AsyncMock(
            return_value=_mock_response(
                json_data=[{"name": "v1", "commit": {"sha": "a"}}], headers={"Link": _NEXT_LINK}
            )
        )
        result = await gi.list_tags("o", "r")
        assert result["has_more"] is True
        assert result["count"] == 1

    @pytest.mark.anyio
    async def test_the_last_page_carries_no_next_link(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[{"name": "v1", "commit": {"sha": "a"}}]))
        assert (await gi.list_tags("o", "r"))["has_more"] is False

    @pytest.mark.anyio
    async def test_a_rel_last_without_a_rel_next_is_not_more(self, gi: GitHubIntegration):
        """The final page still names first and last, so matching the header alone
        would report another page that is not there."""
        link = '<https://api.github.com/x?page=1>; rel="first", <https://api.github.com/x?page=3>; rel="last"'
        gi._http.request = AsyncMock(return_value=_mock_response(json_data=[], headers={"Link": link}))
        assert (await gi.list_tags("o", "r"))["has_more"] is False

    @pytest.mark.anyio
    async def test_count_is_the_page_not_the_result_set(self, gi: GitHubIntegration):
        """list_tags used to answer total with the page length, so a repository
        with 83 pages of tags reported 30."""
        gi._http.request = AsyncMock(
            return_value=_mock_response(
                json_data=[{"name": f"v{n}", "commit": {"sha": "a"}} for n in range(50)],
                headers={"Link": _NEXT_LINK},
            )
        )
        result = await gi.list_tags("o", "r")
        assert result["count"] == 50
        assert "total" not in result
        assert result["has_more"] is True

    @pytest.mark.anyio
    async def test_a_search_keeps_the_real_total_beside_the_page(self, gi: GitHubIntegration):
        """GitHub gives a genuine match count here, so total stays and means it."""
        gi._http.request = AsyncMock(
            return_value=_mock_response(
                json_data={
                    "total_count": 900,
                    "incomplete_results": False,
                    "items": [{
                        "html_url": "https://github.com/o/r/issues/7",
                        "title": "Rate limits",
                        "number": 7,
                        "state": "open",
                        "created_at": "2026-07-01T00:00:00Z",
                        "updated_at": "2026-07-02T00:00:00Z",
                        "user": _NOISE_USER,
                        "labels": [{"name": "bug"}],
                    }],
                },
                headers={"Link": _NEXT_LINK},
            )
        )
        result = await gi.search_issues_prs("rate limit")
        assert result["total"] == 900
        assert result["count"] == 1
        assert result["has_more"] is True

    @pytest.mark.anyio
    async def test_a_replayed_page_still_knows_it_has_a_successor(self, gi: GitHubIntegration):
        """A 304 rebuilds the response from cache. Dropping Link there would tell
        the caller a cached first page was the last one."""
        first = _mock_response(
            json_data=[{"name": "v1", "commit": {"sha": "a"}}], etag="W/abc", headers={"Link": _NEXT_LINK}
        )
        responses = iter([first, _mock_response(status_code=304)])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        assert (await gi.list_tags("o", "r"))["has_more"] is True
        assert (await gi.list_tags("o", "r"))["has_more"] is True

    @pytest.mark.anyio
    async def test_every_rest_list_tool_defaults_to_the_same_page_size(self):
        import inspect as _inspect

        names = [
            "list_pr_comments", "list_open_issues_prs", "search_issues_prs", "list_repo_labels",
            "list_repos", "list_milestones", "list_releases", "list_tags", "list_project_items",
        ]
        defaults = {
            name: _inspect.signature(getattr(GitHubIntegration, name)).parameters["per_page"].default
            for name in names
        }
        assert set(defaults.values()) == {50}, defaults
