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
from ip_integration import IPIntegration as IP

# Set up logging for the application
logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)

class PRIssueAnalyser:
    def __init__(self):
        # Initialize GitHub token
        self.gi = GI()
        self.ip = IP()
        
        # Initialize MCP Server
        self.mcp = FastMCP("GitHub PR Analyse, Issue Create and Update")
        logging.info("MCP Server initialized")
        
        # Register MCP tools
        self._register_tools()
    
    def _register_tools(self):
        """Register MCP tools for GitHub PR analysis."""
        @self.mcp.tool()
        async def get_github_pr_diff(repo_owner: str, repo_name: str, pr_number: int) -> str:
            """
            Fetch the diff/patch of a pull request.
            Args:
                repo_owner: The owner of the GitHub repository
                repo_name: The name of the GitHub repository
                pr_number: The number of the pull request to analyze
            Returns:
                A string containing the diff/patch of the pull request
            """
            logging.info(f"Fetching PR #{pr_number} diff from {repo_owner}/{repo_name}")
            try:
                pr_diff = self.gi.get_pr_diff(repo_owner, repo_name, pr_number)
                if pr_diff is None:
                    logging.info("No changes returned from get_pr_diff")
                    return ""
                logging.info(f"Successfully fetched PR diff")
                return pr_diff
            except Exception as e:
                logging.error(f"Error fetching PR diff: {str(e)}")
                traceback.print_exc(file=sys.stderr)
                return str(e)

        @self.mcp.tool()
        async def get_github_pr_content(repo_owner: str, repo_name: str, pr_number: int) -> Dict[str, Any]:
            """
            Fetch the content of a GitHub pull request.
            If get_github_pr_content() errors or fails, use get_github_pr_diff().
            Args:
                repo_owner: The owner of the GitHub repository
                repo_name: The name of the GitHub repository
                pr_number: The number of the pull request to analyze
            Returns:
                A dictionary containing PR metadata and file changes
            """
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
        async def update_github_pr_description(repo_owner: str, repo_name: str, pr_number: int, new_title: str, new_description: str) -> str:
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
                new_title: The new title for the pull request, one of feat, fix, chore, docs, refactor, style, test
                new_description: The new description for the pull request
            Returns:
                A message indicating the success or failure of the update
            """
            logging.info(f"Updating PR #{pr_number} description")
            try:
                self.gi.update_pr_description(repo_owner, repo_name, pr_number, new_title, new_description)
                logging.info(f"Successfully updated PR #{pr_number} description")
                return f"Successfully updated PR #{pr_number} description"
            except Exception as e:
                error_msg = f"Error updating PR description: {str(e)}"
                logging.error(error_msg)
                traceback.print_exc(file=sys.stderr)
                return error_msg
            
        @self.mcp.tool()
        async def create_github_issue(repo_owner: str, repo_name: str, title: str, body: str, labels: list[str]) -> str:
            """
            Create a GitHub issue.
            Prefix the title with one of chore, feat, or fix.
            In the body indicate if the PR closes, fixes or resolves the issue.
            Args:
                repo_owner: The owner of the GitHub repository
                repo_name: The name of the GitHub repository
                title: The title of the issue, one of feat, fix, chore, docs, refactor, style, test
                body: The body content of the issue, this should include description, why, details, references
                labels: The labels to add to the issue
            Returns:
                A message indicating the success or failure of the issue creation
            """
            logging.info(f"Creating GitHub issue: {title}")
            try:
                issue = self.gi.create_issue(repo_owner, repo_name, title, body, labels)
                logging.info(f"GitHub issue '{title}' created successfully!")
                return f"GitHub issue '{title}' created successfully!"
            except Exception as e:
                error_msg = f"Error creating GitHub issue: {str(e)}"
                logging.error(error_msg)
                traceback.print_exc(file=sys.stderr)
                return error_msg

        @self.mcp.tool()
        async def update_github_issue(repo_owner: str, repo_name: str, issue_number: int, title: str, body: str, labels: list[str], state: str) -> str:
            """
            Update a GitHub issue.
            Prefix the title with one of chore, feat, or fix.
            In the body indicate if the PR closes, fixes or resolves the issue.
            Args:
                repo_owner: The owner of the GitHub repository
                repo_name: The name of the GitHub repository
                issue_number: The number of the issue to update
                title: The new title of the issue, one of feat, fix, chore, docs, refactor, style, test
                body: The body content of the issue, this should include description, why, details, references
                      if there is a PR number, add resolve by PR number
                labels: The labels to add to the issue
                state: The new state of the issue (open/closed)
            Returns:
                A message indicating the success or failure of the issue update
            """
            logging.info(f"Updating GitHub issue #{issue_number}: {title}")
            try:
                issue = self.gi.update_issue(repo_owner, repo_name, issue_number, title, body, labels, state)
                logging.info(f"GitHub issue #{issue_number} updated successfully!")
                return f"GitHub issue #{issue_number} updated successfully!"
            except Exception as e:
                error_msg = f"Error updating GitHub issue: {str(e)}"
                logging.error(error_msg)
                traceback.print_exc(file=sys.stderr)
                return error_msg
            
        @self.mcp.tool()
        async def create_github_tag(repo_owner: str, repo_name: str, tag_name: str, message: str) -> str:
            """
            Create a GitHub tag.
            Args:
                repo_owner: The owner of the GitHub repository
                repo_name: The name of the GitHub repository
                tag_name: The name of the tag to create
                message: The message for the tag
            Returns:
                A message indicating the success or failure of the tag creation
            """
            logging.info(f"Creating GitHub tag: {tag_name}")
            try:
                tag = self.gi.create_tag(repo_owner, repo_name, tag_name, message)
                logging.info(f"GitHub tag '{tag_name}' created successfully!")
                return f"GitHub tag '{tag_name}' created successfully!"
            except Exception as e:
                error_msg = f"Error creating GitHub tag: {str(e)}"
                logging.error(error_msg)
                traceback.print_exc(file=sys.stderr)
                return error_msg
            
        @self.mcp.tool()
        async def create_github_release(repo_owner: str, repo_name: str, tag_name: str, release_name: str, body: str) -> str:
            """
            Create a GitHub release.
            Args:
                repo_owner: The owner of the GitHub repository
                repo_name: The name of the GitHub repository
                tag_name: The name of the tag to create the release from
                release_name: The name of the release
                body: The body of the release
            Returns:
                A message indicating the success or failure of the release creation
            """
            logging.info(f"Creating GitHub release '{release_name}' from tag '{tag_name}'")
            try:
                release = self.gi.create_release(repo_owner, repo_name, tag_name, release_name, body)
                logging.info(f"GitHub release '{release_name}' created successfully!")
                return f"GitHub release '{release_name}' created successfully!"
            except Exception as e:
                error_msg = f"Error creating GitHub release: {str(e)}"
                logging.error(error_msg)
                traceback.print_exc(file=sys.stderr)
                return error_msg
            
        @self.mcp.tool()
        async def get_ipv4_ipv6_info() -> Dict[str, Any]:
            """
            Fetch IPv4 and IPv6 information.
            Returns:
                A dictionary containing the IPv4 and IPv6 information
            """
            logging.info(f"Fetching IPv4 and IPv6 information")
            try:
                ipv4_info = self.ip.get_ipv4_info()
                ipv6_info = self.ip.get_ipv6_info()
                if ipv4_info is None:
                    logging.info("No changes returned from getv4_ip_info")
                    return {}
                if ipv6_info is None:
                    logging.info("No changes returned from getv6_ip_info")
                    return {}
                if ipv4_info and ipv6_info:
                    ipv4_info["ipv6"] = ipv6_info["ip"]
                logging.info(f"Successfully fetched IP information")
                return ipv4_info
            except Exception as e:
                logging.error(f"Error fetching IP info: {str(e)}")
                traceback.print_exc(file=sys.stderr)
                return {}

    def run(self):
        """Run the MCP server."""
        MCP_ENABLE_SSE = getenv("ENABLE_SSE", None)
        try:
            logging.info("Running MCP Server for GitHub PR Analysis.")
            if MCP_ENABLE_SSE:
                self.mcp.run(transport='sse')
            else:
                self.mcp.run(transport='stdio')
        except Exception as e:
            logging.error(f"Fatal Error in MCP Server: {str(e)}")
            traceback.print_exc(file=sys.stderr)


if __name__ == "__main__":
    review = PRIssueAnalyser()
    review.run()
