#!/usr/bin/env python3

# /*
#  * Copyright Said Sef
#  *
#  * Licensed under the Apache License, Version 2.0 (the "License");
#  * you may not use this file except in compliance with the License.
#  * You may obtain a copy of the License at
#  *
#  *      https://www.apache.org/licenses/LICENSE-2.0
#  *
#  * Unless required by applicable law or agreed to in writing, software
#  * distributed under the License is distributed on an "AS IS" BASIS,
#  * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  * See the License for the specific language governing permissions and
#  * limitations under the License.
#  */

from __future__ import annotations

import inspect
import logging
import sys
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from os import getenv
from pathlib import Path
from time import perf_counter
from typing import Any

from fastmcp import FastMCP
from fastmcp.apps.choice import Choice
from fastmcp.apps.generative import GenerativeUI
from fastmcp.exceptions import AuthorizationError, InsufficientScopeError, NotFoundError
from fastmcp.server.auth import AuthProvider, MultiAuth, restrict_tag
from fastmcp.server.middleware import AuthMiddleware, Middleware, MiddlewareContext
from fastmcp.server.providers.skills import SkillsDirectoryProvider
from fastmcp_tasks import TasksExtension
from mcp.types import ToolListChangedNotification
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    PlatformCollector,
    ProcessCollector,
    generate_latest,
)
from starlette.middleware import Middleware as ASGIMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from . import admin
from .auth import (
    UnconfiguredCredentials,
    aclose_token_store,
    get_token_store,
    oauth_configured,
    setup_token_store,
)
from .github_integration import GitHubIntegration as GI
from .preferences import ToolPreferences, read_record
from .tool_annotations import GATED_SCOPES

logger = logging.getLogger(__name__)

PORT = int(getenv("PORT", 8081))
HOST = getenv("HOST", "localhost")
LOG_LEVEL = getenv("LOG_LEVEL", "WARNING")


def _package_version() -> str:
    """The installed distribution version, or "unknown" when the package is not installed."""
    try:
        return version("mcp-github-pr-issue-analyser")
    except PackageNotFoundError:
        return "unknown"


VERSION = _package_version()


def _env_enabled(name: str) -> bool:
    """True only for an explicit yes. A plain emptiness check read the text "false"
    as on, which is the opposite of what it says. See #303."""
    return getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


MCP_ENABLE_REMOTE = _env_enabled("MCP_ENABLE_REMOTE")

TOOL_PREFIX = "github_"

# Where the Python name and the tool name should differ, because the method name
# describes the call worse than the tool name can. See #406.
_TOOL_NAMES = {"update_reviews": "submit_review", "update_assignees": "set_assignees"}


def _tool_name(method_name: str) -> str:
    """The name a Python method is registered under."""
    return f"{TOOL_PREFIX}{_TOOL_NAMES.get(method_name, method_name)}"

try:
    # CPU, memory and runtime metrics alongside the tool counters below
    ProcessCollector(registry=REGISTRY)
    PlatformCollector(registry=REGISTRY)
except ValueError:
    pass

TOOL_CALLS = Counter("mcp_tool_invocations_total", "Total tool calls", ["tool_name", "outcome"])
TOOL_DURATION = Histogram("mcp_tool_duration_seconds", "Tool call duration", ["tool_name", "outcome"])
TOOL_IN_PROGRESS = Gauge("mcp_tool_in_progress", "Tool calls currently running")
# Unlabelled on purpose. A label carrying the name asked for would let a caller
# grow the series count by inventing tools, which is what #304 fixed.
STALE_TOOL_LIST = Counter("mcp_stale_tool_list_total", "Tool calls naming a tool the server does not register")


