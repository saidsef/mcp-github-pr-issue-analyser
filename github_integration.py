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
from typing import Dict, Any, Optional

GITHUB_TOKEN = getenv('GITHUB_TOKEN')

# Set up logging for the application
logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)

class GitHubIntegration:
    def __init__(self):
        """
        Initialize the GitHubIntegration class.

        Returns:
            None

        Raises:
            ValueError: If the GitHub token is not found in environment variables.
        """
        self.github_token = GITHUB_TOKEN
        if not self.github_token:
            raise ValueError("Missing GitHub GITHUB_TOKEN in environment variables")
        
        logging.info("GitHub Integration initialized")

    def _get_headers(self):
        """
        Return the headers required for GitHub API requests.

        Returns:
            dict: A dictionary containing the required HTTP headers.

        Raises:
            ValueError: If the GitHub token is missing.

        Error Handling:
            Raises ValueError if the GitHub token is not set.
        """
        if not self.github_token:
            raise ValueError("GitHub token is missing for API requests")
        return {
            'Authorization': f'token {self.github_token}',
            'Accept': 'application/vnd.github.v3+json'
        }
    
    def _get_pr_url(self, repo_owner: str, repo_name: str, pr_number: int) -> str:
        """
        Generates the GitHub API URL for a specific pull request in a given repository.
        Args:
            repo_owner (str): The owner of the GitHub repository.
            repo_name (str): The name of the GitHub repository.
            pr_number (int): The pull request number.
        Returns:
            str: The formatted GitHub API URL for the specified pull request.
        Raises:
            ValueError: If any of the arguments are empty or if pr_number is not a positive integer.
        """
        
        return f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}"
    
    def get_pr_diff(self, repo_owner: str, repo_name: str, pr_number: int) -> str:
        """
        Fetches the diff/patch of a pull request from a GitHub repository.
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
            response = requests.get(f"https://patch-diff.githubusercontent.com/raw/{repo_owner}/{repo_name}/pull/{pr_number}.patch", headers=self._get_headers())
            response.raise_for_status()
            pr_patch = response.text
            
            logging.info(f"Successfully fetched PR diff/patch")
            return pr_patch
            
        except Exception as e:
            logging.error(f"Error fetching PR diff: {str(e)}")
            traceback.print_exc()
            return None

    def get_pr_content(self, repo_owner: str, repo_name: str, pr_number: int) -> Dict[str, Any]:
        """
        Fetches the content of a specific pull request from a GitHub repository.
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
            response = requests.get(pr_url, headers=self._get_headers())
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
            
            logging.info(f"Successfully fetched PR content")
            return pr_info
            
        except Exception as e:
            logging.error(f"Error fetching PR content: {str(e)}")
            traceback.print_exc()
            return None

    def add_pr_comments(self, repo_owner: str, repo_name: str, pr_number: int, comment: str) -> Dict[str, Any]:
        """
        Adds a comment to a specified pull request on GitHub.
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
            response = requests.post(comments_url, headers=self._get_headers(), json={'body': comment})
            response.raise_for_status()
            comment_data = response.json()

            logging.info(f"Comment added successfully")
            return comment_data

        except Exception as e:
            logging.error(f"Error adding comment: {str(e)}")
            traceback.print_exc()
            return None

    def update_pr_description(self, repo_owner: str, repo_name: str, pr_number: int, new_title: str, new_description: str) -> Dict[str, Any]:
        """
        Updates the title and description of a pull request on GitHub.
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
            })
            response.raise_for_status()
            pr_data = response.json()
            
            logging.info(f"PR description updated successfully")
            return pr_data
        except Exception as e:
            logging.error(f"Error updating PR description: {str(e)}")
            traceback.print_exc()
            return None

    def create_issue(self, repo_owner: str, repo_name: str, title: str, body: str, labels: list[str]) -> Dict[str, Any]:
        """
        Creates a new issue in the specified GitHub repository.
        When issue is created add comment to PR with "Resolves: #<issue_number>" using add_pr_comments function.
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
            })
            response.raise_for_status()
            issue_data = response.json()
            
            logging.info(f"Issue created successfully")
            return issue_data

        except Exception as e:
            logging.error(f"Error creating issue: {str(e)}")
            traceback.print_exc()
            return None

    def update_issue(self, repo_owner: str, repo_name: str, issue_number: int, title: str, body: str, labels: list[str] = [], state: str = 'open') -> Dict[str, Any]:
        """
        Updates an existing GitHub issue with the specified parameters.
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
            })
            response.raise_for_status()
            issue_data = response.json()
            logging.info(f"Issue updated successfully")
            return issue_data
        except Exception as e:
            logging.error(f"Error updating issue: {str(e)}")
            traceback.print_exc()
            return None

    def get_latest_sha(self, repo_owner: str, repo_name: str) -> Optional[str]:
        """
        Fetches the latest commit SHA from a specified GitHub repository.
        Args:
            repo_owner (str): The owner of the GitHub repository.
            repo_name (str): The name of the GitHub repository.
        Returns:
            Optional[str]: The SHA string of the latest commit if found, otherwise None.
        Error Handling:
            Logs errors and warnings if the request fails, the response is invalid, or no commits are found.
            Returns None in case of exceptions or if the repository has no commits.
        """
        logging.info(f"Fetching latest commit SHA for {repo_owner}/{repo_name}")

        # Construct the commits URL
        commits_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/commits"

        try:
            # Fetch the latest commit
            response = requests.get(commits_url, headers=self._get_headers())
            response.raise_for_status()
            commits_data = response.json()

            if commits_data:
                latest_sha = commits_data[0]['sha']
                logging.info(f"Latest commit SHA fetched successfully")
                return latest_sha
            else:
                logging.warning("No commits found in the repository")
                return None

        except Exception as e:
            logging.error(f"Error fetching latest commit SHA: {str(e)}")
            traceback.print_exc()
            return None

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
            })
            response.raise_for_status()
            tag_data = response.json()

            logging.info(f"Tag created successfully")
            return tag_data

        except Exception as e:
            logging.error(f"Error creating tag: {str(e)}")
            traceback.print_exc()
            return None

    def create_release(self, repo_owner: str, repo_name: str, tag_name: str, release_name: str, body: str) -> Dict[str, Any]:
        """
        Creates a new release in the specified GitHub repository.
        Args:
            repo_owner (str): The owner of the repository.
            repo_name (str): The name of the repository.
            tag_name (str): The tag name for the release.
            release_name (str): The name of the release.
            body (str): The description or body content of the release.
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
                'draft': False,
                'prerelease': False,
                'generate_release_notes': True
            })
            response.raise_for_status()
            release_data = response.json()

            logging.info(f"Release created successfully")
            return release_data

        except Exception as e:
            logging.error(f"Error creating release: {str(e)}")
            traceback.print_exc()
            return None
