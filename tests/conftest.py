"""Fixtures every test module gets. The token store globals are process-wide, so
a test that builds one would otherwise hand it to whatever runs next."""

import pytest

from mcp_github import auth


@pytest.fixture(autouse=True)
def _reset_token_store():
    """build_token_store records what it built and get_token_store caches it, so drop
    both between tests along with the encrypted view over them."""
    yield
    auth._token_store = None
    auth._shared_store = None
    auth._admin_store = None
