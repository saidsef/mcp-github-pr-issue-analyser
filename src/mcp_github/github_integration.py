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

import logging
import requests
import traceback
from os import getenv
from pydantic import BaseModel, conint
from typing import Annotated, Any, Dict, Optional, Literal

GITHUB_TOKEN = getenv('GITHUB_TOKEN')
TIMEOUT = int(getenv('GITHUB_API_TIMEOUT', '5'))  # seconds, configurable via env

# Set up logging for the application
logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)

class GitHubIntegration:
    PerPage = conint(ge=1, le=100)

    def __init__(self):
        """
        Initializes the GitHubIntegration instance by setting up the GitHub token from environment variables.
        Returns:
            None
        Error Handling:
            Raises ValueError if the GITHUB_TOKEN environment variable is not set.
        """
        self.github_token = GITHUB_TOKEN
        if not self.github_token:
            raise ValueError("Missing GitHub GITHUB_TOKEN in environment variables")
        
        logging.info("GitHub Integration Initialised")

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
            'Authorization': f'token {self.github_token}',
            'Accept': 'application/vnd.github.v3+json'
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
            str: The raw patch/diff text of the pull request if successful, otherwise None.
        Error Handling:
            Logs an error message and prints the traceback if the request fails or an exception occurs.
        """
        logging.info(f"Fetching PR diff for {repo_owner}/{repo_name}#{pr_number}")
        
        try:
            # Fetch PR details
            response = requests.get(f"https://patch-diff.githubusercontent.com/raw/{repo_owner}/{repo_name}/pull/{pr_number}.patch", headers=self._get_headers(), timeout=TIMEOUT)
            response.raise_for_status()
            pr_patch = response.text
            
            logging.info("Successfully fetched PR diff/patch")
            return pr_patch
            
        except Exception as e:
            logging.error(f"Error fetching PR diff: {str(e)}")
            traceback.print_exc()
            return str(e)

    def get_pr_content(self, repo_owner: str, repo_name: str, pr_number: int) -> Dict[str, Any]:
        """
        Fetches the content/details of a specific pull request from a GitHub repository.
        Args:
            repo_owner (str): The owner of the repository.
            repo_name (str): The name of the repository.
            pr_number (int): The pull request number.
        Returns:
            Dict[str, Any]: A dictionary containing the pull request's title, description, author, creation and update timestamps, and state.
            Returns None if an error occurs during the fetch operation.
        Error Handling:
            Logs an error message and prints the traceback if the request fails or an exception is raised during processing.
        """
        logging.info(f"Fetching PR content for {repo_owner}/{repo_name}#{pr_number}")
        
        # Construct the PR URL
        pr_url = self._get_pr_url(repo_owner, repo_name, pr_number)
        
        try:
            # Fetch PR details
            response = requests.get(pr_url, headers=self._get_headers(), timeout=TIMEOUT)
            response.raise_for_status()
            pr_data = response.json()
            
            # Extract relevant information
            pr_info = {
                'title': pr_data['title'],
                'description': pr_data['body'],
                'author': pr_data['user']['login'],
                'created_at': pr_data['created_at'],
                'updated_at': pr_data['updated_at'],
                'state': pr_data['state']
            }

            logging.info("Successfully fetched PR content")
            return pr_info
            
        except Exception as e:
            logging.error(f"Error fetching PR content: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def add_pr_comments(self, repo_owner: str, repo_name: str, pr_number: int, comment: str) -> Dict[str, Any]:
        """
        Adds a comment to a specific pull request on GitHub.
        Args:
            repo_owner (str): The owner of the repository.
            repo_name (str): The name of the repository.
            pr_number (int): The pull request number to which the comment will be added.
            comment (str): The content of the comment to add.
        Returns:
            Dict[str, Any]: The JSON response from the GitHub API containing the comment data if successful.
            None: If an error occurs while adding the comment.
        Error Handling:
            Logs an error message and prints the traceback if the request fails or an exception is raised.
        """
        logging.info(f"Adding comment to PR {repo_owner}/{repo_name}#{pr_number}")

        # Construct the comments URL
        comments_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/{pr_number}/comments"

        try:
            # Add the comment
            response = requests.post(comments_url, headers=self._get_headers(), json={'body': comment}, timeout=TIMEOUT)
            response.raise_for_status()
            comment_data = response.json()

            logging.info("Comment added successfully")
            return comment_data

        except Exception as e:
            logging.error(f"Error adding comment: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def add_inline_pr_comment(self, repo_owner: str, repo_name: str, pr_number: int, path: str, line: int, comment_body: str) -> Dict[str, Any]:
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
        logging.info(f"Adding inline review comment to PR {repo_owner}/{repo_name}#{pr_number} on {path}:{line}")

        # Construct the review comments URL
        review_comments_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}/comments"

        try:
            pr_url = self._get_pr_url(repo_owner, repo_name, pr_number)
            pr_response = requests.get(pr_url, headers=self._get_headers(), timeout=TIMEOUT)
            pr_response.raise_for_status()
            pr_data = pr_response.json()
            commit_id = pr_data['head']['sha']

            payload = {
                "body": comment_body,
                "commit_id": commit_id,
                "path": path,
                "line": line,
                "side": "RIGHT"
            }

            response = requests.post(review_comments_url, headers=self._get_headers(), json=payload, timeout=TIMEOUT)
            response.raise_for_status()
            comment_data = response.json()

            logging.info("Inline review comment added successfully")
            return comment_data

        except Exception as e:
            logging.error(f"Error adding inline review comment: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def update_pr_description(self, repo_owner: str, repo_name: str, pr_number: int, new_title: str, new_description: str) -> Dict[str, Any]:
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
        logging.info(f"Updating PR description for {repo_owner}/{repo_name}#{pr_number}")

        # Construct the PR URL
        pr_url = self._get_pr_url(repo_owner, repo_name, pr_number)
        try:
            # Update the PR description
            response = requests.patch(pr_url, headers=self._get_headers(), json={
                'title': new_title,
                'body': new_description
            }, timeout=TIMEOUT)
            response.raise_for_status()
            pr_data = response.json()

            logging.info("PR description updated successfully")
            return pr_data
        except Exception as e:
            logging.error(f"Error updating PR description: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def create_pr(self, repo_owner: str, repo_name: str, title: str, body: str, head: str, base: str, draft: bool = False) -> Dict[str, Any]:
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
            response = requests.post(pr_url, headers=self._get_headers(), json={
                'title': title,
                'body': body,
                'head': head,
                'base': base,
                'draft': draft
            }, timeout=TIMEOUT)
            response.raise_for_status()
            pr_data = response.json()

            logging.info("PR created successfully")
            return {
                "pr_url": pr_data.get('html_url'),
                "pr_number": pr_data.get('number'),
                "status": pr_data.get('state'),
                "title": pr_data.get('title'),
            }

        except Exception as e:
            logging.error(f"Error creating PR: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def list_open_issues_prs(
            self,
            repo_owner: str,
            issue: Literal['pr', 'issue'] = 'pr',
            filtering: Literal['user', 'owner', 'involves'] = 'involves',
            per_page: Annotated[PerPage, "Number of results per page (1-100)"] = 50,
            page: int = 1
    ) -> Dict[str, Any]:
        """
        Lists open pull requests or issues for a specified GitHub repository owner.
        Args:
            repo_owner (str): The owner of the repository.
            issue (Literal['pr', 'issue']): The type of items to list, either 'pr' for pull requests or 'issue' for issues. Defaults to 'pr'.
            filtering (Literal['user', 'owner', 'involves']): The filtering criteria for the search. Defaults to 'involves'.
            per_page (Annotated[int, PerPage]): The number of results to return per page, range 1-100. Defaults to 50.
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
            response = requests.get(search_url, headers=self._get_headers(), timeout=TIMEOUT)
            response.raise_for_status()
            pr_data = response.json()
            open_prs = {
                "total": pr_data['total_count'],
                f"open_{issue}s": [
                    {
                        "url": item['html_url'],
                        "title": item['title'],
                        "number": item['number'],
                        "state": item['state'],
                        "created_at": item['created_at'],
                        "updated_at": item['updated_at'],
                        "author": item['user']['login'],
                        "label_names": [label['name'] for label in item.get('labels', [])],
                        "is_draft": item.get('draft', False),
                    }
                    for item in pr_data['items']
                ]
            }

            logging.info(f"Open {issue}s listed successfully")
            return open_prs

        except Exception as e:
            logging.error(f"Error listing open {issue}s: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def create_issue(self, repo_owner: str, repo_name: str, title: str, body: str, labels: list[str]) -> Dict[str, Any]:
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
            issue_labels = ['mcp'] if not labels else labels + ['mcp']
            response = requests.post(issues_url, headers=self._get_headers(), json={
                'title': title,
                'body': body,
                'labels': issue_labels
            }, timeout=TIMEOUT)
            response.raise_for_status()
            issue_data = response.json()
            
            logging.info("Issue created successfully")
            return issue_data

        except Exception as e:
            logging.error(f"Error creating issue: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def merge_pr(self, repo_owner: str, repo_name: str, pr_number: int, commit_title: Optional[str] = None, commit_message: Optional[str] = None, merge_method: Literal['merge', 'squash', 'rebase'] = 'squash') -> Dict[str, Any]:
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
            response = requests.put(merge_url, headers=self._get_headers(), json={
                'commit_title': commit_title,
                'commit_message': commit_message,
                'merge_method': merge_method
            }, timeout=TIMEOUT)
            response.raise_for_status()
            merge_data = response.json()

            logging.info("PR merged successfully")
            return merge_data

        except Exception as e:
            logging.error({"status": "error", "message": str(e)})
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def update_issue(self, repo_owner: str, repo_name: str, issue_number: int, title: str, body: str, labels: list[str] = [], state: Literal['open', 'closed'] = 'open') -> Dict[str, Any]:
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
            response = requests.patch(issue_url, headers=self._get_headers(), json={
                'title': title,
                'body': body,
                'labels': labels,
                'state': state
            }, timeout=TIMEOUT)
            response.raise_for_status()
            issue_data = response.json()
            logging.info("Issue updated successfully")
            return issue_data
        except Exception as e:
            logging.error(f"Error updating issue: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def update_reviews(self, repo_owner: str, repo_name: str, pr_number: int, event: Literal['APPROVE', 'REQUEST_CHANGES', 'COMMENT'], body: Optional[str] = None) -> Dict[str, Any]:
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
            response = requests.post(reviews_url, headers=self._get_headers(), json={
                'body': body,
                'event': event
            }, timeout=TIMEOUT)
            response.raise_for_status()
            review_data = response.json()

            logging.info("Review submitted successfully")
            return review_data

        except Exception as e:
            logging.error(f"Error submitting review: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def update_assignees(self, repo_owner: str, repo_name: str, issue_number: int, assignees: list[str]) -> Dict[str, Any]:
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
        logging.info(f"Updating assignees for issue/PR {repo_owner}/{repo_name}#{issue_number}")
        # Construct the issue URL
        issue_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/{issue_number}"
        try:
            # Update the assignees
            response = requests.patch(issue_url, headers=self._get_headers(), json={
                'assignees': assignees
            }, timeout=TIMEOUT)
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
        logging.info({"status": "info", "message": f"Fetching latest commit SHA for {repo_owner}/{repo_name}"})

        # Construct the commits URL
        commits_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/commits"

        try:
            # Fetch the latest commit
            response = requests.get(commits_url, headers=self._get_headers(), timeout=TIMEOUT)
            response.raise_for_status()
            commits_data = response.json()

            if commits_data:
                latest_sha = commits_data[0]['sha']
                logging.info({"status": "info", "message": f"Latest commit SHA: {latest_sha}"})
                return latest_sha
            else:
                logging.warning({"status": "warning", "message": "No commits found in the repository"})
                return "No commits found in the repository"

        except Exception as e:
            logging.error(f"Error fetching latest commit SHA: {str(e)}")
            traceback.print_exc()
            return str(e)

    def create_tag(self, repo_owner: str, repo_name: str, tag_name: str, message: str) -> Dict[str, Any]:
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
            response = requests.post(tags_url, headers=self._get_headers(), json={
                'ref': f'refs/tags/{tag_name}',
                'sha': latest_sha,
                'message': message
            }, timeout=TIMEOUT)
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
            make_latest: Literal['true', 'false', 'legacy'] = 'true'
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
            response = requests.post(releases_url, headers=self._get_headers(), json={
                'tag_name': tag_name,
                'name': release_name,
                'body': body,
                'draft': draft,
                'prerelease': prerelease,
                'generate_release_notes': generate_release_notes,
                'make_latest': make_latest
            }, timeout=TIMEOUT)
            response.raise_for_status()
            release_data = response.json()

            logging.info("Release created successfully")
            return release_data

        except Exception as e:
            logging.error(f"Error creating release: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def user_activity_query(self, variables: dict[str, Any], query: str) -> Dict[str, Any]:
        """
        Performs a user activity query using GitHub's GraphQL API with support for organization-specific 
        and cross-organization queries.

        **Query Modes**:
        
        1. **Organization-Specific Activity** (fastest, most comprehensive):
           - Query organization repositories directly
           - Access all private repos in the org (with proper token scopes)
           - Get detailed commit history, PRs, and issues
           - Variables: {"orgName": "Pelle-Tech", "from": "2024-10-01T00:00:00Z", "to": "2024-10-31T23:59:59Z"}
           - Variable types: `$orgName: String!`, `$from: GitTimestamp!`, `$to: GitTimestamp!`
        
        2. **Authenticated User Activity Across All Orgs** (slower, summary only):
           - Query viewer's contribution collection
           - Includes all orgs where user is a member
           - Summary counts only (no detailed commit messages)
           - Variables: {"from": "2024-10-01T00:00:00Z", "to": "2024-10-31T23:59:59Z"}
           - Variable types: `$from: DateTime!`, `$to: DateTime!`
        
        3. **User Activity in Specific Organization** (most restrictive):
           - Query organization repos filtered by user
           - Requires combining org query with author filtering
           - Variables: {"orgName": "Pelle-Tech", "username": "saidsef", "from": "2024-10-01T00:00:00Z", "to": "2024-10-31T23:59:59Z"}
           - Variable types: `$orgName: String!`, `$username: String!`, `$from: GitTimestamp!`, `$to: GitTimestamp!`

        **Performance Tips**:
        - Use pagination parameters to limit initial data: `first: 50` instead of `first: 100`
        - Query only required fields to reduce response size
        - Use org-specific queries when possible (faster than viewer queries)
        - For large date ranges, split into smaller queries
        - Cache results for repeated queries

        **Example Queries**:

        **Fast Org Query with Pagination**:
        ```graphql
        query($orgName: String!, $from: GitTimestamp!, $to: GitTimestamp!, $repoCount: Int = 50) {
          organization(login: $orgName) {
            login
            repositories(first: $repoCount, privacy: PRIVATE, orderBy: {field: PUSHED_AT, direction: DESC}) {
              pageInfo {
                hasNextPage
                endCursor
              }
              nodes {
                name
                isPrivate
                defaultBranchRef {
                  target {
                    ... on Commit {
                      history(since: $from, until: $to, first: 100) {
                        totalCount
                        pageInfo {
                          hasNextPage
                          endCursor
                        }
                        nodes {
                          author { 
                            user { login }
                            email
                          }
                          committedDate
                          message
                          additions
                          deletions
                        }
                      }
                    }
                  }
                }
                pullRequests(first: 50, states: [OPEN, CLOSED, MERGED], orderBy: {field: UPDATED_AT, direction: DESC}) {
                  totalCount
                  nodes {
                    number
                    title
                    author { login }
                    createdAt
                    state
                    additions
                    deletions
                  }
                }
              }
            }
          }
        }
        ```

        **User-Filtered Org Query**:
        ```graphql
        query($orgName: String!, $username: String!, $from: GitTimestamp!, $to: GitTimestamp!) {
          organization(login: $orgName) {
            login
            repositories(first: 100, privacy: PRIVATE) {
              nodes {
                name
                defaultBranchRef {
                  target {
                    ... on Commit {
                      history(since: $from, until: $to, author: {emails: [$username]}, first: 100) {
                        totalCount
                        nodes {
                          author { user { login } }
                          committedDate
                          message
                        }
                      }
                    }
                  }
                }
                pullRequests(first: 100, states: [OPEN, CLOSED, MERGED]) {
                  nodes {
                    author { login }
                    title
                    createdAt
                    state
                  }
                }
              }
            }
          }
        }
        ```

        **Cross-Org Viewer Query**:
        ```graphql
        query($from: DateTime!, $to: DateTime!) {
          viewer {
            login
            contributionsCollection(from: $from, to: $to) {
              commitContributionsByRepository(maxRepositories: 100) {
                repository { 
                  name 
                  isPrivate 
                  owner { login }
                }
                contributions { totalCount }
              }
              pullRequestContributionsByRepository(maxRepositories: 100) {
                repository { 
                  name 
                  isPrivate 
                  owner { login }
                }
                contributions { totalCount }
              }
              issueContributionsByRepository(maxRepositories: 100) {
                repository { 
                  name 
                  isPrivate 
                  owner { login }
                }
                contributions { totalCount }
              }
            }
            organizations(first: 100) {
              nodes { 
                login 
                viewerCanAdminister
              }
            }
          }
        }
        ```

        Args:
            variables (dict[str, Any]): Query variables. Supported combinations:
                - Org-specific: {"orgName": "Pelle-Tech", "from": "...", "to": "..."}
                - Cross-org: {"from": "...", "to": "..."}
                - User-filtered org: {"orgName": "Pelle-Tech", "username": "saidsef", "from": "...", "to": "..."}
                - With pagination: Add {"repoCount": 50, "prCount": 50} for custom limits
            query (str): GraphQL query string. Must declare correct variable types:
                - Organization queries: Use `GitTimestamp!` for $from/$to
                - Viewer queries: Use `DateTime!` for $from/$to
                - Both types accept ISO 8601 format: "YYYY-MM-DDTHH:MM:SSZ"

        Returns:
            Dict[str, Any]: GraphQL response with activity data or error information.
                - Success: {"data": {...}}
                - Errors: {"errors": [...], "data": null}
                - Network error: {"status": "error", "message": "..."}

        Error Handling:
            - Validates response status codes
            - Logs GraphQL errors with details
            - Returns structured error responses
            - Includes traceback for debugging

        Required Token Scopes:
            - `repo`: Full control of private repositories
            - `read:org`: Read org and team membership
            - `read:user`: Read user profile data

        Performance Notes:
            - Org queries are ~3x faster than viewer queries
            - Large date ranges (>1 year) may timeout
            - Use pagination for repos with >100 commits
            - Response size correlates with date range and repo count
        """
        # Validate inputs
        if not query or not isinstance(query, str):
            return {"status": "error", "message": "Query must be a non-empty string"}
        
        if not variables or not isinstance(variables, dict):
            return {"status": "error", "message": "Variables must be a non-empty dictionary"}
        
        # Determine query type for optimized logging
        query_type = "unknown"
        if "orgName" in variables and "username" in variables:
            query_type = "user-filtered-org"
        elif "orgName" in variables:
            query_type = "org-specific"
        elif "from" in variables and "to" in variables:
            query_type = "cross-org-viewer"
        
        logging.info(f"Performing GraphQL query [type: {query_type}] with variables: {variables}")

        try:
            # Make GraphQL request with optimized timeout
            response = requests.post(
                'https://api.github.com/graphql',
                json={'query': query, 'variables': variables},
                headers=self._get_headers(),
                timeout=TIMEOUT * 2  # Double timeout for GraphQL queries (can be complex)
            )
            response.raise_for_status()
            query_data = response.json()

            # Handle GraphQL errors (API accepts request but query has issues)
            if 'errors' in query_data:
                error_messages = [err.get('message', 'Unknown error') for err in query_data['errors']]
                logging.error(f"GraphQL query errors: {error_messages}")
                
                # Check for common errors and provide helpful messages
                for error in query_data['errors']:
                    error_type = error.get('extensions', {}).get('code')
                    if error_type == 'variableMismatch':
                        logging.error(f"Variable type mismatch: Use GitTimestamp for org queries, DateTime for viewer queries")
                    elif error_type == 'NOT_FOUND':
                        logging.error(f"Resource not found: Check org/user name is correct and case-sensitive")
                    elif error_type == 'FORBIDDEN':
                        logging.error(f"Access forbidden: Check token has required scopes (repo, read:org)")
                
                return query_data  # Return with errors for caller to handle
            
            # Log success with summary
            if 'data' in query_data:
                data_keys = list(query_data['data'].keys())
                logging.info(f"GraphQL query successful [type: {query_type}], returned data keys: {data_keys}")
            
            return query_data

        except requests.exceptions.Timeout:
            error_msg = f"GraphQL query timeout after {TIMEOUT * 2}s. Try reducing date range or repo count."
            logging.error(error_msg)
            return {"status": "error", "message": error_msg, "timeout": True}
        
        except requests.exceptions.RequestException as req_err:
            error_msg = f"Request error during GraphQL query: {str(req_err)}"
            logging.error(error_msg)
            traceback.print_exc()
            return {"status": "error", "message": error_msg, "request_exception": True}
        
        except Exception as e:
            error_msg = f"Unexpected error performing GraphQL query: {str(e)}"
            logging.error(error_msg)
            traceback.print_exc()
            return {"status": "error", "message": error_msg, "unexpected": True}

    def get_user_org_activity(
        self, 
        org_name: str, 
        username: str, 
        from_date: str, 
        to_date: str,
        page: int = 1,
        per_page: int = 50
    ) -> Dict[str, Any]:
        """
        Gets comprehensive activity for a SPECIFIC USER across ALL repositories in an organization.
        
        **PAGINATED RESULTS** - Returns a manageable subset of data to prevent context overflow.
        
        Efficiently filters by user at the GraphQL level - does NOT scan entire repos.
        Captures ALL branches, not just main/default branch.
        
        Includes:
        - Commits by the user (paginated)
        - PRs where user was: author, reviewer, merger, commenter, or assigned (paginated)
        - Issues where user was: author, assigned, commenter, or participant (paginated)
        - Handles reviewed, open, merged, closed, and approved PRs
        
        Args:
            org_name (str): GitHub organization name
            username (str): GitHub username to query
            from_date (str): Start date ISO 8601 (e.g., "2024-01-01T00:00:00Z")
            to_date (str): End date ISO 8601 (e.g., "2024-12-31T23:59:59Z")
            page (int): Page number (1-indexed, default: 1)
            per_page (int): Items per page (default: 50, max: 100)
        
        Returns:
            Dict containing: 
            - status: success/error
            - summary: aggregate statistics
            - commits[]: paginated commits (most recent first)
            - prs[]: paginated PRs (most recent first)
            - issues[]: paginated issues (most recent first)
            - pagination: current_page, per_page, total_items, total_pages, has_next_page
        """
        logging.info(f"Fetching ALL activity for '{username}' in '{org_name}' from {from_date} to {to_date}")
        
        # Step 1: Get user's email addresses for efficient commit filtering
        user_emails = self._get_user_emails(username)
        logging.info(f"Found {len(user_emails)} email(s) for filtering commits")
        
        # Step 2: Get repositories where user actually contributed (optimized approach)
        # First try to get repos from user's contribution collection
        contributed_repos = self._get_user_contributed_repos(username, org_name, from_date, to_date)
        
        if contributed_repos:
            logging.info(f"Found {len(contributed_repos)} repos with user contributions via contributionsCollection")
            org_repos = contributed_repos
        else:
            # Fallback: Get ALL repositories in organization
            logging.info(f"Fallback: Scanning all org repos (contributionsCollection returned no results)")
            org_repos = self._get_all_org_repos(org_name)
            logging.info(f"Found {len(org_repos)} total repositories in {org_name}")
        
        if not org_repos:
            return self._empty_activity_response(username, org_name, from_date, to_date, page, per_page)
        
        # Step 3: Process each repo - filter by user at GraphQL level
        all_commits = []
        all_prs = []
        all_issues = []
        repos_with_activity = 0
        
        for repo_info in org_repos:
            repo_name = repo_info.get("name")
            repo_url = repo_info.get("url")
            
            logging.info(f"Scanning {org_name}/{repo_name} for {username}")
            
            # Fetch user-specific data from this repo
            repo_activity = self._fetch_repo_user_activity(
                org_name, repo_name, repo_url, username, user_emails, from_date, to_date
            )
            
            if repo_activity:
                all_commits.extend(repo_activity.get("commits", []))
                all_prs.extend(repo_activity.get("prs", []))
                all_issues.extend(repo_activity.get("issues", []))
                
                if repo_activity.get("has_activity"):
                    repos_with_activity += 1
        
        # Sort by date (most recent first)
        all_commits.sort(key=lambda x: x["date"], reverse=True)
        all_prs.sort(key=lambda x: x["updated_at"], reverse=True)
        all_issues.sort(key=lambda x: x["updated_at"], reverse=True)
        
        # Calculate pagination
        per_page = min(max(1, per_page), 100)  # Clamp between 1-100
        page = max(1, page)  # Must be at least 1
        
        total_commits = len(all_commits)
        total_prs = len(all_prs)
        total_issues = len(all_issues)
        
        # Calculate pages
        commits_total_pages = (total_commits + per_page - 1) // per_page if total_commits > 0 else 1
        prs_total_pages = (total_prs + per_page - 1) // per_page if total_prs > 0 else 1
        issues_total_pages = (total_issues + per_page - 1) // per_page if total_issues > 0 else 1
        
        # Slice data for current page
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        
        paginated_commits = all_commits[start_idx:end_idx]
        paginated_prs = all_prs[start_idx:end_idx]
        paginated_issues = all_issues[start_idx:end_idx]
        
        # Generate summary (based on ALL data, not just current page)
        user_authored_prs = [pr for pr in all_prs if "Author" in pr["user_roles"]]
        
        summary = {
            "user": username,
            "organization": org_name,
            "date_range": f"{from_date} to {to_date}",
            "total_commits": total_commits,
            "total_prs_involved": total_prs,
            "prs_authored": len(user_authored_prs),
            "prs_reviewed": len([pr for pr in all_prs if any(r in pr["user_roles"] for r in ["Approved", "Requested Changes", "Reviewed"])]),
            "prs_merged": len([pr for pr in all_prs if "Merged" in pr["user_roles"]]),
            "prs_commented": len([pr for pr in all_prs if "Commented" in pr["user_roles"]]),
            "total_issues_involved": len(all_issues),
            "issues_authored": len([issue for issue in all_issues if "Author" in issue["user_roles"]]),
            "issues_assigned": len([issue for issue in all_issues if "Assigned" in issue["user_roles"]]),
            "issues_commented": len([issue for issue in all_issues if "Commented" in issue["user_roles"]]),
            "total_additions": sum(c["additions"] for c in all_commits),
            "total_deletions": sum(c["deletions"] for c in all_commits),
        }
        
        logging.info(f"Activity complete: Page {page}/{max(commits_total_pages, prs_total_pages, issues_total_pages)} - Returning {len(paginated_commits)} commits, {len(paginated_prs)} PRs, {len(paginated_issues)} issues from {repos_with_activity}/{len(org_repos)} repos")
        
        return {
            "status": "success",
            "summary": summary,
            "commits": paginated_commits,
            "prs": paginated_prs,
            "issues": paginated_issues,
            "pagination": {
                "current_page": page,
                "per_page": per_page,
                "commits": {
                    "total": total_commits,
                    "total_pages": commits_total_pages,
                    "has_next_page": page < commits_total_pages,
                    "returned": len(paginated_commits)
                },
                "prs": {
                    "total": total_prs,
                    "total_pages": prs_total_pages,
                    "has_next_page": page < prs_total_pages,
                    "returned": len(paginated_prs)
                },
                "issues": {
                    "total": total_issues,
                    "total_pages": issues_total_pages,
                    "has_next_page": page < issues_total_pages,
                    "returned": len(paginated_issues)
                },
                "repos": {
                    "total_in_org": len(org_repos),
                    "with_user_activity": repos_with_activity
                }
            }
        }
    
    def _get_user_emails(self, username: str) -> list:
        """Get user's email addresses for commit filtering."""
        query = """
        query($username: String!) {
          user(login: $username) {
            email
            emails(first: 10) {
              nodes { email }
            }
          }
        }
        """
        
        result = self.user_activity_query({"username": username}, query)
        emails = []
        
        if "data" in result:
            user_data = result.get("data", {}).get("user", {})
            if user_data.get("email"):
                emails.append(user_data["email"])
            for node in user_data.get("emails", {}).get("nodes", []):
                if node.get("email") and node["email"] not in emails:
                    emails.append(node["email"])
        
        return emails
    
    def _get_user_contributed_repos(self, username: str, org_name: str, from_date: str, to_date: str) -> list:
        """Get repositories where user actually contributed within date range."""
        # Note: from_date/to_date need to be in DateTime format for contributionsCollection
        query = """
        query($username: String!, $from: DateTime!, $to: DateTime!) {
          user(login: $username) {
            contributionsCollection(from: $from, to: $to, organizationID: null) {
              commitContributionsByRepository(maxRepositories: 100) {
                repository {
                  name
                  url
                  owner { login }
                }
                contributions { totalCount }
              }
              pullRequestContributionsByRepository(maxRepositories: 100) {
                repository {
                  name
                  url
                  owner { login }
                }
                contributions { totalCount }
              }
              issueContributionsByRepository(maxRepositories: 100) {
                repository {
                  name
                  url
                  owner { login }
                }
                contributions { totalCount }
              }
            }
          }
        }
        """
        
        result = self.user_activity_query({"username": username, "from": from_date, "to": to_date}, query)
        
        repos_dict = {}  # Use dict to deduplicate by repo name
        
        if "data" in result and result.get("data", {}).get("user"):
            contributions = result["data"]["user"]["contributionsCollection"]
            
            # Collect repos from commits
            for item in contributions.get("commitContributionsByRepository", []):
                repo = item.get("repository", {})
                owner = repo.get("owner", {}).get("login", "")
                if owner.lower() == org_name.lower():  # Filter by org
                    repo_name = repo.get("name")
                    if repo_name:
                        repos_dict[repo_name] = {"name": repo_name, "url": repo.get("url", "")}
            
            # Collect repos from PRs
            for item in contributions.get("pullRequestContributionsByRepository", []):
                repo = item.get("repository", {})
                owner = repo.get("owner", {}).get("login", "")
                if owner.lower() == org_name.lower():
                    repo_name = repo.get("name")
                    if repo_name and repo_name not in repos_dict:
                        repos_dict[repo_name] = {"name": repo_name, "url": repo.get("url", "")}
            
            # Collect repos from issues
            for item in contributions.get("issueContributionsByRepository", []):
                repo = item.get("repository", {})
                owner = repo.get("owner", {}).get("login", "")
                if owner.lower() == org_name.lower():
                    repo_name = repo.get("name")
                    if repo_name and repo_name not in repos_dict:
                        repos_dict[repo_name] = {"name": repo_name, "url": repo.get("url", "")}
        
        return list(repos_dict.values())
    
    def _get_all_org_repos(self, org_name: str) -> list:
        """Get ALL repositories in organization with pagination."""
        all_repos = []
        has_next_page = True
        cursor = None
        
        while has_next_page:
            cursor_arg = f', after: "{cursor}"' if cursor else ''
            query = f"""
            query($orgName: String!) {{
              organization(login: $orgName) {{
                repositories(first: 100{cursor_arg}, orderBy: {{field: UPDATED_AT, direction: DESC}}) {{
                  pageInfo {{ hasNextPage endCursor }}
                  nodes {{ name url }}
                }}
              }}
            }}
            """
            
            result = self.user_activity_query({"orgName": org_name}, query)
            
            if "data" not in result or "errors" in result:
                break
            
            repos_data = result.get("data", {}).get("organization", {}).get("repositories", {})
            all_repos.extend(repos_data.get("nodes", []))
            
            page_info = repos_data.get("pageInfo", {})
            has_next_page = page_info.get("hasNextPage", False)
            cursor = page_info.get("endCursor")
        
        return all_repos
    
    def _fetch_repo_user_activity(self, org_name: str, repo_name: str, repo_url: str, 
                                   username: str, user_emails: list, from_date: str, to_date: str) -> Dict:
        """Fetch user-specific activity from a single repo - FILTERED at GraphQL level."""
        
        # Build email filter for commits (server-side filtering)
        if user_emails:
            emails_json = str(user_emails).replace("'", '"')
            author_filter = f'author: {{emails: {emails_json}}}, '
        else:
            author_filter = ""
        
        # First, check if user has ANY activity in this repo within date range
        # This prevents fetching from repos where user has no contributions
        check_query = """
        query($orgName: String!, $repoName: String!, $from: GitTimestamp!, $to: GitTimestamp!) {
          repository(owner: $orgName, name: $repoName) {
            defaultBranchRef {
              target {
                ... on Commit {
                  history(since: $from, until: $to, """ + author_filter + """first: 1) {
                    totalCount
                  }
                }
              }
            }
          }
        }
        """
        
        check_result = self.user_activity_query({
            "orgName": org_name,
            "repoName": repo_name,
            "from": from_date,
            "to": to_date
        }, check_query)
        
        # Skip if no commits found (user has no activity in default branch)
        if "data" in check_result and check_result.get("data", {}).get("repository"):
            if check_result["data"]["repository"].get("defaultBranchRef"):
                commit_count = check_result["data"]["repository"]["defaultBranchRef"]["target"]["history"]["totalCount"]
                if commit_count == 0:
                    logging.info(f"  No commits by {username} in {repo_name}, checking PRs/Issues only")
        
        # Query with user filtering at GraphQL level
        query = """
        query($orgName: String!, $repoName: String!, $from: GitTimestamp!, $to: GitTimestamp!) {
          repository(owner: $orgName, name: $repoName) {
            refs(refPrefix: "refs/heads/", first: 100) {
              nodes {
                name
                target {
                  ... on Commit {
                    history(since: $from, until: $to, """ + author_filter + """first: 100) {
                      nodes {
                        oid
                        messageHeadline
                        author {
                          user { login }
                          email
                          name
                        }
                        committedDate
                        additions
                        deletions
                        url
                      }
                    }
                  }
                }
              }
            }
            pullRequests(first: 100, orderBy: {field: UPDATED_AT, direction: DESC}) {
              nodes {
                number
                title
                url
                state
                isDraft
                author { login }
                createdAt
                updatedAt
                mergedAt
                closedAt
                commits { totalCount }
                additions
                deletions
                changedFiles
                mergedBy { login }
                assignees(first: 10) { nodes { login } }
                reviews(first: 50) {
                  nodes {
                    author { login }
                    state
                    submittedAt
                  }
                }
                comments(first: 50) { nodes { author { login } } }
                labels(first: 10) { nodes { name } }
              }
            }
            issues(first: 100, orderBy: {field: UPDATED_AT, direction: DESC}) {
              nodes {
                number
                title
                url
                state
                author { login }
                createdAt
                updatedAt
                closedAt
                assignees(first: 10) { nodes { login } }
                participants(first: 50) { nodes { login } }
                comments(first: 50) { nodes { author { login } } }
                labels(first: 10) { nodes { name } }
              }
            }
          }
        }
        """
        
        variables = {
            "orgName": org_name,
            "repoName": repo_name,
            "from": from_date,
            "to": to_date
        }
        
        result = self.user_activity_query(variables, query)
        
        if "data" not in result or "errors" in result:
            return None
        
        repo_data = result.get("data", {}).get("repository", {})
        if not repo_data:
            return None
        
        # Parse commits (deduplicate by OID across branches)
        commits = []
        seen_oids = set()
        for ref in repo_data.get("refs", {}).get("nodes", []):
            branch = ref.get("name")
            for commit in ref.get("target", {}).get("history", {}).get("nodes", []):
                oid = commit.get("oid")
                if oid not in seen_oids:
                    seen_oids.add(oid)
                    commits.append({
                        "repo": repo_name,
                        "repo_url": repo_url,
                        "branch": branch,
                        "oid": oid[:7],
                        "full_oid": oid,
                        "message": commit.get("messageHeadline", ""),
                        "author": commit.get("author", {}).get("name", "Unknown"),
                        "date": commit.get("committedDate", ""),
                        "additions": commit.get("additions", 0),
                        "deletions": commit.get("deletions", 0),
                        "url": commit.get("url", "")
                    })
        
        # Parse PRs (filter by user involvement)
        prs = []
        for pr in repo_data.get("pullRequests", {}).get("nodes", []):
            pr_author = pr.get("author", {}).get("login", "") if pr.get("author") else ""
            merged_by = pr.get("mergedBy", {}).get("login", "") if pr.get("mergedBy") else ""
            assignees = [a.get("login") for a in pr.get("assignees", {}).get("nodes", [])]
            reviewers = [r.get("author", {}).get("login") for r in pr.get("reviews", {}).get("nodes", []) if r.get("author")]
            commenters = [c.get("author", {}).get("login") for c in pr.get("comments", {}).get("nodes", []) if c.get("author")]
            
            if username in [pr_author, merged_by] + assignees + reviewers + commenters:
                roles = []
                if pr_author == username: roles.append("Author")
                if merged_by == username: roles.append("Merged")
                if username in assignees: roles.append("Assigned")
                
                if username in reviewers:
                    user_reviews = [r for r in pr.get("reviews", {}).get("nodes", []) if r.get("author", {}).get("login") == username]
                    states = set(r.get("state") for r in user_reviews)
                    if "APPROVED" in states: roles.append("Approved")
                    elif "CHANGES_REQUESTED" in states: roles.append("Requested Changes")
                    elif "COMMENTED" in states: roles.append("Reviewed")
                
                if username in commenters and "Author" not in roles:
                    roles.append("Commented")
                
                prs.append({
                    "repo": repo_name,
                    "repo_url": repo_url,
                    "number": pr.get("number", 0),
                    "title": pr.get("title", ""),
                    "author": pr_author,
                    "state": pr.get("state", ""),
                    "is_draft": pr.get("isDraft", False),
                    "created_at": pr.get("createdAt", ""),
                    "updated_at": pr.get("updatedAt", ""),
                    "merged_at": pr.get("mergedAt", ""),
                    "merged_by": merged_by,
                    "additions": pr.get("additions", 0),
                    "deletions": pr.get("deletions", 0),
                    "changed_files": pr.get("changedFiles", 0),
                    "commits_count": pr.get("commits", {}).get("totalCount", 0),
                    "url": pr.get("url", ""),
                    "user_roles": ", ".join(roles),
                    "labels": [l.get("name") for l in pr.get("labels", {}).get("nodes", [])]
                })
        
        # Parse issues (filter by user involvement)
        issues = []
        for issue in repo_data.get("issues", {}).get("nodes", []):
            issue_author = issue.get("author", {}).get("login", "") if issue.get("author") else ""
            assignees = [a.get("login") for a in issue.get("assignees", {}).get("nodes", [])]
            participants = [p.get("login") for p in issue.get("participants", {}).get("nodes", [])]
            commenters = [c.get("author", {}).get("login") for c in issue.get("comments", {}).get("nodes", []) if c.get("author")]
            
            if username in [issue_author] + assignees + participants + commenters:
                roles = []
                if issue_author == username: roles.append("Author")
                if username in assignees: roles.append("Assigned")
                if username in commenters and "Author" not in roles:
                    count = len([c for c in issue.get("comments", {}).get("nodes", []) if c.get("author", {}).get("login") == username])
                    roles.append(f"Commented ({count})")
                if username in participants and not roles:
                    roles.append("Participant")
                
                issues.append({
                    "repo": repo_name,
                    "repo_url": repo_url,
                    "number": issue.get("number", 0),
                    "title": issue.get("title", ""),
                    "author": issue_author,
                    "state": issue.get("state", ""),
                    "created_at": issue.get("createdAt", ""),
                    "updated_at": issue.get("updatedAt", ""),
                    "closed_at": issue.get("closedAt", ""),
                    "url": issue.get("url", ""),
                    "user_roles": ", ".join(roles),
                    "labels": [l.get("name") for l in issue.get("labels", {}).get("nodes", [])]
                })
        
        return {
            "commits": commits,
            "prs": prs,
            "issues": issues,
            "has_activity": len(commits) > 0 or len(prs) > 0 or len(issues) > 0
        }
    
    def _empty_activity_response(self, username: str, org_name: str, from_date: str, to_date: str, page: int = 1, per_page: int = 50) -> Dict:
        """Return empty activity response with pagination info."""
        return {
            "status": "success",
            "summary": {
                "user": username,
                "organization": org_name,
                "date_range": f"{from_date} to {to_date}",
                "total_commits": 0,
                "total_prs_involved": 0,
                "prs_authored": 0,
                "prs_reviewed": 0,
                "prs_merged": 0,
                "total_additions": 0,
                "total_deletions": 0,
            },
            "commits": [],
            "prs": [],
            "issues": [],
            "pagination": {
                "current_page": page,
                "per_page": per_page,
                "commits": {"total": 0, "total_pages": 1, "has_next_page": False, "returned": 0},
                "prs": {"total": 0, "total_pages": 1, "has_next_page": False, "returned": 0},
                "issues": {"total": 0, "total_pages": 1, "has_next_page": False, "returned": 0},
                "repos": {"total_in_org": 0, "with_user_activity": 0}
            }
        }
