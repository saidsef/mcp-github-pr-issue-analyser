"""Tests for counting the stars a user's repositories gained in a window."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from mcp_github.activity import MAX_HISTORY_PAGES, MAX_REPO_PAGES
from mcp_github.github_integration import GitHubIntegration
from tests.support import OLD_WEEK, mock_response, week, weeks_back


class TestGetRepoStarsSince:
    @pytest.mark.anyio
    async def test_short_repo_listing_is_not_truncated(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=[]))
        result = await gi.get_repo_stars_since("u", since="2090-01-01")
        assert result["truncated"] is False
        assert gi._http.request.call_count == 1

    @pytest.mark.anyio
    async def test_repo_listing_pages_past_the_first_hundred(self, gi: GitHubIntegration):
        page1 = [{"name": f"r{i}", "stargazers_count": 1, "html_url": "u", "description": None} for i in range(100)]
        page2 = [{"name": "popular", "stargazers_count": 999, "html_url": "u", "description": None}]
        history = [week("2090-01-01", [1, 0, 0, 0, 0, 0, 0]), OLD_WEEK]
        responses = iter(
            [mock_response(json_data=page1), mock_response(json_data=page2)]
            + [mock_response(json_data=history) for _ in range(30)]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        result = await gi.get_repo_stars_since("u", since="2090-01-01", max_repos=1)
        assert result["truncated"] is False
        assert result["repos"][0]["repo"] == "popular"

    @pytest.mark.anyio
    async def test_running_out_of_pages_is_reported(self, gi: GitHubIntegration):
        full = [{"name": f"r{i}", "stargazers_count": 0, "html_url": "u", "description": None} for i in range(100)]
        gi._http.request = AsyncMock(return_value=mock_response(json_data=full))
        result = await gi.get_repo_stars_since("u", since="2090-01-01")
        assert result["truncated"] is True
        assert gi._http.request.call_count == MAX_REPO_PAGES

    @pytest.mark.anyio
    async def test_returns_repos_sorted_by_new_stars(self, gi: GitHubIntegration):
        repos_payload = [
            {"name": "repo-a", "stargazers_count": 10, "html_url": "https://github.com/u/repo-a", "description": None},
            {"name": "repo-b", "stargazers_count": 5, "html_url": "https://github.com/u/repo-b", "description": "B"},
        ]
        history_a = [week("2090-01-01", [1, 1, 0, 0, 0, 0, 0]), OLD_WEEK]
        history_b = [
            week("2090-01-01", [1, 0, 0, 0, 0, 0, 0]),
            week("2089-12-25", [1, 0, 0, 0, 0, 0, 0]),
            OLD_WEEK,
        ]

        responses = iter(
            [
                mock_response(json_data=repos_payload),
                mock_response(json_data=history_a),
                mock_response(json_data=history_b),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))

        result = await gi.get_repo_stars_since("u", since="2090-01-01")

        assert result["username"] == "u"
        assert result["since"] == "2090-01-01T00:00:00Z"
        assert len(result["repos"]) == 2
        assert result["repos"][0]["repo"] == "repo-a"
        assert result["repos"][0]["new_stars"] == 2
        assert result["repos"][1]["repo"] == "repo-b"
        assert result["repos"][1]["new_stars"] == 1

    @pytest.mark.anyio
    async def test_day_exactly_on_the_cutoff_counts(self, gi: GitHubIntegration):
        """The cutoff is inclusive, so the day it names is new."""
        repos_payload = [
            {"name": "edge", "stargazers_count": 2, "html_url": "https://github.com/u/edge", "description": None},
        ]
        history = [week("2090-01-01", [0, 0, 1, 0, 0, 0, 0]), OLD_WEEK]
        responses = iter([mock_response(json_data=repos_payload), mock_response(json_data=history)])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))

        result = await gi.get_repo_stars_since("u", since="2090-01-03")

        assert result["repos"][0]["new_stars"] == 1

    @pytest.mark.anyio
    async def test_week_straddling_the_cutoff_counts_only_later_days(self, gi: GitHubIntegration):
        """The week holding the cutoff contributes its days from the cutoff onwards,
        never its whole total. See #398."""
        repos_payload = [
            {"name": "straddle", "stargazers_count": 20, "html_url": "https://github.com/u/s", "description": None},
        ]
        history = [week("2090-01-01", [5, 5, 1, 2, 3, 0, 0]), OLD_WEEK]
        responses = iter([mock_response(json_data=repos_payload), mock_response(json_data=history)])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))

        result = await gi.get_repo_stars_since("u", since="2090-01-03")

        assert result["repos"][0]["new_stars"] == 6

    @pytest.mark.anyio
    async def test_stops_before_reading_older_history_pages(self, gi: GitHubIntegration):
        """The walk ends at the first week wholly older than the cutoff, so a repo
        with years of history still costs one request for a recent window."""
        repos_payload = [
            {"name": "big", "stargazers_count": 250, "html_url": "https://github.com/u/big", "description": None},
        ]
        page1 = [week("2090-01-01", [3] * 7), week("2089-12-25", [4] * 7)]
        requested: list[int] = []

        async def fake_request(method, url, **kw):
            if url.endswith("/repos"):
                return mock_response(json_data=repos_payload)
            requested.append(kw["params"]["page"])
            return mock_response(json_data=page1 if kw["params"]["page"] == 1 else [])

        gi._http.request = AsyncMock(side_effect=fake_request)

        result = await gi.get_repo_stars_since("u", since="2090-01-01")

        assert requested == [1]
        assert result["repos"][0]["new_stars"] == 21

    @pytest.mark.anyio
    async def test_counts_across_more_than_one_history_page(self, gi: GitHubIntegration):
        """A window longer than the 30 weeks one page holds carries on into the next
        page, so the count covers the whole window rather than the first page."""
        repos_payload = [
            {"name": "long", "stargazers_count": 400, "html_url": "https://github.com/u/long", "description": None},
        ]
        pages = {
            1: weeks_back("2090-01-01", 30, 1),
            2: weeks_back("2089-06-05", 30, 1),
        }
        requested: list[int] = []

        async def fake_request(method, url, **kw):
            if url.endswith("/repos"):
                return mock_response(json_data=repos_payload)
            requested.append(kw["params"]["page"])
            return mock_response(json_data=pages.get(kw["params"]["page"], []))

        gi._http.request = AsyncMock(side_effect=fake_request)

        result = await gi.get_repo_stars_since("u", since="2089-06-05")

        assert requested == [1, 2]
        assert result["repos"][0]["new_stars"] == 217
        assert result["truncated"] is False

    @pytest.mark.anyio
    async def test_history_page_cap_marks_the_result_truncated(self, gi: GitHubIntegration):
        """Every page comes back newer than the cutoff, so the walk runs out of pages
        before it finishes and has to report the count as short."""
        repos_payload = [
            {"name": "ancient", "stargazers_count": 9000, "html_url": "https://github.com/u/a", "description": None},
        ]
        page = weeks_back("2090-01-01", 30, 1)
        requested: list[int] = []

        async def fake_request(method, url, **kw):
            if url.endswith("/repos"):
                return mock_response(json_data=repos_payload)
            requested.append(kw["params"]["page"])
            return mock_response(json_data=page)

        gi._http.request = AsyncMock(side_effect=fake_request)

        result = await gi.get_repo_stars_since("u", since="2000-01-01")

        assert requested == list(range(1, MAX_HISTORY_PAGES + 1))
        assert result["truncated"] is True

    @pytest.mark.anyio
    async def test_reads_the_star_history_endpoint(self, gi: GitHubIntegration):
        """The plain stargazers endpoint answers 404 on any repo the token neither
        admins nor collaborates on, so the URL is part of the contract. See #398."""
        repos_payload = [
            {"name": "r", "stargazers_count": 4, "html_url": "https://github.com/u/r", "description": None},
        ]
        history = [week("2090-01-01", [1, 0, 0, 0, 0, 0, 0]), OLD_WEEK]
        responses = iter([mock_response(json_data=repos_payload), mock_response(json_data=history)])
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))

        await gi.get_repo_stars_since("u", since="2090-01-01")

        assert gi._http.request.call_args.args[1] == "https://api.github.com/repos/u/r/stargazers/history"

    @pytest.mark.anyio
    async def test_excludes_repos_with_no_new_stars(self, gi: GitHubIntegration):
        repos_payload = [
            {
                "name": "old-repo",
                "stargazers_count": 3,
                "html_url": "https://github.com/u/old-repo",
                "description": None,
            },
        ]
        history_old = [OLD_WEEK]

        responses = iter(
            [
                mock_response(json_data=repos_payload),
                mock_response(json_data=history_old),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))

        result = await gi.get_repo_stars_since("u", since="2090-01-01")

        assert result["repos"] == []

    @pytest.mark.anyio
    async def test_top_n_caps_results(self, gi: GitHubIntegration):
        repos_payload = [
            {
                "name": f"repo-{i}",
                "stargazers_count": 1,
                "html_url": f"https://github.com/u/repo-{i}",
                "description": None,
            }
            for i in range(5)
        ]
        history_new = [week("2090-01-01", [1, 0, 0, 0, 0, 0, 0]), OLD_WEEK]
        calls = iter([mock_response(json_data=repos_payload)] + [mock_response(json_data=history_new)] * 5)
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(calls))

        result = await gi.get_repo_stars_since("u", since="2090-01-01", top_n=3)

        assert len(result["repos"]) == 3

    @pytest.mark.anyio
    async def test_default_since_is_30_days_ago(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=[]))
        result = await gi.get_repo_stars_since("u")
        assert result["since"].endswith("Z")
        assert len(result["since"]) == 20
