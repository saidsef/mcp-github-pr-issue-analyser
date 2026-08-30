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
from os import getenv
from pathlib import Path
from time import perf_counter
from typing import Any

from fastmcp import FastMCP
from fastmcp.apps.choice import Choice
from fastmcp.apps.generative import GenerativeUI
from fastmcp.exceptions import NotFoundError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.providers.skills import SkillsDirectoryProvider
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
from starlette.requests import Request
from starlette.responses import Response

from .auth import (
    GITHUB_OAUTH_BASE_URL,
    GITHUB_OAUTH_CLIENT_ID,
    GITHUB_OAUTH_CLIENT_SECRET,
    aclose_token_store,
    setup_token_store,
)
from .github_integration import GitHubIntegration as GI

logger = logging.getLogger(__name__)

PORT = int(getenv("PORT", 8081))
HOST = getenv("HOST", "localhost")
LOG_LEVEL = getenv("LOG_LEVEL", "WARNING")


def _env_enabled(name: str) -> bool:
    """True only for an explicit yes. A plain emptiness check read the text "false"
    as on, which is the opposite of what it says. See #303."""
    return getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


MCP_ENABLE_REMOTE = _env_enabled("MCP_ENABLE_REMOTE")

try:
    # CPU, memory and runtime metrics alongside the tool counters below
    ProcessCollector(registry=REGISTRY)
    PlatformCollector(registry=REGISTRY)
except ValueError:
    pass

TOOL_CALLS = Counter("mcp_tool_invocations_total", "Total tool calls", ["tool_name", "outcome"])
TOOL_DURATION = Histogram("mcp_tool_duration_seconds", "Tool call duration", ["tool_name", "outcome"])
TOOL_IN_PROGRESS = Gauge("mcp_tool_in_progress", "Tool calls currently running")


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
- Update PR descriptions and post inline review comments
- Create and update GitHub issues
- Place issues and PRs on project boards and set their fields
- Create tags and releases

## Prerequisites
1. GitHub integration is preconfigured
2. Appropriate permissions and GitHub API key is set

## Best Practices
- Use all tools available for a comprehensive understanding of the PR and issue landscape.
- Use list_repos when you do not already know the repository name, rather than guessing at one
- Use get_pr_diff (preferred) and get_pr_content for detailed PR analysis
- Use single dashes instead of em-dashes in PR descriptions and issue bodies
- Use update_pr_description to keep PRs up-to-date
- Use create_issue and update_issue for issue management
- Use set_issue_milestone to file an issue under a milestone after it exists, since update_issue cannot clear one
- Use create_tag and create_release for release management
- Use get_project_fields before set_project_field, since option names differ per board
- Always maintain a professional, clear and concise tone

## Skills
Workflow guidance is available as MCP resources under the skill:// URI scheme:
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

        def _select_auth():
            if not MCP_ENABLE_REMOTE:
                return None
            if GITHUB_OAUTH_CLIENT_ID and GITHUB_OAUTH_CLIENT_SECRET and GITHUB_OAUTH_BASE_URL:
                return self.gi._oauth_verifier
            return self.gi.verifier

        self.mcp = FastMCP(
            name="GitHub PR and Issue Analyser",
            auth=_select_auth(),
            instructions=_MCP_INSTRUCTIONS,
            lifespan=self._lifespan,
        )
        self.mcp.add_provider(Choice(name="github_pr_issue_analyser"))
        self.mcp.add_provider(GenerativeUI(tool_name="github_pr_issue_analyser_ui"))
        self.mcp.add_middleware(MetricsMiddleware())

        @self.mcp.custom_route("/metrics", methods=["GET"])
        async def metrics_route(_request: Request) -> Response:
            """Prometheus scrape endpoint, served in HTTP mode only."""
            return Response(generate_latest(registry=REGISTRY), media_type=CONTENT_TYPE_LATEST)

        logger.info("MCP Server initialised")
        self.register_tools()

    @asynccontextmanager
    async def _lifespan(self, _server: FastMCP) -> AsyncIterator[None]:
        """Prepares the token store before the first request, then releases the GitHub
        HTTP clients and the token store's client when the server shuts down.
        See #315, #357 and #363."""
        try:
            await setup_token_store()
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
                    self.mcp.tool(annotations=annotations, task=task)(method)
        self.mcp.add_provider(SkillsDirectoryProvider(Path(__file__).parent / "skills"))

    def run(self) -> None:
        """Runs the MCP server. Uses HTTP when MCP_ENABLE_REMOTE is true, otherwise stdio."""
        try:
            logger.info("Running MCP Server for GitHub PR Analysis.")
            if MCP_ENABLE_REMOTE:
                self.mcp.run(transport="http", host=HOST, port=PORT, stateless_http=True)
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
