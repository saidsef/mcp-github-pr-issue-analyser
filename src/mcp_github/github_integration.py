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
from contextlib import asynccontextmanager
from os import getenv
from typing import Annotated, Any, Literal, TypedDict

import httpx
from fastmcp import Context
from fastmcp.exceptions import ToolError

from .activity import ActivityMixin
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
from .graphql_queries import (
    CHECK_SUITE_RUNS_QUERY,
    PR_LINKED_ISSUES_QUERY,
    PR_STATUS_CHECKS_QUERY,
    SEARCH_USER_QUERY,
)
from .tool_annotations import _read_only, _write


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
    author: str
    html_url: str
    created_at: str


class IssueData(TypedDict):
    number: int
    title: str
    body: str | None
    state: str
    author: str
    labels: list[str]
    html_url: str
    created_at: str
    updated_at: str


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


class LinkedIssuesResult(TypedDict):
    pr_number: int
    linked_issues: list[dict[str, Any]]


class StatusChecksResult(TypedDict):
    pr_number: int
    overall: str
    check_runs: list[dict[str, Any]]
    commit_statuses: list[dict[str, Any]]
    truncated: bool


GITHUB_TOKEN = getenv("GITHUB_TOKEN")
TIMEOUT = int(getenv("GITHUB_API_TIMEOUT", "5"))  # seconds, bounds reading the response
CONNECT_TIMEOUT = int(getenv("GITHUB_API_CONNECT_TIMEOUT", "3"))  # seconds, bounds opening the connection
MAX_STATUS_CHECKS_SUITE_PAGES = 5  # 50 suites per page × 5 = 250 suite ceiling
MAX_STATUS_CHECKS_RUN_PAGES_PER_SUITE = 5  # 100 runs per page × 5 = 500 run ceiling per suite

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)


def _timeout() -> httpx.Timeout:
    """Connecting and reading want different budgets. A host that has not
    answered in a few seconds is unreachable, while a large diff legitimately
    takes a while to stream. See #313."""
    return httpx.Timeout(TIMEOUT, connect=CONNECT_TIMEOUT)


