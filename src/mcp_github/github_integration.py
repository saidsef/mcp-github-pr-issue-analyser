#!/usr/bin/env python3

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

import asyncio
import logging
from os import getenv
from typing import Annotated, Any, Literal, TypedDict

import httpx
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from .auth import (
    GITHUB_OAUTH_BASE_URL,
    GITHUB_OAUTH_CLIENT_ID,
    GITHUB_OAUTH_CLIENT_SECRET,
    APIKeyVerifier,
    get_oauth_verifier,
    resolve_token,
)
from .exceptions import (
    GitHubAPIError,
    GitHubAuthError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubValidationError,
)
from .graphql_client import GraphQLClient
from .graphql_queries import PR_LINKED_ISSUES_QUERY, PR_STATUS_CHECKS_QUERY, SEARCH_USER_QUERY, USER_CONTRIBUTIONS_QUERY


class PRContent(TypedDict):
    title: str
    description: str | None
    author: str
    created_at: str
    updated_at: str
    state: str


class CommentData(TypedDict):
    id: int
    body: str
    user: dict[str, Any]
    created_at: str
    updated_at: str


class IssueData(TypedDict):
    id: int
    number: int
    title: str
    body: str | None
    state: str
    user: dict[str, Any]
    created_at: str
    updated_at: str
    labels: list[dict[str, Any]]


class UserSearchResult(TypedDict):
    login: str
    name: str | None
    email: str | None
    company: str | None
    location: str | None
    bio: str | None
    url: str
    avatar_url: str | None
    created_at: str
    updated_at: str
    followers: int
    following: int
    public_repos: int
    recent_repos: list[dict[str, Any]]
    organizations: list[dict[str, Any]]


class UserActivityResult(TypedDict):
    username: str
    date_range: dict[str, str] | None
    total_contributions: dict[str, int]
    commits: list[dict[str, Any]]
    pull_requests: list[dict[str, Any]]
    issues: list[dict[str, Any]]
    reviews: list[dict[str, Any]]


class LinkedIssuesResult(TypedDict):
    pr_number: int
    linked_issues: list[dict[str, Any]]


class StatusChecksResult(TypedDict):
    pr_number: int
    overall: str
    check_runs: list[dict[str, Any]]
    commit_statuses: list[dict[str, Any]]


GITHUB_TOKEN = getenv("GITHUB_TOKEN")
TIMEOUT = int(getenv("GITHUB_API_TIMEOUT", "5"))  # seconds, configurable via env

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)


def _annotate(*, ro: bool = False, destructive: bool = False) -> Any:
    def deco(fn: Any = None, *, task: bool = False) -> Any:
        def apply(f: Any) -> Any:
            f._mcp_annotations = ToolAnnotations(readOnlyHint=ro, destructiveHint=destructive)
            f._mcp_task = task
            return f

        if fn is not None:
            return apply(fn)
        return apply

    return deco


_read_only = _annotate(ro=True)
_write = _annotate()
_destructive = _annotate(destructive=True)


