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

GITHUB_TOKEN = getenv('GITHUB_TOKEN')

# Set up logging for the application
logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)

class GutHubIntegration:
    def __init__(self):
        # Initialize GitHub token
        self.github_token = GITHUB_TOKEN
        if not self.github_token:
            raise ValueError("Missing GitHub GITHUB_TOKEN in environment variables")
        
        logging.info("GitHub Integration initialized")

    def _get_headers(self):
        """Get headers for GitHub API requests."""
        return {
            'Authorization': f'token {self.github_token}',
            'Accept': 'application/vnd.github.v3+json'
        }
    
    def _get_pr_url(self, repo_owner: str, repo_name: str, pr_number: int) -> str:
        """Construct the URL for a specific pull request."""
        return f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}"

    def get_pr_content(self, repo_owner: str, repo_name: str, pr_number: int) -> dict:
        """Fetch the content of a pull request.
        
        Args:
            repo_owner: The owner of the GitHub repository
            repo_name: The name of the GitHub repository
            pr_number: The number of the pull request to analyze
            
        Returns:
            A dictionary containing PR metadata and file changes
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
    def update_pr_description(self, repo_owner: str, repo_name: str, pr_number: int, new_description: str) -> dict:
        """Update the description of a pull request.

        Args:
            repo_owner: The owner of the GitHub repository
            repo
            repo_name: The name of the GitHub repository
            pr_number: The number of the pull request to update
            new_description: The new description for the pull request
        Returns:
            A dictionary containing the updated pull request's metadata
        """
        logging.info(f"Updating PR description for {repo_owner}/{repo_name}#{pr_number}")


        # Construct the PR URL
        pr_url = self._get_pr_url(repo_owner, repo_name, pr_number)
        try:
            # Update the PR description
            response = requests.patch(pr_url, headers=self._get_headers(), json={
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

    def create_issue(self, repo_owner: str, repo_name: str, title: str, body: str) -> dict:
        """Create a new issue in the specified GitHub repository.
        
        Args:
            repo_owner: The owner of the GitHub repository
            repo_name: The name of the GitHub repository
            title: The title of the issue
            body: The body content of the issue
        Returns:
            A dictionary containing the created issue's metadata
        """
        logging.info(f"Creating issue in {repo_owner}/{repo_name}")
        
        # Construct the issues URL
        issues_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues"
        
        try:
            # Create the issue
            response = requests.post(issues_url, headers=self._get_headers(), json={
                'title': title,
                'body': body,
                'labels': ['mcp']
            })
            response.raise_for_status()
            issue_data = response.json()
            
            logging.info(f"Issue created successfully")
            return issue_data
            
        except Exception as e:
            logging.error(f"Error creating issue: {str(e)}")
            traceback.print_exc()
            return None

    def update_issue(self, repo_owner: str, repo_name: str, issue_number: int, title: str, body: str) -> dict:
        """Update an existing issue in the specified GitHub repository.
        
        Args:
            repo_owner: The owner of the GitHub repository
            repo_name: The name of the GitHub repository
            issue_number: The number of the issue to update
            title: The new title of the issue
            body: The new body content of the issue
        Returns:
            A dictionary containing the updated issue's metadata
        """
        logging.info(f"Updating issue {issue_number} in {repo_owner}/{repo_name}")

        # Construct the issue URL
        issue_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/{issue_number}"

        try:
            # Update the issue
            response = requests.patch(issue_url, headers=self._get_headers(), json={
                'title': title,
                'body': body
            })
            response.raise_for_status()
            issue_data = response.json()
            logging.info(f"Issue updated successfully")
            return issue_data
        except Exception as e:
            logging.error(f"Error updating issue: {str(e)}")
            traceback.print_exc()
            return None