class StaleToolList(Middleware):
    """Tells a client to re-read the tool list when it names a tool that is gone.

    A client fetches the list when it connects and caches it, so a rename leaves
    it calling a name the server no longer has. The notification rides the
    in-flight request, which is the only channel back to a sessionless
    connection. A retired name and a misspelled one are the same thing here, and
    both are answered the same way. See #439.

    A name nobody has reaches this in two shapes. Over stdio the lookup fails and
    raises NotFoundError. Over HTTP the authorization middleware gets there first
    and answers "not found or not authorized" for a tool that is absent and for
    one the caller may not see alike, deliberately, so as not to disclose which.
    Either way the client's own list lacks the name, so asking it to re-read
    discloses nothing it cannot already see.
    """

    async def on_call_tool(self, context: MiddlewareContext, call_next: Any) -> Any:
        try:
            return await call_next(context)
        except InsufficientScopeError:
            # The tool exists and the grant is short. Re-reading would return the
            # same list, so this is a permissions answer rather than a stale one.
            raise
        except (NotFoundError, AuthorizationError):
            STALE_TOOL_LIST.inc()
            logger.info(f"No tool named {getattr(context.message, 'name', '')}; asking the client to re-read the list")
            await self._ask_for_a_refresh(context)
            raise

    @staticmethod
    async def _ask_for_a_refresh(context: MiddlewareContext) -> None:
        """Best effort. A transport with no session to push down cannot be told,
        and losing the notification must not replace the caller's own error with
        one about notifying them."""
        if context.fastmcp_context is None:
            return
        try:
            await context.fastmcp_context.send_notification(ToolListChangedNotification())
        except Exception as reason:
            logger.info(f"Could not ask the client to re-read the tool list: {reason}")


class MetricsMiddleware(Middleware):
    """Counts and times every tool call, whether it succeeded or failed.

    A name no registered tool answers to arrives here as NotFoundError and is
    recorded as "unknown", so a client cannot grow the series count by asking
    for tools that do not exist. See #304.
    """

    async def on_call_tool(self, context: MiddlewareContext, call_next: Any) -> Any:
        start = perf_counter()
        name = getattr(context.message, "name", "unknown")
        outcome = "success"
        TOOL_IN_PROGRESS.inc()
        try:
            return await call_next(context)
        except NotFoundError:
            name, outcome = "unknown", "error"
            raise
        except Exception:
            outcome = "error"
            raise
        finally:
            TOOL_IN_PROGRESS.dec()
            TOOL_CALLS.labels(tool_name=name, outcome=outcome).inc()
            TOOL_DURATION.labels(tool_name=name, outcome=outcome).observe(perf_counter() - start)


_MCP_INSTRUCTIONS = """
# GitHub PR and Issue Analyser

This server provides tools to analyse GitHub Pull Requests (PRs) and manage GitHub Issues, Tags, Releases and Project boards.

## Features
- Fetch PR diffs, content, linked issues, and CI status
- Read a repository's files and directory tree at any branch, tag or SHA
- Update PR descriptions and post inline review comments
- Create and update GitHub issues
- Place issues and PRs on project boards and set their fields
- Create tags and releases

## Prerequisites
1. GitHub integration is preconfigured
2. Appropriate permissions and GitHub API key is set

## Best Practices
- Use all tools available for a comprehensive understanding of the PR and issue landscape.
- Use github_get_skill to read the guidance covering a task before starting it, since it carries constraints the tool schemas do not
- Use github_list_repos when you do not already know the repository name, rather than guessing at one
- Use github_get_pr_diff (preferred) and github_get_pr_content for detailed PR analysis
- Use github_get_repository_file to read the code a hunk sits in, since a few lines of context rarely settle whether a change is right
- Use single dashes instead of em-dashes in PR descriptions and issue bodies
- Use github_update_pr to change a PR's title, body, state, base branch or labels, leaving out whatever is not changing
- Use github_create_issue and github_update_issue for issue management
- Use the labels parameter on github_create_pr and github_update_pr to label a pull request, since GitHub keeps PR labels on the issues endpoint
- Use github_set_issue_milestone to file an issue under a milestone after it exists, since github_update_issue cannot clear one
- Use github_create_tag and github_create_release for release management
- Use github_get_project_fields before github_set_project_field, since option names differ per board
- Always maintain a professional, clear and concise tone

## Skills
Workflow guidance ships with the server. Call github_list_skills for the set and
github_get_skill to read one, which needs nothing but tool support. The same content is served as MCP
resources under the skill:// URI scheme, for a client that reads resources:
- skill://pr-analysis/SKILL.md -- fetch a PR's metadata, diff, linked issues and CI status
- skill://pr-review/SKILL.md -- post inline comments and submit review decisions
- skill://pr-management/SKILL.md -- create, update, assign, refresh and merge PRs
- skill://issue-management/SKILL.md -- create, update, list and search issues and PRs, list labels, and run milestones
- skill://release-management/SKILL.md -- tag commits, publish releases, and correct or withdraw what is published
- skill://project-boards/SKILL.md -- place issues on a project board, set their fields, and read a board
- skill://user-activity/SKILL.md -- find repositories, and look up user profiles, contributions and star growth
- skill://error-handling/SKILL.md -- read the error codes and decide whether to retry
- skill://interactive-ui/SKILL.md -- ask the user to choose, or render data as a UI panel
"""