class GitHubIntegration:
    def __init__(self):
        """Initialises the GitHubIntegration instance."""
        self.github_token = GITHUB_TOKEN

        # Detect OAuth2 mode first so the token check can be conditional
        self._oauth_mode = bool(GITHUB_OAUTH_CLIENT_ID and GITHUB_OAUTH_CLIENT_SECRET and GITHUB_OAUTH_BASE_URL)

        # GITHUB_TOKEN is required only in static-token (non-OAuth2) mode
        if not self._oauth_mode and not self.github_token:
            raise ValueError("Missing GitHub GITHUB_TOKEN in environment variables")

        # APIKeyVerifier only used in static-token mode
        self.verifier = APIKeyVerifier(self.github_token) if self.github_token else None

        # GraphQL client: token overridden per-call in OAuth2 mode via _resolve_token()
        self.graphql = GraphQLClient(self.github_token or "", timeout=TIMEOUT)

        logger.info("GitHub Integration Initialised")

    @property
    def _oauth_verifier(self):
        """Returns a GitHubProvider instance for OAuth2 authentication."""
        return get_oauth_verifier()

    def _resolve_token(self) -> str:
        """Return the token for the current request."""
        return resolve_token(self.github_token, self._oauth_mode)

    def _handle_response_error(self, response: httpx.Response, context: str = ""):
        """Handle HTTP errors from GitHub API with specific exceptions."""
        status = response.status_code

        try:
            response_body = response.json()
        except Exception:
            response_body = None

        if status == 401:
            msg = "Authentication failed. Check your GitHub token."
            if self._oauth_mode:
                msg += (
                    " The GitHub OAuth authorization may have been revoked — please re-authenticate via the OAuth flow."
                )
            raise GitHubAuthError(msg, response_body=response_body)

        if status == 403:
            self._raise_for_403(response, response_body)

        if status == 404:
            raise GitHubNotFoundError(
                f"{context}: Resource not found" if context else "Resource not found",
                response_body=response_body,
            )

        if status == 422:
            raise GitHubValidationError("Validation failed. Check your input data.", response_body=response_body)

        message = f"GitHub API error ({context})" if context else "GitHub API error"
        raise GitHubAPIError(
            f"{message}: {status} - {response.reason_phrase}",
            status_code=status,
            response_body=response_body,
        )

    def _raise_for_403(self, response: httpx.Response, response_body: dict | None):
        """Handle 403 response — distinguishes rate limit from permission error."""
        error_text = response.text.lower()
        if "rate limit" not in error_text and "api rate limit" not in error_text:
            raise GitHubAPIError(
                "Permission denied. Check your token permissions.",
                status_code=403,
                response_body=response_body,
            )
        reset_header = response.headers.get("X-RateLimit-Reset")
        raise GitHubRateLimitError(
            "GitHub API rate limit exceeded. Please wait before making more requests.",
            response_body=response_body,
            reset_timestamp=int(reset_header) if reset_header else None,
        )

    def _raise_for_status(self, response: httpx.Response, context: str = "") -> None:
        """Raise the appropriate exception if the response indicates an error."""
        if not response.is_success:
            self._handle_response_error(response, context)

    def _get_headers(self):
        """Constructs the HTTP headers required for GitHub API requests."""
        token = self._resolve_token()
        if not token:
            raise ValueError("GitHub token is missing for API requests")
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }
        return headers

    async def _request(self, method: str, url: str, *, context: str = "", **kwargs: Any) -> httpx.Response:
        """Make an HTTP request and handle errors."""
        ctx = context or url
        logger.info(f"{method.upper()} {ctx}")
        try:
            async with httpx.AsyncClient() as client:
                response = await client.request(method, url, headers=self._get_headers(), timeout=TIMEOUT, **kwargs)
            self._raise_for_status(response, context)
            logger.info(f"Success {method.upper()} {ctx}")
            return response
        except GitHubAuthError:
            raise
        except Exception as e:
            raise ToolError(str(e)) from e

    @_read_only
    async def get_pr_diff(self, repo_owner: str, repo_name: str, pr_number: int) -> str:
        """Fetches the diff/patch of a specific pull request."""
        url = f"https://patch-diff.githubusercontent.com/raw/{repo_owner}/{repo_name}/pull/{pr_number}.patch"
        return (await self._request("GET", url, context=f"PR #{pr_number} diff")).text

    @_read_only
    async def get_pr_content(self, repo_owner: str, repo_name: str, pr_number: int) -> PRContent:
        """Fetches the content/details of a specific pull request."""
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}"
        data = (await self._request("GET", url, context=f"PR #{pr_number}")).json()
        return {
            "title": data["title"],
            "description": data["body"],
            "author": data["user"]["login"],
            "created_at": data["created_at"],
            "updated_at": data["updated_at"],
            "state": data["state"],
        }

    @_write
    async def add_pr_comments(self, repo_owner: str, repo_name: str, pr_number: int, comment: str) -> CommentData:
        """Adds a comment to a specific pull request."""
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/{pr_number}/comments"
        return (await self._request("POST", url, context=f"PR #{pr_number} comment", json={"body": comment})).json()

    @_write
    async def add_inline_pr_comment(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        path: str,
        line: int,
        comment_body: str,
    ) -> CommentData:
        """Adds an inline review comment to a specific line in a file within a PR."""
        pr_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}"
        pr_data = (await self._request("GET", pr_url, context=f"PR #{pr_number}")).json()
        commit_id = pr_data.get("head", {}).get("sha")
        if not commit_id:
            raise ToolError(f"Could not retrieve head SHA for PR #{pr_number}")
        review_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}/comments"
        payload = {"body": comment_body, "commit_id": commit_id, "path": path, "line": line, "side": "RIGHT"}
        return (
            await self._request("POST", review_url, context=f"inline comment on {path}:{line}", json=payload)
        ).json()

    @_write
    async def update_pr_description(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        new_title: str,
        new_description: str,
    ) -> PRContent:
        """Updates the title and description of a specific pull request."""
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}"
        await self._request(
            "PATCH", url, context=f"PR #{pr_number}", json={"title": new_title, "body": new_description}
        )
        return await self.get_pr_content(repo_owner, repo_name, pr_number)

    @_write
    async def create_pr(
        self,
        repo_owner: str,
        repo_name: str,
        title: str,
        body: str,
        head: str,
        base: str,
        draft: bool = False,
    ) -> dict[str, Any]:
        """Creates a new pull request."""
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls"
        data = (
            await self._request(
                "POST",
                url,
                context=f"create PR {head} -> {base}",
                json={"title": title, "body": body, "head": head, "base": base, "draft": draft},
            )
        ).json()
        return {
            "pr_url": data.get("html_url"),
            "pr_number": data.get("number"),
            "status": data.get("state"),
            "title": data.get("title"),
        }

    @_read_only
    async def list_open_issues_prs(
        self,
        repo_owner: str,
        repo_name: str = "",
        issue: Literal["pr", "issue"] = "pr",
        filtering: Literal["user", "org", "repo", "involves"] = "involves",
        per_page: Annotated[int, "Number of results per page (1-100)"] = 50,
        page: int = 1,
    ) -> dict[str, Any]:
        """Lists open pull requests or issues."""
        if filtering == "repo":
            if not repo_name:
                raise ToolError("repo_name is required when filtering='repo'")
            search_target = f"{repo_owner}/{repo_name}"
        else:
            search_target = repo_owner
        url = f"https://api.github.com/search/issues?q=is:{issue}+is:open+{filtering}:{search_target}&per_page={per_page}&page={page}"
        data = (await self._request("GET", url, context=f"list open {issue}s for {search_target}")).json()
        return {
            "total": data["total_count"],
            f"open_{issue}s": [
                {
                    "url": item["html_url"],
                    "title": item["title"],
                    "number": item["number"],
                    "state": item["state"],
                    "created_at": item["created_at"],
                    "updated_at": item["updated_at"],
                    "author": item["user"]["login"],
                    "label_names": [label["name"] for label in item.get("labels", [])],
                    "is_draft": item.get("draft", False),
                }
                for item in data["items"]
            ],
        }

    @_write
    async def create_issue(
        self, repo_owner: str, repo_name: str, title: str, body: str, labels: list[str]
    ) -> IssueData:
        """Creates a new issue."""
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues"
        issue_labels = ["mcp"] if not labels else labels + ["mcp"]
        return (
            await self._request(
                "POST",
                url,
                context=f"create issue in {repo_owner}/{repo_name}",
                json={"title": title, "body": body, "labels": issue_labels},
            )
        ).json()

    @_destructive
    async def merge_pr(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        commit_title: str | None = None,
        commit_message: str | None = None,
        merge_method: Literal["merge", "squash", "rebase"] = "squash",
    ) -> dict[str, Any]:
        """Merges a specific pull request."""
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}/merge"
        payload: dict[str, Any] = {"merge_method": merge_method}
        if commit_title is not None:
            payload["commit_title"] = commit_title
        if commit_message is not None:
            payload["commit_message"] = commit_message
        try:
            return (await self._request("PUT", url, context=f"PR #{pr_number} merge", json=payload)).json()
        except GitHubAPIError as e:
            github_msg = (e.response_body or {}).get("message", "") if e.response_body else ""
            detail = github_msg or str(e)
            logger.error(f"Error merging PR: {detail}")
            raise ToolError(detail) from e

    @_write
    async def update_pr_branch(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        expected_head_sha: str | None = None,
    ) -> dict[str, Any]:
        """Updates the pull request branch with the latest upstream changes."""
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}/update-branch"
        payload: dict[str, Any] = {}
        if expected_head_sha is not None:
            payload["expected_head_sha"] = expected_head_sha
        return (await self._request("PUT", url, context=f"PR #{pr_number} update branch", json=payload)).json()

    @_write
    async def update_issue(
        self,
        repo_owner: str,
        repo_name: str,
        issue_number: int,
        title: str,
        body: str,
        labels: list[str] = [],
        state: Literal["open", "closed"] = "open",
    ) -> IssueData:
        """Updates an existing issue."""
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/{issue_number}"
        return (
            await self._request(
                "PATCH",
                url,
                context=f"issue #{issue_number}",
                json={"title": title, "body": body, "labels": labels, "state": state},
            )
        ).json()

    @_write
    async def update_reviews(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        event: Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"],
        body: str | None = None,
    ) -> dict[str, Any]:
        """Submits a review for a specific pull request."""
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}/reviews"
        return (
            await self._request("POST", url, context=f"PR #{pr_number} review", json={"body": body, "event": event})
        ).json()

    @_write
    async def update_assignees(
        self, repo_owner: str, repo_name: str, issue_number: int, assignees: list[str]
    ) -> dict[str, Any]:
        """Updates the assignees for a specific issue or pull request."""
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/{issue_number}"
        data = (
            await self._request(
                "PATCH", url, context=f"issue/PR #{issue_number} assignees", json={"assignees": assignees}
            )
        ).json()
        actual_logins = {u["login"] for u in data.get("assignees", [])}
        requested = set(assignees)
        missing = requested - actual_logins
        if missing:
            logger.warning(f"Some assignees were not applied: {missing}")
            return {
                "status": "partial",
                "message": f"The following assignees could not be applied (not a collaborator or user does not exist): {sorted(missing)}",
                "assignees_requested": sorted(requested),
                "assignees_applied": sorted(actual_logins),
                "issue": data,
            }
        return data

    @_read_only
    async def get_latest_sha(self, repo_owner: str, repo_name: str) -> str | None:
        """Fetches the SHA of the latest commit."""
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/commits"
        data = (await self._request("GET", url, context=f"commits for {repo_owner}/{repo_name}")).json()
        if data:
            return data[0]["sha"]
        return "No commits found in the repository"

    @_write
    async def create_tag(self, repo_owner: str, repo_name: str, tag_name: str, message: str) -> dict[str, Any]:
        """Creates a new tag."""
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/git/refs"
        latest_sha = await self.get_latest_sha(repo_owner, repo_name)
        if not latest_sha:
            raise ValueError("Failed to fetch the latest commit SHA")
        return (
            await self._request(
                "POST",
                url,
                context=f"create tag {tag_name}",
                json={"ref": f"refs/tags/{tag_name}", "sha": latest_sha, "message": message},
            )
        ).json()

    @_write
    async def create_release(
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
    ) -> dict[str, Any]:
        """Creates a new release."""
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/releases"
        return (
            await self._request(
                "POST",
                url,
                context=f"create release {release_name}",
                json={
                    "tag_name": tag_name,
                    "name": release_name,
                    "body": body,
                    "draft": draft,
                    "prerelease": prerelease,
                    "generate_release_notes": generate_release_notes,
                    "make_latest": make_latest,
                },
            )
        ).json()

    @_read_only(task=True)
    async def search_user(self, username: str) -> UserSearchResult:
        """Search for a GitHub user by username using GraphQL API."""
        logger.info(f"Searching for GitHub user: {username}")
        try:
            result = await asyncio.to_thread(
                self.graphql.execute_query,
                SEARCH_USER_QUERY,
                variables={"username": username},
                token=self._resolve_token(),
            )
            user_data = result.get("user")
            if not user_data:
                raise GitHubNotFoundError(f"User '{username}' not found")
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
            logger.info(f"Successfully found user: {username}")
            return user_info
        except GitHubNotFoundError:
            raise
        except Exception as e:
            logger.error(f"Error searching for user {username}: {e}")
            raise GitHubAPIError(f"Failed to search for user: {e}") from e

    def _filtered_contributions(self, collection: dict[str, Any], key: str, org: str, repo: str):
        for repo_contrib in collection.get(key, []):
            repo_info = repo_contrib["repository"]
            owner = repo_info["owner"]["login"]
            repo_name = repo_info["name"]
            if org and owner.lower() != org.lower():
                continue
            if repo and repo_name.lower() != repo.lower():
                continue
            yield repo_contrib, owner, repo_name

    @_read_only(task=True)
    async def get_user_activities(
        self,
        username: str,
        org: str = "",
        repo: str = "",
        since: str = "",
        until: str = "",
        max_results: int = 50,
    ) -> UserActivityResult:
        """Get user activities with optional filtering by org, repo, and date range using GraphQL API. since/until accept YYYY-MM-DD or full ISO 8601 (YYYY-MM-DDTHH:MM:SSZ)."""
        logger.info(f"Fetching user activities for {username} (org={org}, repo={repo}, since={since}, until={until})")
        try:
            variables: dict[str, Any] = {"username": username}
            if since:
                variables["since"] = since + "T00:00:00Z" if len(since) == 10 else since
            if until:
                variables["until"] = until + "T23:59:59Z" if len(until) == 10 else until
            result = await asyncio.to_thread(
                self.graphql.execute_query,
                USER_CONTRIBUTIONS_QUERY,
                variables=variables,
                token=self._resolve_token(),
            )
            user_data = result.get("user")
            if not user_data:
                raise GitHubNotFoundError(f"User '{username}' not found")
            collection = user_data.get("contributionsCollection", {})
            date_range = None
            if since or until:
                date_range = {
                    "since": variables.get("since", collection.get("startedAt", "")),
                    "until": variables.get("until", collection.get("endedAt", "")),
                }
            commits = []
            pull_requests = []
            issues = []
            reviews = []
            for repo_contrib, owner, repo_name in self._filtered_contributions(
                collection, "commitContributionsByRepository", org, repo
            ):
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
            for repo_contrib, owner, repo_name in self._filtered_contributions(
                collection, "pullRequestContributionsByRepository", org, repo
            ):
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
            for repo_contrib, owner, repo_name in self._filtered_contributions(
                collection, "issueContributionsByRepository", org, repo
            ):
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
            for repo_contrib, owner, repo_name in self._filtered_contributions(
                collection, "pullRequestReviewContributionsByRepository", org, repo
            ):
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
            logger.info(
                f"Successfully fetched activities: {len(commits)} commits, "
                f"{len(pull_requests)} PRs, {len(issues)} issues, {len(reviews)} reviews"
            )
            return activity_result
        except GitHubNotFoundError:
            raise
        except Exception as e:
            logger.error(f"Error fetching user activities for {username}: {e}")
            raise GitHubAPIError(f"Failed to fetch user activities: {e}") from e

    @_read_only(task=True)
    async def get_pr_linked_issues(self, repo_owner: str, repo_name: str, pr_number: int) -> LinkedIssuesResult:
        """Return the issues that will be auto-closed when a pull request is merged."""
        logger.info(f"Fetching linked issues for PR #{pr_number} in {repo_owner}/{repo_name}")
        try:
            result = await asyncio.to_thread(
                self.graphql.execute_query,
                PR_LINKED_ISSUES_QUERY,
                variables={"owner": repo_owner, "repo": repo_name, "number": pr_number},
                token=self._resolve_token(),
            )
            repo_data = result.get("repository")
            if not repo_data or not repo_data.get("pullRequest"):
                raise GitHubNotFoundError(f"PR #{pr_number} not found in {repo_owner}/{repo_name}")
            nodes = repo_data["pullRequest"]["closingIssuesReferences"]["nodes"]
            linked_issues = [
                {
                    "number": issue["number"],
                    "title": issue["title"],
                    "state": issue["state"],
                    "url": issue["url"],
                    "created_at": issue["createdAt"],
                    "labels": [label["name"] for label in issue["labels"]["nodes"]],
                }
                for issue in nodes
            ]
            logger.info(f"Found {len(linked_issues)} linked issue(s) for PR #{pr_number}")
            return {"pr_number": pr_number, "linked_issues": linked_issues}
        except GitHubNotFoundError:
            raise
        except Exception as e:
            logger.error(f"Error fetching linked issues for PR #{pr_number}: {e}")
            raise GitHubAPIError(f"Failed to fetch linked issues: {e}") from e

    def _flatten_check_runs(self, head_target: dict[str, Any]) -> list[dict[str, Any]]:
        """Flatten check suites into a single list of check run dicts."""
        check_runs: list[dict[str, Any]] = []
        for suite in (head_target.get("checkSuites") or {}).get("nodes", []):
            app_name = (suite.get("app") or {}).get("name", "unknown")
            for run in (suite.get("checkRuns") or {}).get("nodes", []):
                check_runs.append(
                    {
                        "name": run["name"],
                        "status": run["status"],
                        "conclusion": run.get("conclusion"),
                        "details_url": run.get("detailsUrl"),
                        "suite_app": app_name,
                    }
                )
        return check_runs

    def _extract_commit_statuses(self, head_target: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract legacy commit status contexts from a HEAD commit target."""
        commit_status = head_target.get("status") or {}
        return [
            {
                "context": ctx["context"],
                "state": ctx["state"],
                "description": ctx.get("description"),
                "target_url": ctx.get("targetUrl"),
            }
            for ctx in commit_status.get("contexts", [])
        ]

    def _has_failing_checks(self, check_runs: list[dict[str, Any]], legacy: set[str]) -> bool:
        failing = {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}
        conclusions = {r["conclusion"] for r in check_runs if r["conclusion"]}
        return bool(conclusions & failing) or "FAILURE" in legacy or "ERROR" in legacy

    def _has_pending_checks(self, check_runs: list[dict[str, Any]], legacy: set[str]) -> bool:
        pending = {"IN_PROGRESS", "QUEUED", "WAITING", "REQUESTED", "PENDING"}
        in_progress = {r["status"] for r in check_runs if r["status"] != "COMPLETED"}
        return bool(in_progress & pending) or "PENDING" in legacy

    def _derive_overall(self, check_runs: list[dict[str, Any]], commit_statuses: list[dict[str, Any]]) -> str:
        """Derive a single overall status string from check runs and commit statuses."""
        if not check_runs and not commit_statuses:
            return "unknown"
        legacy = {ctx["state"] for ctx in commit_statuses}
        if self._has_failing_checks(check_runs, legacy):
            return "failing"
        if self._has_pending_checks(check_runs, legacy):
            return "pending"
        return "passing"

    @_read_only(task=True)
    async def get_pr_status_checks(self, repo_owner: str, repo_name: str, pr_number: int) -> StatusChecksResult:
        """Return the CI check runs and commit status for a pull request's HEAD commit."""
        logger.info(f"Fetching status checks for PR #{pr_number} in {repo_owner}/{repo_name}")
        try:
            result = await asyncio.to_thread(
                self.graphql.execute_query,
                PR_STATUS_CHECKS_QUERY,
                variables={"owner": repo_owner, "repo": repo_name, "number": pr_number},
                token=self._resolve_token(),
            )
            repo_data = result.get("repository")
            if not repo_data or not repo_data.get("pullRequest"):
                raise GitHubNotFoundError(f"PR #{pr_number} not found in {repo_owner}/{repo_name}")
            head_target = (repo_data["pullRequest"].get("headRef") or {}).get("target") or {}
            check_runs = self._flatten_check_runs(head_target)
            commit_statuses = self._extract_commit_statuses(head_target)
            overall = self._derive_overall(check_runs, commit_statuses)
            logger.info(f"Status checks for PR #{pr_number}: overall={overall}, runs={len(check_runs)}")
            return {
                "pr_number": pr_number,
                "overall": overall,
                "check_runs": check_runs,
                "commit_statuses": commit_statuses,
            }
        except GitHubNotFoundError:
            raise
        except Exception as e:
            logger.error(f"Error fetching status checks for PR #{pr_number}: {e}")
            raise GitHubAPIError(f"Failed to fetch status checks: {e}") from e
