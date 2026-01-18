#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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

import sys
import logging
import inspect
import traceback
from os import getenv
from typing import Any
from mcp.server.fastmcp import FastMCP
from .github_integration import GitHubIntegration as GI
from .ip_integration import IPIntegration as IP

# Set up logging for the application
logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)

PORT = int(getenv("PORT", 8081))
HOST = getenv("HOST", "localhost")

class PRIssueAnalyser:
    """
    PRIssueAnalyser is a class that provides an interface for analyzing GitHub Pull Requests (PRs) and managing GitHub Issues, Tags, and Releases, as well as retrieving IP information. It integrates with GitHub and an MCP (Multi-Component Platform) server to expose a set of tools for PR and issue management, and can be run as an MCP server using either SSE or stdio transport.
    Methods
    -------
    __init__():
        Initialises the PRIssueAnalyser instance by setting up GitHub integration, IP processing, and the MCP server. Registers all MCP tools for PR and issue management.
            Any exceptions during initialization are not explicitly handled and will propagate.
    _register_tools():
        Registers a set of asynchronous MCP tools for:
            - Fetching PR diffs and content
            - Updating PR descriptions
            - Creating and updating GitHub issues
            - Creating tags and releases
            - Fetching IPv4 and IPv6 information
            Each tool handles its own exceptions, logging errors and returning appropriate error messages or empty results.
    run():
        Runs the MCP server for GitHub PR analysis, selecting the transport mechanism based on the 'MCP_ENABLE_REMOTE' environment variable.
            Logs and prints any exceptions that occur during server execution, including a fatal error message and traceback.
    """
    def __init__(self):
        """
        Initialises the main components required for the Issue and PR Analyser.
        This constructor performs the following actions:
        - Initialises the GitHub integration and issue processing components.
        - Sets up the MCP (Multi-Component Platform) server for handling PR analysis, issue creation, and updates.
        - Registers the necessary MCP tools for further processing.
        - Logs the successful initialization of the MCP server.
        """

        self.gi = GI()
        self.ip = IP()

        # Initialize MCP Server
        self.mcp = FastMCP(
            name="GitHub PR and Issue Analyser",
            instructions="""
          # GitHub PR and Issue Analyser

          This server provides tools to analyse GitHub Pull Requests (PRs) and manage GitHub Issues, Tags, and Releases, as well as retrieve IP information.

          ## Features
          - Fetch PR diffs and content
          - Update PR descriptions
          - Create and update GitHub issues
          - Create tags and releases
          - Fetch IPv4 and IPv6 information

          ## Prerequisites
          1. GitHub and IP integrations are preconfigured
          2. Appropriate permissions and GitHub API key is set

          ## Best Practices
          - Use all tools available for a comprehensive understanding of the PR and issue landscape.
          - Use get_pr_diff (preferred) and get_pr_content for detailed PR analysis
          - Use update_pr_description to keep PRs up-to-date
          - Use create_issue and update_issue for issue management
          - Use create_tag and create_release for release management
          - Use get_ipv4_info and get_ipv6_info for IP information
          - Always maintain a professional, clear and concise tone
            """,
            host=HOST,
            port=PORT,
        )
        logging.info("MCP Server initialized")

        self._register_tools()

    def _register_tools(self):
        self.register_tools(self.gi)
        self.register_tools(self.ip)

    def register_tools(self, methods: Any = None) -> None:
        for name, method in inspect.getmembers(methods):
            if (inspect.isfunction(method) or inspect.ismethod(method)) and not name.startswith("_"):
                self.mcp.add_tool(method)

    def run(self):
        """
        Runs the MCP server for GitHub PR analysis.
        Uses HTTP transport if MCP_ENABLE_REMOTE is set, otherwise uses stdio.
        """
        MCP_ENABLE_REMOTE = getenv("MCP_ENABLE_REMOTE", False)
        try:
            logging.info("Running MCP Server for GitHub PR Analysis.")
            if MCP_ENABLE_REMOTE:
                self.mcp.run(transport='streamable-http')
            else:
                self.mcp.run(transport='stdio')
        except Exception as e:
            logging.error(f"Fatal Error in MCP Server: {str(e)}")
            traceback.print_exc(file=sys.stderr)

def main():
    """
    Main function to run the PRIssueAnalyser.
    This function initializes the PRIssueAnalyser and starts the MCP server.
    Returns:
        None
    Error Handling:
        Catches all exceptions, logs the error message, prints the traceback,
        and terminates the program with exit code 1.
    """
    try:
        review = PRIssueAnalyser()
        review.run()
    except Exception as e:
        logging.error(f"Error running main analyzer: {str(e)}")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
