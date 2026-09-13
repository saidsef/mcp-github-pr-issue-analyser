"""Tests for the MCP server lifespan and its custom routes."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from unittest.mock import AsyncMock, patch

import pytest
from starlette.middleware import Middleware as ASGIMiddleware
from starlette.testclient import TestClient

from mcp_github.auth import MISSING_CREDENTIALS, UnconfiguredCredentials
from mcp_github.issues_pr_analyser import VERSION, PRIssueAnalyser, _package_version

_TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
_MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _analyser() -> PRIssueAnalyser:
    with patch("mcp_github.github_integration.GITHUB_TOKEN", "test-token"):
        return PRIssueAnalyser()


def _unconfigured() -> PRIssueAnalyser:
    """An analyser holding neither the OAuth trio nor a static token."""
    with (
        patch("mcp_github.github_integration.GITHUB_TOKEN", None),
        patch("mcp_github.github_integration.GITHUB_OAUTH_CLIENT_ID", None),
        patch("mcp_github.github_integration.GITHUB_OAUTH_CLIENT_SECRET", None),
        patch("mcp_github.github_integration.GITHUB_OAUTH_BASE_URL", None),
    ):
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
