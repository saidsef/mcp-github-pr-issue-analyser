"""Tests for the MCP server lifespan and its custom routes."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from importlib.metadata import PackageNotFoundError
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastmcp.exceptions import InsufficientScopeError
from fastmcp.server.auth import AccessToken, MultiAuth
from fastmcp.server.auth.providers.github import GitHubProvider
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
from mcp_github.issues_pr_analyser import (
    TOOL_PREFIX,
    VERSION,
    PRIssueAnalyser,
    _package_version,
    _tool_name,
)
from mcp_github.skills_access import SKILLS_DIR
from mcp_github.tool_annotations import GATED_SCOPES, WRITE_SCOPES

_TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
_MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
_STATIC_TOKEN = "test-token"
_SNAKE = {
    "readOnlyHint": "read_only_hint",
    "destructiveHint": "destructive_hint",
    "idempotentHint": "idempotent_hint",
    "openWorldHint": "open_world_hint",
}
_AUTH_HEADERS = _MCP_HEADERS | {"Authorization": f"Bearer {_STATIC_TOKEN}"}
_A_RELEASE = {"repo_owner": "o", "repo_name": "r", "release_id": 1}
_OAUTH_SETTINGS = {
    "GITHUB_OAUTH_CLIENT_ID": "Ov23liExample",
    "GITHUB_OAUTH_CLIENT_SECRET": "oauth-client-secret",
    "GITHUB_OAUTH_BASE_URL": "https://mcp.example.com",
}


@contextmanager
def _deployment(*, token: str | None, oauth: bool, remote: bool = False) -> Iterator[None]:
    """The settings an analyser reads while it is being built. The OAuth trio is read
    from auth, the static token from github_integration, and the transport flag from
    the server module. The token store stays in memory whatever the environment holds."""
    with ExitStack() as stack:
        stack.enter_context(patch("mcp_github.github_integration.GITHUB_TOKEN", token))
        for name, value in _OAUTH_SETTINGS.items():
            stack.enter_context(patch(f"mcp_github.auth.{name}", value if oauth else None))
        stack.enter_context(patch("mcp_github.issues_pr_analyser.MCP_ENABLE_REMOTE", remote))
        stack.enter_context(patch("mcp_github.auth.REDIS_HOST_PORT", None))
        stack.enter_context(patch("mcp_github.auth.DYNAMODB_TABLE_ARN", None))
        yield


def _analyser() -> PRIssueAnalyser:
    with _deployment(token=_STATIC_TOKEN, oauth=False):
        return PRIssueAnalyser()


def _unconfigured() -> PRIssueAnalyser:
    """An analyser holding neither the OAuth trio nor a static token."""
    with _deployment(token=None, oauth=False):
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
            assert tools[_tool_name(name)].tags == set(scopes), name

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

        assert "github_create_issue" in listed
        assert listed.isdisjoint({"github_add_to_project", "github_set_project_field", "github_remove_from_project"})

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
            await analyser.mcp.call_tool("github_delete_release", arguments)

        assert refusal.value.required_scopes == list(WRITE_SCOPES)
        assert "github_delete_release" in str(refusal.value)
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
    with _deployment(token=_STATIC_TOKEN, oauth=False, remote=True):
        analyser = PRIssueAnalyser()
    with patch("mcp_github.auth.GITHUB_SCOPES", granted), TestClient(_app(analyser)) as client:
        yield client


def _payload(response) -> dict:
    """The JSON body of a streamable HTTP response, which arrives as an SSE event."""
    body = response.text
    return json.loads(body.split("data: ", 1)[1] if body.startswith("event:") else body)


def _rpc(client: TestClient, method: str, params: dict | None = None) -> dict:
    """One JSON-RPC call over the streamable HTTP transport."""
    response = client.post(
        "/mcp", headers=_AUTH_HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    )
    assert response.status_code == 200, response.text
    return _payload(response)


class TestScopeGateOverHTTP:
    """The gate as a caller meets it, through the transport that enforces the floor
    before the middleware sees the request. See #388."""

    def test_a_grant_holding_every_scope_reaches_every_tool(self):
        with _remote_client(GITHUB_SCOPES) as client:
            listed = {tool["name"] for tool in _rpc(client, "tools/list")["result"]["tools"]}

        assert {"github_get_pr_diff", "github_create_issue", "github_add_to_project"} <= listed

    def test_a_floor_only_grant_is_admitted_and_filtered(self):
        """The transport lets the request through, and the gate answers it."""
        with _remote_client(REQUIRED_SCOPES) as client:
            listed = {tool["name"] for tool in _rpc(client, "tools/list")["result"]["tools"]}
            refused = _rpc(client, "tools/call", {"name": "github_delete_release", "arguments": _A_RELEASE})

        assert "github_get_pr_diff" in listed
        assert listed.isdisjoint({"github_create_issue", "github_add_to_project"})
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
        with _deployment(token=None, oauth=True):
            provider = get_oauth_verifier()

        # The transport reads the first and the GitHub token check the second, and
        # both refuse a grant short of them before any tool is reached.
        assert provider.required_scopes == list(REQUIRED_SCOPES)
        assert provider._token_validator.required_scopes == list(REQUIRED_SCOPES)

    def test_the_provider_still_offers_every_scope_the_tools_need(self):
        """Requiring less must not ask GitHub for less, or no grant would ever hold
        the scopes the gated tools want."""
        with _deployment(token=None, oauth=True):
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

    def test_the_composed_floor_comes_from_the_oauth_provider(self):
        """The static token is verified against the floor too, so its grant has to clear
        it. What a tool needs beyond the floor is left to the scope gate. See #388."""
        auth = self._auth(token=_STATIC_TOKEN, oauth=True)

        assert auth.required_scopes == list(REQUIRED_SCOPES)
        assert set(REQUIRED_SCOPES) <= set(GITHUB_SCOPES)

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

    def test_the_static_token_reaches_the_gated_tools(self):
        """Its grant reports every scope the flow asks GitHub for, so the gate lists the
        tools that write as well as the ones that read. See #388."""
        listed = {tool["name"] for tool in _payload(self._post(_STATIC_TOKEN))["result"]["tools"]}

        assert {"github_get_pr_diff", "github_create_issue", "github_add_to_project"} <= listed

    def test_a_token_matching_neither_is_refused(self):
        assert self._post("neither-credential").status_code == 401

    def test_the_refusal_names_no_credential(self):
        """The WWW-Authenticate header still carries the RFC 9728 metadata URL, which
        points at discovery rather than at whichever credential the token failed."""
        body = self._post("neither-credential").json()

        assert body["error"] == "invalid_token"
        assert "GITHUB_TOKEN" not in body["error_description"]
        assert "OAuth" not in body["error_description"]


