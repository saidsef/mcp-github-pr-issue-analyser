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

"""User activity tools: contribution history and repository star growth."""

from __future__ import annotations

import logging
import math
from collections.abc import Iterator
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, NamedTuple, TypedDict

import httpx
from fastmcp import Context

from .exceptions import GitHubNotFoundError
from .graphql_queries import USER_CONTRIBUTIONS_QUERY
from .tool_annotations import _read_only

logger = logging.getLogger(__name__)


class UserActivityResult(TypedDict):
    username: str
    date_range: dict[str, str] | None
    total_contributions: dict[str, int]
    commits: list[dict[str, Any]]
    pull_requests: list[dict[str, Any]]
    issues: list[dict[str, Any]]
    reviews: list[dict[str, Any]]
    repo_stars: list[dict[str, Any]]


class RepoStarsSinceResult(TypedDict):
    username: str
    since: str
    repos: list[dict[str, Any]]
    truncated: bool


def _map_commit(contrib: dict[str, Any]) -> dict[str, Any]:
    return {
        "commit_count": contrib.get("commitCount", 0),
        "url": contrib.get("url", ""),
        "date": contrib.get("occurredAt", ""),
    }


def _map_pull_request(contrib: dict[str, Any]) -> dict[str, Any]:
    pr = contrib["pullRequest"]
    return {
        "number": pr["number"],
        "title": pr["title"],
        "state": pr["state"],
        "url": pr["url"],
        "created": pr["createdAt"],
        "merged": pr.get("merged", False),
    }


def _map_issue(contrib: dict[str, Any]) -> dict[str, Any]:
    issue = contrib["issue"]
    return {
        "number": issue["number"],
        "title": issue["title"],
        "state": issue["state"],
        "url": issue["url"],
        "created": issue["createdAt"],
    }


def _map_review(contrib: dict[str, Any]) -> dict[str, Any]:
    review = contrib["pullRequestReview"]
    pr = contrib["pullRequest"]
    return {
        "pr_number": pr["number"],
        "pr_title": pr["title"],
        "pr_url": pr["url"],
        "review_state": review["state"],
        "review_url": review["url"],
        "date": contrib["occurredAt"],
    }


class _Section(NamedTuple):
    """One contribution section of get_user_activities."""

    field: str
    key: str
    message: str
    mapper: Any


# Drives the mapping and the ctx progress sequence, so neither hardcodes a
# count. A new section also needs a key on UserActivityResult. See #298.
ACTIVITY_SECTIONS: tuple[_Section, ...] = (
    _Section("commits", "commitContributionsByRepository", "Fetching commits...", _map_commit),
    _Section("pull_requests", "pullRequestContributionsByRepository", "Fetching pull requests...", _map_pull_request),
    _Section("issues", "issueContributionsByRepository", "Fetching issues...", _map_issue),
    _Section("reviews", "pullRequestReviewContributionsByRepository", "Fetching reviews...", _map_review),
)

# Sections plus the trailing repo-stars stage.
ACTIVITY_STAGES = len(ACTIVITY_SECTIONS) + 1
MAX_REPO_PAGES = 5  # 100 repos per page × 5 = 500 repo ceiling


def _normalise_since(value: str) -> str:
    """Expand a YYYY-MM-DD date to the start of that day, leaving ISO 8601 alone."""
    return value + "T00:00:00Z" if len(value) == 10 else value


def _normalise_until(value: str) -> str:
    """Expand a YYYY-MM-DD date to the end of that day, leaving ISO 8601 alone."""
    return value + "T23:59:59Z" if len(value) == 10 else value


