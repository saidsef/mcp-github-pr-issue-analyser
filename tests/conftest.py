"""Fixtures every test module gets. The token store globals outlive a test, so one
that builds a store would otherwise hand it to whatever runs next."""

from unittest.mock import AsyncMock, patch

import pytest

from mcp_github import auth
from mcp_github.github_integration import GitHubIntegration


@pytest.fixture(autouse=True)
def _reset_token_store():
    """build_token_store records what it built and get_token_store caches it, so drop
    both between tests."""
    yield
    auth._token_store = None
    auth._shared_store = None


@pytest.fixture
def gi() -> GitHubIntegration:
    """GitHubIntegration instance with a mocked HTTP client and test token."""
    with patch("mcp_github.github_integration.GITHUB_TOKEN", "test-token"):
        instance = GitHubIntegration()
    instance._http = AsyncMock()
    return instance
