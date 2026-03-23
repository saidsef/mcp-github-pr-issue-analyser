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

import logging
import requests
import traceback
from os import getenv
from typing import Annotated, Any, Dict, Optional, Literal, TypedDict

from .exceptions import (
    GitHubAPIError,
    GitHubAuthError,
    GitHubRateLimitError,
    GitHubNotFoundError,
    GitHubValidationError,
)
from .graphql_client import GraphQLClient
from .graphql_queries import SEARCH_USER_QUERY, USER_CONTRIBUTIONS_QUERY


# TypedDict definitions for common return types
class PRContent(TypedDict):
    """Structure of PR content data."""

    title: str
    description: str | None
    author: str
    created_at: str
    updated_at: str
    state: str


class CommentData(TypedDict):
    """Structure of comment data from GitHub API."""

    id: int
    body: str
    user: dict[str, Any]
    created_at: str
    updated_at: str


class IssueData(TypedDict):
    """Structure of issue data from GitHub API."""

    id: int
    number: int
    title: str
    body: str | None
    state: str
    user: dict[str, Any]
    created_at: str
    updated_at: str
    labels: list[dict[str, Any]]


class ActivityData(TypedDict):
    """Structure of user activity data."""

    commits: list[dict[str, Any]]
    prs: list[dict[str, Any]]
    issues: list[dict[str, Any]]


class ActivityResult(TypedDict):
    """Structure of activity query result."""

    status: str
    activity: ActivityData


class UserSearchResult(TypedDict):
    """Structure of user search result."""

    login: str
    name: Optional[str]
    email: Optional[str]
    company: Optional[str]
    location: Optional[str]
    bio: Optional[str]
    url: str
    avatar_url: Optional[str]
    created_at: str
    updated_at: str
    followers: int
    following: int
    public_repos: int
    recent_repos: list[dict[str, Any]]
    organizations: list[dict[str, Any]]


class UserActivityResult(TypedDict):
    """Structure of user activity query result from GraphQL."""

    username: str
    date_range: Optional[dict[str, str]]
    total_contributions: dict[str, int]
    commits: list[dict[str, Any]]
    pull_requests: list[dict[str, Any]]
    issues: list[dict[str, Any]]
    reviews: list[dict[str, Any]]


GITHUB_TOKEN = getenv("GITHUB_TOKEN")
TIMEOUT = int(getenv("GITHUB_API_TIMEOUT", "5"))  # seconds, configurable via env

# Set up logging for the application
logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)


