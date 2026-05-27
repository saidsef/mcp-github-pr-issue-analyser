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
from os import getenv
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.apps.choice import Choice
from fastmcp.apps.generative import GenerativeUI
from fastmcp.server.providers.skills import SkillsDirectoryProvider

from .auth import (
    GITHUB_OAUTH_BASE_URL,
    GITHUB_OAUTH_CLIENT_ID,
    GITHUB_OAUTH_CLIENT_SECRET,
)
from .github_integration import GitHubIntegration as GI

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)

PORT = int(getenv("PORT", 8081))
HOST = getenv("HOST", "localhost")
MCP_ENABLE_REMOTE = getenv("MCP_ENABLE_REMOTE", False)

_MCP_INSTRUCTIONS = """
# GitHub PR and Issue Analyser

This server provides tools to analyse GitHub Pull Requests (PRs) and manage GitHub Issues, Tags, and Releases.

## Features
- Fetch PR diffs, content, linked issues, and CI status
- Update PR descriptions and post inline review comments
- Create and update GitHub issues
- Create tags and releases

## Prerequisites
1. GitHub integration is preconfigured
2. Appropriate permissions and GitHub API key is set

## Best Practices
- Use all tools available for a comprehensive understanding of the PR and issue landscape.
- Use get_pr_diff (preferred) and get_pr_content for detailed PR analysis
- Use single dashes instead of em-dashes in PR descriptions and issue bodies
- Use update_pr_description to keep PRs up-to-date
- Use create_issue and update_issue for issue management
- Use create_tag and create_release for release management
- Always maintain a professional, clear and concise tone

## Skills
Workflow guidance is available as MCP resources under the skill:// URI scheme:
- skill://pr-analysis/SKILL.md -- fetch and analyse PR diffs and metadata
- skill://pr-review/SKILL.md -- post inline comments and submit review decisions
- skill://pr-management/SKILL.md -- create, update, assign, and merge PRs
- skill://issue-management/SKILL.md -- create, update, and list issues
- skill://release-management/SKILL.md -- tag commits and publish releases
- skill://user-activity/SKILL.md -- look up user profiles and contribution history
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
        )
        self.mcp.add_provider(Choice(name="github_pr_issue_analyser"))
        self.mcp.add_provider(GenerativeUI(tool_name="github_pr_issue_analyser_ui"))
        logger.info("MCP Server initialised")
        self.register_tools()

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
        """Runs the MCP server. Uses HTTP if MCP_ENABLE_REMOTE is set, otherwise stdio."""
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
    """Main entry point."""
    try:
        review = PRIssueAnalyser()
        review.run()
    except Exception as e:
        logger.error(f"Error running main analyzer: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