class TestSkillsAreReachable:
    """Skills are published as skill:// resources and as tools. A tool-only
    client never issues resources/list, so the tools are the path that has to
    work everywhere. See #414."""

    @pytest.mark.anyio
    async def test_a_tool_only_client_can_list_and_read_a_skill(self):
        analyser = _analyser()
        names = {tool.name for tool in await analyser.mcp.list_tools(run_middleware=False)}

        assert {"github_list_skills", "github_get_skill"} <= names

    @pytest.mark.anyio
    async def test_the_skill_tools_need_no_scope(self):
        """A read tool is ungated, so the guidance is reachable on any grant."""
        analyser = _analyser()
        tools = {tool.name: tool for tool in await analyser.mcp.list_tools(run_middleware=False)}

        assert not tools["github_list_skills"].tags
        assert not tools["github_get_skill"].tags

    @pytest.mark.anyio
    async def test_both_paths_carry_the_same_set(self):
        """The resources and the tools read the same files, so a skill added to
        one path cannot go missing from the other."""
        analyser = _analyser()
        listed = {entry["name"] for entry in (await analyser.gi.list_skills())["skills"]}
        published = {
            str(resource.uri).removeprefix("skill://").removesuffix("/SKILL.md")
            for resource in await analyser.mcp.list_resources()
            if str(resource.uri).endswith("/SKILL.md")
        }

        assert listed == published
        assert listed

    @pytest.mark.anyio
    async def test_the_instructions_name_the_tool_path(self):
        """The instructions used to offer skill:// URIs alone, which a tool-only
        client cannot act on."""
        instructions = _analyser().mcp.instructions or ""

        assert "github_list_skills" in instructions
        assert "github_get_skill" in instructions


