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

import functools
import logging
import time
from contextlib import asynccontextmanager
from os import getenv
from typing import Annotated, Any, Literal, TypedDict
from urllib.parse import quote, quote_plus

import httpx
from fastmcp.exceptions import ToolError

from .activity import ActivityMixin
from .auth import (
    MISSING_CREDENTIALS,
    APIKeyVerifier,
    get_oauth_verifier,
    oauth_configured,
    resolve_token,
)
from .exceptions import (
    GitHubAPIError,
    GitHubAuthError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubValidationError,
)
from .graphql_client import API, GRAPHQL_URL, handle_graphql_errors
from .graphql_queries import (
    ADD_PROJECT_ITEM_MUTATION,
    CHECK_SUITE_RUNS_QUERY,
    CONVERT_PR_TO_DRAFT_MUTATION,
    DELETE_PROJECT_ITEM_MUTATION,
    ISSUE_PROJECT_ITEMS_QUERY,
    MARK_PR_READY_MUTATION,
    PR_LINKED_ISSUES_QUERY,
    PR_STATUS_CHECKS_QUERY,
    PROJECT_ITEMS_QUERY,
    PROJECT_QUERY,
    PROJECTS_QUERY,
    SEARCH_USER_QUERY,
    SET_PROJECT_FIELD_MUTATION,
)
from .skills_access import SkillsMixin
from .tool_annotations import PROJECT_SCOPES, _destructive, _read_only, _write


class PRContent(TypedDict):
    title: str
    description: str | None
    author: str
    created_at: str
    updated_at: str
    state: str
    head_sha: str | None
    head_ref: str | None
    base_ref: str | None
    requested_reviewers: list[str]
    requested_teams: list[str]


class CommentData(TypedDict):
    id: int
    body: str
    author: str
    html_url: str
    created_at: str


class ReviewData(TypedDict):
    id: int
    author: str
    state: str
    body: str | None
    html_url: str
    submitted_at: str | None
    commit_id: str | None


class ReviewCommentData(CommentData, total=False):
    path: str | None
    line: int | None
    side: str | None
    start_line: int | None
    start_side: str | None
    in_reply_to_id: int | None


class IssueData(TypedDict):
    number: int
    title: str
    body: str | None
    state: str
    author: str
    labels: list[str]
    assignees: list[str]
    milestone: str | None
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


class FileResult(TypedDict):
    path: str
    ref: str | None
    content: str
    binary: bool
    bytes_returned: int
    bytes_total: int
    truncated: bool
    next_offset: int | None


class TreeResult(TypedDict):
    path: str
    ref: str | None
    total: int
    entries: list[dict[str, Any]]
    truncated: bool


class DiffResult(TypedDict):
    pr_number: int
    patch: str
    bytes_returned: int
    bytes_total: int
    truncated: bool


class JobLogResult(TypedDict):
    job_id: int
    log: str
    bytes_returned: int
    bytes_total: int
    truncated: bool


class StatusChecksResult(TypedDict):
    pr_number: int
    head_sha: str | None
    overall: str
    check_runs: list[dict[str, Any]]
    commit_statuses: list[dict[str, Any]]
    truncated: bool


class EmptyCommitResult(TypedDict):
    commit_sha: str
    parent_sha: str
    parent_tree_sha: str
    tree_sha: str
    branch: str
    html_url: str


GITHUB_TOKEN = getenv("GITHUB_TOKEN")
TIMEOUT = int(getenv("GITHUB_API_TIMEOUT", "5"))
CONNECT_TIMEOUT = int(getenv("GITHUB_API_CONNECT_TIMEOUT", "3"))
ETAG_CACHE_ENTRIES = int(getenv("GITHUB_ETAG_CACHE_ENTRIES", "256"))
DIFF_MAX_BYTES = int(getenv("GITHUB_DIFF_MAX_BYTES", "131072"))
FILE_MAX_BYTES = int(getenv("GITHUB_FILE_MAX_BYTES", "131072"))
MAX_MILESTONE_PAGES = 5
MAX_RELEASE_PAGES = 5
MAX_STATUS_CHECKS_SUITE_PAGES = 5
MAX_STATUS_CHECKS_RUN_PAGES_PER_SUITE = 5

_COMMENT_SEGMENTS = {"conversation": "issues", "inline": "pulls"}

_RELEASE_FIELDS = ("id", "tag_name", "name", "html_url", "draft", "prerelease", "body")

_MILESTONE_FIELDS = (
    "number",
    "title",
    "description",
    "state",
    "due_on",
    "open_issues",
    "closed_issues",
    "html_url",
)

_PROJECT_VALUE_KEYS = ("text", "number", "date", "name", "title")

# The tools read these headers off a successful response, and the cache keeps
# them with the body so a 304 rebuilt from it matches the original. See #516.
_CACHED_HEADERS = ("Link", "content-type")

_OpenClosed = Literal["open", "closed"]
_MilestoneState = Literal["open", "closed", "all"]
_RepoSort = Literal["updated", "pushed", "created", "full_name"]
_SearchType = Literal["lexical", "semantic", "hybrid"]
_Side = Literal["LEFT", "RIGHT"]
_IfExists = Literal["fail", "update"]
_Names = list[str] | None
_WorkflowInputs = dict[str, Any]

_FAILING_CONCLUSIONS = {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}
_PENDING_STATUSES = {"IN_PROGRESS", "QUEUED", "WAITING", "REQUESTED", "PENDING"}

_WORKFLOW_SCOPE_REFUSAL = "`workflow` scope"
_WORKFLOW_SCOPE_GUIDANCE = (
    " The token is missing the `workflow` scope, which GitHub requires for a write to a file under"
    " .github/workflows. Re-authorise this server to grant it."
)

logger = logging.getLogger(__name__)


def _repo(owner: str, name: str) -> str:
    """The REST base for one repository, which nearly every tool builds on."""
    return f"{API}/repos/{owner}/{name}"


def _supplied(**fields: Any) -> dict[str, Any]:
    """The fields given a value, so an update sends only what the caller means to change."""
    return {name: value for name, value in fields.items() if value is not None}


def _changes(**fields: Any) -> dict[str, Any]:
    """As _supplied, refusing a call that names nothing to change rather than sending an empty PATCH."""
    payload = _supplied(**fields)
    if not payload:
        *rest, last = fields
        named = " or ".join(part for part in (", ".join(rest), last) if part)
        raise GitHubValidationError(f"Supply at least one of {named} to update.")
    return payload


def _with_mcp_label(labels: list[str], mcp_label: bool) -> list[str]:
    """The label set to write, with the tracking label appended once when it is wanted."""
    return [*labels, "mcp"] if mcp_label and "mcp" not in labels else list(labels)


def _timeout() -> httpx.Timeout:
    """Connecting and reading want different budgets. A host that has not
    answered in a few seconds is unreachable, while a large diff legitimately
    takes a while to stream. See #313."""
    return httpx.Timeout(TIMEOUT, connect=CONNECT_TIMEOUT)


def _has_more(response: httpx.Response) -> bool:
    """GitHub names the next page in the Link header and omits it on the last one,
    so paging can stop on a fact rather than on a speculative extra call. A full
    page is not the signal: a set that divides exactly by per_page would loop
    forever on that guess. See #405."""
    return 'rel="next"' in response.headers.get("Link", "")


def _page(response: httpx.Response, items: list[Any]) -> dict[str, Any]:
    """The paging half of a list reply. count is what came back this call, which
    is not the size of the result set, and has_more says whether to ask again."""
    return {"count": len(items), "has_more": _has_more(response)}


