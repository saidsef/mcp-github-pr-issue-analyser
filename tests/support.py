"""Helpers the tests share: an analyser built under a chosen set of credentials, a
request carrying an OAuth grant, and the GitHub payloads the tools are fed."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastmcp.server.auth import AccessToken
from fastmcp.server.context import reset_transport, set_transport

from mcp_github.issues_pr_analyser import PRIssueAnalyser

STATIC_TOKEN = "test-token"

OAUTH_SETTINGS = {
    "GITHUB_OAUTH_CLIENT_ID": "Ov23liExample",
    "GITHUB_OAUTH_CLIENT_SECRET": "oauth-client-secret",
    "GITHUB_OAUTH_BASE_URL": "https://mcp.example.com",
}

PROVIDED_TOOLS = {"choose", "github_pr_issue_analyser_ui", "github_search_prefab_components"}


@contextmanager
def deployment(*, token: str | None = STATIC_TOKEN, oauth: bool = False, remote: bool = False) -> Iterator[None]:
    """The settings an analyser reads while it is being built. The OAuth trio is read
    from auth, the static token from github_integration, and the transport flag from
    the server module. The token store stays in memory whatever the environment holds."""
    with ExitStack() as stack:
        stack.enter_context(patch("mcp_github.github_integration.GITHUB_TOKEN", token))
        for name, value in OAUTH_SETTINGS.items():
            stack.enter_context(patch(f"mcp_github.auth.{name}", value if oauth else None))
        stack.enter_context(patch("mcp_github.issues_pr_analyser.MCP_ENABLE_REMOTE", remote))
        stack.enter_context(patch("mcp_github.auth.REDIS_HOST_PORT", None))
        stack.enter_context(patch("mcp_github.auth.DYNAMODB_TABLE_ARN", None))
        yield


def analyser(*, token: str | None = STATIC_TOKEN, oauth: bool = False, remote: bool = False) -> PRIssueAnalyser:
    """An analyser built under those settings, holding a static token over stdio by default."""
    with deployment(token=token, oauth=oauth, remote=remote):
        return PRIssueAnalyser()


@contextmanager
def grant(scopes: list[str]) -> Iterator[None]:
    """A request over an HTTP transport carrying an OAuth grant with these scopes."""
    token = AccessToken(token="t", client_id="c", expires_at=None, scopes=scopes, claims={})
    transport = set_transport("streamable-http")
    try:
        with patch("fastmcp.server.middleware.authorization.get_access_token", return_value=token):
            yield
    finally:
        reset_transport(transport)


def mock_response(
    status_code: int = 200,
    json_data: dict | list | None = None,
    text: str = "",
    etag: str | None = None,
    headers: dict[str, str] | None = None,
    reason_phrase: str = "OK",
) -> MagicMock:
    """An httpx response as the mocked client returns it."""
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


def week(sunday: str, days: list[int]) -> dict:
    """One entry of a stargazers/history page. `sunday` is the UTC Sunday the week
    opens on, and `days` holds that week's star counts from the Sunday onwards."""
    return {
        "week": int(datetime.fromisoformat(sunday + "T00:00:00+00:00").timestamp()),
        "total": sum(days),
        "days": days,
    }


def weeks_back(newest_sunday: str, count: int, per_day: int) -> list[dict]:
    """A run of consecutive history weeks, newest first, as GitHub returns them."""
    newest = datetime.fromisoformat(newest_sunday + "T00:00:00+00:00")
    return [week((newest - timedelta(weeks=i)).strftime("%Y-%m-%d"), [per_day] * 7) for i in range(count)]


OLD_WEEK = week("2000-01-02", [0] * 7)


def mock_ctx() -> AsyncMock:
    """A FastMCP context that records progress and info calls."""
    ctx = AsyncMock()
    ctx.info = AsyncMock()
    ctx.report_progress = AsyncMock()
    ctx.elicit = AsyncMock()
    return ctx


EMPTY_CONTRIBUTIONS = {
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


EMPTY_STATUS_CHECKS = {
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


NOISE_USER = {
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


NOISE_REACTIONS = {
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


def issue_payload(**overrides) -> dict:
    """An issue as GitHub serves it, with the noise a real payload carries."""
    payload = {
        "id": 999,
        "node_id": "I_abc",
        "url": "https://api.github.com/repos/o/r/issues/7",
        "repository_url": "https://api.github.com/repos/o/r",
        "number": 7,
        "title": "A bug",
        "body": "Details",
        "state": "open",
        "user": NOISE_USER,
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
        "reactions": NOISE_REACTIONS,
        "timeline_url": "https://api.github.com/repos/o/r/issues/7/timeline",
    }
    payload.update(overrides)
    return payload


def pr_payload(**overrides) -> dict:
    """A pull request as GitHub serves it."""
    payload = {
        "title": "A change",
        "body": "Details",
        "user": NOISE_USER,
        "created_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-07-02T00:00:00Z",
        "state": "open",
        "node_id": "PR_abc",
    }
    payload.update(overrides)
    return payload


CREATED_PR = {"html_url": "https://github.com/o/r/pull/7", "number": 7, "state": "open", "title": "A change"}


def label_payload(*names: str) -> dict:
    """The issue payload GitHub answers a label write with."""
    return pr_payload(labels=[{"name": name} for name in names])


def release_payload(**overrides) -> dict:
    """A release as GitHub serves it."""
    payload = {
        "id": 55,
        "node_id": "RE_abc",
        "tag_name": "v1.0.0",
        "name": "v1.0.0",
        "html_url": "https://github.com/o/r/releases/tag/v1.0.0",
        "draft": False,
        "prerelease": False,
        "body": "notes",
        "author": NOISE_USER,
        "assets": [],
    }
    payload.update(overrides)
    return payload


def milestone_payload(**overrides) -> dict:
    """A milestone as GitHub serves it."""
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
        "creator": NOISE_USER,
    }
    payload.update(overrides)
    return payload


def repo_payload(**overrides) -> dict:
    """A repository as GitHub serves it."""
    payload = {
        "name": "toolbox",
        "full_name": "acme/toolbox",
        "owner": NOISE_USER,
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


def review_payload(**overrides) -> dict:
    """A review as GitHub serves it."""
    return {
        "id": 80,
        "node_id": "PRR_abc",
        "user": NOISE_USER,
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
