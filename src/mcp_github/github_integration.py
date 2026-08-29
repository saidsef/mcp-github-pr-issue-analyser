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

import logging
from contextlib import asynccontextmanager
from os import getenv
from typing import Annotated, Any, Literal, TypedDict
from urllib.parse import quote_plus

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
from .graphql_client import GRAPHQL_URL, handle_graphql_errors
from .graphql_queries import (
    CHECK_SUITE_RUNS_QUERY,
    CONVERT_PR_TO_DRAFT_MUTATION,
    MARK_PR_READY_MUTATION,
    PR_LINKED_ISSUES_QUERY,
    PR_STATUS_CHECKS_QUERY,
    SEARCH_USER_QUERY,
)
from .tool_annotations import _destructive, _read_only, _write


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


class ReviewCommentData(CommentData, total=False):
    path: str | None
    line: int | None
    in_reply_to_id: int | None


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


class DiffResult(TypedDict):
    pr_number: int
    patch: str
    bytes_returned: int
    bytes_total: int
    truncated: bool


class StatusChecksResult(TypedDict):
    pr_number: int
    overall: str
    check_runs: list[dict[str, Any]]
    commit_statuses: list[dict[str, Any]]
    truncated: bool


GITHUB_TOKEN = getenv("GITHUB_TOKEN")
TIMEOUT = int(getenv("GITHUB_API_TIMEOUT", "5"))  # seconds, bounds reading the response
CONNECT_TIMEOUT = int(getenv("GITHUB_API_CONNECT_TIMEOUT", "3"))  # seconds, bounds opening the connection
ETAG_CACHE_ENTRIES = int(getenv("GITHUB_ETAG_CACHE_ENTRIES", "256"))  # 0 disables conditional reads
DIFF_MAX_BYTES = int(getenv("GITHUB_DIFF_MAX_BYTES", "131072"))  # 128 KB, wider than any patch this repo produces
MAX_STATUS_CHECKS_SUITE_PAGES = 5  # 50 suites per page × 5 = 250 suite ceiling
MAX_STATUS_CHECKS_RUN_PAGES_PER_SUITE = 5  # 100 runs per page × 5 = 500 run ceiling per suite

# Conversation comments hang off the issue, review comments off the pull request.
_COMMENT_SEGMENTS = {"conversation": "issues", "inline": "pulls"}

_RELEASE_FIELDS = ("id", "tag_name", "name", "html_url", "draft", "prerelease", "body")

logger = logging.getLogger(__name__)


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


def _already_exists(response: httpx.Response) -> bool:
    """A 422 from GitHub carries an errors array saying what was wrong. Only the
    duplicate case is recoverable, so the rest still surface as validation errors."""
    try:
        errors = response.json().get("errors") or []
    except Exception:
        return False
    return any(error.get("code") == "already_exists" for error in errors)


def _search_item(item: dict[str, Any]) -> dict[str, Any]:
    """Trim one issue or pull request from a search result. Both the open
    listing and the free-text search return this shape."""
    return {
        "url": item["html_url"],
        "title": item["title"],
        "number": item["number"],
        "state": item["state"],
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
        "author": (item.get("user") or {}).get("login", ""),
        "label_names": [label["name"] for label in item.get("labels", [])],
        "is_draft": item.get("draft", False),
    }


