"""Helpers the server-level tests share: an analyser built under a chosen set of
credentials, and a request carrying an OAuth grant."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

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
