"""These tests call the GitHub API through the tools, make no writes, and assert on
the shape of what comes back rather than its content. Each case guards a fault the
mocked suite passed, and each skips without the token it needs. See #520.

GITHUB_TOKEN=$(gh auth token) uv run pytest tests/test_contract.py
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest

from mcp_github.exceptions import GitHubValidationError
from mcp_github.github_integration import GitHubIntegration

OWNER = "saidsef"
REPO = "mcp-github-pr-issue-analyser"

# Merged, with its head branch deleted. See #515.
DELETED_BRANCH_PR = 513
DELETED_BRANCH_HEAD = "d3b63336fba23d59600b726aaf937621ef87d5a7"

# Merged after three commits. See #514.
MULTI_COMMIT_PR = 505


def _client(token: str) -> GitHubIntegration:
    with patch("mcp_github.github_integration.GITHUB_TOKEN", token):
        return GitHubIntegration()


@pytest.fixture
async def github() -> AsyncIterator[GitHubIntegration]:
    """A client on its own HTTP connection, reading this public repository."""
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        pytest.skip("set GITHUB_TOKEN to run the contract tests against GitHub")
    async with _client(token) as client:
        yield client


@pytest.fixture
async def private_github() -> AsyncIterator[GitHubIntegration]:
    """A client holding the stored token for a private repository."""
    token = os.getenv("CONTRACT_PRIVATE_REPO_TOKEN")
    if not (token and os.getenv("CONTRACT_PRIVATE_PR")):
        pytest.skip("set CONTRACT_PRIVATE_REPO_TOKEN and CONTRACT_PRIVATE_PR to read a private pull request")
    async with _client(token) as client:
        yield client


class TestPublicRepository:
    @pytest.mark.anyio
    async def test_a_merged_pr_with_a_deleted_branch_reports_its_head_checks(self, github: GitHubIntegration):
        result = await github.get_pr_status_checks(OWNER, REPO, DELETED_BRANCH_PR)
        assert result.get("head_sha") == DELETED_BRANCH_HEAD
        assert result["check_runs"]
        assert result["overall"] != "unknown"

    @pytest.mark.anyio
    async def test_a_multi_commit_pr_diff_is_one_net_diff(self, github: GitHubIntegration):
        """A format-patch series opens on a From line and repeats a file for each
        commit that touched it. The net diff has one header per changed file."""
        diff = await github.get_pr_diff(OWNER, REPO, MULTI_COMMIT_PR)
        assert diff["patch"].startswith("diff --git ")
        content = await github.get_pr_content(OWNER, REPO, MULTI_COMMIT_PR)
        assert content["commits"] > 1
        assert not diff["truncated"]
        assert diff["patch"].count("\ndiff --git ") + 1 == content["changed_files"]

    @pytest.mark.anyio
    async def test_a_directory_read_twice_is_refused_both_times(self, github: GitHubIntegration):
        """GitHub answers the second read with a 304, so the ETag cache serves it. See #516."""
        for _ in range(2):
            with pytest.raises(GitHubValidationError):
                await github.get_repository_file(OWNER, REPO, "docs")


class TestPrivateRepository:
    @pytest.mark.anyio
    async def test_a_private_pr_diff_reads_with_a_stored_token(self, private_github: GitHubIntegration):
        """CI supplies the pull request as owner/repo#number from a secret, so no
        private repository is named here. See #514."""
        owner, _, rest = os.environ["CONTRACT_PRIVATE_PR"].partition("/")
        repo, _, number = rest.partition("#")
        diff = await private_github.get_pr_diff(owner, repo, int(number))
        assert diff["patch"].startswith("diff --git ")