class TestListOpenIssuesPrsSchema:
    """What `filtering` does to `repo_owner` only ever reached the skill, so a
    tool-only client had to guess at the field. See #412."""

    @staticmethod
    async def _schema() -> Any:
        tools = {tool.name: tool for tool in await _analyser().mcp.list_tools(run_middleware=False)}
        return tools["github_list_open_issues_prs"]

    @pytest.mark.anyio
    async def test_repo_owner_carries_the_per_mode_meaning(self):
        properties = (await self._schema()).parameters["properties"]
        described = properties["repo_owner"]["description"].lower()

        assert "username" in described
        assert "organisation" in described
        assert "owner" in described

    @pytest.mark.anyio
    async def test_filtering_names_what_each_mode_returns(self):
        described = (await self._schema()).parameters["properties"]["filtering"]["description"].lower()

        assert {"involves", "user", "org", "repo"} <= set(described.split())

    @pytest.mark.anyio
    async def test_every_parameter_is_described(self):
        properties = (await self._schema()).parameters["properties"]

        assert [name for name, field in properties.items() if not field.get("description")] == []

    @pytest.mark.anyio
    async def test_the_description_states_open_only_and_points_onward(self):
        description = (await self._schema()).description or ""

        assert "is:open" in description
        assert "search_issues_prs" in description


class TestRemovedTools:
    """A name the server no longer answers to must not survive in the prose a
    client reads, or it advertises a tool nobody can call. See #399."""

    @pytest.mark.anyio
    async def test_update_pr_description_is_gone(self):
        names = {tool.name for tool in await _analyser().mcp.list_tools(run_middleware=False)}

        assert "github_update_pr_description" not in names
        assert "github_update_pr" in names

    @pytest.mark.anyio
    async def test_the_instructions_do_not_name_it(self):
        assert "github_update_pr_description" not in (_analyser().mcp.instructions or "")

    def test_no_skill_still_points_at_it(self):
        stale = [
            path.parent.name
            for path in SKILLS_DIR.glob("*/SKILL.md")
            if "github_update_pr_description" in path.read_text(encoding="utf-8")
        ]

        assert stale == []


class TestToolRationales:
    """A tool description that explains itself has to be right, since a tool-only
    client cannot cross-check it against the skill. See #400."""

    @staticmethod
    async def _described(name: str) -> str:
        tools = {tool.name: tool for tool in await _analyser().mcp.list_tools(run_middleware=False)}
        return tools[f"github_{name}"].description or ""

    @pytest.mark.anyio
    async def test_update_release_says_how_to_move_the_latest_badge(self):
        """The old wording blamed a parameter budget, and update_release is the
        smaller of the two signatures."""
        description = await self._described("update_release")

        assert "parameter budget" not in description
        assert "make_latest" in description
        assert "publishing it again" in description

    @pytest.mark.anyio
    async def test_set_issue_milestone_gives_a_reason_that_holds(self):
        """update_issue dropping nulls is a rule this server writes for itself,
        and labels already escape it with [], so it cannot be the reason."""
        description = await self._described("set_issue_milestone")

        assert "drops every argument" not in description
        assert "title" in description

    @pytest.mark.anyio
    async def test_the_skill_and_the_description_agree_on_make_latest(self):
        skill = (SKILLS_DIR / "release-management" / "SKILL.md").read_text(encoding="utf-8")
        description = await self._described("update_release")

        for claim in ("create_release", "make_latest"):
            assert claim in skill and claim in description
        assert "parameter budget" not in skill


