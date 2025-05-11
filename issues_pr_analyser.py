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
import traceback
from os import getenv
from typing import Any, List, Dict
from mcp.server.fastmcp import FastMCP
from github_integration import GutHubIntegration as GI

# Set up logging for the application
logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)

class PRIssueAnalyser:
    def __init__(self):
        # Initialize GitHub token
        self.gi = GI()
        
        # Initialize MCP Server
        self.mcp = FastMCP("GitHub PR Analyse, Issue Create and Update")
        logging.info("MCP Server initialized")
        
        # Register MCP tools
        self._register_tools()
    
    def _register_tools(self):
        """Register MCP tools for GitHub PR analysis."""
        @self.mcp.tool()
        async def fetch_pr(repo_owner: str, repo_name: str, pr_number: int) -> Dict[str, Any]:
            """Fetch changes from a GitHub pull request."""
            logging.info(f"Fetching PR #{pr_number} from {repo_owner}/{repo_name}")
            try:
                pr_info = self.gi.get_pr_content(repo_owner, repo_name, pr_number)
                if pr_info is None:
                    logging.info("No changes returned from get_pr_content")
                    return {}
                logging.info(f"Successfully fetched PR information")
                return pr_info
            except Exception as e:
                logging.error(f"Error fetching PR: {str(e)}")
                traceback.print_exc(file=sys.stderr)
                return {}
            
        @self.mcp.tool()
        async def update_pr_description(repo_owner: str, repo_name: str, pr_number: int, new_description: str) -> str:
            """
            Update the description of a GitHub pull request.
            List files changed in the PR and add them to the description.
            Where possible include What, Why and How if you have enough information in the PR.
            eample:
            ## What? 
            Added support for authentication. #123
            ## Why?
            These changes complete the user login and account creation experience, see #123 for full details
            ## How?
            This includes a migration, model and controller for user authentication..
            Args:
                repo_owner: The owner of the GitHub repository
                repo_name: The name of the GitHub repository
                pr_number: The number of the pull request to update
                new_description: The new description for the pull request
            Returns:
                A message indicating the success or failure of the update
            """
            logging.info(f"Updating PR #{pr_number} description")
            try:
                self.gi.update_pr_description(repo_owner, repo_name, pr_number, new_description)
                logging.info(f"Successfully updated PR #{pr_number} description")
                return f"Successfully updated PR #{pr_number} description"
            except Exception as e:
                error_msg = f"Error updating PR description: {str(e)}"
                logging.error(error_msg)
                traceback.print_exc(file=sys.stderr)
                return error_msg
            
        @self.mcp.tool()
        async def create_github_issue(repo_owner: str, repo_name: str, title: str, body: str) -> str:
            """
            Create a GitHub issue.
            Prefix the title with one of chore, feat, or fix.
            In the body indicate if the PR closes, fixes or resolves the issue.
            Args:
                repo_owner: The owner of the GitHub repository
                repo_name: The name of the GitHub repository
                title: The title of the issue
                body: The body of the issue
            Returns:
                A message indicating the success or failure of the issue creation
            """
            logging.info(f"Creating GitHub issue: {title}")
            try:
                issue = self.gi.create_issue(repo_owner, repo_name, title, body)
                logging.info(f"GitHub issue '{title}' created successfully!")
                return f"GitHub issue '{title}' created successfully!"
            except Exception as e:
                error_msg = f"Error creating GitHub issue: {str(e)}"
                logging.error(error_msg)
                traceback.print_exc(file=sys.stderr)
                return error_msg

        @self.mcp.tool()
        async def update_github_issue(repo_owner: str, repo_name: str, issue_number: int, title: str, body: str) -> str:
            """
            Update a GitHub issue.
            Prefix the title with one of chore, feat, or fix.
            In the body indicate if the PR closes, fixes or resolves the issue.
            Args:
                repo_owner: The owner of the GitHub repository
                repo_name: The name of the GitHub repository
                issue_number: The number of the issue to update
                title: The new title of the issue
                body: The new body of the issue
            Returns:
                A message indicating the success or failure of the issue update
            """
            logging.info(f"Updating GitHub issue #{issue_number}: {title}")
            try:
                issue = self.gi.update_issue(repo_owner, repo_name, issue_number, title, body)
                logging.info(f"GitHub issue #{issue_number} updated successfully!")
                return f"GitHub issue #{issue_number} updated successfully!"
            except Exception as e:
                error_msg = f"Error updating GitHub issue: {str(e)}"
                logging.error(error_msg)
                traceback.print_exc(file=sys.stderr)
                return error_msg

    def run_local(self):
        """Run the MCP server."""
        try:
            logging.info("Running Local MCP Server for GitHub PR Analysis and Issue Create.")
            self.mcp.run(transport='stdio')
        except Exception as e:
            logging.error(f"Fatal Error in MCP Server: {str(e)}")
            traceback.print_exc(file=sys.stderr)

    def run_remote(self):
        """Run the MCP server remotely."""
        try:
            logging.info("Running Remote MCP Server for GitHub PR Analysis and Issue Create.")
            self.mcp.run(transport='sse')
        except Exception as e:
            logging.error(f"Fatal Error in MCP Server: {str(e)}")
            traceback.print_exc(file=sys.stderr)

if __name__ == "__main__":
    MCP_ENABLE_SSE = getenv("ENABLE_SSE")
    review = PRIssueAnalyser()
    if MCP_ENABLE_SSE:
        review.run_remote()
    else:
        review.run_local()
