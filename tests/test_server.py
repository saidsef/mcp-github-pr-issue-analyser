"""Tests for the MCP server lifespan and its custom routes."""

from __future__ import annotations

import json
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

from mcp_github.auth import (
    GITHUB_SCOPES,
    MISSING_CREDENTIALS,
    REQUIRED_SCOPES,
    APIKeyVerifier,
    UnconfiguredCredentials,
    get_oauth_verifier,
)
from mcp_github.issues_pr_analyser import VERSION, PRIssueAnalyser, _package_version
from mcp_github.tool_annotations import GATED_SCOPES, WRITE_SCOPES

_TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
_MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
_AUTH_HEADERS = _MCP_HEADERS | {"Authorization": "Bearer test-token"}
_A_RELEASE = {"repo_owner": "o", "repo_name": "r", "release_id": 1}


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


@contextmanager
def _oauth_env() -> Iterator[None]:
    """The OAuth trio set, with the token store left in memory."""
    with (
        patch("mcp_github.auth.GITHUB_OAUTH_CLIENT_ID", "client-id"),
        patch("mcp_github.auth.GITHUB_OAUTH_CLIENT_SECRET", "client-secret-long-enough"),
        patch("mcp_github.auth.GITHUB_OAUTH_BASE_URL", "https://mcp.example.test"),
        patch("mcp_github.auth.REDIS_HOST_PORT", None),
        patch("mcp_github.auth.DYNAMODB_TABLE_ARN", None),
    ):
        yield


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
        with _grant(list(GATED_SCOPES)):
            listed = await analyser.mcp.list_tools()

        assert len(listed) == len(registered)

    @pytest.mark.anyio
    async def test_a_grant_without_the_project_scope_loses_the_board_tools(self):
        """A board sits outside the repository it tracks, so repo alone does not
        reach it. See #351."""
        analyser = _analyser()
        with _grant(list(WRITE_SCOPES)):
            listed = {tool.name for tool in await analyser.mcp.list_tools()}

        assert "create_issue" in listed
        assert listed.isdisjoint({"add_to_project", "set_project_field", "remove_from_project"})

    @pytest.mark.anyio
    async def test_the_gate_holds_a_check_for_every_scope_a_tool_declares(self):
        analyser = _analyser()
        declared = {scope for tool in await analyser.mcp.list_tools(run_middleware=False) for scope in tool.tags}

        assert declared == set(GATED_SCOPES)

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


@contextmanager
def _remote_client(granted: tuple[str, ...]) -> Iterator[TestClient]:
    """The app the remote deployment runs, answering a bearer token whose grant holds
    these scopes."""
    with (
        patch("mcp_github.issues_pr_analyser.MCP_ENABLE_REMOTE", True),
        patch("mcp_github.github_integration.GITHUB_TOKEN", "test-token"),
    ):
        analyser = PRIssueAnalyser()
    with patch("mcp_github.auth.GITHUB_SCOPES", granted), TestClient(_app(analyser)) as client:
        yield client


def _rpc(client: TestClient, method: str, params: dict | None = None) -> dict:
    """One JSON-RPC call over the streamable HTTP transport."""
    response = client.post(
        "/mcp", headers=_AUTH_HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    )
    assert response.status_code == 200, response.text
    body = response.text
    return json.loads(body.split("data: ", 1)[1] if body.startswith("event:") else body)


class TestScopeGateOverHTTP:
    """The gate as a caller meets it, through the transport that enforces the floor
    before the middleware sees the request. See #388."""

    def test_a_grant_holding_every_scope_reaches_every_tool(self):
        with _remote_client(GITHUB_SCOPES) as client:
            listed = {tool["name"] for tool in _rpc(client, "tools/list")["result"]["tools"]}

        assert {"get_pr_diff", "create_issue", "add_to_project"} <= listed

    def test_a_floor_only_grant_is_admitted_and_filtered(self):
        """The transport lets the request through, and the gate answers it."""
        with _remote_client(REQUIRED_SCOPES) as client:
            listed = {tool["name"] for tool in _rpc(client, "tools/list")["result"]["tools"]}
            refused = _rpc(client, "tools/call", {"name": "delete_release", "arguments": _A_RELEASE})

        assert "get_pr_diff" in listed
        assert listed.isdisjoint({"create_issue", "add_to_project"})
        assert refused["result"]["isError"] is True
        assert "insufficient scope (required: repo)" in refused["result"]["content"][0]["text"]


class TestScopeFloor:
    """What the transport requires of every grant, which is what the gate can filter.
    See #388."""

    def test_the_floor_names_no_scope_the_gate_checks(self):
        """The transport refuses a grant short of the floor before the gate runs, so a
        gated scope in the floor would cost the read-only tools their access too."""
        assert set(REQUIRED_SCOPES).isdisjoint(GATED_SCOPES)

    def test_the_provider_requires_only_the_floor(self):
        with _oauth_env():
            provider = get_oauth_verifier()

        # The transport reads the first and the GitHub token check the second, and
        # both refuse a grant short of them before any tool is reached.
        assert provider.required_scopes == list(REQUIRED_SCOPES)
        assert provider._token_validator.required_scopes == list(REQUIRED_SCOPES)

    def test_the_provider_still_offers_every_scope_the_tools_need(self):
        """Requiring less must not ask GitHub for less, or no grant would ever hold
        the scopes the gated tools want."""
        with _oauth_env():
            provider = get_oauth_verifier()

        assert provider.scopes_supported == list(GITHUB_SCOPES)
        assert provider.client_registration_options.default_scopes == list(GITHUB_SCOPES)
        assert set(GATED_SCOPES) <= set(GITHUB_SCOPES)


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