def _review_comment_result(data: dict[str, Any]) -> ReviewCommentData:
    """A review comment is a comment plus where it sits, which is what tells a
    caller whether it has already said something about that line."""
    return {
        **_comment_result(data),
        "path": data.get("path"),
        "line": data.get("line"),
        "in_reply_to_id": data.get("in_reply_to_id"),
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

        self._http = httpx.AsyncClient(timeout=_timeout())
        self._etags: dict[str, tuple[str, bytes]] = {}

        logger.info("GitHub Integration Initialised")

    async def aclose(self) -> None:
        """Close the shared HTTP client, which REST and GraphQL both use."""
        await self._http.aclose()

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

    def _cache_key(self, url: str, params: Any) -> str:
        """Params ride outside the URL, so page 2 must not read page 1's body."""
        return f"{url}?{sorted(params.items())}" if params else url

    async def _request(
        self,
        method: str,
        url: str,
        *,
        context: str = "",
        headers: dict[str, str] | None = None,
        allow_status: tuple[int, ...] = (),
        **kwargs: Any,
    ) -> httpx.Response:
        """Make an HTTP request and handle errors. A repeated GET is sent
        conditionally, and GitHub does not charge rate limit for a 304.
        Statuses named in allow_status come back for the caller to read
        instead of raising, so an expected 404 or 422 can be a branch."""
        ctx = context or url
        logger.info(f"{method.upper()} {ctx}")
        sent = {**self._get_headers(), **(headers or {})}
        key = self._cache_key(url, kwargs.get("params")) if method == "GET" and ETAG_CACHE_ENTRIES else None
        cached = self._etags.get(key) if key else None
        if cached:
            sent["If-None-Match"] = cached[0]
        try:
            response = await self._http.request(method, url, headers=sent, **kwargs)
            if response.status_code == 304 and cached:
                logger.info(f"Not modified {ctx}")
                return httpx.Response(200, content=cached[1], request=response.request)
            if response.status_code in allow_status:
                logger.info(f"Expected {response.status_code} {ctx}")
                return response
            self._raise_for_status(response, context)
            if key and (etag := response.headers.get("ETag")):
                self._remember_etag(key, etag, response.content)
            logger.info(f"Success {method.upper()} {ctx}")
            return response
        except GitHubAuthError:
            raise
        except Exception as e:
            raise ToolError(str(e)) from e

    def _remember_etag(self, key: str, etag: str, content: bytes) -> None:
        """Oldest out first once the cache is full, so a long-running server
        does not grow a body per URL it has ever read."""
        self._etags.pop(key, None)
        while len(self._etags) >= ETAG_CACHE_ENTRIES:
            self._etags.pop(next(iter(self._etags)))
        self._etags[key] = (etag, content)

    @_read_only
    async def get_pr_diff(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        max_bytes: Annotated[
            int, "Cap on the patch returned. Pass 0 to learn the size without reading the patch"
        ] = DIFF_MAX_BYTES,
    ) -> DiffResult:
        """Fetches the diff/patch of a specific pull request, capped at max_bytes.
        bytes_total is the whole patch either way, so a truncated reply says what
        was left behind. See #314."""
        if max_bytes < 0:
            raise GitHubValidationError("max_bytes cannot be negative.")
        url = f"https://patch-diff.githubusercontent.com/raw/{repo_owner}/{repo_name}/pull/{pr_number}.patch"
        content = (await self._request("GET", url, context=f"PR #{pr_number} diff")).content
        kept = content[:max_bytes]
        # Cutting on a byte boundary can split a character, so drop the partial one.
        return {
            "pr_number": pr_number,
            "patch": kept.decode("utf-8", errors="ignore"),
            "bytes_returned": len(kept),
            "bytes_total": len(content),
            "truncated": len(kept) < len(content),
        }

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

    @_read_only
    async def list_pr_comments(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        kind: Annotated[
            Literal["conversation", "inline"], "conversation for the PR thread, inline for review comments on lines"
        ] = "conversation",
        per_page: Annotated[int, "Number of results per page (1-100)"] = 50,
        page: int = 1,
    ) -> dict[str, Any]:
        """Lists the comments on a pull request. Inline comments carry the file
        and line they sit on, so a second review can tell what it already said."""
        segment = _COMMENT_SEGMENTS[kind]
        url = (
            f"https://api.github.com/repos/{repo_owner}/{repo_name}/{segment}/{pr_number}/comments"
            f"?per_page={per_page}&page={page}"
        )
        data = (await self._request("GET", url, context=f"{kind} comments on PR #{pr_number}")).json()
        trim = _review_comment_result if kind == "inline" else _comment_result
        comments = [trim(comment) for comment in data]
        return {"total": len(comments), "kind": kind, "comments": comments}

    @_write(idempotent=True)
    async def update_pr_comment(
        self,
        repo_owner: str,
        repo_name: str,
        comment_id: Annotated[int, "The comment's own id, as returned by list_pr_comments, not the PR number"],
        body: str,
        kind: Literal["conversation", "inline"] = "conversation",
    ) -> CommentData:
        """Rewrites a comment already posted. Conversation and review comments
        have separate id spaces, so the kind has to match where the id came from."""
        segment = _COMMENT_SEGMENTS[kind]
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/{segment}/comments/{comment_id}"
        data = (await self._request("PATCH", url, context=f"{kind} comment {comment_id}", json={"body": body})).json()
        return _comment_result(data)

    @_write
    async def reply_to_review_comment(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        comment_id: Annotated[int, "The review comment being replied to, which sets the thread"],
        body: str,
    ) -> ReviewCommentData:
        """Replies on an existing review thread rather than starting a new one."""
        url = (
            f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}/comments/{comment_id}/replies"
        )
        data = (await self._request("POST", url, context=f"reply to comment {comment_id}", json={"body": body})).json()
        return _review_comment_result(data)

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

    @_write(idempotent=True)
    async def update_pr(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        title: Annotated[str | None, "Replacement title. Omit to leave the current title alone"] = None,
        body: Annotated[str | None, "Replacement body in Markdown. Omit to leave the current body alone"] = None,
        state: Annotated[
            Literal["open", "closed"] | None, "Omit to leave the pull request in whichever state it is already in"
        ] = None,
        base: Annotated[str | None, "Branch to retarget the pull request onto"] = None,
    ) -> PRContent:
        """Updates an existing pull request. Only the fields supplied are sent, the
        rest keep their current values, so a title can change without restating the body."""
        fields: dict[str, Any] = {"title": title, "body": body, "state": state, "base": base}
        payload = {name: value for name, value in fields.items() if value is not None}
        if not payload:
            raise GitHubValidationError("Supply at least one of title, body, state or base to update.")
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}"
        data = (await self._request("PATCH", url, context=f"PR #{pr_number}", json=payload)).json()
        return _pr_content(data)

    @_write(idempotent=True)
    async def set_pr_draft(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        draft: Annotated[bool, "True puts the pull request back into draft, False marks it ready for review"],
    ) -> dict[str, Any]:
        """Moves a pull request between draft and ready for review. REST accepts
        draft only when the pull request is created, so this goes through GraphQL."""
        pr_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}"
        node_id = (await self._request("GET", pr_url, context=f"PR #{pr_number}")).json().get("node_id")
        if not node_id:
            raise ToolError(f"Could not retrieve the node id for PR #{pr_number}")
        mutation, field = (
            (CONVERT_PR_TO_DRAFT_MUTATION, "convertPullRequestToDraft")
            if draft
            else (MARK_PR_READY_MUTATION, "markPullRequestReadyForReview")
        )
        async with self._guard("change draft status"):
            result = await self._execute_graphql(mutation, {"pullRequestId": node_id})
        pr_data = (result.get(field) or {}).get("pullRequest") or {}
        return {
            "pr_number": pr_data.get("number", pr_number),
            "is_draft": pr_data.get("isDraft"),
            "url": pr_data.get("url"),
        }

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
            f"open_{issue}s": [_search_item(item) for item in data["items"]],
        }

    @_read_only
    async def search_issues_prs(
        self,
        query: Annotated[str, "Terms and qualifiers, e.g. 'rate limit repo:owner/name is:closed label:bug'"],
        per_page: Annotated[int, "Number of results per page (1-100)"] = 50,
        page: int = 1,
    ) -> dict[str, Any]:
        """Searches issues and pull requests by text and qualifiers. Unlike
        list_open_issues_prs the query is the caller's, so closed and merged
        items are reachable and any qualifier GitHub search accepts works."""
        if not query.strip():
            raise GitHubValidationError("Supply a search query.")
        # advanced_search=true is what the current qualifier set is served under.
        url = (
            "https://api.github.com/search/issues"
            f"?q={quote_plus(query)}&advanced_search=true&per_page={per_page}&page={page}"
        )
        data = (await self._request("GET", url, context=f"search issues and PRs for {query!r}")).json()
        return {
            "total": data["total_count"],
            "incomplete_results": data.get("incomplete_results", False),
            "items": [_search_item(item) for item in data["items"]],
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
        # per_page=1 because only the newest SHA is read. The default of 30
        # returns every field of 30 commits to answer with 40 characters.
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/commits?per_page=1"
        data = (await self._request("GET", url, context=f"commits for {repo_owner}/{repo_name}")).json()
        if data:
            return data[0]["sha"]
        return None

    @_write
    async def create_tag(
        self,
        repo_owner: str,
        repo_name: str,
        tag_name: str,
        message: str = "",
        sha: Annotated[str | None, "Commit to tag. Omit to tag the newest commit on the default branch"] = None,
    ) -> dict[str, Any]:
        """Creates a new tag. With a message it is an annotated tag, which stores
        the message; without one it is a lightweight ref."""
        base = f"https://api.github.com/repos/{repo_owner}/{repo_name}"
        target = sha or await self.get_latest_sha(repo_owner, repo_name)
        if not target:
            raise GitHubNotFoundError(f"No commits found in {repo_owner}/{repo_name}; cannot create tag {tag_name}")
        ref_target = target
        if message:
            # POST /git/refs has no message field. An annotated tag is a separate
            # object that holds one, and the ref then points at that object.
            ref_target = (
                await self._request(
                    "POST",
                    f"{base}/git/tags",
                    context=f"create annotated tag {tag_name}",
                    json={"tag": tag_name, "message": message, "object": target, "type": "commit"},
                )
            ).json()["sha"]
        return (
            await self._request(
                "POST",
                f"{base}/git/refs",
                context=f"create tag {tag_name}",
                json={"ref": f"refs/tags/{tag_name}", "sha": ref_target},
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
        """Creates a new release. A tag that already carries one is updated instead
        of rejected, so a retry after a half-finished release recovers. See #347."""
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/releases"
        response = await self._request(
            "POST",
            url,
            context=f"create release {release_name}",
            allow_status=(422,),
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
        if response.status_code == 422:
            if not _already_exists(response):
                self._handle_response_error(response, f"create release {release_name}")
            logger.info(f"Release for {tag_name} already exists, updating it instead")
            return await self.update_release(
                repo_owner,
                repo_name,
                tag_name,
                name=release_name,
                body=body,
                draft=draft,
                prerelease=prerelease,
                make_latest=make_latest,
            )
        return _pick(response.json(), *_RELEASE_FIELDS)

    async def _release_by_tag(self, repo_owner: str, repo_name: str, tag_name: str) -> dict[str, Any] | None:
        """The release published for a tag, or None when the tag carries none."""
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/releases/tags/{tag_name}"
        response = await self._request(
            "GET", url, context=f"release for tag {tag_name}", allow_status=(404,)
        )
        return None if response.status_code == 404 else response.json()

    async def _require_release_by_tag(self, repo_owner: str, repo_name: str, tag_name: str) -> dict[str, Any]:
        """As _release_by_tag, for the callers that cannot proceed without one."""
        release = await self._release_by_tag(repo_owner, repo_name, tag_name)
        if release is None:
            raise GitHubNotFoundError(f"No release found for tag '{tag_name}' in {repo_owner}/{repo_name}")
        return release

    @_read_only
    async def list_releases(
        self,
        repo_owner: str,
        repo_name: str,
        per_page: Annotated[int, "Number of results per page (1-100)"] = 30,
        page: int = 1,
    ) -> dict[str, Any]:
        """Lists a repository's releases, newest first."""
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/releases?per_page={per_page}&page={page}"
        data = (await self._request("GET", url, context=f"releases for {repo_owner}/{repo_name}")).json()
        releases = [_pick(release, *_RELEASE_FIELDS) for release in data]
        return {"total": len(releases), "releases": releases}

    @_read_only
    async def get_release(
        self,
        repo_owner: str,
        repo_name: str,
        tag_name: Annotated[str | None, "Tag to fetch. Omit for the latest published release"] = None,
    ) -> dict[str, Any]:
        """Fetches one release, by tag or the latest published one."""
        if tag_name:
            return _pick(await self._require_release_by_tag(repo_owner, repo_name, tag_name), *_RELEASE_FIELDS)
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/releases/latest"
        data = (await self._request("GET", url, context=f"latest release for {repo_owner}/{repo_name}")).json()
        return _pick(data, *_RELEASE_FIELDS)

    @_write(idempotent=True)
    async def update_release(
        self,
        repo_owner: str,
        repo_name: str,
        tag_name: Annotated[str, "Tag of the release to change"],
        name: Annotated[str | None, "Replacement title. Omit to leave it alone"] = None,
        body: Annotated[str | None, "Replacement notes. Omit to leave them alone"] = None,
        draft: bool | None = None,
        prerelease: bool | None = None,
        make_latest: Literal["true", "false", "legacy"] | None = None,
    ) -> dict[str, Any]:
        """Changes a published release in place. Only the fields supplied are sent,
        so correcting a title does not wipe the notes."""
        fields: dict[str, Any] = {
            "name": name,
            "body": body,
            "draft": draft,
            "prerelease": prerelease,
            "make_latest": make_latest,
        }
        payload = {key: value for key, value in fields.items() if value is not None}
        if not payload:
            raise GitHubValidationError("Supply at least one field to update on the release.")
        release = await self._require_release_by_tag(repo_owner, repo_name, tag_name)
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/releases/{release['id']}"
        data = (await self._request("PATCH", url, context=f"release {tag_name}", json=payload)).json()
        return _pick(data, *_RELEASE_FIELDS)

    @_destructive
    async def delete_release(
        self,
        repo_owner: str,
        repo_name: str,
        tag_name: str,
        delete_tag: Annotated[bool, "Also remove the tag the release was published from"] = False,
    ) -> dict[str, Any]:
        """Deletes a release. The tag it was published from survives unless
        delete_tag asks for it, since the commit history usually should not move."""
        release = await self._require_release_by_tag(repo_owner, repo_name, tag_name)
        base = f"https://api.github.com/repos/{repo_owner}/{repo_name}"
        await self._request("DELETE", f"{base}/releases/{release['id']}", context=f"delete release {tag_name}")
        if delete_tag:
            await self._delete_tag_ref(repo_owner, repo_name, tag_name)
        return {
            "status": "deleted",
            "tag_name": tag_name,
            "release_id": release["id"],
            "tag_deleted": delete_tag,
        }

    @_read_only
    async def list_tags(
        self,
        repo_owner: str,
        repo_name: str,
        per_page: Annotated[int, "Number of results per page (1-100)"] = 30,
        page: int = 1,
    ) -> dict[str, Any]:
        """Lists a repository's tags and the commit each points at."""
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/tags?per_page={per_page}&page={page}"
        data = (await self._request("GET", url, context=f"tags for {repo_owner}/{repo_name}")).json()
        tags = [{"name": tag["name"], "sha": (tag.get("commit") or {}).get("sha")} for tag in data]
        return {"total": len(tags), "tags": tags}

    async def _delete_tag_ref(self, repo_owner: str, repo_name: str, tag_name: str) -> None:
        """Removes the tag ref itself."""
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/git/refs/tags/{tag_name}"
        await self._request("DELETE", url, context=f"delete tag {tag_name}")

    @_destructive
    async def delete_tag(
        self,
        repo_owner: str,
        repo_name: str,
        tag_name: str,
        force: Annotated[bool, "Delete the tag even though a release was published from it"] = False,
    ) -> dict[str, Any]:
        """Deletes a tag. A tag a release points at is refused unless force is set,
        because removing it leaves the release without the code it names."""
        release = await self._release_by_tag(repo_owner, repo_name, tag_name)
        if release and not force:
            raise GitHubValidationError(
                f"Tag '{tag_name}' is used by release '{release.get('name') or tag_name}'. "
                "Delete the release first, or pass force=True."
            )
        await self._delete_tag_ref(repo_owner, repo_name, tag_name)
        return {"status": "deleted", "tag_name": tag_name, "release_still_published": bool(release)}

    async def _execute_graphql(
        self, query: str, variables: dict[str, Any], *, token: str | None = None
    ) -> dict[str, Any]:
        """Run a GraphQL query on the same async client the REST calls use,
        resolving the request token unless one is supplied."""
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables
        logger.debug(f"Executing GraphQL query with variables: {variables}")
        try:
            response = await self._http.post(
                GRAPHQL_URL,
                json=payload,
                headers={
                    **self._get_headers(),
                    "Authorization": f"Bearer {token or self._resolve_token()}",
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as e:
            raise GitHubAPIError(f"GraphQL request failed: {e}") from e
        self._raise_for_status(response, "GraphQL query")
        data = response.json()
        if "errors" in data:
            handle_graphql_errors(data["errors"])
        return data.get("data", {})

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