def _pick(data: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Trim a GitHub API payload to the given keys (absent keys become None)."""
    return {k: data.get(k) for k in keys}


def _pr_content(data: dict[str, Any]) -> PRContent:
    """Trim a GitHub pull request payload to the PRContent contract. head_sha is
    what github_update_pr_branch takes as expected_head_sha, which had no source before.
    See #411."""
    head = data.get("head") or {}
    return {
        "title": data["title"],
        "description": data["body"],
        "author": data["user"]["login"],
        "created_at": data["created_at"],
        "updated_at": data["updated_at"],
        "state": data["state"],
        "head_sha": head.get("sha"),
        "head_ref": head.get("ref"),
        "base_ref": (data.get("base") or {}).get("ref"),
        "requested_reviewers": [user["login"] for user in data.get("requested_reviewers") or []],
        "requested_teams": [team["slug"] for team in data.get("requested_teams") or []],
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


def _github_detail(response_body: dict | None) -> str:
    """GitHub names the actual cause in the response body, and the exception text is
    all an MCP client ever sees, so every branch folds it in rather than replacing
    it with a status-class guess."""
    if not isinstance(response_body, dict):
        return ""
    parts: list[str] = []
    if message := str(response_body.get("message") or "").strip():
        parts.append(message)
    for error in response_body.get("errors") or []:
        if isinstance(error, dict):
            text = str(error.get("message") or error.get("code") or "").strip()
            if field := error.get("field"):
                text = f"{field}: {text}" if text else str(field)
        else:
            text = str(error or "").strip()
        if text and text not in parts:
            parts.append(text)
    return f" GitHub said: {'; '.join(parts)}" if parts else ""


def _reset_timestamp(response: httpx.Response) -> int | None:
    """When to try again, as Unix epoch seconds. A secondary limit reports its wait
    in Retry-After, where X-RateLimit-Reset is absent or points at the unrelated
    primary window. Either header can be missing or non-numeric."""
    retry_after = (response.headers.get("Retry-After") or "").strip()
    if retry_after.isdigit():
        return int(time.time()) + int(retry_after)
    reset = (response.headers.get("X-RateLimit-Reset") or "").strip()
    return int(reset) if reset.isdigit() else None


def _rate_limit_error(
    response: httpx.Response, response_body: dict | None, where: str, detail: str
) -> GitHubRateLimitError:
    """GitHub sends either limit as a 403 or a 429, so the error keeps the status
    that arrived. See #519."""
    msg = (
        "GitHub secondary rate limit hit. Wait for the retry window before making more requests."
        if "secondary rate limit" in response.text.lower()
        else "GitHub API rate limit exceeded. Please wait before making more requests."
    )
    return GitHubRateLimitError(
        f"{where}{msg}{detail}",
        response_body=response_body,
        reset_timestamp=_reset_timestamp(response),
        status_code=response.status_code,
    )


def _repo_result(repo: dict[str, Any]) -> dict[str, Any]:
    """Trim a repository payload to what a caller needs to pick one and call
    the next tool with it."""
    return {
        "name": repo["name"],
        "owner": (repo.get("owner") or {}).get("login", ""),
        "description": repo.get("description"),
        "default_branch": repo.get("default_branch"),
        "private": repo.get("private", False),
        "fork": repo.get("fork", False),
        "archived": repo.get("archived", False),
        "pushed_at": repo.get("pushed_at"),
        "html_url": repo.get("html_url"),
    }


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


def _review_result(data: dict[str, Any]) -> ReviewData:
    """Trim a GitHub review payload. submitted_at is absent on a PENDING review,
    which is how a caller tells one that was written from one that was sent."""
    return {
        "id": data["id"],
        "author": (data.get("user") or {}).get("login", ""),
        "state": data.get("state", ""),
        "body": data.get("body"),
        "html_url": data["html_url"],
        "submitted_at": data.get("submitted_at"),
        "commit_id": data.get("commit_id"),
    }


def _review_comment_result(data: dict[str, Any]) -> ReviewCommentData:
    """A review comment is a comment plus where it sits, which is what tells a
    caller whether it has already said something about that line."""
    return {
        **_comment_result(data),
        "path": data.get("path"),
        "line": data.get("line"),
        "side": data.get("side"),
        "start_line": data.get("start_line"),
        "start_side": data.get("start_side"),
        "in_reply_to_id": data.get("in_reply_to_id"),
    }


def _review_request_result(data: dict[str, Any], users: list[str], teams: list[str], remove: bool) -> dict[str, Any]:
    """The reviewer lists off the pull request a review request answers with. A
    name the call asked about is unapplied when a request left it off the lists,
    or a removal left it on them."""
    logins = {user["login"] for user in data["requested_reviewers"]}
    slugs = {team["slug"] for team in data["requested_teams"]}
    if remove:
        unapplied = sorted(logins.intersection(users) | slugs.intersection(teams))
    else:
        unapplied = sorted((set(users) - logins) | (set(teams) - slugs))
    result: dict[str, Any] = {
        "status": "partial" if unapplied else "ok",
        "requested_reviewers": sorted(logins),
        "requested_teams": sorted(slugs),
        "pr_url": data.get("html_url"),
    }
    if unapplied:
        logger.warning(f"Some reviewers were not applied: {unapplied}")
        result["message"] = f"GitHub did not {'withdraw' if remove else 'add'} these reviewers: {unapplied}"
    return result


def _issue_result(data: dict[str, Any]) -> IssueData:
    """Trim a GitHub issue payload to the IssueData contract."""
    return {
        "number": data["number"],
        "title": data["title"],
        "body": data.get("body"),
        "state": data["state"],
        "author": (data.get("user") or {}).get("login", ""),
        "labels": [label["name"] for label in data.get("labels", [])],
        "assignees": [user["login"] for user in data.get("assignees") or []],
        "milestone": (data.get("milestone") or {}).get("title"),
        "html_url": data["html_url"],
        "created_at": data["created_at"],
        "updated_at": data["updated_at"],
    }


def _require_project(result: dict[str, Any], owner: str, number: int) -> dict[str, Any]:
    """The project a repositoryOwner query resolved, or a message naming what was
    not found. A project the token cannot see comes back null the same way one
    that does not exist does, so the message covers both."""
    project = (result.get("repositoryOwner") or {}).get("projectV2")
    if not project:
        raise GitHubNotFoundError(
            f"No project #{number} for '{owner}'. It may exist but be invisible to this token: "
            "Projects (v2) needs 'read:project' on a classic token, or Projects read on a fine-grained one."
        )
    return project


def _project_field_summary(node: dict[str, Any]) -> dict[str, Any]:
    """One field of a board, with the options it accepts when it is a select."""
    return {
        "id": node.get("id"),
        "name": node.get("name"),
        "data_type": node.get("dataType"),
        "options": [option["name"] for option in node.get("options") or []],
    }


def _project_field_ids(project: dict[str, Any], field: str, option: str) -> tuple[str, str]:
    """The field and option ids behind two names, so a caller can say Status and
    In Progress rather than carry node ids around. What was not found is named,
    along with what was there instead."""
    nodes = [node for node in (project.get("fields") or {}).get("nodes") or [] if node.get("name")]
    match = next((node for node in nodes if node["name"].lower() == field.lower()), None)
    if match is None:
        known = ", ".join(sorted(node["name"] for node in nodes)) or "none"
        raise GitHubNotFoundError(f"No field named '{field}' on this project. Fields: {known}")
    options = match.get("options")
    if not options:
        raise GitHubValidationError(
            f"Field '{match['name']}' is a {match.get('dataType') or 'non-select'} field. "
            "Only single-select fields can be set by option name."
        )
    chosen = next((entry for entry in options if entry["name"].lower() == option.lower()), None)
    if chosen is None:
        known = ", ".join(entry["name"] for entry in options)
        raise GitHubNotFoundError(f"No option named '{option}' on field '{match['name']}'. Options: {known}")
    return match["id"], chosen["id"]


def _item_on_project(node: dict[str, Any], project_id: str) -> str | None:
    """The board item for this issue on the given project, if it is on it."""
    items = (node.get("projectItems") or {}).get("nodes") or []
    return next((item["id"] for item in items if (item.get("project") or {}).get("id") == project_id), None)


def _project_field_values(node: dict[str, Any]) -> dict[str, Any]:
    """Flatten the field-value union into field name to value. A value type the
    query does not select arrives empty, carrying no field name to key on."""
    values: dict[str, Any] = {}
    for value in (node.get("fieldValues") or {}).get("nodes") or []:
        name = (value.get("field") or {}).get("name")
        if not name:
            continue
        held = next((key for key in _PROJECT_VALUE_KEYS if key in value), None)
        if held:
            values[name] = value[held]
    return values


def _project_summary(node: dict[str, Any]) -> dict[str, Any]:
    """One board as the listing reports it. The number is what every other
    board tool takes, and state says whether the board is still in use."""
    return {
        "number": node.get("number"),
        "title": node.get("title"),
        "state": "closed" if node.get("closed") else "open",
        "url": node.get("url"),
    }


def _project_item_summary(node: dict[str, Any]) -> dict[str, Any]:
    """One card on a board: what it points at, and what its fields say."""
    content = node.get("content") or {}
    return {
        "item_id": node.get("id"),
        "type": node.get("type"),
        "number": content.get("number"),
        "title": content.get("title"),
        "state": content.get("state"),
        "url": content.get("url"),
        "repository": (content.get("repository") or {}).get("nameWithOwner"),
        "fields": _project_field_values(node),
    }


def _run_dict(run: dict[str, Any], app_name: str) -> dict[str, Any]:
    """Trim a GraphQL check-run node to the check_runs contract. The id of a
    GitHub Actions run is its job id, which github_get_job_log takes. See #500."""
    return {
        "id": run.get("databaseId"),
        "name": run["name"],
        "status": run["status"],
        "conclusion": run.get("conclusion"),
        "details_url": run.get("detailsUrl"),
        "suite_app": app_name,
    }


def _head_commit(pull_request: dict[str, Any]) -> dict[str, Any]:
    """The PR's last commit, which still resolves once its head branch is deleted.
    An empty dict means GitHub resolved no commit. See #515."""
    nodes = (pull_request.get("commits") or {}).get("nodes") or [None]
    return (nodes[-1] or {}).get("commit") or {}


def _check_runs(head_target: dict[str, Any]) -> list[dict[str, Any]]:
    """Every run across a commit's check suites, each naming the app its suite belongs to."""
    return [
        _run_dict(run, (suite.get("app") or {}).get("name", "unknown"))
        for suite in (head_target.get("checkSuites") or {}).get("nodes", [])
        for run in (suite.get("checkRuns") or {}).get("nodes", [])
    ]


def _commit_statuses(head_target: dict[str, Any]) -> list[dict[str, Any]]:
    """The legacy commit status contexts on a commit."""
    return [
        {
            "context": ctx["context"],
            "state": ctx["state"],
            "description": ctx.get("description"),
            "target_url": ctx.get("targetUrl"),
        }
        for ctx in (head_target.get("status") or {}).get("contexts", [])
    ]


def _overall(check_runs: list[dict[str, Any]], commit_statuses: list[dict[str, Any]], truncated: bool) -> str:
    """One word for the state of a commit's checks. A failure or a pending run is
    authoritative. A clean read that was cut short is unknown rather than passing,
    since the pages not read could hold the failure."""
    if not check_runs and not commit_statuses:
        return "unknown"
    legacy = {ctx["state"] for ctx in commit_statuses}
    conclusions = {run["conclusion"] for run in check_runs if run["conclusion"]}
    if conclusions & _FAILING_CONCLUSIONS or legacy & {"FAILURE", "ERROR"}:
        return "failing"
    statuses = {run["status"] for run in check_runs if run["status"] != "COMPLETED"}
    if statuses & _PENDING_STATUSES or "PENDING" in legacy:
        return "pending"
    return "unknown" if truncated else "passing"


class GitHubIntegration(ActivityMixin, SkillsMixin):
    def __init__(self):
        """Initialises the GitHubIntegration instance."""
        self.github_token = GITHUB_TOKEN

        self._oauth_mode = oauth_configured()
        self.verifier = APIKeyVerifier(self.github_token) if self.github_token else None

        self._http = httpx.AsyncClient(timeout=_timeout())
        self._etags: dict[str, tuple[str, bytes, dict[str, str]]] = {}

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

    @functools.cached_property
    def _oauth_verifier(self):
        """Returns a GitHubProvider instance for OAuth2 authentication.

        Cached because each build takes a store consumer with it, and a second
        provider would leave the first backend client unreleased."""
        return get_oauth_verifier()

    @property
    def credentials_configured(self) -> bool:
        """True when the server holds the OAuth trio or a static token."""
        return bool(self._oauth_mode or self.github_token)

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

        detail = _github_detail(response_body)
        where = f"{context}: " if context else ""

        if status == 401:
            msg = "Authentication failed. Check your GitHub token."
            if self._oauth_mode:
                msg += (
                    " The GitHub OAuth authorization may have been revoked, please re-authenticate via the OAuth flow."
                )
            raise GitHubAuthError(f"{where}{msg}{detail}", response_body=response_body)

        if status == 403:
            self._raise_for_403(response, response_body, where, detail)

        if status == 429:
            raise _rate_limit_error(response, response_body, where, detail)

        if status == 404:
            msg = "Resource not found"
            if self._oauth_mode:
                msg += (
                    " If this is a private organisation repository, the org admin may need to"
                    " approve this OAuth App under Org Settings -> Third-party access -> OAuth App access policy."
                )
            raise GitHubNotFoundError(f"{where}{msg}{detail}", response_body=response_body)

        if status == 422:
            raise GitHubValidationError(
                f"{where}Validation failed. Check your input data.{detail}", response_body=response_body
            )

        message = f"GitHub API error ({context})" if context else "GitHub API error"
        raise GitHubAPIError(
            f"{message}: {status} - {response.reason_phrase}{detail}",
            status_code=status,
            response_body=response_body,
        )

    def _raise_for_403(
        self, response: httpx.Response, response_body: dict | None, where: str = "", detail: str = ""
    ) -> None:
        """Handle a 403, which is a rate limit or a refusal. A refusal covers a missing
        scope, SAML enforcement and a branch protection rule alike, and only GitHub's
        own wording separates them, so detail carries it into every message."""
        error_text = response.text.lower()
        if "rate limit" not in error_text:
            msg = "Refused." if detail else "Permission denied. Check your token permissions."
            if needed := response.headers.get("X-Accepted-GitHub-Permissions"):
                msg += f" This endpoint needs the `{needed}` permission."
            if _WORKFLOW_SCOPE_REFUSAL in error_text:
                msg += _WORKFLOW_SCOPE_GUIDANCE
            elif self._oauth_mode:
                msg += (
                    " If accessing a private organisation repository, the org admin may need to"
                    " approve this OAuth App under Org Settings -> Third-party access -> OAuth App access policy."
                )
            raise GitHubAPIError(f"{where}{msg}{detail}", status_code=403, response_body=response_body)
        raise _rate_limit_error(response, response_body, where, detail)

    def _get_headers(self):
        """Constructs the HTTP headers required for GitHub API requests."""
        token = self._resolve_token()
        if not token:
            raise GitHubAuthError(MISSING_CREDENTIALS)
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }
        return headers

    def _cache_key(self, url: str, params: Any, headers: dict[str, str]) -> str:
        """Params ride outside the URL, so page 2 must not read page 1's body. One
        URL serves JSON or a diff by its Accept header, so each form keeps its own
        entry. See #514."""
        key = f"{url}?{sorted(params.items())}" if params else url
        return f"{key} {headers.get('Accept', '')}"

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
        key = self._cache_key(url, kwargs.get("params"), sent) if method == "GET" and ETAG_CACHE_ENTRIES else None
        cached = self._etags.get(key) if key else None
        if cached:
            sent["If-None-Match"] = cached[0]
        try:
            response = await self._http.request(method, url, headers=sent, **kwargs)
            if response.status_code == 304 and cached:
                logger.info(f"Not modified {ctx}")
                return httpx.Response(200, content=cached[1], headers=cached[2], request=response.request)
            if response.status_code in allow_status:
                logger.info(f"Expected {response.status_code} {ctx}")
                return response
            if not response.is_success:
                self._handle_response_error(response, context)
            if key and (etag := response.headers.get("ETag")):
                self._remember_etag(key, etag, response)
            logger.info(f"Success {method.upper()} {ctx}")
            return response
        # A rate limit keeps its type, so a guarded tool still reports RATE_LIMITED. See #519.
        except GitHubAuthError, GitHubRateLimitError:
            raise
        except Exception as e:
            raise ToolError(str(e)) from e

    def _remember_etag(self, key: str, etag: str, response: httpx.Response) -> None:
        """Oldest out first once the cache is full, so a long-running server
        does not grow a body per URL it has ever read."""
        headers = {name: value for name in _CACHED_HEADERS if (value := response.headers.get(name))}
        self._etags.pop(key, None)
        while len(self._etags) >= ETAG_CACHE_ENTRIES:
            self._etags.pop(next(iter(self._etags)))
        self._etags[key] = (etag, response.content, headers)

    async def _pr(self, repo_owner: str, repo_name: str, pr_number: int) -> dict[str, Any]:
        """The pull request as GitHub serves it, for the tools that read one field off it."""
        url = f"{_repo(repo_owner, repo_name)}/pulls/{pr_number}"
        return (await self._request("GET", url, context=f"PR #{pr_number}")).json()

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
        """Fetches the net unified diff of a specific pull request, capped at
        max_bytes. bytes_total is the whole diff either way, so a truncated reply
        says what was left behind. See #314 and #514."""
        if max_bytes < 0:
            raise GitHubValidationError("max_bytes cannot be negative.")
        url = f"{_repo(repo_owner, repo_name)}/pulls/{pr_number}"
        diff = {"Accept": "application/vnd.github.diff"}
        content = (await self._request("GET", url, context=f"PR #{pr_number} diff", headers=diff)).content
        kept = content[:max_bytes]
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
        return _pr_content(await self._pr(repo_owner, repo_name, pr_number))

    @_write
    async def add_pr_comments(self, repo_owner: str, repo_name: str, pr_number: int, comment: str) -> CommentData:
        """Adds a comment to a specific pull request."""
        url = f"{_repo(repo_owner, repo_name)}/issues/{pr_number}/comments"
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
        side: Annotated[_Side, "LEFT for a line the PR deletes, RIGHT for one it adds or leaves as context"] = "RIGHT",
        start_line: Annotated[int | None, "First line of a range ending at line. Omit to comment on line alone"] = None,
        start_side: Annotated[_Side | None, "Side start_line sits on. Omit to match side"] = None,
        expected_head_sha: Annotated[
            str | None, "Attach the comment to this SHA, as github_get_pr_content reports it. Omit for the current head"
        ] = None,
    ) -> CommentData:
        """Adds an inline review comment to a line, or to a range of lines, in a
        file within a PR. The line must fall inside the PR's diff hunks: GitHub
        rejects a comment on an unchanged line outside any hunk, and on a path not
        in the diff. A deleted line exists only on side LEFT. Pass head_sha from
        github_get_pr_content as expected_head_sha, so line numbers taken from that
        diff apply to that commit. See #517."""
        if start_line is not None and start_line >= line:
            raise GitHubValidationError(f"start_line {start_line} must come before line {line} on {path}.")
        commit_id = expected_head_sha
        if not commit_id:
            commit_id = ((await self._pr(repo_owner, repo_name, pr_number)).get("head") or {}).get("sha")
        if not commit_id:
            raise GitHubAPIError(f"Could not retrieve head SHA for PR #{pr_number}")
        review_url = f"{_repo(repo_owner, repo_name)}/pulls/{pr_number}/comments"
        payload: dict[str, Any] = {
            "body": comment_body,
            "commit_id": commit_id,
            "path": path,
            "line": line,
            "side": side,
        }
        if start_line is not None:
            payload["start_line"] = start_line
            payload["start_side"] = start_side or side
        where = f"{path}:{start_line}-{line}" if start_line is not None else f"{path}:{line}"
        data = (await self._request("POST", review_url, context=f"inline comment on {where}", json=payload)).json()
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
        url = f"{_repo(repo_owner, repo_name)}/{_COMMENT_SEGMENTS[kind]}/{pr_number}/comments"
        response = await self._request(
            "GET", url, context=f"{kind} comments on PR #{pr_number}", params={"per_page": per_page, "page": page}
        )
        trim = _review_comment_result if kind == "inline" else _comment_result
        comments = [trim(comment) for comment in response.json()]
        return {**_page(response, comments), "kind": kind, "comments": comments}

    @_write(idempotent=True)
    async def update_pr_comment(
        self,
        repo_owner: str,
        repo_name: str,
        comment_id: Annotated[int, "The comment's own id, as returned by github_list_pr_comments, not the PR number"],
        body: str,
        kind: Literal["conversation", "inline"] = "conversation",
    ) -> CommentData:
        """Rewrites a comment already posted. Conversation and review comments
        have separate id spaces, so the kind has to match where the id came from."""
        segment = _COMMENT_SEGMENTS[kind]
        url = f"{_repo(repo_owner, repo_name)}/{segment}/comments/{comment_id}"
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
        url = f"{_repo(repo_owner, repo_name)}/pulls/{pr_number}/comments/{comment_id}/replies"
        data = (await self._request("POST", url, context=f"reply to comment {comment_id}", json={"body": body})).json()
        return _review_comment_result(data)

    async def _replace_labels(self, repo_owner: str, repo_name: str, number: int, labels: list[str]) -> dict[str, Any]:
        """Replaces the label set on an issue or a pull request, and returns the
        issue payload GitHub answers with. Labels hang off the issues endpoint,
        which serves pull requests too, so they cannot ride along with a pull
        request payload."""
        url = f"{_repo(repo_owner, repo_name)}/issues/{number}"
        return (await self._request("PATCH", url, context=f"#{number} labels", json={"labels": labels})).json()

    @_write(idempotent=True)
    async def update_pr(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        title: Annotated[str | None, "Replacement title. Omit to leave the current title alone"] = None,
        body: Annotated[str | None, "Replacement body in Markdown. Omit to leave the current body alone"] = None,
        state: Annotated[_OpenClosed | None, "Omit to leave the state alone"] = None,
        base: Annotated[str | None, "Branch to retarget the pull request onto"] = None,
        labels: Annotated[
            list[str] | None,
            "Replacement label set. Omit to keep the current labels, pass [] to strip them all. "
            "The 'mcp' tracking label is not re-added here",
        ] = None,
    ) -> PRContent:
        """Updates an existing pull request. Only the fields supplied are sent, the
        rest keep their current values, so a title can change without restating the body.
        Labels take a second call, since the pull request payload carries none. That
        call answers with the issue, which has no head, base or reviewers, so a
        labels-only update reads the pull request back."""
        payload = _supplied(title=title, body=body, state=state, base=base)
        if not payload and labels is None:
            raise GitHubValidationError("Supply at least one of title, body, state, base or labels to update.")
        data: dict[str, Any] = {}
        if payload:
            url = f"{_repo(repo_owner, repo_name)}/pulls/{pr_number}"
            data = (await self._request("PATCH", url, context=f"PR #{pr_number}", json=payload)).json()
        if labels is not None:
            await self._replace_labels(repo_owner, repo_name, pr_number, labels)
        return _pr_content(data or await self._pr(repo_owner, repo_name, pr_number))

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
        node_id = (await self._pr(repo_owner, repo_name, pr_number)).get("node_id")
        if not node_id:
            raise GitHubAPIError(f"Could not retrieve the node id for PR #{pr_number}")
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
        labels: Annotated[
            list[str] | None,
            "Labels to apply, with the 'mcp' tracking label appended unless mcp_label is False. "
            "Omit to leave the pull request unlabelled",
        ] = None,
        mcp_label: Annotated[bool, "Append the 'mcp' tracking label to labels. Pass False to opt out"] = True,
    ) -> dict[str, Any]:
        """Creates a new pull request. Labels are applied in a second call, since
        the create endpoint takes none, and they come back under labels so a set
        the token could not write is visible."""
        url = f"{_repo(repo_owner, repo_name)}/pulls"
        data = (
            await self._request(
                "POST",
                url,
                context=f"create PR {head} -> {base}",
                json={"title": title, "body": body, "head": head, "base": base, "draft": draft},
            )
        ).json()
        result = {
            "pr_url": data.get("html_url"),
            "pr_number": data.get("number"),
            "status": data.get("state"),
            "title": data.get("title"),
        }
        if labels is not None and result["pr_number"] is not None:
            labelled = await self._replace_labels(
                repo_owner, repo_name, result["pr_number"], _with_mcp_label(labels, mcp_label)
            )
            result["labels"] = [label["name"] for label in labelled.get("labels", [])]
        return result

    @_read_only
    async def list_open_issues_prs(
        self,
        repo_owner: Annotated[
            str, "Username under involves and user, organisation under org, repository owner under repo"
        ],
        repo_name: Annotated[str, "Repository name. Required when filtering is repo, ignored otherwise"] = "",
        issue: Annotated[Literal["pr", "issue"], "pr for pull requests, issue for issues"] = "pr",
        filtering: Annotated[
            Literal["user", "org", "repo", "involves"],
            "involves for items that user authored, is assigned, is mentioned in or commented on, anywhere on GitHub. "
            "user for items in that user's repositories. org for an organisation's. repo for one repository",
        ] = "involves",
        per_page: Annotated[int, "Number of results per page (1-100)"] = 50,
        page: Annotated[int, "Which page of results to return, counting from 1"] = 1,
    ) -> dict[str, Any]:
        """Lists open pull requests or issues. The search is fixed to is:open, so
        closed and merged items are out of reach here. Call github_search_issues_prs for
        those, and for any qualifier this tool does not expose."""
        if filtering == "repo":
            if not repo_name:
                raise GitHubValidationError("repo_name is required when filtering='repo'")
            search_target = f"{repo_owner}/{repo_name}"
        else:
            search_target = repo_owner
        url = f"{API}/search/issues?q=is:{issue}+is:open+{filtering}:{search_target}&per_page={per_page}&page={page}"
        response = await self._request("GET", url, context=f"list open {issue}s for {search_target}")
        data = response.json()
        items = [_search_item(item) for item in data["items"]]
        return {
            "total": data["total_count"],
            **_page(response, items),
            f"open_{issue}s": items,
        }

    @_read_only
    async def search_issues_prs(
        self,
        query: Annotated[str, "Terms and qualifiers, e.g. 'rate limit repo:owner/name is:closed label:bug'"],
        per_page: Annotated[int, "Number of results per page (1-100)"] = 50,
        page: int = 1,
        search_type: Annotated[
            _SearchType,
            "lexical matches words. semantic and hybrid match issues by meaning and need is:issue in the query",
        ] = "lexical",
    ) -> dict[str, Any]:
        """Searches issues and pull requests by text and qualifiers. Unlike
        github_list_open_issues_prs the query is the caller's, so closed and merged
        items are reachable and any qualifier GitHub search accepts works. GitHub
        runs a lexical search when search_type is absent, so the default sends none.
        Semantic and hybrid cover issues alone, and GitHub falls back to lexical for
        a query that can reach a PR."""
        if not query.strip():
            raise GitHubValidationError("Supply a search query.")
        mode = "" if search_type == "lexical" else f"&search_type={search_type}"
        url = f"{API}/search/issues?q={quote_plus(query)}&advanced_search=true{mode}&per_page={per_page}&page={page}"
        response = await self._request("GET", url, context=f"search issues and PRs for {query!r}")
        data = response.json()
        items = [_search_item(item) for item in data["items"]]
        return {
            "total": data["total_count"],
            "incomplete_results": data.get("incomplete_results", False),
            "search_type": data.get("search_type"),
            "lexical_fallback_reason": data.get("lexical_fallback_reason"),
            **_page(response, items),
            "items": items,
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
        url = f"{_repo(repo_owner, repo_name)}/labels"
        response = await self._request(
            "GET", url, context=f"labels for {repo_owner}/{repo_name}", params={"per_page": per_page, "page": page}
        )
        labels = [_pick(label, "name", "description", "color") for label in response.json()]
        return {**_page(response, labels), "labels": labels}

    async def _milestone_by_title(self, repo_owner: str, repo_name: str, title: str) -> dict[str, Any] | None:
        """The milestone with this title, open or closed, or None. GitHub addresses
        a milestone by number while people think in titles, so every tool that
        takes a title comes through here."""
        url = f"{_repo(repo_owner, repo_name)}/milestones"
        for page in range(1, MAX_MILESTONE_PAGES + 1):
            batch = (
                await self._request(
                    "GET",
                    url,
                    context=f"milestones for {repo_owner}/{repo_name}",
                    params={"state": "all", "per_page": 100, "page": page},
                )
            ).json()
            for milestone in batch:
                if milestone.get("title") == title:
                    return milestone
            if len(batch) < 100:
                break
        return None

    async def _milestone_number(self, repo_owner: str, repo_name: str, title: str) -> int:
        """As _milestone_by_title, for the callers that cannot proceed without one."""
        milestone = await self._milestone_by_title(repo_owner, repo_name, title)
        if milestone is None:
            raise GitHubNotFoundError(f"No milestone titled '{title}' in {repo_owner}/{repo_name}")
        return milestone["number"]

    async def _account_kind(self, owner: str) -> str:
        """User or Organization. The two repo endpoints are not interchangeable,
        so the account type has to be read rather than guessed at."""
        response = await self._request("GET", f"{API}/users/{owner}", context=f"account {owner}", allow_status=(404,))
        if response.status_code == 404:
            raise GitHubNotFoundError(f"No user or organisation named '{owner}'")
        return response.json().get("type", "User")

    @_read_only
    async def list_repos(
        self,
        owner: Annotated[
            str, "User or organisation. Omit for the caller's own, which is the only way to see private ones"
        ] = "",
        sort: _RepoSort = "updated",
        per_page: Annotated[int, "Number of results per page (1-100)"] = 50,
        page: int = 1,
    ) -> dict[str, Any]:
        """Lists repositories for a user, an organisation, or the caller. The
        owner's account type picks the endpoint, since /orgs 404s on a person and
        /users hides an organisation's private repositories. See #354."""
        if not owner:
            path = "user/repos"
        elif await self._account_kind(owner) == "Organization":
            path = f"orgs/{owner}/repos"
        else:
            path = f"users/{owner}/repos"
        response = await self._request(
            "GET",
            f"{API}/{path}",
            context=f"repos for {owner or 'the authenticated user'}",
            params={"sort": sort, "per_page": per_page, "page": page},
        )
        repos = [_repo_result(repo) for repo in response.json()]
        return {**_page(response, repos), "repos": repos}

    @_read_only
    async def list_milestones(
        self,
        repo_owner: str,
        repo_name: str,
        state: Annotated[_MilestoneState, "Which milestones to return"] = "open",
        per_page: Annotated[int, "Number of results per page (1-100)"] = 50,
        page: int = 1,
    ) -> dict[str, Any]:
        """Lists a repository's milestones with the count of issues in each."""
        url = f"{_repo(repo_owner, repo_name)}/milestones"
        response = await self._request(
            "GET",
            url,
            context=f"{state} milestones for {repo_owner}/{repo_name}",
            params={"state": state, "per_page": per_page, "page": page},
        )
        milestones = [_pick(milestone, *_MILESTONE_FIELDS) for milestone in response.json()]
        return {**_page(response, milestones), "state": state, "milestones": milestones}

    @_write
    async def create_milestone(
        self,
        repo_owner: str,
        repo_name: str,
        title: str,
        description: str = "",
        due_on: Annotated[str | None, "Due date as ISO 8601, e.g. 2026-12-31T23:59:59Z"] = None,
        state: _OpenClosed = "open",
    ) -> dict[str, Any]:
        """Opens a milestone. A title the repository already uses fails, since
        titles are unique per repository. Edit the existing one with
        github_update_milestone."""
        url = f"{_repo(repo_owner, repo_name)}/milestones"
        payload: dict[str, Any] = {"title": title, "state": state, "description": description}
        if due_on:
            payload["due_on"] = due_on
        data = (await self._request("POST", url, context=f"create milestone {title}", json=payload)).json()
        return _pick(data, *_MILESTONE_FIELDS)

    @_write(idempotent=True)
    async def update_milestone(
        self,
        repo_owner: str,
        repo_name: str,
        title: Annotated[str, "Title of the milestone to change"],
        new_title: Annotated[str | None, "Replacement title. Omit to leave it alone"] = None,
        description: Annotated[str | None, "Replacement description. Omit to leave it alone"] = None,
        due_on: Annotated[str | None, "Replacement due date as ISO 8601"] = None,
        state: Annotated[_OpenClosed | None, "Pass closed to close the milestone"] = None,
    ) -> dict[str, Any]:
        """Changes a milestone in place. Only the fields supplied are sent, so
        closing one leaves its title and due date alone."""
        payload = _changes(new_title=new_title, description=description, due_on=due_on, state=state)
        payload = {("title" if name == "new_title" else name): value for name, value in payload.items()}
        number = await self._milestone_number(repo_owner, repo_name, title)
        url = f"{_repo(repo_owner, repo_name)}/milestones/{number}"
        data = (await self._request("PATCH", url, context=f"milestone {title}", json=payload)).json()
        return _pick(data, *_MILESTONE_FIELDS)

    @_write(idempotent=True)
    async def set_issue_milestone(
        self,
        repo_owner: str,
        repo_name: str,
        issue_number: int,
        milestone: Annotated[str | None, "Milestone title to file it under. Omit or pass null for no milestone"] = None,
    ) -> IssueData:
        """Files an issue under a milestone, or takes it off one. Takes the title
        rather than the number GitHub wants, and looks it up, which is the work
        github_update_issue is kept clear of. Omit the title to clear the milestone."""
        number = await self._milestone_number(repo_owner, repo_name, milestone) if milestone else None
        url = f"{_repo(repo_owner, repo_name)}/issues/{issue_number}"
        data = (
            await self._request("PATCH", url, context=f"issue #{issue_number} milestone", json={"milestone": number})
        ).json()
        return _issue_result(data)

    @_read_only
    async def get_issue(self, repo_owner: str, repo_name: str, issue_number: int) -> IssueData:
        """Fetches a single issue by number, with its body, labels, assignees and
        milestone. Reads straight from the issue rather than the search index, so
        it sees a write immediately. See #358."""
        url = f"{_repo(repo_owner, repo_name)}/issues/{issue_number}"
        data = (await self._request("GET", url, context=f"issue #{issue_number}")).json()
        if "pull_request" in data:
            raise GitHubValidationError(f"#{issue_number} is a pull request. Use github_get_pr_content instead.")
        return _issue_result(data)

    @_write
    async def create_issue(
        self,
        repo_owner: str,
        repo_name: str,
        title: str,
        body: str,
        labels: Annotated[
            list[str] | None,
            "Labels to apply, with the 'mcp' tracking label appended unless mcp_label is False. "
            "Omit to leave the issue unlabelled",
        ] = None,
        milestone: Annotated[str | None, "Milestone title to file it under. Omit or pass null for no milestone"] = None,
        mcp_label: Annotated[bool, "Append the 'mcp' tracking label to labels. Pass False to opt out"] = True,
    ) -> IssueData:
        """Creates a new issue. The update tools replace the label set as given
        and never re-add 'mcp'; only the create tools append it, and only when
        mcp_label is left enabled."""
        url = f"{_repo(repo_owner, repo_name)}/issues"
        payload: dict[str, Any] = {"title": title, "body": body}
        if labels is not None:
            payload["labels"] = _with_mcp_label(labels, mcp_label)
        if milestone:
            payload["milestone"] = await self._milestone_number(repo_owner, repo_name, milestone)
        data = (
            await self._request("POST", url, context=f"create issue in {repo_owner}/{repo_name}", json=payload)
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
        expected_head_sha: Annotated[
            str | None, "Refuse unless the head still matches this SHA, as github_get_pr_content reports it"
        ] = None,
    ) -> dict[str, Any]:
        """Merges a specific pull request. Read head_sha from github_get_pr_content
        and pass it as expected_head_sha, and GitHub refuses with a 409 rather than
        merge a push that arrived after the read. See #517."""
        url = f"{_repo(repo_owner, repo_name)}/pulls/{pr_number}/merge"
        fields = _supplied(commit_title=commit_title, commit_message=commit_message, sha=expected_head_sha)
        payload = {"merge_method": merge_method, **fields}
        return (await self._request("PUT", url, context=f"PR #{pr_number} merge", json=payload)).json()

    @_write(idempotent=True)
    async def update_pr_branch(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        expected_head_sha: Annotated[
            str | None, "Refuse unless the head still matches this SHA, as github_get_pr_content reports it"
        ] = None,
    ) -> dict[str, Any]:
        """Updates the pull request branch with the latest upstream changes. Read
        head_sha from github_get_pr_content and pass it as expected_head_sha to be refused
        rather than to overwrite a push that landed since."""
        url = f"{_repo(repo_owner, repo_name)}/pulls/{pr_number}/update-branch"
        payload = _supplied(expected_head_sha=expected_head_sha)
        return (await self._request("PUT", url, context=f"PR #{pr_number} update branch", json=payload)).json()

    async def _branch_head(
        self, repo_owner: str, repo_name: str, branch: str | None, pr_number: int | None
    ) -> tuple[str, str, str]:
        """The branch to commit to, the commit at its head and the default branch. A
        fork's head branch lives in the fork, so the same name here is another branch."""
        if pr_number is None:
            base = _repo(repo_owner, repo_name)
            repo = (await self._request("GET", base, context=f"repository {repo_owner}/{repo_name}")).json()
            target: str = branch or repo["default_branch"]
            url = f"{base}/git/ref/heads/{quote(target)}"
            ref = (await self._request("GET", url, context=f"branch {target}")).json()
            return target, ref["object"]["sha"], repo["default_branch"]
        pr = await self._pr(repo_owner, repo_name, pr_number)
        head, home = pr["head"], pr["base"]["repo"]
        if (head.get("repo") or {}).get("full_name") != home["full_name"]:
            raise GitHubValidationError(f"PR #{pr_number} comes from another repository, not {home['full_name']}.")
        return head["ref"], head["sha"], home["default_branch"]

    @_write
    async def create_empty_commit(
        self,
        repo_owner: str,
        repo_name: str,
        branch: Annotated[str | None, "Branch to commit to. Omit it and pr_number for the default branch"] = None,
        pr_number: Annotated[int | None, "Pull request whose head branch to commit to, in place of branch"] = None,
        message: Annotated[
            str, "Commit message. The default carries [dependabot skip], so Dependabot keeps rebasing the branch"
        ] = "chore: trigger CI [dependabot skip]",
        expected_head_sha: Annotated[str | None, "Refuse unless the head still matches this SHA"] = None,
        allow_default_branch: Annotated[bool, "Commit to the default branch, which is refused otherwise"] = False,
    ) -> EmptyCommitResult:
        """Pushes a commit that changes no file, so CI starts a new run under the
        caller's name. A re-run keeps the privileges of the first run, which for
        Dependabot is a read-only token. Dependabot stops rebasing a branch once
        another commit is pushed to it, unless the message carries [dependabot skip],
        as the default does. See #490."""
        if branch and pr_number is not None:
            raise GitHubValidationError("Pass branch or pr_number, not both.")
        branch, parent_sha, default_branch = await self._branch_head(repo_owner, repo_name, branch, pr_number)
        if branch == default_branch and not allow_default_branch:
            raise GitHubValidationError(
                f"'{branch}' is the default branch. Pass allow_default_branch=True to commit to it."
            )
        if expected_head_sha and expected_head_sha != parent_sha:
            raise GitHubValidationError(f"'{branch}' is at {parent_sha}, not {expected_head_sha}.")
        base = _repo(repo_owner, repo_name)
        parent = (await self._request("GET", f"{base}/git/commits/{parent_sha}", context=f"commit {parent_sha}")).json()
        payload = {"message": message, "tree": parent["tree"]["sha"], "parents": [parent_sha]}
        commit = (
            await self._request("POST", f"{base}/git/commits", context=f"empty commit on {branch}", json=payload)
        ).json()
        # The update must fast-forward, so a push that arrived after the read fails rather than being overwritten.
        await self._request(
            "PATCH",
            f"{base}/git/refs/heads/{quote(branch)}",
            context=f"move {branch}",
            json={"sha": commit["sha"], "force": False},
        )
        return {
            "commit_sha": commit["sha"],
            "parent_sha": parent_sha,
            "parent_tree_sha": parent["tree"]["sha"],
            "tree_sha": commit["tree"]["sha"],
            "branch": branch,
            "html_url": commit["html_url"],
        }

    @_write
    async def rerun_job(
        self,
        repo_owner: str,
        repo_name: str,
        job_id: Annotated[int, "The check run's id, as github_get_pr_status_checks reports it"],
        enable_debug_logging: Annotated[bool, "Run the job again with runner and step debug logging"] = False,
    ) -> dict[str, Any]:
        """Re-runs one Actions job and every job that depends on it, without a
        commit on the branch. Each call starts another attempt, so calling it twice
        runs the job twice. A re-run keeps the privileges of the first run, so a
        Dependabot job that failed on its read-only token wants
        github_create_empty_commit instead. See #503."""
        url = f"{_repo(repo_owner, repo_name)}/actions/jobs/{job_id}/rerun"
        payload = {"enable_debug_logging": enable_debug_logging}
        await self._request("POST", url, context=f"re-run job {job_id}", json=payload)
        return {"job_id": job_id, "status": "requested"}

    @_write
    async def dispatch_workflow(
        self,
        repo_owner: str,
        repo_name: str,
        workflow_id: Annotated[int | str, "The workflow's id, or its file name such as ci.yml"],
        ref: Annotated[str, "Branch or tag to run the workflow on"],
        inputs: Annotated[_WorkflowInputs | None, "Values for the inputs the workflow declares, at most 25"] = None,
    ) -> dict[str, Any]:
        """Starts a workflow that declares a workflow_dispatch trigger, on ref, with
        inputs keyed by the names its file declares. GitHub takes at most 25 inputs,
        so more are refused before any request. The request sets return_run_details,
        since without it GitHub answers 204 and says nothing of the run. See #503."""
        if inputs and len(inputs) > 25:
            raise GitHubValidationError(f"GitHub takes at most 25 inputs, and {len(inputs)} were passed.")
        url = f"{_repo(repo_owner, repo_name)}/actions/workflows/{quote(str(workflow_id), safe='')}/dispatches"
        payload = {"ref": ref, "return_run_details": True, **_supplied(inputs=inputs)}
        response = await self._request("POST", url, context=f"dispatch {workflow_id} on {ref}", json=payload)
        return _pick(response.json(), "workflow_run_id", "run_url", "html_url")

    @_write(idempotent=True)
    async def update_issue(
        self,
        repo_owner: str,
        repo_name: str,
        issue_number: int,
        title: Annotated[str | None, "Replacement title. Omit to leave the current title alone"] = None,
        body: Annotated[str | None, "Replacement body in Markdown. Omit to leave the current body alone"] = None,
        labels: Annotated[
            list[str] | None,
            "Replacement label set. Omit to keep the current labels, pass [] to strip them all. "
            "The 'mcp' tracking label is not re-added here",
        ] = None,
        state: Annotated[_OpenClosed | None, "Omit to leave the issue in whichever state it is already in"] = None,
    ) -> IssueData:
        """Updates an existing issue. Only the fields supplied are sent, the rest keep their current values."""
        payload = _changes(title=title, body=body, labels=labels, state=state)
        url = f"{_repo(repo_owner, repo_name)}/issues/{issue_number}"
        data = (await self._request("PATCH", url, context=f"issue #{issue_number}", json=payload)).json()
        return _issue_result(data)

    @_read_only
    async def list_pr_reviews(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        per_page: Annotated[int, "Number of results per page (1-100)"] = 50,
        page: Annotated[int, "Which page of results to return, counting from 1"] = 1,
    ) -> dict[str, Any]:
        """Lists the reviews submitted on a pull request, oldest first, each with
        its author and verdict. A review is not a comment: github_list_pr_comments returns
        what was said on lines and in the thread, not whether anyone approved.
        Read requested_reviewers from github_get_pr_content to tell nobody has reviewed
        from nobody having been asked. See #408."""
        url = f"{_repo(repo_owner, repo_name)}/pulls/{pr_number}/reviews"
        response = await self._request(
            "GET", url, context=f"reviews on PR #{pr_number}", params={"per_page": per_page, "page": page}
        )
        reviews = [_review_result(review) for review in response.json()]
        return {**_page(response, reviews), "reviews": reviews}

    @_write
    async def update_reviews(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        event: Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"],
        body: str | None = None,
        expected_head_sha: Annotated[
            str | None, "Attach the review to this SHA, as github_get_pr_content reports it. Omit for the newest commit"
        ] = None,
    ) -> dict[str, Any]:
        """Submits a review for a specific pull request. GitHub applies a review to
        the newest commit unless told otherwise, so pass head_sha from
        github_get_pr_content as expected_head_sha to review the commit that was read. See #517."""
        url = f"{_repo(repo_owner, repo_name)}/pulls/{pr_number}/reviews"
        payload = {"body": body, "event": event, **_supplied(commit_id=expected_head_sha)}
        data = (await self._request("POST", url, context=f"PR #{pr_number} review", json=payload)).json()
        return _pick(data, "id", "state", "body", "html_url", "submitted_at")

    @_write(idempotent=True)
    async def update_assignees(
        self, repo_owner: str, repo_name: str, issue_number: int, assignees: list[str]
    ) -> dict[str, Any]:
        """Updates the assignees for a specific issue or pull request."""
        url = f"{_repo(repo_owner, repo_name)}/issues/{issue_number}"
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

    @_write(idempotent=True)
    async def request_reviewers(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        reviewers: Annotated[_Names, "User logins, each a collaborator on the repository"] = None,
        team_reviewers: Annotated[_Names, "Team slugs in the repository's organisation"] = None,
        remove: Annotated[bool, "Withdraw the request from these reviewers rather than ask them"] = False,
    ) -> dict[str, Any]:
        """Asks users and teams to review a pull request, or withdraws the request.
        Both calls answer with the pull request, so its reviewer lists show what took
        effect without a second read. The removal endpoint requires reviewers, so
        withdrawing teams alone sends an empty list. See #502."""
        users, teams = reviewers or [], team_reviewers or []
        url = f"{_repo(repo_owner, repo_name)}/pulls/{pr_number}/requested_reviewers"
        context = f"{'withdraw' if remove else 'request'} review from {', '.join(users + teams)} on PR #{pr_number}"
        payload = {"reviewers": users, "team_reviewers": teams}
        data = (await self._request("DELETE" if remove else "POST", url, context=context, json=payload)).json()
        return _review_request_result(data, users, teams, remove)

    @_read_only
    async def get_repository_file(
        self,
        repo_owner: str,
        repo_name: str,
        path: Annotated[str, "File path from the repository root, e.g. src/mcp_github/auth.py"],
        ref: Annotated[str | None, "Branch, tag or SHA to read at. Omit for the default branch"] = None,
        offset: Annotated[int, "Byte to start the window at. Pass a previous next_offset to carry on"] = 0,
        limit: Annotated[int, "Cap on the bytes returned. Pass 0 to learn the size without reading"] = FILE_MAX_BYTES,
    ) -> FileResult:
        """Reads one file from a repository at ref, windowed to limit bytes from
        offset. bytes_total is the whole file either way and next_offset says where
        to carry on, so a partial read never passes for the lot. A directory
        belongs to github_list_repository_tree. See #409."""
        if offset < 0 or limit < 0:
            raise GitHubValidationError("offset and limit cannot be negative.")
        url = f"{_repo(repo_owner, repo_name)}/contents/{quote(path.lstrip('/'))}"
        if ref:
            url += f"?ref={quote(ref, safe='')}"
        where = f"{path} at {ref or 'the default branch'}"
        response = await self._request("GET", url, context=where, headers={"Accept": "application/vnd.github.raw"})
        if response.headers.get("content-type", "").startswith("application/json"):
            raise GitHubValidationError(f"{path} is a directory. Call github_list_repository_tree to list it.")
        content = response.content
        window = content[offset : offset + limit]
        end = offset + len(window)
        binary = b"\x00" in window
        return {
            "path": path,
            "ref": ref,
            "content": "" if binary else window.decode("utf-8", errors="ignore"),
            "binary": binary,
            "bytes_returned": len(window),
            "bytes_total": len(content),
            "truncated": end < len(content),
            "next_offset": end if end < len(content) else None,
        }

    @_read_only
    async def list_repository_tree(
        self,
        repo_owner: str,
        repo_name: str,
        path: Annotated[str, "Subdirectory to list. Omit for the repository root"] = "",
        ref: Annotated[str | None, "Branch, tag or SHA to list at. Omit for the default branch"] = None,
        recursive: Annotated[bool, "Descend into every subdirectory rather than one level"] = False,
    ) -> TreeResult:
        """Lists a repository's tree at ref, one level deep unless recursive. Each
        entry carries path, mode, type, size and sha. truncated is True where the
        tree exceeded GitHub's cap, which no amount of paging widens. See #409."""
        target = f"{ref or 'HEAD'}:{path.strip('/')}" if path.strip("/") else (ref or "HEAD")
        url = f"{_repo(repo_owner, repo_name)}/git/trees/{quote(target, safe='')}"
        if recursive:
            url += "?recursive=1"
        where = f"{path or 'the root'} at {ref or 'the default branch'}"
        data = (await self._request("GET", url, context=f"tree of {where}")).json()
        entries = [_pick(entry, "path", "mode", "type", "size", "sha") for entry in data.get("tree", [])]
        return {
            "path": path,
            "ref": ref,
            "total": len(entries),
            "entries": entries,
            "truncated": data.get("truncated", False),
        }

    @_read_only
    async def get_latest_sha(
        self,
        repo_owner: str,
        repo_name: str,
        ref: Annotated[
            str | None, "Branch, tag or SHA to read the newest commit of. Omit for the default branch"
        ] = None,
    ) -> str | None:
        """Fetches the SHA of the newest commit on ref, or on the default branch when
        ref is omitted. Returns None if the repository has no commits. The answer is
        a reading rather than a pin, so a push landing afterwards moves it."""
        url = f"{_repo(repo_owner, repo_name)}/commits?per_page=1"
        if ref:
            url += f"&sha={quote(ref, safe='')}"
        where = f"{ref or 'default branch'} of {repo_owner}/{repo_name}"
        response = await self._request("GET", url, context=f"commits for {where}", allow_status=(409,))
        if response.status_code == 409:
            return None
        data = response.json()
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
        base = _repo(repo_owner, repo_name)
        target = sha or await self.get_latest_sha(repo_owner, repo_name)
        if not target:
            raise GitHubNotFoundError(f"No commits found in {repo_owner}/{repo_name}; cannot create tag {tag_name}")
        ref_target = target
        if message:
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
        if_exists: Annotated[
            _IfExists, "fail if the tag already carries a release, or update to overwrite its title and notes"
        ] = "fail",
    ) -> dict[str, Any]:
        """Publishes a release for a tag. A tag that already carries one fails,
        since overwriting published notes cannot be undone. Pass if_exists='update'
        to replace them deliberately, and read updated on the reply to tell which
        happened. See #401.

        The update path sends the title, notes, draft and prerelease only, so
        make_latest and generate_release_notes are dropped and the generated
        changelog from the first publish is replaced by body alone."""
        url = f"{_repo(repo_owner, repo_name)}/releases"
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
            if if_exists == "fail":
                raise GitHubValidationError(
                    f"A release already exists for tag '{tag_name}' in {repo_owner}/{repo_name}. "
                    "Edit it with github_update_release, or pass if_exists='update' to overwrite its title and notes."
                )
            logger.info(f"Release for {tag_name} already exists, updating it instead")
            updated = await self.update_release(
                repo_owner,
                repo_name,
                tag_name,
                name=release_name,
                body=body,
                draft=draft,
                prerelease=prerelease,
            )
            return {**updated, "updated": True}
        return {**_pick(response.json(), *_RELEASE_FIELDS), "updated": False}

    async def _release_by_tag(self, repo_owner: str, repo_name: str, tag_name: str) -> dict[str, Any] | None:
        """The release for a tag, draft or published, or None when the tag carries
        none. GitHub serves only published releases by tag, so a draft is found in
        the listing, which shows drafts to a token with push access."""
        base = _repo(repo_owner, repo_name)
        response = await self._request(
            "GET", f"{base}/releases/tags/{tag_name}", context=f"release for tag {tag_name}", allow_status=(404,)
        )
        if response.status_code != 404:
            return response.json()
        for page in range(1, MAX_RELEASE_PAGES + 1):
            listing = await self._request(
                "GET",
                f"{base}/releases",
                context=f"releases for {repo_owner}/{repo_name}",
                params={"per_page": 100, "page": page},
            )
            match = next((release for release in listing.json() if release.get("tag_name") == tag_name), None)
            if match or not _has_more(listing):
                return match
        return None

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
        per_page: Annotated[int, "Number of results per page (1-100)"] = 50,
        page: int = 1,
    ) -> dict[str, Any]:
        """Lists a repository's releases, newest first."""
        url = f"{_repo(repo_owner, repo_name)}/releases"
        response = await self._request(
            "GET", url, context=f"releases for {repo_owner}/{repo_name}", params={"per_page": per_page, "page": page}
        )
        releases = [_pick(release, *_RELEASE_FIELDS) for release in response.json()]
        return {**_page(response, releases), "releases": releases}

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
        url = f"{_repo(repo_owner, repo_name)}/releases/latest"
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
    ) -> dict[str, Any]:
        """Changes a release in place, a draft included. Only the fields supplied are sent,
        so correcting a title does not wipe the notes. make_latest is settable on
        github_create_release alone, and only on the call that first publishes the tag,
        since publishing again falls through to here and this tool does not send
        it. Move the latest badge by deleting the release and publishing it again,
        or in the GitHub UI."""
        payload = _changes(name=name, body=body, draft=draft, prerelease=prerelease)
        release = await self._require_release_by_tag(repo_owner, repo_name, tag_name)
        url = f"{_repo(repo_owner, repo_name)}/releases/{release['id']}"
        data = (await self._request("PATCH", url, context=f"release {tag_name}", json=payload)).json()
        return _pick(data, *_RELEASE_FIELDS)

    @_destructive
    async def delete_release(
        self,
        repo_owner: str,
        repo_name: str,
        tag_name: str,
        delete_tag: Annotated[
            bool, "Also remove the tag the release was published from, without the delete_tag force check"
        ] = False,
    ) -> dict[str, Any]:
        """Deletes a release. The tag it was published from survives unless
        delete_tag asks for it, since the commit history usually should not move.

        delete_tag=True removes the ref that the delete_tag tool refuses to touch
        while a release names it. No force flag is asked for here because the
        release goes first, so the dangling release that guard protects against
        cannot be what is left behind. See #404."""
        release = await self._require_release_by_tag(repo_owner, repo_name, tag_name)
        base = _repo(repo_owner, repo_name)
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
        per_page: Annotated[int, "Number of results per page (1-100)"] = 50,
        page: int = 1,
    ) -> dict[str, Any]:
        """Lists a repository's tags and the commit each points at."""
        url = f"{_repo(repo_owner, repo_name)}/tags"
        response = await self._request(
            "GET", url, context=f"tags for {repo_owner}/{repo_name}", params={"per_page": per_page, "page": page}
        )
        tags = [{"name": tag["name"], "sha": (tag.get("commit") or {}).get("sha")} for tag in response.json()]
        return {**_page(response, tags), "tags": tags}

    async def _delete_tag_ref(self, repo_owner: str, repo_name: str, tag_name: str) -> None:
        """Removes the tag ref itself."""
        url = f"{_repo(repo_owner, repo_name)}/git/refs/tags/{tag_name}"
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

    async def _project(self, owner: str, number: int) -> dict[str, Any]:
        """Resolve a board from its owner's login and its number, fields included."""
        async with self._guard("look up project"):
            result = await self._execute_graphql(PROJECT_QUERY, {"owner": owner, "number": number})
        return _require_project(result, owner, number)

    async def _issue_node(self, repo_owner: str, repo_name: str, issue_number: int) -> dict[str, Any]:
        """The node id of an issue or pull request, and the boards already
        holding it, since a node id is what the project mutations take."""
        async with self._guard("look up issue"):
            result = await self._execute_graphql(
                ISSUE_PROJECT_ITEMS_QUERY,
                {"owner": repo_owner, "repo": repo_name, "number": issue_number},
            )
        node = (result.get("repository") or {}).get("issueOrPullRequest") or {}
        if not node.get("id"):
            raise GitHubNotFoundError(f"No issue or pull request #{issue_number} in {repo_owner}/{repo_name}")
        return node

    async def _add_project_item(self, project_id: str, content_id: str) -> str:
        """Put a node on a board and return its item id."""
        async with self._guard("add to project"):
            result = await self._execute_graphql(
                ADD_PROJECT_ITEM_MUTATION, {"projectId": project_id, "contentId": content_id}
            )
        item_id = ((result.get("addProjectV2ItemById") or {}).get("item") or {}).get("id")
        if not item_id:
            raise GitHubAPIError("Adding the item to the project returned no item id")
        return item_id

    @_read_only
    async def list_projects(
        self,
        project_owner: Annotated[str, "User or organisation whose boards to list"],
        per_page: Annotated[int, "Number of boards per page (1-100)"] = 50,
        after: Annotated[str | None, "next_cursor from a previous call, to read the following page"] = None,
    ) -> dict[str, Any]:
        """Lists the Projects (v2) boards a user or organisation owns, with the
        number each other board tool takes, so a board can be found without
        reading its URL. The account type is resolved by GitHub, so one login
        serves for a person and an organisation alike. Pages by cursor: pass
        next_cursor back as after until has_more comes back false. See #465."""
        async with self._guard("list projects"):
            result = await self._execute_graphql(
                PROJECTS_QUERY, {"owner": project_owner, "first": per_page, "after": after}
            )
        owner = result.get("repositoryOwner")
        if owner is None:
            raise GitHubNotFoundError(f"No user or organisation named '{project_owner}'")
        connection = owner.get("projectsV2") or {}
        page = connection.get("pageInfo") or {}
        projects = [_project_summary(node) for node in connection.get("nodes") or [] if node]
        return {
            "project_owner": project_owner,
            "total": connection.get("totalCount"),
            "count": len(projects),
            "has_more": bool(page.get("hasNextPage")),
            "projects": projects,
            "next_cursor": page.get("endCursor") if page.get("hasNextPage") else None,
        }

    @_read_only
    async def get_project_fields(
        self,
        project_owner: Annotated[str, "User or organisation that owns the board"],
        project_number: Annotated[int, "Project number, as it appears in the board's URL"],
    ) -> dict[str, Any]:
        """Lists a project's fields and the options each single-select one accepts,
        which is what github_set_project_field expects to be named."""
        project = await self._project(project_owner, project_number)
        nodes = (project.get("fields") or {}).get("nodes") or []
        return {
            "project_number": project_number,
            "title": project.get("title"),
            "url": project.get("url"),
            "fields": [_project_field_summary(node) for node in nodes if node.get("name")],
        }

    @_read_only
    async def list_project_items(
        self,
        project_owner: str,
        project_number: int,
        per_page: Annotated[int, "Number of items per page (1-100)"] = 50,
        after: Annotated[str | None, "next_cursor from a previous call, to read the following page"] = None,
    ) -> dict[str, Any]:
        """Lists what is on a project board with each card's field values, so a
        backlog can be read by Status rather than one issue at a time. A board is
        a GraphQL connection, which pages by cursor rather than page number, so
        pass next_cursor back as after until has_more comes back false."""
        async with self._guard("list project items"):
            result = await self._execute_graphql(
                PROJECT_ITEMS_QUERY,
                {"owner": project_owner, "number": project_number, "first": per_page, "after": after},
            )
        project = _require_project(result, project_owner, project_number)
        items = project.get("items") or {}
        page = items.get("pageInfo") or {}
        cards = [_project_item_summary(node) for node in items.get("nodes") or []]
        return {
            "project_number": project_number,
            "title": project.get("title"),
            "total": items.get("totalCount"),
            "count": len(cards),
            "has_more": bool(page.get("hasNextPage")),
            "items": cards,
            "next_cursor": page.get("endCursor") if page.get("hasNextPage") else None,
        }

    @_write(idempotent=True, scopes=PROJECT_SCOPES)
    async def add_to_project(
        self,
        project_owner: str,
        project_number: int,
        repo_owner: str,
        repo_name: str,
        issue_number: Annotated[int, "Issue or pull request to put on the board"],
    ) -> dict[str, Any]:
        """Puts an issue or pull request on a project board. One the board already
        holds comes back with the card it already has, so a retry adds nothing and
        overwrites nothing."""
        project = await self._project(project_owner, project_number)
        node = await self._issue_node(repo_owner, repo_name, issue_number)
        item_id = await self._add_project_item(project["id"], node["id"])
        return {
            "item_id": item_id,
            "project_number": project_number,
            "project_title": project.get("title"),
            "issue_number": issue_number,
            "url": node.get("url"),
        }

    @_write(idempotent=True, scopes=PROJECT_SCOPES)
    async def set_project_field(
        self,
        project_owner: str,
        project_number: int,
        repo_owner: str,
        repo_name: str,
        issue_number: int,
        field: Annotated[str, "Single-select field to set, such as Status"],
        option: Annotated[str, "Option to set it to, such as In Progress"],
    ) -> dict[str, Any]:
        """Sets a single-select field on an issue's card, naming the field and the
        option rather than their node ids. An issue not yet on the board is added
        first, since a field value has nowhere to live otherwise."""
        project = await self._project(project_owner, project_number)
        field_id, option_id = _project_field_ids(project, field, option)
        node = await self._issue_node(repo_owner, repo_name, issue_number)
        item_id = _item_on_project(node, project["id"]) or await self._add_project_item(project["id"], node["id"])
        async with self._guard("set project field"):
            await self._execute_graphql(
                SET_PROJECT_FIELD_MUTATION,
                {"projectId": project["id"], "itemId": item_id, "fieldId": field_id, "optionId": option_id},
            )
        return {
            "item_id": item_id,
            "project_number": project_number,
            "issue_number": issue_number,
            "field": field,
            "option": option,
        }

    @_destructive(scopes=PROJECT_SCOPES)
    async def remove_from_project(
        self,
        project_owner: str,
        project_number: int,
        repo_owner: str,
        repo_name: str,
        issue_number: int,
    ) -> dict[str, Any]:
        """Takes an issue or pull request off a project board. The issue itself is
        untouched and stays open, but the field values its card held go with it."""
        project = await self._project(project_owner, project_number)
        node = await self._issue_node(repo_owner, repo_name, issue_number)
        item_id = _item_on_project(node, project["id"])
        if item_id is None:
            raise GitHubNotFoundError(
                f"#{issue_number} in {repo_owner}/{repo_name} is not on project #{project_number}"
            )
        async with self._guard("remove from project"):
            result = await self._execute_graphql(
                DELETE_PROJECT_ITEM_MUTATION, {"projectId": project["id"], "itemId": item_id}
            )
        return {
            "status": "removed",
            "item_id": (result.get("deleteProjectV2Item") or {}).get("deletedItemId") or item_id,
            "project_number": project_number,
            "issue_number": issue_number,
        }

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
        if not response.is_success:
            self._handle_response_error(response, "GraphQL query")
        data = response.json()
        if "errors" in data:
            handle_graphql_errors(data["errors"], reset_timestamp=_reset_timestamp(response))
        return data.get("data", {})

    @asynccontextmanager
    async def _guard(self, action: str):
        """Let GitHubNotFoundError, GitHubAuthError and GitHubRateLimitError pass
        through, and wrap any other failure in a GitHubAPIError labelled with the
        action that failed. An auth error or a rate limit already says what to do,
        and relabelling it buries that. See #519."""
        try:
            yield
        except GitHubNotFoundError, GitHubAuthError, GitHubRateLimitError:
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
                runs.append(_run_dict(run, app_name))
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
    ) -> StatusChecksResult:
        """Return the CI check runs and commit status for a pull request's HEAD commit.

        Pages through up to MAX_STATUS_CHECKS_SUITE_PAGES of check suites
        (50 per page). For any suite whose first 100 runs are not the full
        set, drains up to MAX_STATUS_CHECKS_RUN_PAGES_PER_SUITE additional
        pages via the supplemental query. If either cap is hit before the
        connection is exhausted, the result is flagged truncated=True and
        overall is downgraded from 'passing' to 'unknown' so the caller
        does not act on a partial view. head_sha names the commit read, and is
        None when GitHub resolves no commit for the pull request. See #515.

        """
        logger.info(f"Fetching status checks for PR #{pr_number} in {repo_owner}/{repo_name}")
        async with self._guard("fetch status checks"):
            check_runs: list[dict[str, Any]] = []
            commit_statuses: list[dict[str, Any]] = []
            head_sha: str | None = None
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
                head_target = _head_commit(repo_data["pullRequest"])

                if suites_after is None:
                    commit_statuses = _commit_statuses(head_target)
                    head_sha = head_target.get("oid")

                suites_page = head_target.get("checkSuites") or {}
                suites_nodes = suites_page.get("nodes") or []
                check_runs.extend(_check_runs(head_target))

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

            overall = _overall(check_runs, commit_statuses, truncated)
            logger.info(
                f"Status checks for PR #{pr_number}: overall={overall}, runs={len(check_runs)}, truncated={truncated}"
            )
            return {
                "pr_number": pr_number,
                "head_sha": head_sha,
                "overall": overall,
                "check_runs": check_runs,
                "commit_statuses": commit_statuses,
                "truncated": truncated,
            }

    @_read_only
    async def get_job_log(
        self,
        repo_owner: str,
        repo_name: str,
        job_id: Annotated[int, "The id of a GitHub Actions check run from github_get_pr_status_checks"],
        max_bytes: Annotated[
            int, "Cap on the log returned, kept from the end. Pass 0 to learn the size without reading the log"
        ] = DIFF_MAX_BYTES,
    ) -> JobLogResult:
        """Fetches the end of a GitHub Actions job's log, capped at max_bytes. A job
        reports its failure last, so a cut keeps the tail. GitHub redirects to a
        storage host for one minute, and the download goes there without the
        token. See #500."""
        if max_bytes < 0:
            raise GitHubValidationError("max_bytes cannot be negative.")
        url = f"{_repo(repo_owner, repo_name)}/actions/jobs/{job_id}/logs"
        redirect = await self._request("GET", url, context=f"job {job_id} log", allow_status=(302,))
        try:
            response = await self._http.get(redirect.headers["Location"])
        except httpx.HTTPError as e:
            raise GitHubAPIError(f"Job {job_id} log download failed: {e}") from e
        if not response.is_success:
            raise GitHubAPIError(f"Job {job_id} log download failed", status_code=response.status_code)
        content = response.content
        kept = content[max(len(content) - max_bytes, 0) :]
        return {
            "job_id": job_id,
            "log": kept.decode("utf-8", errors="ignore"),
            "bytes_returned": len(kept),
            "bytes_total": len(content),
            "truncated": len(kept) < len(content),
        }