def _pick(data: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Trim a GitHub API payload to the given keys (absent keys become None)."""
    return {k: data.get(k) for k in keys}


def _pr_content(data: dict[str, Any]) -> PRContent:
    """Trim a GitHub pull request payload to the PRContent contract."""
    return {
        "title": data["title"],
        "description": data["body"],
        "author": data["user"]["login"],
        "created_at": data["created_at"],
        "updated_at": data["updated_at"],
        "state": data["state"],
    }


def _comment_result(data: dict[str, Any]) -> CommentData:
    """Trim a GitHub comment payload to the CommentData contract."""
    return {
        "id": data["id"],
        "body": data["body"],
        "author": (data.get("user") or {}).get("login", ""),
        "html_url": data["html_url"],
        "created_at": data["created_at"],
    }


def _issue_result(data: dict[str, Any]) -> IssueData:
    """Trim a GitHub issue payload to the IssueData contract."""
    return {
        "number": data["number"],
        "title": data["title"],
        "body": data.get("body"),
        "state": data["state"],
        "author": (data.get("user") or {}).get("login", ""),
        "labels": [label["name"] for label in data.get("labels", [])],
        "html_url": data["html_url"],
        "created_at": data["created_at"],
        "updated_at": data["updated_at"],
    }


class GitHubIntegration(ActivityMixin):
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
        self.graphql = GraphQLClient(self.github_token or "", timeout=TIMEOUT, connect_timeout=CONNECT_TIMEOUT)

        self._http = httpx.AsyncClient(timeout=_timeout())

        logger.info("GitHub Integration Initialised")

    async def aclose(self) -> None:
        """Close both HTTP clients, the async REST one and the sync GraphQL one."""
        try:
            await self._http.aclose()
        finally:
            self.graphql.close()

    async def __aenter__(self) -> GitHubIntegration:
        """Enter async context manager."""
        return self

    async def __aexit__(self, *_: Any) -> None:
        """Exit async context manager and close HTTP client."""
        await self.aclose()

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
            msg = f"{context}: Resource not found" if context else "Resource not found"
            if self._oauth_mode:
                msg += (
                    " If this is a private organisation repository, the org admin may need to"
                    " approve this OAuth App under Org Settings -> Third-party access -> OAuth App access policy."
                )
            raise GitHubNotFoundError(msg, response_body=response_body)

        if status == 422:
            raise GitHubValidationError("Validation failed. Check your input data.", response_body=response_body)

        message = f"GitHub API error ({context})" if context else "GitHub API error"
        gh_message = response_body.get("message") if isinstance(response_body, dict) else None
        detail = f"{status} - {response.reason_phrase}"
        if gh_message:
            detail = f"{detail} - {gh_message}"
        raise GitHubAPIError(
            f"{message}: {detail}",
            status_code=status,
            response_body=response_body,
        )

    def _raise_for_403(self, response: httpx.Response, response_body: dict | None):
        """Handle 403 response — distinguishes rate limit from permission error."""
        error_text = response.text.lower()
        if "rate limit" not in error_text and "api rate limit" not in error_text:
            msg = "Permission denied. Check your token permissions."
            if self._oauth_mode:
                msg += (
                    " If accessing a private organisation repository, the org admin may need to"
                    " approve this OAuth App under Org Settings -> Third-party access -> OAuth App access policy."
                )
            raise GitHubAPIError(msg, status_code=403, response_body=response_body)
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
            response = await self._http.request(method, url, headers=self._get_headers(), **kwargs)
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
        return _pr_content(data)

    @_write
    async def add_pr_comments(self, repo_owner: str, repo_name: str, pr_number: int, comment: str) -> CommentData:
        """Adds a comment to a specific pull request."""
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/{pr_number}/comments"
        data = (await self._request("POST", url, context=f"PR #{pr_number} comment", json={"body": comment})).json()
        return _comment_result(data)

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
        data = (
            await self._request("POST", review_url, context=f"inline comment on {path}:{line}", json=payload)
        ).json()
        return _comment_result(data)

    @_write(idempotent=True)
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
        data = (
            await self._request(
                "PATCH", url, context=f"PR #{pr_number}", json={"title": new_title, "body": new_description}
            )
        ).json()
        return _pr_content(data)

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

    @_read_only
    async def list_repo_labels(
        self,
        repo_owner: str,
        repo_name: str,
        per_page: Annotated[int, "Number of results per page (1-100)"] = 50,
        page: int = 1,
    ) -> dict[str, Any]:
        """Lists the labels defined in a repository."""
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/labels?per_page={per_page}&page={page}"
        data = (await self._request("GET", url, context=f"labels for {repo_owner}/{repo_name}")).json()
        labels = [_pick(label, "name", "description", "color") for label in data]
        return {"total": len(labels), "labels": labels}

    @_write
    async def create_issue(
        self, repo_owner: str, repo_name: str, title: str, body: str, labels: list[str]
    ) -> IssueData:
        """Creates a new issue."""
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues"
        issue_labels = ["mcp"] if not labels else labels + ["mcp"]
        data = (
            await self._request(
                "POST",
                url,
                context=f"create issue in {repo_owner}/{repo_name}",
                json={"title": title, "body": body, "labels": issue_labels},
            )
        ).json()
        return _issue_result(data)

    @_write
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
        return (await self._request("PUT", url, context=f"PR #{pr_number} merge", json=payload)).json()

    @_write(idempotent=True)
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

    @_write(idempotent=True)
    async def update_issue(
        self,
        repo_owner: str,
        repo_name: str,
        issue_number: int,
        title: Annotated[str | None, "Replacement title. Omit to leave the current title alone"] = None,
        body: Annotated[str | None, "Replacement body in Markdown. Omit to leave the current body alone"] = None,
        labels: Annotated[
            list[str] | None, "Replacement label set. Omit to keep the current labels, pass [] to strip them all"
        ] = None,
        state: Annotated[
            Literal["open", "closed"] | None, "Omit to leave the issue in whichever state it is already in"
        ] = None,
    ) -> IssueData:
        """Updates an existing issue. Only the fields supplied are sent, the rest keep their current values."""
        fields: dict[str, Any] = {"title": title, "body": body, "labels": labels, "state": state}
        payload = {name: value for name, value in fields.items() if value is not None}
        if not payload:
            raise GitHubValidationError("Supply at least one of title, body, labels or state to update.")
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/{issue_number}"
        data = (
            await self._request(
                "PATCH",
                url,
                context=f"issue #{issue_number}",
                json=payload,
            )
        ).json()
        return _issue_result(data)

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
        data = (
            await self._request("POST", url, context=f"PR #{pr_number} review", json={"body": body, "event": event})
        ).json()
        return _pick(data, "id", "state", "body", "html_url", "submitted_at")

    @_write(idempotent=True)
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
        result: dict[str, Any] = {
            "status": "partial" if missing else "ok",
            "assignees_requested": sorted(requested),
            "assignees_applied": sorted(actual_logins),
            "issue_url": data.get("html_url"),
        }
        if missing:
            logger.warning(f"Some assignees were not applied: {missing}")
            result["message"] = (
                f"The following assignees could not be applied (not a collaborator or user does not exist): {sorted(missing)}"
            )
        return result

    @_read_only
    async def get_latest_sha(self, repo_owner: str, repo_name: str) -> str | None:
        """Fetches the SHA of the latest commit."""
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/commits"
        data = (await self._request("GET", url, context=f"commits for {repo_owner}/{repo_name}")).json()
        if data:
            return data[0]["sha"]
        return None

    @_write
    async def create_tag(self, repo_owner: str, repo_name: str, tag_name: str, message: str) -> dict[str, Any]:
        """Creates a new tag."""
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/git/refs"
        latest_sha = await self.get_latest_sha(repo_owner, repo_name)
        if not latest_sha:
            raise GitHubNotFoundError(f"No commits found in {repo_owner}/{repo_name}; cannot create tag {tag_name}")
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
        data = (
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
        return _pick(data, "id", "tag_name", "name", "html_url", "draft", "prerelease", "body")

    async def _execute_graphql(
        self, query: str, variables: dict[str, Any], *, token: str | None = None
    ) -> dict[str, Any]:
        """Run a GraphQL query off-thread (the client is sync), resolving the
        request token unless one is supplied."""
        return await asyncio.to_thread(
            self.graphql.execute_query,
            query,
            variables=variables,
            token=token or self._resolve_token(),
        )

    @asynccontextmanager
    async def _guard(self, action: str):
        """Let GitHubNotFoundError pass through; wrap any other failure in a
        GitHubAPIError labelled with the action that failed."""
        try:
            yield
        except GitHubNotFoundError:
            raise
        except Exception as e:
            logger.error(f"Error during {action}: {e}")
            raise GitHubAPIError(f"Failed to {action}: {e}") from e

    @_read_only(task=True)
    async def search_user(self, username: str) -> UserSearchResult:
        """Search for a GitHub user by username using GraphQL API."""
        logger.info(f"Searching for GitHub user: {username}")
        async with self._guard("search for user"):
            result = await self._execute_graphql(SEARCH_USER_QUERY, {"username": username})
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

    @_read_only(task=True)
    async def get_pr_linked_issues(self, repo_owner: str, repo_name: str, pr_number: int) -> LinkedIssuesResult:
        """Return the issues that will be auto-closed when a pull request is merged."""
        logger.info(f"Fetching linked issues for PR #{pr_number} in {repo_owner}/{repo_name}")
        async with self._guard("fetch linked issues"):
            result = await self._execute_graphql(
                PR_LINKED_ISSUES_QUERY,
                {"owner": repo_owner, "repo": repo_name, "number": pr_number},
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

    @staticmethod
    def _run_dict(run: dict[str, Any], app_name: str) -> dict[str, Any]:
        """Trim a GraphQL check-run node to the check_runs contract."""
        return {
            "name": run["name"],
            "status": run["status"],
            "conclusion": run.get("conclusion"),
            "details_url": run.get("detailsUrl"),
            "suite_app": app_name,
        }

    def _flatten_check_runs(self, head_target: dict[str, Any]) -> list[dict[str, Any]]:
        """Flatten check suites into a single list of check run dicts."""
        check_runs: list[dict[str, Any]] = []
        for suite in (head_target.get("checkSuites") or {}).get("nodes", []):
            app_name = (suite.get("app") or {}).get("name", "unknown")
            for run in (suite.get("checkRuns") or {}).get("nodes", []):
                check_runs.append(self._run_dict(run, app_name))
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

    def _derive_overall(
        self,
        check_runs: list[dict[str, Any]],
        commit_statuses: list[dict[str, Any]],
        truncated: bool = False,
    ) -> str:
        """Derive a single overall status string from check runs and commit statuses.

        When truncated is True and no failure or pending signal is observed,
        return 'unknown' rather than 'passing' — the missed pages could
        contain a failing run. Failure and pending signals stay authoritative.

        """
        if not check_runs and not commit_statuses:
            return "unknown"
        legacy = {ctx["state"] for ctx in commit_statuses}
        if self._has_failing_checks(check_runs, legacy):
            return "failing"
        if self._has_pending_checks(check_runs, legacy):
            return "pending"
        if truncated:
            return "unknown"
        return "passing"

    async def _drain_suite_runs(
        self, suite_id: str, app_name: str, after: str | None, token: str
    ) -> tuple[list[dict[str, Any]], bool]:
        """Page through remaining check runs for a single suite via CHECK_SUITE_RUNS_QUERY.

        Returns the accumulated run dicts and whether the per-suite page cap
        was hit before exhausting the connection.

        """
        runs: list[dict[str, Any]] = []
        cursor = after
        for _ in range(MAX_STATUS_CHECKS_RUN_PAGES_PER_SUITE):
            result = await self._execute_graphql(
                CHECK_SUITE_RUNS_QUERY, {"suiteId": suite_id, "after": cursor}, token=token
            )
            node = result.get("node") or {}
            run_conn = node.get("checkRuns") or {}
            for run in run_conn.get("nodes") or []:
                runs.append(self._run_dict(run, app_name))
            page_info = run_conn.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                return runs, False
            cursor = page_info.get("endCursor")
        return runs, True

    @_read_only(task=True)
    async def get_pr_status_checks(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        ctx: Context | None = None,
    ) -> StatusChecksResult:
        """Return the CI check runs and commit status for a pull request's HEAD commit.

        Pages through up to MAX_STATUS_CHECKS_SUITE_PAGES of check suites
        (50 per page). For any suite whose first 100 runs are not the full
        set, drains up to MAX_STATUS_CHECKS_RUN_PAGES_PER_SUITE additional
        pages via the supplemental query. If either cap is hit before the
        connection is exhausted, the result is flagged truncated=True and
        overall is downgraded from 'passing' to 'unknown' so the caller
        does not act on a partial view.

        """
        logger.info(f"Fetching status checks for PR #{pr_number} in {repo_owner}/{repo_name}")
        async with self._guard("fetch status checks"):
            check_runs: list[dict[str, Any]] = []
            commit_statuses: list[dict[str, Any]] = []
            n_suites = 0
            truncated = False
            suites_after: str | None = None
            token = self._resolve_token()

            for _ in range(MAX_STATUS_CHECKS_SUITE_PAGES):
                result = await self._execute_graphql(
                    PR_STATUS_CHECKS_QUERY,
                    {
                        "owner": repo_owner,
                        "repo": repo_name,
                        "number": pr_number,
                        "suitesAfter": suites_after,
                    },
                    token=token,
                )
                repo_data = result.get("repository")
                if not repo_data or not repo_data.get("pullRequest"):
                    raise GitHubNotFoundError(f"PR #{pr_number} not found in {repo_owner}/{repo_name}")
                head_target = (repo_data["pullRequest"].get("headRef") or {}).get("target") or {}

                if suites_after is None:
                    commit_statuses = self._extract_commit_statuses(head_target)

                suites_page = head_target.get("checkSuites") or {}
                suites_nodes = suites_page.get("nodes") or []
                n_suites += len(suites_nodes)
                check_runs.extend(self._flatten_check_runs(head_target))

                for suite in suites_nodes:
                    runs_page = (suite.get("checkRuns") or {}).get("pageInfo") or {}
                    if not runs_page.get("hasNextPage"):
                        continue
                    extra_runs, runs_capped = await self._drain_suite_runs(
                        suite_id=suite["id"],
                        app_name=(suite.get("app") or {}).get("name", "unknown"),
                        after=runs_page.get("endCursor"),
                        token=token,
                    )
                    check_runs.extend(extra_runs)
                    if runs_capped:
                        truncated = True

                page_info = suites_page.get("pageInfo") or {}
                if not page_info.get("hasNextPage"):
                    break
                suites_after = page_info.get("endCursor")
            else:
                truncated = True

            if ctx:
                trailer = " (truncated)" if truncated else ""
                await ctx.info(
                    f"Found {n_suites} check suites, "
                    f"{len(check_runs)} runs, {len(commit_statuses)} legacy statuses{trailer}"
                )
            overall = self._derive_overall(check_runs, commit_statuses, truncated=truncated)
            logger.info(
                f"Status checks for PR #{pr_number}: overall={overall}, "
                f"runs={len(check_runs)}, truncated={truncated}"
            )
            return {
                "pr_number": pr_number,
                "overall": overall,
                "check_runs": check_runs,
                "commit_statuses": commit_statuses,
                "truncated": truncated,
            }