class PRIssueAnalyser:
    """PRIssueAnalyser exposes GitHub PR and issue management as MCP tools."""

    def __init__(self):
        self.gi = GI()

        def _select_auth() -> AuthProvider | None:
            """The transport's authentication. Where both credentials are configured the
            OAuth provider owns the routes and the metadata while the static token is
            verified beside it, so either kind of caller reaches the server. See #389."""
            if not MCP_ENABLE_REMOTE:
                return None
            oauth = self.gi._oauth_verifier if self.gi._oauth_mode else None
            if oauth is not None and self.gi.verifier is not None:
                return MultiAuth(server=oauth, verifiers=[self.gi.verifier])
            return oauth or self.gi.verifier

        self.mcp = FastMCP(
            name="GitHub PR and Issue Analyser",
            auth=_select_auth(),
            instructions=_MCP_INSTRUCTIONS,
            lifespan=self._lifespan,
        )
        self.mcp.add_provider(Choice(name="github_pr_issue_analyser"))
        self.mcp.add_provider(
            GenerativeUI(
                tool_name="github_pr_issue_analyser_ui",
                components_tool_name="github_search_prefab_components",
            )
        )
        self.mcp.add_middleware(StaleToolList())
        self.mcp.add_middleware(MetricsMiddleware())
        # A tool carries the scopes it needs as its tags, so one check per scope gates
        # every tool that declares it. The middleware names the shortfall on a refused
        # call, which a per-tool check cannot do. See #388.
        self.mcp.add_middleware(AuthMiddleware(auth=[restrict_tag(s, scopes=[s]) for s in GATED_SCOPES]))
        # After the scope gate, so a tool the grant cannot reach stays unreachable
        # whatever the user turned on. See #451.
        self.mcp.add_middleware(ToolPreferences())
        # Background tasks are an extension in FastMCP 4, so a tool marked task=True
        # runs in the request path until the extension is registered.
        self.mcp.add_extension(TasksExtension())

        @self.mcp.custom_route("/metrics", methods=["GET"])
        async def metrics_route(_request: Request) -> Response:
            """Prometheus scrape endpoint, served in HTTP mode only."""
            return Response(generate_latest(registry=REGISTRY), media_type=CONTENT_TYPE_LATEST)

        @self.mcp.custom_route("/", methods=["GET"])
        async def landing_route(_request: Request) -> Response:
            """Liveness endpoint, served in HTTP mode only. Reports no dependency state,
            so a GitHub outage cannot mark the server down."""
            tools = await self.mcp.list_tools(run_middleware=False)
            return JSONResponse({"status": "ok", "service": self.mcp.name, "version": VERSION, "tools": len(tools)})

        if self._admin_enabled:
            self._register_admin_routes()

        logger.info("MCP Server initialised")
        self.register_tools()

    @property
    def _admin_enabled(self) -> bool:
        """The admin page needs an HTTP surface to be reached on and an OAuth flow to
        identify a user by, and a static-token deployment has neither. See #451."""
        return MCP_ENABLE_REMOTE and oauth_configured()

    def _register_admin_routes(self) -> None:
        """The browser sign-in and the per-user tool list."""
        provider = self.gi._oauth_verifier

        @self.mcp.custom_route(admin.ADMIN_PATH, methods=["GET"])
        async def admin_route(request: Request) -> Response:
            session = await admin.read_session(request)
            if session is None:
                return await admin.start_login(provider, admin.ADMIN_PATH)
            record = await read_record(str(session.get("sub", "")))
            tools = await self.mcp.list_tools(run_middleware=False)
            return HTMLResponse(
                admin.render_page(
                    login=str(session.get("login", "")),
                    tools=list(tools),
                    disabled={str(name) for name in record.get("disabled") or []},
                    csrf=str(session.get("csrf", "")),
                    saved=request.query_params.get("saved") == "1",
                )
            )

        @self.mcp.custom_route(admin.CALLBACK_PATH, methods=["GET"])
        async def admin_callback_route(request: Request) -> Response:
            return await admin.complete_login(provider, request)

        @self.mcp.custom_route(f"{admin.ADMIN_PATH}/preferences", methods=["POST"])
        async def admin_preferences_route(request: Request) -> Response:
            return await admin.save_preferences(request)

        @self.mcp.custom_route(f"{admin.ADMIN_PATH}/logout", methods=["POST"])
        async def admin_logout_route(request: Request) -> Response:
            form = await request.form()
            return await admin.sign_out(request, everywhere=form.get("everywhere") == "1")

    @asynccontextmanager
    async def _lifespan(self, _server: FastMCP) -> AsyncIterator[None]:
        """Prepares the token store before the first request, then releases the GitHub
        HTTP clients and the token store's client when the server shuts down.
        See #315, #357 and #363."""
        try:
            # The store records itself only once something builds it, and setup reads
            # that record, so the shared store is claimed before the table check runs.
            get_token_store()
            await setup_token_store()
            if self._admin_enabled:
                await admin.register_admin_client(self.gi._oauth_verifier)
            yield
        finally:
            await self.gi.aclose()
            await aclose_token_store()

    def register_tools(self, methods: Any = None) -> None:
        if methods is None:
            methods = self.gi
        for name in dir(methods):
            if name.startswith("_"):
                continue
            method = getattr(methods, name)
            if inspect.isroutine(method):
                annotations = getattr(method, "_mcp_annotations", None)
                if annotations is not None:
                    task = getattr(method, "_mcp_task", False)
                    scopes: set[str] = set(getattr(method, "_mcp_scopes", ()))
                    # The service prefix goes on the tool name rather than the Python
                    # one, so a session holding this server and a GitLab one does not
                    # offer an agent three tools called create_issue. See #406.
                    registered = _tool_name(name)
                    self.mcp.tool(registered, annotations=annotations, task=task, tags=scopes or None)(method)
        self.mcp.add_provider(SkillsDirectoryProvider(Path(__file__).parent / "skills"))

    def run(self) -> None:
        """Runs the MCP server. Uses HTTP when MCP_ENABLE_REMOTE is true, otherwise stdio."""
        try:
            logger.info("Running MCP Server for GitHub PR Analysis.")
            if MCP_ENABLE_REMOTE:
                self.mcp.run(
                    transport="http",
                    host=HOST,
                    port=PORT,
                    stateless_http=True,
                    middleware=[
                        ASGIMiddleware(UnconfiguredCredentials, configured=lambda: self.gi.credentials_configured)
                    ],
                )
            else:
                self.mcp.run(transport="stdio")
        except Exception as e:
            logger.error(f"Fatal Error in MCP Server: {e}")
            traceback.print_exc(file=sys.stderr)


def main() -> None:
    """Main entry point. Configures the root logger, which is the application's
    job and not something importing this package should do. See #318."""
    logging.basicConfig(level=getattr(logging, LOG_LEVEL.upper(), logging.WARNING))
    try:
        review = PRIssueAnalyser()
        review.run()
    except Exception as e:
        logger.error(f"Error running main analyzer: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
