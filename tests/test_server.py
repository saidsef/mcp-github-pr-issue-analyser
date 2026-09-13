"""Tests for the MCP server lifespan and its custom routes."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from importlib.metadata import PackageNotFoundError
from unittest.mock import AsyncMock, patch

import pytest
from fastmcp.server.auth import MultiAuth
from fastmcp.server.auth.providers.github import GitHubProvider
from starlette.middleware import Middleware as ASGIMiddleware
from starlette.testclient import TestClient

from mcp_github.auth import GITHUB_SCOPES, MISSING_CREDENTIALS, APIKeyVerifier, UnconfiguredCredentials
from mcp_github.issues_pr_analyser import VERSION, PRIssueAnalyser, _package_version

_TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
_MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
_STATIC_TOKEN = "test-token"
_OAUTH_SETTINGS = {
    "GITHUB_OAUTH_CLIENT_ID": "Ov23liExample",
    "GITHUB_OAUTH_CLIENT_SECRET": "oauth-client-secret",
    "GITHUB_OAUTH_BASE_URL": "https://mcp.example.com",
}


@contextmanager
def _deployment(*, token: str | None, oauth: bool, remote: bool = False):
    """The settings an analyser reads while it is being built. The OAuth trio is read
    from auth, the static token from github_integration, and the transport flag from
    the server module."""
    with ExitStack() as stack:
        stack.enter_context(patch("mcp_github.github_integration.GITHUB_TOKEN", token))
        for name, value in _OAUTH_SETTINGS.items():
            stack.enter_context(patch(f"mcp_github.auth.{name}", value if oauth else None))
        stack.enter_context(patch("mcp_github.issues_pr_analyser.MCP_ENABLE_REMOTE", remote))
        yield


def _analyser() -> PRIssueAnalyser:
    with _deployment(token=_STATIC_TOKEN, oauth=False):
        return PRIssueAnalyser()


def _unconfigured() -> PRIssueAnalyser:
    """An analyser holding neither the OAuth trio nor a static token."""
    with _deployment(token=None, oauth=False):
        return PRIssueAnalyser()


def _app(analyser: PRIssueAnalyser):
    """The HTTP app as run() builds it, middleware included."""
    return analyser.mcp.http_app(
        transport="http",
        stateless_http=True,
        middleware=[
            ASGIMiddleware(UnconfiguredCredentials, configured=lambda: analyser.gi.credentials_configured)
        ],
    )


class TestLifespan:
    """Shutdown releases the GitHub HTTP clients."""

    def test_http_shutdown_closes_the_integration(self):
        analyser = _analyser()
        analyser.gi.aclose = AsyncMock()
        app = analyser.mcp.http_app(transport="http", stateless_http=True)
        with TestClient(app) as client:
            client.get("/metrics")
        analyser.gi.aclose.assert_awaited_once()

    @pytest.mark.anyio
    async def test_integration_closes_when_the_server_raises(self):
        analyser = _analyser()
        analyser.gi.aclose = AsyncMock()
        with pytest.raises(RuntimeError):
            async with analyser._lifespan(analyser.mcp):
                raise RuntimeError("boom")
        analyser.gi.aclose.assert_awaited_once()


class TestLandingRoute:
    """The landing endpoint served at the root path in HTTP mode."""

    def test_reports_the_service_is_up(self):
        app = _analyser().mcp.http_app(transport="http", stateless_http=True)
        with TestClient(app) as client:
            response = client.get("/")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"
        body = response.json()
        assert body["status"] == "ok"
        assert body["service"] == "GitHub PR and Issue Analyser"
        assert body["version"] == VERSION

    @pytest.mark.anyio
    async def test_reports_the_registered_tool_count(self):
        analyser = _analyser()
        app = analyser.mcp.http_app(transport="http", stateless_http=True)
        with TestClient(app) as client:
            body = client.get("/").json()

        assert body["tools"] == len(await analyser.mcp.list_tools())
        assert body["tools"] > 0

    def test_head_request_succeeds(self):
        app = _analyser().mcp.http_app(transport="http", stateless_http=True)
        with TestClient(app) as client:
            assert client.head("/").status_code == 200


class TestPackageVersion:
    """Where the reported version comes from."""

    def test_reads_the_installed_distribution(self):
        with patch("mcp_github.issues_pr_analyser.version", return_value="1.2.3"):
            assert _package_version() == "1.2.3"

    def test_falls_back_when_the_package_is_absent(self):
        with patch("mcp_github.issues_pr_analyser.version", side_effect=PackageNotFoundError):
            assert _package_version() == "unknown"


class TestStartsWithoutCredentials:
    """A server holding no GitHub credentials starts and refuses calls. See #386."""

    def test_the_server_starts(self):
        assert _unconfigured().gi.credentials_configured is False

    def test_a_configured_server_says_so(self):
        assert _analyser().gi.credentials_configured is True

    def test_the_landing_route_still_answers(self):
        with TestClient(_app(_unconfigured())) as client:
            response = client.get("/")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_the_metrics_route_still_answers(self):
        with TestClient(_app(_unconfigured())) as client:
            assert client.get("/metrics").status_code == 200

    def test_the_mcp_route_refuses_with_the_reason(self):
        with TestClient(_app(_unconfigured())) as client:
            response = client.post("/mcp/", json=_TOOLS_LIST, headers=_MCP_HEADERS)

        assert response.status_code == 401
        assert response.json()["error"] == {"code": "AUTH_FAILED", "message": MISSING_CREDENTIALS}
        assert response.headers["www-authenticate"].startswith("Bearer")

    def test_a_configured_server_is_not_refused(self):
        with TestClient(_app(_analyser())) as client:
            response = client.post("/mcp/", json=_TOOLS_LIST, headers=_MCP_HEADERS)

        assert response.status_code != 401
        assert MISSING_CREDENTIALS not in response.text


