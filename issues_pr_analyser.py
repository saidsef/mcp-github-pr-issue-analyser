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
from github_integration import GitHubIntegration as GI
from ip_integration import IPIntegration as IP

# Set up logging for the application
logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)

class PRIssueAnalyser:
    """
    PRIssueAnalyser is a class that provides an interface for analyzing GitHub Pull Requests (PRs) and managing GitHub Issues, Tags, and Releases, as well as retrieving IP information. It integrates with GitHub and an MCP (Multi-Component Platform) server to expose a set of tools for PR and issue management, and can be run as an MCP server using either SSE or stdio transport.
    Methods
    -------
    __init__():
        Initializes the PRIssueAnalyser instance by setting up GitHub integration, IP processing, and the MCP server. Registers all MCP tools for PR and issue management.
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
        Runs the MCP server for GitHub PR analysis, selecting the transport mechanism based on the 'ENABLE_SSE' environment variable.
            Logs and prints any exceptions that occur during server execution, including a fatal error message and traceback.
    """
    def __init__(self):
        """
        Initializes the main components required for the Issue and PR Analyser.
        This constructor performs the following actions:
        - Initializes the GitHub integration and issue processing components.
        - Sets up the MCP (Multi-Component Platform) server for handling PR analysis, issue creation, and updates.
        - Registers the necessary MCP tools for further processing.
        - Logs the successful initialization of the MCP server.
        """
        
        # Initialize GitHub token
        self.gi = GI()
        self.ip = IP()
        
        # Initialize MCP Server
        self.mcp = FastMCP("GitHub PR Analyse, Issue Create and Update")
        logging.info("MCP Server initialized")
        
        # Register MCP tools
        self._register_tools()
    
    def _register_tools(self):
        """Registers the MCP tools for GitHub PR and issue management."""

        @self.mcp.tool()
        async def get_github_pr_diff(repo_owner: str, repo_name: str, pr_number: int) -> str:
            """
            Fetches the diff of a specific pull request from a GitHub repository.

            Args:
                repo_owner (str): The owner of the GitHub repository.
                repo_name (str): The name of the GitHub repository.
                pr_number (int): The pull request number to fetch the diff for.

            Returns:
                str: The diff of the pull request as a string. Returns an empty string if no changes are found,
                     or an error message string if an exception occurs.

            Error Handling:
                Logs and returns the error message if an exception is raised during the fetch operation.
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
            First use get_pr_diff to fetch the diff of a specific pull request from a GitHub repository.
            Then, if you still need more context use get_pr_content to fetch the content of the pull request.
            Fetches the content of a GitHub pull request for a given repository and PR number.
            Args:
                repo_owner (str): The owner of the GitHub repository.
                repo_name (str): The name of the GitHub repository.
                pr_number (int): The pull request number to fetch.
            Returns:
                Dict[str, Any]: A dictionary containing the pull request information if successful, or an empty dictionary if no information is found or an error occurs.
            Error Handling:
                Logs an error message and prints the traceback to stderr if an exception is raised during the fetch operation. Returns an empty dictionary in case of errors.
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
            Updates the title and description of a GitHub pull request.

            Args:
                repo_owner (str): The owner of the repository.
                repo_name (str): The name of the repository.
                pr_number (int): The pull request number to update.
                new_title (str): The new title for the pull request.
                new_description (str): The new description for the pull request.

            Returns:
                str: A message indicating the result of the update operation. Returns a success message if the update is successful, or an error message if an exception occurs.

            Error Handling:
                Catches and logs any exceptions that occur during the update process. If an error is encountered, the error message is logged and returned.
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
        async def add_github_pr_comment(repo_owner: str, repo_name: str, pr_number: int, comment: str) -> str:
            """
            Adds a comment to a GitHub pull request.

            Args:
                repo_owner (str): The owner of the repository.
                repo_name (str): The name of the repository.
                pr_number (int): The pull request number to add the comment to.
                comment (str): The comment text to be added.

            Returns:
                str: A message indicating the result of the comment addition. Returns a success message if the comment is added successfully, or an error message if an exception occurs.

            Error Handling:
                Catches and logs any exceptions that occur during the comment addition process. If an error is encountered, the error message is logged and returned.
            """
            logging.info(f"Adding comment to PR #{pr_number}")
            try:
                self.gi.add_pr_comment(repo_owner, repo_name, pr_number, comment)
                logging.info(f"Successfully added comment to PR #{pr_number}")
                return f"Successfully added comment to PR #{pr_number}"
            except Exception as e:
                error_msg = f"Error adding comment to PR: {str(e)}"
                logging.error(error_msg)
                traceback.print_exc(file=sys.stderr)
                return error_msg

        @self.mcp.tool()
        async def create_github_issue(repo_owner: str, repo_name: str, title: str, body: str, labels: list[str]) -> str:
            """
            Creates a GitHub issue in the specified repository.
            Then use add_github_pr_comment to link the issue to the PR with comment "Resolves #<issue_number>".
            Args:
                repo_owner (str): The owner of the GitHub repository.
                repo_name (str): The name of the GitHub repository.
                title (str): The title of the issue to be created.
                body (str): The body content of the issue.
                labels (list[str]): A list of labels to assign to the issue.
            Returns:
                str: A message indicating the result of the issue creation. Returns a success message if the issue is created successfully, or an error message if an exception occurs.
            Error Handling:
                Logs and returns an error message if an exception is raised during the issue creation process. The exception traceback is also printed to stderr for debugging purposes.
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
            Updates a GitHub issue with the specified parameters.
            Args:
                repo_owner (str): The owner of the GitHub repository.
                repo_name (str): The name of the GitHub repository.
                issue_number (int): The number of the issue to update.
                title (str): The new title for the issue.
                body (str): The new body content for the issue.
                labels (list[str]): A list of labels to assign to the issue.
                state (str): The state to set for the issue (e.g., 'open', 'closed').
            Returns:
                str: A message indicating whether the issue was updated successfully or an error occurred.
            Error Handling:
                Logs and returns an error message if an exception occurs during the update process.
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
            Creates a GitHub tag for the specified repository.
            Args:
                repo_owner (str): The owner of the GitHub repository.
                repo_name (str): The name of the GitHub repository.
                tag_name (str): The name of the tag to create.
                message (str): The message associated with the tag.
            Returns:
                str: A success message if the tag is created successfully, or an error message if an exception occurs.
            Error Handling:
                Logs and returns an error message if the tag creation fails due to an exception.
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
            Creates a GitHub release for the specified repository and tag.
            Args:
                repo_owner (str): The owner of the GitHub repository.
                repo_name (str): The name of the GitHub repository.
                tag_name (str): The tag name to create the release from.
                release_name (str): The name of the release to be created.
                body (str): The description or body content of the release.
            Returns:
                str: A success message if the release is created successfully, or an error message if an exception occurs.
            Error Handling:
                Logs and returns an error message if the release creation fails due to an exception.
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
            Fetches IPv4 and IPv6 information.
            This function retrieves IPv4 and IPv6 address information using the `self.ip` interface.
            If either IPv4 or IPv6 information is unavailable, it logs the event and returns an empty dictionary.
            If both are available, the IPv6 address is added to the IPv4 info dictionary under the "ipv6" key.
            Returns:
                Dict[str, Any]: A dictionary containing IPv4 information, with the IPv6 address included if available.
                                Returns an empty dictionary if either IPv4 or IPv6 information is missing or if an error occurs.
            Error Handling:
                Logs any exceptions encountered during the fetching process and returns an empty dictionary.
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
        """
        Runs the MCP Server for GitHub PR Analysis using the appropriate transport.
        This method checks the 'ENABLE_SSE' environment variable to determine whether to use
        Server-Sent Events (SSE) or standard input/output (stdio) as the transport mechanism
        for the MCP server. It logs the server startup and handles any exceptions that occur
        during execution, logging errors and printing the traceback to standard error.
        Returns:
            None
        Error Handling:
            Logs any exceptions that occur during server execution and prints the traceback
            to standard error for debugging purposes.
        """
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

def main():
    """
    Main entry point for the PR and Issue Analyzer.
    This function initializes the PRIssueAnalyser and executes its main logic.
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