class GitHubIntegration:
    def __init__(self):
        """
        Initializes the GitHubIntegration instance by setting up the GitHub token from environment variables.
        Returns:
            None
        Error Handling:
            Raises ValueError if the GITHUB_TOKEN environment variable is not set.
        """
        self.github_token = GITHUB_TOKEN
        self.base_url = "https://api.github.com"
        if not self.github_token:
            raise ValueError("Missing GitHub GITHUB_TOKEN in environment variables")

        # Initialize GraphQL client
        self.graphql = GraphQLClient(self.github_token, timeout=TIMEOUT)

        logging.info("GitHub Integration Initialised")

    def _handle_response_error(self, response: requests.Response, context: str = ""):
        """
        Handle HTTP errors from GitHub API with specific exceptions.

        Args:
            response: The requests Response object
            context: Additional context for error messages

        Raises:
            GitHubAuthError: For 401 responses
            GitHubRateLimitError: For 403 rate limit responses
            GitHubNotFoundError: For 404 responses
            GitHubValidationError: For 422 responses
            GitHubAPIError: For other error responses
        """
        try:
            response_body = response.json()
        except Exception:
            response_body = None

        message = "GitHub API error"
        if context:
            message = f"{message} ({context})"

        if response.status_code == 401:
            raise GitHubAuthError(
                "Authentication failed. Check your GitHub token.",
                response_body=response_body,
            )
        elif response.status_code == 403:
            # Check if it's a rate limit error
            error_text = response.text.lower()
            if "rate limit" in error_text or "api rate limit" in error_text:
                reset_header = response.headers.get("X-RateLimit-Reset")
                raise GitHubRateLimitError(
                    "GitHub API rate limit exceeded. Please wait before making more requests.",
                    response_body=response_body,
                    reset_timestamp=int(reset_header) if reset_header else None,
                )
            raise GitHubAPIError(
                "Permission denied. Check your token permissions.",
                status_code=403,
                response_body=response_body,
            )
        elif response.status_code == 404:
            raise GitHubNotFoundError(
                f"{context}: Resource not found" if context else "Resource not found",
                response_body=response_body,
            )
        elif response.status_code == 422:
            raise GitHubValidationError(
                "Validation failed. Check your input data.", response_body=response_body
            )
        else:
            raise GitHubAPIError(
                f"GitHub API error: {response.status_code} - {response.reason}",
                status_code=response.status_code,
                response_body=response_body,
            )

    def _get_headers(self):
        """
        Constructs the HTTP headers required for GitHub API requests, including the authorization token.
        Returns:
            dict: A dictionary containing the required HTTP headers.
        Error Handling:
            Raises ValueError if the GitHub token is not set.
        """
        if not self.github_token:
            raise ValueError("GitHub token is missing for API requests")
        headers = {
            "Authorization": f"token {self.github_token}",
            "Accept": "application/vnd.github.v3+json",
        }
        return headers

    def _get_pr_url(self, repo_owner: str, repo_name: str, pr_number: int) -> str:
        """
        Construct the GitHub API URL for a specific pull request.
        Args:
            repo_owner (str): The owner of the GitHub repository.
            repo_name (str): The name of the GitHub repository.
            pr_number (int): The pull request number.
        Returns:
            str: The formatted GitHub API URL for the specified pull request.
        Raises:
            ValueError: If any of the arguments are empty or if pr_number is not a positive integer.
        """
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}"
        return url

    def get_pr_diff(self, repo_owner: str, repo_name: str, pr_number: int) -> str:
        """
        Fetches the diff/patch of a specific pull request from a GitHub repository.
        Args:
            repo_owner (str): The owner of the GitHub repository.
            repo_name (str): The name of the GitHub repository.
            pr_number (int): The pull request number.
        Returns:
            str: The raw patch/diff text of the pull request if successful.
        Raises:
            GitHubNotFoundError: If the PR is not found.
            GitHubAPIError: If the API request fails.
        """
        logging.info(f"Fetching PR diff for {repo_owner}/{repo_name}#{pr_number}")

        try:
            response = requests.get(
                f"https://patch-diff.githubusercontent.com/raw/{repo_owner}/{repo_name}/pull/{pr_number}.patch",
                headers=self._get_headers(),
                timeout=TIMEOUT,
            )
            if not response.ok:
                self._handle_response_error(response, f"PR #{pr_number} diff")

            logging.info("Successfully fetched PR diff/patch")
            return response.text

        except requests.RequestException as e:
            raise GitHubAPIError(f"Request failed: {e}") from e

    def get_pr_content(
        self, repo_owner: str, repo_name: str, pr_number: int
    ) -> PRContent:
        """
        Fetches the content/details of a specific pull request from a GitHub repository.
        Args:
            repo_owner (str): The owner of the repository.
            repo_name (str): The name of the repository.
            pr_number (int): The pull request number.
        Returns:
            Dict[str, Any]: A dictionary containing the pull request's title, description, author, creation and update timestamps, and state.
        Raises:
            GitHubNotFoundError: If the PR is not found.
            GitHubAPIError: If the API request fails.
        """
        logging.info(f"Fetching PR content for {repo_owner}/{repo_name}#{pr_number}")

        pr_url = self._get_pr_url(repo_owner, repo_name, pr_number)

        try:
            response = requests.get(
                pr_url, headers=self._get_headers(), timeout=TIMEOUT
            )
            if not response.ok:
                self._handle_response_error(response, f"PR #{pr_number}")

            pr_data = response.json()

            pr_info = {
                "title": pr_data["title"],
                "description": pr_data["body"],
                "author": pr_data["user"]["login"],
                "created_at": pr_data["created_at"],
                "updated_at": pr_data["updated_at"],
                "state": pr_data["state"],
            }

            logging.info("Successfully fetched PR content")
            return pr_info

        except requests.RequestException as e:
            raise GitHubAPIError(f"Request failed: {e}") from e

    def add_pr_comments(
        self, repo_owner: str, repo_name: str, pr_number: int, comment: str
    ) -> CommentData:
        """
        Adds a comment to a specific pull request on GitHub.
        Args:
            repo_owner (str): The owner of the repository.
            repo_name (str): The name of the repository.
            pr_number (int): The pull request number to which the comment will be added.
            comment (str): The content of the comment to add.
        Returns:
            Dict[str, Any]: The JSON response from the GitHub API containing the comment data if successful.
        Raises:
            GitHubNotFoundError: If the PR is not found.
            GitHubValidationError: If the comment data is invalid.
            GitHubAPIError: If the API request fails.
        """
        logging.info(f"Adding comment to PR {repo_owner}/{repo_name}#{pr_number}")

        comments_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/{pr_number}/comments"

        try:
            response = requests.post(
                comments_url,
                headers=self._get_headers(),
                json={"body": comment},
                timeout=TIMEOUT,
            )
            if not response.ok:
                self._handle_response_error(response, f"PR #{pr_number} comment")

            logging.info("Comment added successfully")
            return response.json()

        except requests.RequestException as e:
            raise GitHubAPIError(f"Request failed: {e}") from e

    def add_inline_pr_comment(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        path: str,
        line: int,
        comment_body: str,
    ) -> CommentData:
        """
        Adds an inline review comment to a specific line in a file within a pull request on GitHub.
        Args:
            repo_owner (str): The owner of the repository.
            repo_name (str): The name of the repository.
            pr_number (int): The pull request number.
            path (str): The relative path to the file (e.g., 'src/main.py').
            line (int): The line number in the file to comment on.
            comment_body (str): The content of the review comment.
        Returns:
            Dict[str, Any]: The JSON response from the GitHub API containing the comment data if successful.
            None: If an error occurs while adding the comment.
        Error Handling:
            Logs an error message and prints the traceback if the request fails or an exception is raised.
        """
        logging.info(
            f"Adding inline review comment to PR {repo_owner}/{repo_name}#{pr_number} on {path}:{line}"
        )

        # Construct the review comments URL
        review_comments_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}/comments"

        try:
            pr_url = self._get_pr_url(repo_owner, repo_name, pr_number)
            pr_response = requests.get(
                pr_url, headers=self._get_headers(), timeout=TIMEOUT
            )
            pr_response.raise_for_status()
            pr_data = pr_response.json()
            commit_id = pr_data["head"]["sha"]

            payload = {
                "body": comment_body,
                "commit_id": commit_id,
                "path": path,
                "line": line,
                "side": "RIGHT",
            }

            response = requests.post(
                review_comments_url,
                headers=self._get_headers(),
                json=payload,
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            comment_data = response.json()

            logging.info("Inline review comment added successfully")
            return comment_data

        except Exception as e:
            logging.error(f"Error adding inline review comment: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def update_pr_description(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        new_title: str,
        new_description: str,
    ) -> PRContent:
        """
        Updates the title and description (body) of a specific pull request on GitHub.
        Args:
            repo_owner (str): The owner of the repository.
            repo_name (str): The name of the repository.
            pr_number (int): The pull request number to update.
            new_title (str): The new title for the pull request.
            new_description (str): The new description (body) for the pull request.
        Returns:
            Dict[str, Any]: The updated pull request data as returned by the GitHub API if the update is successful.
            None: If an error occurs during the update process.
        Error Handling:
            Logs an error message and prints the traceback if the update fails due to an exception (e.g., network issues, invalid credentials, or API errors).
        """
        logging.info(
            f"Updating PR description for {repo_owner}/{repo_name}#{pr_number}"
        )

        # Construct the PR URL
        pr_url = self._get_pr_url(repo_owner, repo_name, pr_number)
        try:
            # Update the PR description
            response = requests.patch(
                pr_url,
                headers=self._get_headers(),
                json={"title": new_title, "body": new_description},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            pr_data = response.json()

            logging.info("PR description updated successfully")
            return pr_data
        except Exception as e:
            logging.error(f"Error updating PR description: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def create_pr(
        self,
        repo_owner: str,
        repo_name: str,
        title: str,
        body: str,
        head: str,
        base: str,
        draft: bool = False,
    ) -> PRContent:
        """
        Creates a new pull request in the specified GitHub repository.
        Args:
            repo_owner (str): The owner of the repository.
            repo_name (str): The name of the repository.
            title (str): The title of the pull request.
            body (str): The body content of the pull request.
            head (str): The name of the branch where your changes are implemented.
            base (str): The name of the branch you want the changes pulled into.
            draft (bool, optional): Whether the pull request is a draft. Defaults to False.
        Returns:
            Dict[str, Any]: The JSON response from the GitHub API containing pull request information if successful.
        Error Handling:
            Logs errors and prints the traceback if the pull request creation fails, returning None.
        """
        logging.info(f"Creating PR in {repo_owner}/{repo_name}")

        pr_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls"

        try:
            response = requests.post(
                pr_url,
                headers=self._get_headers(),
                json={
                    "title": title,
                    "body": body,
                    "head": head,
                    "base": base,
                    "draft": draft,
                },
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            pr_data = response.json()

            logging.info("PR created successfully")
            return {
                "pr_url": pr_data.get("html_url"),
                "pr_number": pr_data.get("number"),
                "status": pr_data.get("state"),
                "title": pr_data.get("title"),
            }

        except Exception as e:
            logging.error(f"Error creating PR: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def list_open_issues_prs(
        self,
        repo_owner: str,
        issue: Literal["pr", "issue"] = "pr",
        filtering: Literal["user", "owner", "involves"] = "involves",
        per_page: Annotated[int, "Number of results per page (1-100)"] = 50,
        page: int = 1,
    ) -> Dict[str, Any]:
        """
        Lists open pull requests or issues for a specified GitHub repository owner.
        Args:
            repo_owner (str): The owner of the repository.
            issue (Literal['pr', 'issue']): The type of items to list, either 'pr' for pull requests or 'issue' for issues. Defaults to 'pr'.
            filtering (Literal['user', 'owner', 'involves']): The filtering criteria for the search. Defaults to 'involves'.
            per_page (Annotated[int, "Number of results per page (1-100)"]): The number of results to return per page, range 1-100. Defaults to 50.
            page (int): The page number to retrieve. Defaults to 1.
        Returns:
            Dict[str, Any]: A dictionary containing the list of open pull requests or issues, depending on the value of the `issue` parameter.
            None: If an error occurs during the request.
        Error Handling:
            Logs an error message and prints the traceback if the request fails or an exception is raised.
        """
        logging.info(f"Listing open {issue}s for {repo_owner}")

        # Construct the search URL
        search_url = f"https://api.github.com/search/issues?q=is:{issue}+is:open+{filtering}:{repo_owner}&per_page={per_page}&page={page}"

        try:
            response = requests.get(
                search_url, headers=self._get_headers(), timeout=TIMEOUT
            )
            response.raise_for_status()
            pr_data = response.json()
            open_prs = {
                "total": pr_data["total_count"],
                f"open_{issue}s": [
                    {
                        "url": item["html_url"],
                        "title": item["title"],
                        "number": item["number"],
                        "state": item["state"],
                        "created_at": item["created_at"],
                        "updated_at": item["updated_at"],
                        "author": item["user"]["login"],
                        "label_names": [
                            label["name"] for label in item.get("labels", [])
                        ],
                        "is_draft": item.get("draft", False),
                    }
                    for item in pr_data["items"]
                ],
            }

            logging.info(f"Open {issue}s listed successfully")
            return open_prs

        except Exception as e:
            logging.error(f"Error listing open {issue}s: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def create_issue(
        self, repo_owner: str, repo_name: str, title: str, body: str, labels: list[str]
    ) -> IssueData:
        """
        Creates a new issue in the specified GitHub repository.
        If the issue is created successfully, a link to the issue must be appended in the PR's description.
        Args:
            repo_owner (str): The owner of the repository.
            repo_name (str): The name of the repository.
            title (str): The title of the issue to be created.
            body (str): The body content of the issue.
            labels (list[str]): A list of labels to assign to the issue. The label 'mcp' will always be included.
        Returns:
            Dict[str, Any]: A dictionary containing the created issue's data if successful.
            None: If an error occurs during issue creation.
        Error Handling:
            Logs errors and prints the traceback if the issue creation fails, returning None.
        """
        logging.info(f"Creating issue in {repo_owner}/{repo_name}")

        # Construct the issues URL
        issues_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues"

        try:
            # Create the issue
            issue_labels = ["mcp"] if not labels else labels + ["mcp"]
            response = requests.post(
                issues_url,
                headers=self._get_headers(),
                json={"title": title, "body": body, "labels": issue_labels},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            issue_data = response.json()

            logging.info("Issue created successfully")
            return issue_data

        except Exception as e:
            logging.error(f"Error creating issue: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def merge_pr(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        commit_title: Optional[str] = None,
        commit_message: Optional[str] = None,
        merge_method: Literal["merge", "squash", "rebase"] = "squash",
    ) -> Dict[str, Any]:
        """
        Merges a specific pull request in a GitHub repository using the specified merge method.
        Args:
            repo_owner (str): The owner of the repository.
            repo_name (str): The name of the repository.
            pr_number (int): The pull request number to merge.
            commit_title (str, optional): The title for the merge commit. Defaults to None.
            commit_message (str, optional): The message for the merge commit. Defaults to None.
            merge_method (Literal['merge', 'squash', 'rebase'], optional): The merge method to use ('merge', 'squash', or 'rebase'). Defaults to 'squash'.
        Returns:
            Dict[str, Any]: The JSON response from the GitHub API containing merge information if successful.
        Error Handling:
            Logs errors and prints the traceback if the merge fails, returning None.
        """
        logging.info(f"Merging PR {repo_owner}/{repo_name}#{pr_number}")

        # Construct the merge URL
        merge_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}/merge"

        try:
            response = requests.put(
                merge_url,
                headers=self._get_headers(),
                json={
                    "commit_title": commit_title,
                    "commit_message": commit_message,
                    "merge_method": merge_method,
                },
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            merge_data = response.json()

            logging.info("PR merged successfully")
            return merge_data

        except Exception as e:
            logging.error({"status": "error", "message": str(e)})
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def update_issue(
        self,
        repo_owner: str,
        repo_name: str,
        issue_number: int,
        title: str,
        body: str,
        labels: list[str] = [],
        state: Literal["open", "closed"] = "open",
    ) -> IssueData:
        """
        Updates an existing issue in the specified GitHub repository.
        Args:
            repo_owner (str): The owner of the repository.
            repo_name (str): The name of the repository.
            issue_number (int): The number of the issue to update.
            title (str): The new title for the issue.
            body (str): The new body content for the issue.
            labels (list[str], optional): A list of labels to assign to the issue. Defaults to an empty list.
            state (str, optional): The state of the issue ('open' or 'closed'). Defaults to 'open'.
        Returns:
            Dict[str, Any]: The updated issue data as returned by the GitHub API if the update is successful.
            None: If an error occurs during the update process.
        Error Handling:
            Logs an error message and prints the traceback if the request fails or an exception is raised.
        """
        logging.info(f"Updating issue {issue_number} in {repo_owner}/{repo_name}")

        # Construct the issue URL
        issue_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/{issue_number}"

        try:
            # Update the issue
            response = requests.patch(
                issue_url,
                headers=self._get_headers(),
                json={"title": title, "body": body, "labels": labels, "state": state},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            issue_data = response.json()
            logging.info("Issue updated successfully")
            return issue_data
        except Exception as e:
            logging.error(f"Error updating issue: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def update_reviews(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        event: Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"],
        body: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Submits a review for a specific pull request in a GitHub repository.
        Args:
            repo_owner (str): The owner of the repository.
            repo_name (str): The name of the repository.
            pr_number (int): The pull request number to review.
            event (Literal['APPROVE', 'REQUEST_CHANGES', 'COMMENT']): The type of review event.
            body (str, optional): Required when using REQUEST_CHANGES or COMMENT for the event parameter. Defaults to None.
        Returns:
            Dict[str, Any]: The JSON response from the GitHub API containing review information if successful.
            None: If an error occurs during the review submission process.
        Error Handling:
            Logs errors and prints the traceback if the review submission fails, returning None.
        """
        logging.info(f"Submitting review for PR {repo_owner}/{repo_name}#{pr_number}")

        # Construct the reviews URL
        reviews_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}/reviews"

        try:
            response = requests.post(
                reviews_url,
                headers=self._get_headers(),
                json={"body": body, "event": event},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            review_data = response.json()

            logging.info("Review submitted successfully")
            return review_data

        except Exception as e:
            logging.error(f"Error submitting review: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def update_assignees(
        self, repo_owner: str, repo_name: str, issue_number: int, assignees: list[str]
    ) -> Dict[str, Any]:
        """
        Updates the assignees for a specific issue or pull request in a GitHub repository.
        Args:
            repo_owner (str): The owner of the repository.
            repo_name (str): The name of the repository.
            issue_number (int): The issue or pull request number to update.
            assignees (list[str]): A list of usernames to assign to the issue or pull request.
        Returns:
            Dict[str, Any]: The updated issue or pull request data as returned by the GitHub API if the update is successful.
            None: If an error occurs during the update process.
        Error Handling:
            Logs an error message and prints the traceback if the request fails or an exception is raised.
        """
        logging.info(
            f"Updating assignees for issue/PR {repo_owner}/{repo_name}#{issue_number}"
        )
        # Construct the issue URL
        issue_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/{issue_number}"
        try:
            # Update the assignees
            response = requests.patch(
                issue_url,
                headers=self._get_headers(),
                json={"assignees": assignees},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            issue_data = response.json()
            logging.info("Assignees updated successfully")
            return issue_data
        except Exception as e:
            logging.error(f"Error updating assignees: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def get_latest_sha(self, repo_owner: str, repo_name: str) -> Optional[str]:
        """
        Fetches the SHA of the latest commit in the specified GitHub repository.
        Args:
            repo_owner (str): The owner of the GitHub repository.
            repo_name (str): The name of the GitHub repository.
        Returns:
            Optional[str]: The SHA string of the latest commit if found, otherwise None.
        Error Handling:
            Logs errors and warnings if the request fails, the response is invalid, or no commits are found.
            Returns None in case of exceptions or if the repository has no commits.
        """
        logging.info(
            {
                "status": "info",
                "message": f"Fetching latest commit SHA for {repo_owner}/{repo_name}",
            }
        )

        # Construct the commits URL
        commits_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/commits"

        try:
            # Fetch the latest commit
            response = requests.get(
                commits_url, headers=self._get_headers(), timeout=TIMEOUT
            )
            response.raise_for_status()
            commits_data = response.json()

            if commits_data:
                latest_sha = commits_data[0]["sha"]
                logging.info(
                    {"status": "info", "message": f"Latest commit SHA: {latest_sha}"}
                )
                return latest_sha
            else:
                logging.warning(
                    {
                        "status": "warning",
                        "message": "No commits found in the repository",
                    }
                )
                return "No commits found in the repository"

        except Exception as e:
            logging.error(f"Error fetching latest commit SHA: {str(e)}")
            traceback.print_exc()
            return str(e)

    def create_tag(
        self, repo_owner: str, repo_name: str, tag_name: str, message: str
    ) -> Dict[str, Any]:
        """
        Creates a new tag in the specified GitHub repository.
        Args:
            repo_owner (str): The owner of the repository.
            repo_name (str): The name of the repository.
            tag_name (str): The name of the tag to create.
            message (str): The message associated with the tag.
        Returns:
            Dict[str, Any]: The response data from the GitHub API if the tag is created successfully.
            None: If an error occurs during the tag creation process.
        Error Handling:
            Logs errors and prints the traceback if fetching the latest commit SHA fails or if the GitHub API request fails.
        """
        logging.info(f"Creating tag {tag_name} in {repo_owner}/{repo_name}")
        # Construct the tags URL
        tags_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/git/refs"
        try:
            # Fetch the latest commit SHA
            latest_sha = self.get_latest_sha(repo_owner, repo_name)
            if not latest_sha:
                raise ValueError("Failed to fetch the latest commit SHA")

            # Create the tag
            response = requests.post(
                tags_url,
                headers=self._get_headers(),
                json={
                    "ref": f"refs/tags/{tag_name}",
                    "sha": latest_sha,
                    "message": message,
                },
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            tag_data = response.json()

            logging.info("Tag created successfully")
            return tag_data

        except Exception as e:
            logging.error(f"Error creating tag: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def create_release(
        self,
        repo_owner: str,
        repo_name: str,
        tag_name: str,
        release_name: str,
        body: str,
        draft: bool = False,
        prerelease: bool = False,
        generate_release_notes: bool = True,
        make_latest: Literal["true", "false", "legacy"] = "true",
    ) -> Dict[str, Any]:
        """
        Creates a new release in the specified GitHub repository.
        Args:
            repo_owner (str): The owner of the repository.
            repo_name (str): The name of the repository.
            tag_name (str): The tag name for the release.
            release_name (str): The name of the release.
            body (str): The description or body content of the release.
            draft (bool, optional): Whether the release is a draft. Defaults to False.
            prerelease (bool, optional): Whether the release is a prerelease. Defaults to False.
            generate_release_notes (bool, optional): Whether to generate release notes automatically. Defaults to True.
            make_latest (Literal['true', 'false', 'legacy'], optional): Whether to mark the release as the latest. Defaults to 'true'.
        Returns:
            Dict[str, Any]: The JSON response from the GitHub API containing release information if successful.
            None: If an error occurs during the release creation process.
        Error Handling:
            Logs errors and prints the traceback if the release creation fails, returning None.
        """
        logging.info(f"Creating release {release_name} in {repo_owner}/{repo_name}")

        # Construct the releases URL
        releases_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/releases"

        try:
            # Create the release
            response = requests.post(
                releases_url,
                headers=self._get_headers(),
                json={
                    "tag_name": tag_name,
                    "name": release_name,
                    "body": body,
                    "draft": draft,
                    "prerelease": prerelease,
                    "generate_release_notes": generate_release_notes,
                    "make_latest": make_latest,
                },
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            release_data = response.json()

            logging.info("Release created successfully")
            return release_data

        except Exception as e:
            logging.error(f"Error creating release: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def search_user(self, username: str) -> UserSearchResult:
        """
        Search for a GitHub user by username using GraphQL API.

        Args:
            username: GitHub username to search for

        Returns:
            UserSearchResult: Dictionary containing user profile information including:
                - login: GitHub username
                - name: Full name
                - email: Email address
                - company: Company name
                - location: Location
                - bio: Bio/description
                - url: Profile URL
                - avatar_url: Avatar image URL
                - created_at: Account creation date
                - updated_at: Last update date
                - followers: Number of followers
                - following: Number of users following
                - public_repos: Total public repositories count
                - recent_repos: List of recent repositories
                - organizations: List of organizations the user belongs to

        Raises:
            GitHubNotFoundError: If the user is not found
            GitHubAPIError: If the API request fails
        """
        logging.info(f"Searching for GitHub user: {username}")

        try:
            result = self.graphql.execute_query(
                SEARCH_USER_QUERY, variables={"username": username}
            )

            user_data = result.get("user")
            if not user_data:
                raise GitHubNotFoundError(f"User '{username}' not found")

            # Transform GraphQL response to our TypedDict format
            user_info: UserSearchResult = {
                "login": user_data["login"],
                "name": user_data.get("name"),
                "email": user_data.get("email"),
                "company": user_data.get("company"),
                "location": user_data.get("location"),
                "bio": user_data.get("bio"),
                "url": user_data["url"],
                "avatar_url": user_data.get("avatarUrl"),
                "created_at": user_data["createdAt"],
                "updated_at": user_data["updatedAt"],
                "followers": user_data["followers"]["totalCount"],
                "following": user_data["following"]["totalCount"],
                "public_repos": user_data["repositories"]["totalCount"],
                "recent_repos": [
                    {
                        "name": repo["name"],
                        "owner": repo["owner"]["login"],
                        "description": repo.get("description"),
                        "url": repo["url"],
                        "updated_at": repo["updatedAt"],
                    }
                    for repo in user_data["repositories"]["nodes"]
                ],
                "organizations": [
                    {
                        "login": org["login"],
                        "name": org.get("name"),
                        "url": org["url"],
                    }
                    for org in user_data["organizations"]["nodes"]
                ],
            }

            logging.info(f"Successfully found user: {username}")
            return user_info

        except GitHubNotFoundError:
            raise
        except Exception as e:
            logging.error(f"Error searching for user {username}: {e}")
            raise GitHubAPIError(f"Failed to search for user: {e}") from e

    def get_user_activities(
        self,
        username: str,
        org: str = "",
        repo: str = "",
        since: str = "",
        until: str = "",
        max_results: int = 50,
    ) -> UserActivityResult:
        """
        Get user activities with optional filtering by org, repo, and date range using GraphQL API.

        This method provides a drill-down capability:
        - username only: Get activities across all repos/orgs
        - username + org: Filter activities to specific organization
        - username + org + repo: Drill down to specific repository
        - + date range: Filter by time period (ISO 8601 format: "2024-01-01T00:00:00Z")

        Args:
            username: GitHub username to fetch activities for
            org: Optional organization name to filter by
            repo: Optional repository name to filter by (requires org)
            since: Optional start date in ISO 8601 format
            until: Optional end date in ISO 8601 format
            max_results: Maximum number of results per category (default: 50)

        Returns:
            UserActivityResult: Dictionary containing:
                - username: The GitHub username
                - date_range: Applied date filter (since/until) if any
                - total_contributions: Summary counts for commits, PRs, issues, reviews
                - commits: List of commit contributions
                - pull_requests: List of PR contributions
                - issues: List of issue contributions
                - reviews: List of PR review contributions

        Raises:
            GitHubNotFoundError: If the user, org, or repo is not found
            GitHubAPIError: If the API request fails
        """
        logging.info(
            f"Fetching user activities for {username} "
            f"(org={org}, repo={repo}, since={since}, until={until})"
        )

        try:
            variables: dict[str, Any] = {"username": username}
            if since:
                variables["since"] = since
            if until:
                variables["until"] = until

            # Execute the contributions query
            result = self.graphql.execute_query(
                USER_CONTRIBUTIONS_QUERY,
                variables=variables,
            )

            user_data = result.get("user")
            if not user_data:
                raise GitHubNotFoundError(f"User '{username}' not found")

            collection = user_data.get("contributionsCollection", {})

            # Build date_range info
            date_range = None
            if since or until:
                date_range = {
                    "since": since if since else collection.get("startedAt", ""),
                    "until": until if until else collection.get("endedAt", ""),
                }

            # Process contributions and apply org/repo filters
            commits = []
            pull_requests = []
            issues = []
            reviews = []

            # Process commit contributions
            for repo_contrib in collection.get("commitContributionsByRepository", []):
                repo_info = repo_contrib["repository"]
                owner = repo_info["owner"]["login"]
                repo_name = repo_info["name"]

                # Apply org filter
                if org and owner.lower() != org.lower():
                    continue
                # Apply repo filter
                if repo and repo_name.lower() != repo.lower():
                    continue

                for contrib in repo_contrib.get("contributions", {}).get("nodes", []):
                    if len(commits) >= max_results:
                        break
                    commits.append(
                        {
                            "repo": repo_name,
                            "owner": owner,
                            "commit_count": contrib.get("commitCount", 0),
                            "url": contrib.get("url", ""),
                            "date": contrib.get("occurredAt", ""),
                        }
                    )

            # Process PR contributions
            for repo_contrib in collection.get(
                "pullRequestContributionsByRepository", []
            ):
                repo_info = repo_contrib["repository"]
                owner = repo_info["owner"]["login"]
                repo_name = repo_info["name"]

                # Apply filters
                if org and owner.lower() != org.lower():
                    continue
                if repo and repo_name.lower() != repo.lower():
                    continue

                for contrib in repo_contrib.get("contributions", {}).get("nodes", []):
                    if len(pull_requests) >= max_results:
                        break
                    pr = contrib["pullRequest"]
                    pull_requests.append(
                        {
                            "repo": repo_name,
                            "owner": owner,
                            "number": pr["number"],
                            "title": pr["title"],
                            "state": pr["state"],
                            "url": pr["url"],
                            "created": pr["createdAt"],
                            "merged": pr.get("merged", False),
                        }
                    )

            # Process issue contributions
            for repo_contrib in collection.get("issueContributionsByRepository", []):
                repo_info = repo_contrib["repository"]
                owner = repo_info["owner"]["login"]
                repo_name = repo_info["name"]

                # Apply filters
                if org and owner.lower() != org.lower():
                    continue
                if repo and repo_name.lower() != repo.lower():
                    continue

                for contrib in repo_contrib.get("contributions", {}).get("nodes", []):
                    if len(issues) >= max_results:
                        break
                    issue = contrib["issue"]
                    issues.append(
                        {
                            "repo": repo_name,
                            "owner": owner,
                            "number": issue["number"],
                            "title": issue["title"],
                            "state": issue["state"],
                            "url": issue["url"],
                            "created": issue["createdAt"],
                        }
                    )

            # Process review contributions
            for repo_contrib in collection.get(
                "pullRequestReviewContributionsByRepository", []
            ):
                repo_info = repo_contrib["repository"]
                owner = repo_info["owner"]["login"]
                repo_name = repo_info["name"]

                # Apply filters
                if org and owner.lower() != org.lower():
                    continue
                if repo and repo_name.lower() != repo.lower():
                    continue

                for contrib in repo_contrib.get("contributions", {}).get("nodes", []):
                    if len(reviews) >= max_results:
                        break
                    review = contrib["pullRequestReview"]
                    pr = contrib["pullRequest"]
                    reviews.append(
                        {
                            "repo": repo_name,
                            "owner": owner,
                            "pr_number": pr["number"],
                            "pr_title": pr["title"],
                            "pr_url": pr["url"],
                            "review_state": review["state"],
                            "review_url": review["url"],
                            "date": contrib["occurredAt"],
                        }
                    )

            activity_result: UserActivityResult = {
                "username": username,
                "date_range": date_range,
                "total_contributions": {
                    "commits": collection.get("totalCommitContributions", 0),
                    "pull_requests": collection.get("totalPullRequestContributions", 0),
                    "issues": collection.get("totalIssueContributions", 0),
                    "reviews": collection.get("totalPullRequestReviewContributions", 0),
                },
                "commits": commits,
                "pull_requests": pull_requests,
                "issues": issues,
                "reviews": reviews,
            }

            logging.info(
                f"Successfully fetched activities: {len(commits)} commits, "
                f"{len(pull_requests)} PRs, {len(issues)} issues, {len(reviews)} reviews"
            )
            return activity_result

        except GitHubNotFoundError:
            raise
        except Exception as e:
            logging.error(f"Error fetching user activities for {username}: {e}")
            raise GitHubAPIError(f"Failed to fetch user activities: {e}") from e
