"""Tests for the MCP server lifespan and its custom routes."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError
from unittest.mock import AsyncMock, patch

import pytest
from fastmcp.exceptions import InsufficientScopeError
from fastmcp.server.auth import AccessToken
from fastmcp.server.context import reset_transport, set_transport
from starlette.middleware import Middleware as ASGIMiddleware
from starlette.testclient import TestClient

from mcp_github.auth import MISSING_CREDENTIALS, APIKeyVerifier, UnconfiguredCredentials
from mcp_github.issues_pr_analyser import VERSION, PRIssueAnalyser, _package_version
from mcp_github.tool_annotations import WRITE_SCOPES

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


@contextmanager
def _grant(scopes: list[str]) -> Iterator[None]:
    """A request over an HTTP transport carrying an OAuth grant with these scopes."""
    token = AccessToken(token="t", client_id="c", expires_at=None, scopes=scopes, claims={})
    transport = set_transport("streamable-http")
    try:
        with patch("fastmcp.server.middleware.authorization.get_access_token", return_value=token):
            yield
    finally:
        reset_transport(transport)


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

        assert body["tools"] == len(await analyser.mcp.list_tools(run_middleware=False))
        assert body["tools"] > 0

    def test_head_request_succeeds(self):
        app = _analyser().mcp.http_app(transport="http", stateless_http=True)
        with TestClient(app) as client:
            assert client.head("/").status_code == 200


class TestScopeGate:
    """Write and destructive tools are gated on the scopes they need. See #388."""

    @pytest.mark.anyio
    async def test_a_tool_carries_the_scopes_it_declares_as_tags(self):
        analyser = _analyser()
        tools = {tool.name: tool for tool in await analyser.mcp.list_tools(run_middleware=False)}
        for name in dir(analyser.gi):
            if name.startswith("_"):
                continue
            scopes = getattr(getattr(analyser.gi, name), "_mcp_scopes", None)
            if scopes is None:
                continue
            assert tools[name].tags == set(scopes), name

    @pytest.mark.anyio
    async def test_a_read_only_grant_sees_only_the_read_only_tools(self):
        analyser = _analyser()
        registered = await analyser.mcp.list_tools(run_middleware=False)
        with _grant(["read:org"]):
            listed = await analyser.mcp.list_tools()

        assert [tool.name for tool in listed] == [tool.name for tool in registered if not tool.tags]
        assert len(listed) < len(registered)

    @pytest.mark.anyio
    async def test_a_grant_holding_the_scopes_sees_every_tool(self):
        analyser = _analyser()
        registered = await analyser.mcp.list_tools(run_middleware=False)
        with _grant(list(WRITE_SCOPES)):
            listed = await analyser.mcp.list_tools()

        assert len(listed) == len(registered)

    @pytest.mark.anyio
    async def test_a_refused_call_names_the_missing_scope(self):
        analyser = _analyser()
        arguments = {"repo_owner": "o", "repo_name": "r", "release_id": 1}
        with _grant(["read:org"]), pytest.raises(InsufficientScopeError) as refusal:
            await analyser.mcp.call_tool("delete_release", arguments)

        assert refusal.value.required_scopes == list(WRITE_SCOPES)
        assert "delete_release" in str(refusal.value)
        for scope in WRITE_SCOPES:
            assert scope in str(refusal.value)

    @pytest.mark.anyio
    async def test_the_static_token_grant_holds_the_scopes_the_gate_needs(self):
        granted = await APIKeyVerifier("test-token").verify_token("test-token")

        assert granted is not None
        assert set(WRITE_SCOPES) <= set(granted.scopes)

    @pytest.mark.anyio
    async def test_stdio_keeps_every_tool(self):
        analyser = _analyser()
        registered = await analyser.mcp.list_tools(run_middleware=False)
        transport = set_transport("stdio")
        try:
            listed = await analyser.mcp.list_tools()
        finally:
            reset_transport(transport)

        assert len(listed) == len(registered)


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
