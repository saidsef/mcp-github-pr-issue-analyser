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
from typing import Any, Dict, List, Literal
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
        Runs the MCP server for GitHub PR analysis, selecting the transport mechanism based on the 'ENABLE_SSE' environment variable.
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
        self._register_tools()

    def _register_tools(self):
        """Registers the MCP tools for GitHub PR and issue management."""

        @self.mcp.tool()
        async def get_github_pr_diff(repo_owner: str, repo_name: str, pr_number: int) -> dict[str, Any]:
            """
            Fetches the diff of a specific pull request from a GitHub repository.
            Args:
                repo_owner (str): The owner of the GitHub repository.
                repo_name (str): The name of the GitHub repository.
                pr_number (int): The pull request number to fetch the diff for.
            Returns:
                dict[str, Any]: A dictionary containing the pull request diff. Returns a 'No changes' string if no changes are found,
                    or an error message string if an exception occurs.
            """
            logging.info(f"Fetching PR #{pr_number} diff from {repo_owner}/{repo_name}")
            try:
                pr_diff = self.gi.get_pr_diff(repo_owner, repo_name, pr_number)
                if pr_diff is None:
                    no_changes = "No changes returned from get_pr_diff"
                    logging.info({"status": "info", "message": no_changes})
                    return {"status": "info", "message": no_changes}
                logging.info({"status": "success", "message": f"Successfully fetched PR #{pr_number} diff"})
                return {"status": "success", "message": pr_diff}
            except Exception as e:
                logging.error({"status": "error", "message": str(e)})
                traceback.print_exc(file=sys.stderr)
                return {"status": "error", "message": str(e)}

        @self.mcp.tool()
        async def get_github_pr_content(repo_owner: str, repo_name: str, pr_number: int) -> dict[str, Any]:
            """
            Fetches the content of a specific pull request from a GitHub repository.
            - Use only get_github_pr_diff to fetch the content of a specific pull request from a GitHub repository.
            - Use get_github_pr_content if there is no diff available.
            Args:
                repo_owner (str): The owner of the GitHub repository.
                repo_name (str): The name of the GitHub repository.
                pr_number (int): The pull request number to fetch the content for.
            Returns:
                dict[str, Any]: A dictionary containing the pull request content. Returns a 'No changes' string if no changes are found,
                    or an error message string if an exception occurs.
            """
            logging.info(f"Fetching PR #{pr_number} from {repo_owner}/{repo_name}")
            try:
                pr_info = self.gi.get_pr_content(repo_owner, repo_name, pr_number)
                if pr_info is None:
                    no_changes = "No changes returned from get_pr_content"
                    logging.info({"status": "error", "message": no_changes})
                    return {"status": "error", "message": no_changes}
                logging.info({"status": "success", "message": f"Successfully fetched PR #{pr_number} content"})
                return pr_info
            except Exception as e:
                logging.error({"status": "error", "message": str(e)})
                traceback.print_exc(file=sys.stderr)
                return {"status": "error", "message": str(e)}

        @self.mcp.tool()
        async def update_github_pr_description(repo_owner: str, repo_name: str, pr_number: int, new_title: str, new_description: str) -> dict[str, Any]:
            """
            Updates the title and description of a specific pull request on GitHub.
            - title should be one of chore, fix, bug, feat, docs etc.
            Args:
                repo_owner (str): The owner of the GitHub repository.
                repo_name (str): The name of the GitHub repository.
                pr_number (int): The pull request number to update.
                new_title (str): The new title for the pull request.
                new_description (str): The new description for the pull request.
            Returns:
                dict[str, Any]: A dictionary containing the status and message of the update operation.
                    Returns a success message if the update is successful, or an error message if an exception occurs
            """
            logging.info(f"Updating PR #{pr_number} description")
            try:
                self.gi.update_pr_description(repo_owner, repo_name, pr_number, new_title, new_description)
                logging.info({"status": "success", "message": f"Successfully updated PR #{pr_number} description"})
                return {"status": "success", "message": f"Successfully updated PR #{pr_number} description"}
            except Exception as e:
                error_msg = f"Error updating PR description: {str(e)}"
                logging.error(error_msg)
                traceback.print_exc(file=sys.stderr)
                return {"status": "error", "message": error_msg}

        @self.mcp.tool()
        async def add_github_pr_inline_comment(repo_owner: str, repo_name: str, pr_number: int, path: str, line: int, comment_body: str) -> dict[str, Any]:
            """
            Adds an inline review comment to a specific line in a pull request on GitHub.
            Args:
                repo_owner (str): The owner of the GitHub repository.
                repo_name (str): The name of the GitHub repository.
                pr_number (int): The pull request number to add the comment to.
                path (str): The file path in the pull request where the comment should be added.
                line (int): The line number in the file where the comment should be added.
                comment_body (str): The content of the comment to be added.
            Returns:
                dict[str, Any]: A dictionary containing the status and message of the comment addition.
                    Returns a success message if the comment is added successfully, or an error message if an exception occurs.
            """
            logging.info(f"Adding inline review comment to PR #{pr_number}")
            try:
                self.gi.add_inline_pr_comment(repo_owner, repo_name, pr_number, path, line, comment_body)
                logging.info({"status": "success", "message": f"Successfully added inline review comment to PR #{pr_number}"})
                return {"status": "success", "message": f"Successfully added inline review comment to PR #{pr_number}"}
            except Exception as e:
                error_msg = f"Error adding inline review comment to PR: {str(e)}"
                logging.error(error_msg)
                traceback.print_exc(file=sys.stderr)
                return {"status": "error", "message": error_msg}

        @self.mcp.tool()
        async def add_github_pr_comment(repo_owner: str, repo_name: str, pr_number: int, comment: str) -> dict[str, Any]:
            """
            Adds a markdown comment to a specific pull request on GitHub.
            Args:
                repo_owner (str): The owner of the GitHub repository.
                repo_name (str): The name of the GitHub repository.
                pr_number (int): The pull request number to add the comment to.
                comment (str): The content of the comment to be added.
            Returns:
                dict[str, Any]: A dictionary containing the status and message of the comment addition.
                    Returns a success message if the comment is added successfully, or an error message if an exception occurs.
            """
            logging.info(f"Adding comment to PR #{pr_number}")
            try:
                self.gi.add_pr_comments(repo_owner, repo_name, pr_number, comment)
                logging.info({"status": "success", "message": f"Successfully added comment to PR #{pr_number}"})
                return {"status": "success", "message": f"Successfully added comment to PR #{pr_number}"}
            except Exception as e:
                error_msg = f"Error adding comment to PR: {str(e)}"
                logging.error(error_msg)
                traceback.print_exc(file=sys.stderr)
                return {"status": "error", "message": error_msg}

        @self.mcp.tool()
        async def list_github_issues_prs(repo_owner: str, issue: Literal['pr', 'issue'] = 'pr', filtering: Literal['user', 'owner', 'involves'] = 'involves') -> dict[str, Any]:
            """
            Lists open issues or pull requests for a specified GitHub repository.
            - Present the issues or pull requests in a markdown table format.
            - Add index column to the table, and make the title link to the issue or pull request.
            Args:
                repo_owner (str): The owner of the GitHub repository.
                issue (Literal['pr', 'issue']): The type of item to list, either 'pr' for pull requests or 'issue' for issues. Defaults to 'pr'.
                filtering (Literal['user', 'owner', 'involves']): The filtering criteria for the search. Defaults to 'involves'.
            Returns:
                dict[str, Any]: A dictionary containing the list of open issues or pull requests.
                    Returns an error message if an exception occurs during the listing process.
            """
            logging.info({"status": "info", "message": f"Listing open {issue} for {repo_owner}"})
            try:
                open_issues_prs = self.gi.list_open_issues_prs(repo_owner, issue, filtering)
                return open_issues_prs
            except Exception as e:
                error_msg = f"Error listing {issue} for {repo_owner}: {str(e)}"
                logging.error(error_msg)
                traceback.print_exc(file=sys.stderr)
                return {"status": "error", "message": error_msg}

        @self.mcp.tool()
        async def create_github_issue(repo_owner: str, repo_name: str, title: str, body: str, labels: list[str]) -> dict[str, Any]:
            """
            Creates a GitHub issue with the specified parameters.
            - title should be one of chore, fix, bug, feat, docs etc.
            Args:
                repo_owner (str): The owner of the GitHub repository.
                repo_name (str): The name of the GitHub repository.
                title (str): The title of the issue.
                body (str): The body content of the issue.
                labels (list[str]): A list of labels to assign to the issue.
            Returns:
                dict[str, Any]: A dictionary containing the status and message of the issue creation.
                    Returns a success message if the issue is created successfully, or an error message if an exception occurs.
            """
            logging.info(f"Creating GitHub issue: {title}")
            try:
                issue = self.gi.create_issue(repo_owner, repo_name, title, body, labels)
                logging.info({"status": "success", "message": f"GitHub issue #{issue} created successfully!"})
                return {"status": "success", "message": f"GitHub issue number #{issue} created successfully!"}
            except Exception as e:
                error_msg = f"Error creating GitHub issue: {str(e)}"
                logging.error(error_msg)
                traceback.print_exc(file=sys.stderr)
                return {"status": "error", "message": error_msg}

        @self.mcp.tool()
        async def update_github_issue(repo_owner: str, repo_name: str, issue_number: int, title: str, body: str, labels: list[str], state: str) -> dict[str, Any]:
            """
            Updates a GitHub issue with the specified parameters.
            - title should be one of chore, fix, bug, feat, docs etc.
            Args:
                repo_owner (str): The owner of the GitHub repository.
                repo_name (str): The name of the GitHub repository.
                issue_number (int): The issue number to update.
                title (str): The new title for the issue.
                body (str): The new body content of the issue.
                labels (list[str]): A list of labels to assign to the issue.
                state (str): The new state of the issue, either 'open' or 'closed'.
            Returns:
                dict[str, Any]: A dictionary containing the status and message of the issue update.
                    Returns a success message if the issue is updated successfully, or an error message if an exception occurs.
            """
            logging.info(f"Updating GitHub issue #{issue_number}: {title}")
            try:
                issue = self.gi.update_issue(repo_owner, repo_name, issue_number, title, body, labels, state)
                logging.info({"status": "success", "message": f"GitHub issue #{issue} updated successfully!"})
                return {"status": "success", "message": f"GitHub issue number #{issue} updated successfully!"}
            except Exception as e:
                error_msg = f"Error updating GitHub issue: {str(e)}"
                logging.error(error_msg)
                traceback.print_exc(file=sys.stderr)
                return {"status": "error", "message": error_msg}

        @self.mcp.tool()
        async def create_github_tag(repo_owner: str, repo_name: str, tag_name: str, message: str) -> dict[str, Any]:
            """
            Creates a GitHub tag for the specified repository.
            - Add a markdown comment to the tag.
            - The tag name should be descriptive and meaningful.
            - The message should contain detailed information about the tag, including changes, features, and fixes
            Args:
                repo_owner (str): The owner of the GitHub repository.
                repo_name (str): The name of the GitHub repository.
                tag_name (str): The name of the tag to be created.
                message (str): The message or description for the tag.
            Returns:
                dict[str, Any]: A dictionary containing the status and message of the tag creation.
                    Returns a success message if the tag is created successfully, or an error message if an exception occurs.
            """
            logging.info(f"Creating GitHub tag: {tag_name}")
            try:
                tag = self.gi.create_tag(repo_owner, repo_name, tag_name, message)
                logging.info({"status": "success", "message": f"GitHub tag '{tag}' created successfully!"})
                return {"status": "success", "message": f"GitHub tag '{tag}' created successfully!"}
            except Exception as e:
                error_msg = f"Error creating GitHub tag: {str(e)}"
                logging.error(error_msg)
                traceback.print_exc(file=sys.stderr)
                return {"status": "error", "message": error_msg}

        @self.mcp.tool()
        async def create_github_release(repo_owner: str, repo_name: str, tag_name: str, release_name: str, body: str) -> dict[str, Any]:
            """
            Creates a GitHub release from a specified tag.
            - Add a markdown comment to the release.
            - The release name should be descriptive and meaningful.
            - The body should contain detailed information about the release, including changes, features, and fixes
            Args:
                repo_owner (str): The owner of the GitHub repository.
                repo_name (str): The name of the GitHub repository.
                tag_name (str): The name of the tag to create the release from.
                release_name (str): The name of the release.
                body (str): The body content of the release.
            Returns:
                dict[str, Any]: A dictionary containing the status and message of the release creation.
                    Returns a success message if the release is created successfully, or an error message if an exception occurs.
            """
            logging.info(f"Creating GitHub release '{release_name}' from tag '{tag_name}'")
            try:
                release = self.gi.create_release(repo_owner, repo_name, tag_name, release_name, body)
                logging.info({"status": "success", "message": f"GitHub release '{release}' created successfully!"})
                return {"status": "success", "message": f"GitHub release '{release}' created successfully!"}
            except Exception as e:
                error_msg = f"Error creating GitHub release: {str(e)}"
                logging.error(error_msg)
                traceback.print_exc(file=sys.stderr)
                return {"status": "error", "message": error_msg}

        @self.mcp.tool()
        async def get_ipv4_ipv6_info() -> dict[str, Any]:
            """
            Fetches IPv4 and IPv6 information.
            This function retrieves IPv4 and IPv6 address information using the `self.ip` interface.
            If either IPv4 or IPv6 information is unavailable, it logs the event and returns an empty dictionary.
            If both are available, the IPv6 address is added to the IPv4 info dictionary under the "ipv6" key.
            Returns:
                dict[str, Any]: A dictionary containing IPv4 information, with the IPv6 address included if available.
                    Returns an empty dictionary if either IPv4 or IPv6 information is missing or if an error occurs.
            Error Handling:
                Logs any exceptions encountered during the fetching process and returns an empty dictionary.
            """
            logging.info({"status": "info", "message": "Fetching IPv4 and IPv6 information"})
            try:
                ipv4_info = self.ip.get_ipv4_info()
                try:
                    ipv6_info = self.ip.get_ipv6_info()
                except Exception as e:
                    logging.error(f"Error fetching IPv6 info: {e}")
                    ipv6_info = None
                if not isinstance(ipv4_info, dict) or ipv4_info is None:
                    logging.info({"status": "error", "message": "No changes returned from getv4_ip_info"})
                    return {"status": "No IPv4 information available"}
                if not isinstance(ipv6_info, dict) or ipv6_info is None or "ip" not in ipv6_info:
                    logging.info({"status": "error", "message": "No changes returned from getv6_ip_info"})
                    return {"status": "No IPv6 information available"}
                else:
                    ipv4_info["ipv6"] = ipv6_info.get("ip", "No IPv6 address found")
                logging.info({"status": "success", "message": "Successfully fetched IPv4 and IPv6 information"})
                return ipv4_info
            except Exception as e:
                error_message = f"Error fetching IP info: {str(e)}"
                logging.error({"status": "error", "message": error_message})
                traceback.print_exc(file=sys.stderr)
                return {"status": "error", "message": error_message}

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
