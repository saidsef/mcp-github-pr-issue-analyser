"""Tests for the paging half of every list reply."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from mcp_github.github_integration import GitHubIntegration
from tests.support import NOISE_USER, mock_response

_NEXT_LINK = '<https://api.github.com/repositories/1/tags?page=2>; rel="next", <...?page=83>; rel="last"'


class TestPaginationMetadata:
    @pytest.mark.anyio
    async def test_has_more_reads_the_link_header(self, gi: GitHubIntegration):
        """A full page is not the signal. A set dividing exactly by per_page would
        loop forever on that guess, so the header decides."""
        gi._http.request = AsyncMock(
            return_value=mock_response(json_data=[{"name": "v1", "commit": {"sha": "a"}}], headers={"Link": _NEXT_LINK})
        )
        result = await gi.list_tags("o", "r")
        assert result["has_more"] is True
        assert result["count"] == 1

    @pytest.mark.anyio
    async def test_the_last_page_carries_no_next_link(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=[{"name": "v1", "commit": {"sha": "a"}}]))
        assert (await gi.list_tags("o", "r"))["has_more"] is False

    @pytest.mark.anyio
    async def test_a_rel_last_without_a_rel_next_is_not_more(self, gi: GitHubIntegration):
        """The final page still names first and last, so matching the header alone
        would report another page that is not there."""
        link = '<https://api.github.com/x?page=1>; rel="first", <https://api.github.com/x?page=3>; rel="last"'
        gi._http.request = AsyncMock(return_value=mock_response(json_data=[], headers={"Link": link}))
        assert (await gi.list_tags("o", "r"))["has_more"] is False

    @pytest.mark.anyio
    async def test_count_is_the_page_not_the_result_set(self, gi: GitHubIntegration):
        """list_tags used to answer total with the page length, so a repository
        with 83 pages of tags reported 30."""
        gi._http.request = AsyncMock(
            return_value=mock_response(
                json_data=[{"name": f"v{n}", "commit": {"sha": "a"}} for n in range(50)],
                headers={"Link": _NEXT_LINK},
            )
        )
        result = await gi.list_tags("o", "r")
        assert result["count"] == 50
        assert "total" not in result
        assert result["has_more"] is True

    @pytest.mark.anyio
    async def test_a_search_keeps_the_real_total_beside_the_page(self, gi: GitHubIntegration):
        """GitHub gives a genuine match count here, so total stays and means it."""
        gi._http.request = AsyncMock(
            return_value=mock_response(
                json_data={
                    "total_count": 900,
                    "incomplete_results": False,
                    "items": [
                        {
                            "html_url": "https://github.com/o/r/issues/7",
                            "title": "Rate limits",
                            "number": 7,
                            "state": "open",
                            "created_at": "2026-07-01T00:00:00Z",
                            "updated_at": "2026-07-02T00:00:00Z",
                            "user": NOISE_USER,
                            "labels": [{"name": "bug"}],
                        }
                    ],
                },
                headers={"Link": _NEXT_LINK},
            )
        )
        result = await gi.search_issues_prs("rate limit")
        assert result["total"] == 900
        assert result["count"] == 1
        assert result["has_more"] is True

    @pytest.mark.anyio
    async def test_a_replayed_page_still_knows_it_has_a_successor(self, gi: GitHubIntegration):
        """A 304 rebuilds the response from cache. Dropping Link there would tell
        the caller a cached first page was the last one."""
        first = mock_response(
            json_data=[{"name": "v1", "commit": {"sha": "a"}}], etag="W/abc", headers={"Link": _NEXT_LINK}
        )
        responses = iter([first, mock_response(status_code=304)])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        assert (await gi.list_tags("o", "r"))["has_more"] is True
        assert (await gi.list_tags("o", "r"))["has_more"] is True

    @pytest.mark.anyio
    async def test_every_rest_list_tool_defaults_to_the_same_page_size(self):
        import inspect as _inspect

        names = [
            "list_pr_comments",
            "list_open_issues_prs",
            "search_issues_prs",
            "list_repo_labels",
            "list_repos",
            "list_milestones",
            "list_releases",
            "list_tags",
            "list_project_items",
        ]
        defaults = {
            name: _inspect.signature(getattr(GitHubIntegration, name)).parameters["per_page"].default for name in names
        }
        assert set(defaults.values()) == {50}, defaults