class TestAuthSelection:
    """Which transport authentication a deployment's credentials select. See #389."""

    def _auth(self, *, token: str | None, oauth: bool, remote: bool = True):
        with _deployment(token=token, oauth=oauth, remote=remote):
            return PRIssueAnalyser().mcp.auth

    def test_both_credentials_compose(self):
        auth = self._auth(token=_STATIC_TOKEN, oauth=True)

        assert isinstance(auth, MultiAuth)
        assert isinstance(auth.server, GitHubProvider)
        assert [type(verifier) for verifier in auth.verifiers] == [APIKeyVerifier]

    def test_the_composed_scopes_come_from_the_oauth_provider(self):
        """The static token is verified against these too, so they have to match."""
        auth = self._auth(token=_STATIC_TOKEN, oauth=True)

        assert auth.required_scopes == GITHUB_SCOPES

    def test_the_oauth_trio_alone_stays_the_oauth_provider(self):
        assert isinstance(self._auth(token=None, oauth=True), GitHubProvider)

    def test_the_static_token_alone_stays_the_key_verifier(self):
        assert isinstance(self._auth(token=_STATIC_TOKEN, oauth=False), APIKeyVerifier)

    def test_neither_credential_leaves_the_transport_unauthenticated(self):
        assert self._auth(token=None, oauth=False) is None

    def test_stdio_takes_no_transport_authentication(self):
        assert self._auth(token=_STATIC_TOKEN, oauth=True, remote=False) is None


class TestCombinedCredentials:
    """A deployment holding the OAuth trio and a static token accepts either. See #389."""

    def _client(self) -> TestClient:
        with _deployment(token=_STATIC_TOKEN, oauth=True, remote=True):
            return TestClient(_app(PRIssueAnalyser()))

    def _post(self, bearer: str):
        with self._client() as client:
            return client.post(
                "/mcp/", json=_TOOLS_LIST, headers={**_MCP_HEADERS, "Authorization": f"Bearer {bearer}"}
            )

    def test_the_static_token_reaches_the_tools(self):
        """An OAuth deployment used to refuse this, which is what forced a second one."""
        assert self._post(_STATIC_TOKEN).status_code == 200

    def test_a_token_matching_neither_is_refused(self):
        assert self._post("neither-credential").status_code == 401

    def test_the_refusal_names_no_credential(self):
        """The WWW-Authenticate header still carries the RFC 9728 metadata URL, which
        points at discovery rather than at whichever credential the token failed."""
        body = self._post("neither-credential").json()

        assert body["error"] == "invalid_token"
        assert "GITHUB_TOKEN" not in body["error_description"]
        assert "OAuth" not in body["error_description"]