class TestAnnotationCoverage:
    """Every tool this repo registers declares all four hints, so a new one
    cannot ship unannotated. See #407."""

    # The Choice and GenerativeUI providers register these, so their annotations
    # are FastMCP's to set rather than this repo's.
    _PROVIDED = {"choose", "github_pr_issue_analyser_ui", "github_search_prefab_components"}

    async def _own_tools(self) -> list[Any]:
        tools = await _analyser().mcp.list_tools(run_middleware=False)
        return [tool for tool in tools if tool.name not in self._PROVIDED]

    @pytest.mark.anyio
    async def test_every_tool_declares_all_four_hints(self):
        missing = {
            tool.name: [
                hint
                for hint in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
                if getattr(tool.annotations, _SNAKE[hint], None) is None
            ]
            for tool in await self._own_tools()
            if tool.annotations is None
            or any(
                getattr(tool.annotations, _SNAKE[hint], None) is None
                for hint in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
            )
        }

        assert missing == {}

    @pytest.mark.anyio
    async def test_every_tool_acts_on_an_open_world(self):
        """All of them reach api.github.com."""
        assert all(tool.annotations.open_world_hint is True for tool in await self._own_tools())

    @pytest.mark.anyio
    async def test_the_coverage_check_sees_the_whole_tool_list(self):
        """A guard that silently skipped every tool would pass the check above."""
        assert len(await self._own_tools()) > 40

    @pytest.mark.anyio
    async def test_no_tool_that_writes_claims_to_be_read_only(self):
        writers = [t for t in await self._own_tools() if t.tags]

        assert writers
        assert all(tool.annotations.read_only_hint is False for tool in writers)


class TestToolNaming:
    """Tool names carry a service prefix, so a session holding this server and a
    GitLab one does not offer an agent three tools called create_issue. See #406."""

    # FastMCP's Choice provider hardcodes this one and exposes no way to rename it.
    _NOT_OURS = {"choose"}

    @pytest.mark.anyio
    async def test_every_tool_this_repo_registers_is_prefixed(self):
        names = {tool.name for tool in await _analyser().mcp.list_tools(run_middleware=False)}
        bare = {name for name in names - self._NOT_OURS if not name.startswith(TOOL_PREFIX)}

        assert bare == set()

    @pytest.mark.anyio
    async def test_the_two_misnamed_tools_say_what_they_do(self):
        """update_reviews submits a new review rather than updating one, and
        update_assignees replaces the set rather than adding to it."""
        names = {tool.name for tool in await _analyser().mcp.list_tools(run_middleware=False)}

        assert {"github_submit_review", "github_set_assignees"} <= names
        assert names.isdisjoint({"github_update_reviews", "github_update_assignees"})

    @pytest.mark.anyio
    async def test_the_python_names_are_untouched(self):
        """Only the registered name moves, so callers of the class keep working."""
        gi = _analyser().gi

        assert callable(gi.update_reviews)
        assert callable(gi.update_assignees)

    @pytest.mark.anyio
    async def test_no_description_points_at_a_name_that_is_not_registered(self):
        """A description naming a bare tool sends a client after something it
        cannot call."""
        tools = {tool.name: tool for tool in await _analyser().mcp.list_tools(run_middleware=False)}
        stale = {
            name: sorted(named - set(tools))
            for name, tool in tools.items()
            if (named := set(re.findall(r"github_[a-z_]+", tool.description or ""))) - set(tools)
        }

        assert stale == {}

    @pytest.mark.anyio
    async def test_the_skill_resources_keep_their_uris(self):
        """The prefix goes on tools alone. Rewriting skill:// would break the
        URIs the instructions publish and the ones github_get_skill builds."""
        uris = {str(resource.uri) for resource in await _analyser().mcp.list_resources()}

        assert "skill://pr-review/SKILL.md" in uris
        assert not any(uri.startswith("skill://github/") for uri in uris)
