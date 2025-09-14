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

import sys
import logging
import inspect
import traceback
from os import getenv
from mcp.server.fastmcp import FastMCP
from .github_integration import GitHubIntegration as GI
from .ip_integration import IPIntegration as IP

# Set up logging for the application
logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)

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

        self.gi = GI() # Initialize GitHub token
        self.ip = IP()

        # Initialize MCP Server
        self.mcp = FastMCP(
            name="GitHub PR and Issue Analyser",
            instructions="""
              You are a GitHub PR and Issue Analyser. You can fetch PR diffs, update PR descriptions, 
              create and update issues, create tags and releases, and fetch IP information.
            """,
        )
        logging.info("MCP Server initialized")

        # Register MCP tools
        for name, method in inspect.getmembers(self.gi):
            if inspect.isfunction(method) and not name.startswith("_"):
                self.mcp.add_tool(method)

    def run(self) -> None:
        """
        Runs the MCP Server for GitHub PR Analysis using the appropriate transport.
        This method checks the 'MCP_ENABLE_REMOTE' environment variable to determine whether to use
        Server-Sent Events (SSE) or standard input/output (stdio) as the transport mechanism
        for the MCP server. It logs the server startup and handles any exceptions that occur
        during execution, logging errors and printing the traceback to standard error.
        Returns:
            None
        Error Handling:
            Logs any exceptions that occur during server execution and prints the traceback
            to standard error for debugging purposes.
        """
        MCP_ENABLE_REMOTE = getenv("MCP_ENABLE_REMOTE", None)
        try:
            logging.info("Running MCP Server for GitHub PR Analysis.")
            if MCP_ENABLE_REMOTE:
                self.mcp.run(transport='sse')
            else:
                self.mcp.run(transport='stdio')
        except Exception as e:
            logging.error(f"Fatal Error in MCP Server: {str(e)}")
            traceback.print_exc(file=sys.stderr)

def main():
    """
    Main entry point for the PR and Issue Analyzer.
    This function Initialises the PRIssueAnalyser and executes its main logic.
    If an exception occurs during execution, it logs the error, prints the traceback,
    and exits the program with a non-zero status code.
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
