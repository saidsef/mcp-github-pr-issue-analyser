"""Fixtures every test module gets. The token store globals outlive a test, so one
that builds a store would otherwise hand it to whatever runs next."""

import pytest

from mcp_github import auth


@pytest.fixture(autouse=True)
def _reset_token_store():
    """build_token_store records what it built and get_token_store caches it, so drop
    both between tests."""
    yield
    auth._token_store = None
    auth._shared_store = None