class ActivityMixin:
    """User activity tools, mixed into GitHubIntegration."""

    if TYPE_CHECKING:  # supplied by GitHubIntegration
        _http: httpx.AsyncClient

        async def _execute_graphql(
            self, query: str, variables: dict[str, Any], *, token: str | None = ...
        ) -> dict[str, Any]: ...

        def _guard(self, action: str) -> AbstractAsyncContextManager[None]: ...

        def _get_headers(self) -> dict[str, str]: ...

        def _raise_for_status(self, response: httpx.Response, context: str = ...) -> None: ...

    def _filtered_contributions(
        self, collection: dict[str, Any], key: str, org: str, repo: str
    ) -> Iterator[tuple[dict[str, Any], str, str]]:
        """Yield (contribution_node, owner, repo_name) for every contribution under the
        given collection key, applying the optional org/repo filters."""
        for repo_contrib in collection.get(key, []):
            repo_info = repo_contrib["repository"]
            owner = repo_info["owner"]["login"]
            repo_name = repo_info["name"]
            if org and owner.lower() != org.lower():
                continue
            if repo and repo_name.lower() != repo.lower():
                continue
            for contrib in repo_contrib.get("contributions", {}).get("nodes", []):
                yield contrib, owner, repo_name

    def _capped_contributions(
        self, collection: dict[str, Any], key: str, org: str, repo: str, max_results: int, mapper: Any
    ) -> list[dict[str, Any]]:
        """Map up to max_results contributions under the given collection key."""
        out: list[dict[str, Any]] = []
        for contrib, owner, repo_name in self._filtered_contributions(collection, key, org, repo):
            if len(out) >= max_results:
                break
            out.append({"repo": repo_name, "owner": owner, **mapper(contrib)})
        return out

    @staticmethod
    def _activity_variables(username: str, since: str, until: str) -> dict[str, Any]:
        """GraphQL variables, expanding date-only bounds to whole days."""
        variables: dict[str, Any] = {"username": username}
        if since:
            variables["since"] = _normalise_since(since)
        if until:
            variables["until"] = _normalise_until(until)
        return variables

    @staticmethod
    def _date_range(
        since: str, until: str, variables: dict[str, Any], collection: dict[str, Any]
    ) -> dict[str, str] | None:
        """The requested window, falling back to the bounds the collection reports."""
        if not (since or until):
            return None
        return {
            "since": variables.get("since", collection.get("startedAt", "")),
            "until": variables.get("until", collection.get("endedAt", "")),
        }

    @staticmethod
    def _activity_totals(collection: dict[str, Any], repo_nodes: list[dict[str, Any]]) -> dict[str, int]:
        """Account-wide totals for the period, before org, repo or cap filtering."""
        return {
            "commits": collection.get("totalCommitContributions", 0),
            "pull_requests": collection.get("totalPullRequestContributions", 0),
            "issues": collection.get("totalIssueContributions", 0),
            "reviews": collection.get("totalPullRequestReviewContributions", 0),
            "repo_stars": sum(n.get("stargazerCount", 0) for n in repo_nodes),
        }

    @staticmethod
    def _map_repo_stars(repo_nodes: list[dict[str, Any]], max_results: int) -> list[dict[str, Any]]:
        """The user's top public repos by current cumulative star count."""
        return [
            {
                "repo": node["name"],
                "owner": node["owner"]["login"],
                "url": node["url"],
                "description": node.get("description"),
                "star_count": node["stargazerCount"],
            }
            for node in repo_nodes[:max_results]
        ]

    async def _collect_sections(
        self, collection: dict[str, Any], org: str, repo: str, max_results: int, ctx: Context | None
    ) -> dict[str, list[dict[str, Any]]]:
        """Map every contribution section, reporting one progress tick each."""
        sections: dict[str, list[dict[str, Any]]] = {}
        for i, section in enumerate(ACTIVITY_SECTIONS):
            if ctx:
                await ctx.report_progress(progress=i, total=ACTIVITY_STAGES)
                await ctx.info(section.message)
            sections[section.field] = self._capped_contributions(
                collection, section.key, org, repo, max_results, section.mapper
            )
        return sections

    @_read_only(task=True)
    async def get_user_activities(
        self,
        username: str,
        org: str = "",
        repo: str = "",
        since: str = "",
        until: str = "",
        max_results: int = 50,
        ctx: Context | None = None,
    ) -> UserActivityResult:
        """Get user activities with optional filtering by org, repo, and date range using GraphQL API. since/until accept YYYY-MM-DD or full ISO 8601 (YYYY-MM-DDTHH:MM:SSZ). Note: repo_stars returns current cumulative star counts, not stars gained within the requested period — GitHub does not expose per-period star deltas."""
        logger.info(f"Fetching user activities for {username} (org={org}, repo={repo}, since={since}, until={until})")
        async with self._guard("fetch user activities"):
            variables = self._activity_variables(username, since, until)
            if ctx:
                await ctx.info(f"Querying GitHub contributions for {username}...")
            result = await self._execute_graphql(USER_CONTRIBUTIONS_QUERY, variables)
            user_data = result.get("user")
            if not user_data:
                raise GitHubNotFoundError(f"User '{username}' not found")
            collection = user_data.get("contributionsCollection", {})
            date_range = self._date_range(since, until, variables, collection)
            sections = await self._collect_sections(collection, org, repo, max_results, ctx)
            if ctx:
                await ctx.report_progress(progress=len(ACTIVITY_SECTIONS), total=ACTIVITY_STAGES)
                await ctx.info("Fetching repo stars...")
            repo_nodes = user_data.get("repositories", {}).get("nodes", [])
            repo_stars = self._map_repo_stars(repo_nodes, max_results)
            if ctx:
                await ctx.report_progress(progress=ACTIVITY_STAGES, total=ACTIVITY_STAGES)
            activity_result: UserActivityResult = {
                "username": username,
                "date_range": date_range,
                "total_contributions": self._activity_totals(collection, repo_nodes),
                "commits": sections["commits"],
                "pull_requests": sections["pull_requests"],
                "issues": sections["issues"],
                "reviews": sections["reviews"],
                "repo_stars": repo_stars,
            }
            logger.info(
                f"Successfully fetched activities: {len(sections['commits'])} commits, "
                f"{len(sections['pull_requests'])} PRs, {len(sections['issues'])} issues, "
                f"{len(sections['reviews'])} reviews, {len(repo_stars)} starred repos"
            )
            return activity_result

    async def _count_new_stars(self, owner: str, repo_name: str, total_stars: int, cutoff: str) -> int:
        """Count stars added since cutoff by walking the stargazer pages backwards,
        stopping at the first page that contains a star older than the cutoff."""
        new_stars = 0
        for page in range(max(1, math.ceil(total_stars / 100)), 0, -1):
            resp = await self._http.request(
                "GET",
                f"https://api.github.com/repos/{owner}/{repo_name}/stargazers",
                headers={**self._get_headers(), "Accept": "application/vnd.github.star+json"},
                params={"per_page": 100, "page": page},
            )
            self._raise_for_status(resp, f"stargazers {owner}/{repo_name} p{page}")
            stargazers = resp.json()
            if not stargazers:
                break
            all_newer = True
            for sg in reversed(stargazers):
                if sg["starred_at"] >= cutoff:
                    new_stars += 1
                else:
                    all_newer = False
                    break
            if not all_newer:
                break
        return new_stars

    async def _star_candidates(self, username: str, max_repos: int) -> tuple[list[dict[str, Any]], bool]:
        """The user's starred public repos, most-starred first, capped at max_repos.
        Also returns whether the repo listing itself ran out of pages, since a
        partial listing can hide the very repos this tool exists to find."""
        all_repos: list[dict[str, Any]] = []
        truncated = False
        for page in range(1, MAX_REPO_PAGES + 1):
            resp = await self._http.request(
                "GET",
                f"https://api.github.com/users/{username}/repos",
                headers=self._get_headers(),
                params={"per_page": 100, "type": "public", "sort": "updated", "page": page},
            )
            self._raise_for_status(resp, f"repos for {username}")
            batch = resp.json()
            if not isinstance(batch, list):
                raise GitHubNotFoundError(f"User '{username}' not found")
            all_repos.extend(batch)
            if len(batch) < 100:
                break
        else:
            # The last page came back full, so there are probably more.
            truncated = True
        # Most-starred first, since those are likeliest to have gained stars recently
        candidates = sorted(
            [r for r in all_repos if r.get("stargazers_count", 0) > 0],
            key=lambda r: r["stargazers_count"],
            reverse=True,
        )[:max_repos]
        return candidates, truncated

    @_read_only(task=True)
    async def get_repo_stars_since(
        self,
        username: str,
        since: str = "",
        top_n: int = 5,
        max_repos: int = 20,
        ctx: Context | None = None,
    ) -> RepoStarsSinceResult:
        """Return the repos owned by username that received the most new stars since a given date. since accepts YYYY-MM-DD or ISO 8601; defaults to 30 days ago. Answers prompts like 'which repos gained the most stars in the last 30 days'. One REST call is made per repo checked — set max_repos conservatively. truncated is True when the account has more public repos than the listing could read, so the answer may miss some."""
        if since:
            cutoff = _normalise_since(since)
        else:
            cutoff = (datetime.now(UTC) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        logger.info(f"Fetching repo stars since {cutoff} for {username} (top_n={top_n}, max_repos={max_repos})")
        async with self._guard("fetch repo stars"):
            if ctx:
                await ctx.info(f"Fetching public repos for {username}...")
            candidates, truncated = await self._star_candidates(username, max_repos)
            if ctx:
                await ctx.report_progress(progress=0, total=len(candidates))
            results: list[dict[str, Any]] = []
            for i, repo in enumerate(candidates):
                new_stars = await self._count_new_stars(username, repo["name"], repo["stargazers_count"], cutoff)
                if new_stars > 0:
                    results.append(
                        {
                            "repo": repo["name"],
                            "owner": username,
                            "url": repo["html_url"],
                            "description": repo.get("description"),
                            "new_stars": new_stars,
                            "total_stars": repo["stargazers_count"],
                        }
                    )
                if ctx:
                    await ctx.report_progress(progress=i + 1, total=len(candidates))
            results.sort(key=lambda r: r["new_stars"], reverse=True)
            logger.info(f"Found {len(results)} repos with new stars since {cutoff} for {username}")
            return {"username": username, "since": cutoff, "repos": results[:top_n], "truncated": truncated}
