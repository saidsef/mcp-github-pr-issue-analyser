"""Tests for the comments and reviews on a pull request."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastmcp.exceptions import ToolError

from mcp_github.exceptions import GitHubValidationError
from mcp_github.github_integration import GitHubIntegration
from tests.support import NOISE_REACTIONS, NOISE_USER, mock_response


class TestCommentTrimming:
    @pytest.mark.anyio
    async def test_add_inline_pr_comment_returns_trimmed_comment(self, gi: GitHubIntegration):
        comment_payload = {
            "id": 22,
            "node_id": "PRRC_abc",
            "pull_request_review_id": 33,
            "diff_hunk": "@@ -1,3 +1,3 @@",
            "path": "app.py",
            "body": "fix this",
            "user": NOISE_USER,
            "html_url": "https://github.com/o/r/pull/5#discussion_r22",
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": "2026-07-01T00:00:00Z",
            "_links": {"self": {"href": "https://api.github.com/x"}},
            "reactions": NOISE_REACTIONS,
        }
        responses = iter(
            [
                mock_response(json_data={"head": {"sha": "abc123"}}),
                mock_response(json_data=comment_payload),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        result = await gi.add_inline_pr_comment("o", "r", 5, "app.py", 3, "fix this")
        assert result == {
            "id": 22,
            "body": "fix this",
            "author": "octocat",
            "html_url": "https://github.com/o/r/pull/5#discussion_r22",
            "created_at": "2026-07-01T00:00:00Z",
        }
        post_kwargs = gi._http.request.call_args_list[1].kwargs
        assert post_kwargs["json"]["commit_id"] == "abc123"


def _inline_responses(**overrides):
    """A head-SHA read followed by the created review comment."""
    comment = {
        "id": 22,
        "path": "app.py",
        "body": "fix this",
        "user": NOISE_USER,
        "html_url": "https://github.com/o/r/pull/5#discussion_r22",
        "created_at": "2026-07-01T00:00:00Z",
        **overrides,
    }
    return iter([mock_response(json_data={"head": {"sha": "abc123"}}), mock_response(json_data=comment)])


class TestInlineCommentPlacement:
    @staticmethod
    def _posted(gi: GitHubIntegration) -> dict:
        return gi._http.request.call_args_list[1].kwargs["json"]

    @pytest.mark.anyio
    async def test_an_added_line_defaults_to_the_right_side(self, gi: GitHubIntegration):
        responses = _inline_responses()
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.add_inline_pr_comment("o", "r", 5, "app.py", 3, "fix this")
        posted = self._posted(gi)
        assert posted["side"] == "RIGHT"
        assert posted["line"] == 3
        assert "start_line" not in posted

    @pytest.mark.anyio
    async def test_a_deleted_line_is_reachable_on_the_left_side(self, gi: GitHubIntegration):
        responses = _inline_responses()
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.add_inline_pr_comment("o", "r", 5, "app.py", 7, "why drop this?", side="LEFT")
        assert self._posted(gi)["side"] == "LEFT"

    @pytest.mark.anyio
    async def test_a_given_sha_is_posted_without_reading_the_head(self, gi: GitHubIntegration):
        *_, created = _inline_responses()
        gi._http.request = AsyncMock(return_value=created)
        await gi.add_inline_pr_comment("o", "r", 5, "app.py", 3, "fix this", expected_head_sha="9341d65")
        gi._http.request.assert_awaited_once()
        assert gi._http.request.call_args.kwargs["json"]["commit_id"] == "9341d65"

    @pytest.mark.anyio
    async def test_a_range_sends_start_line_and_matches_the_side(self, gi: GitHubIntegration):
        responses = _inline_responses()
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.add_inline_pr_comment("o", "r", 5, "app.py", 9, "this block", start_line=4)
        posted = self._posted(gi)
        assert posted["start_line"] == 4
        assert posted["line"] == 9
        assert posted["start_side"] == "RIGHT"

    @pytest.mark.anyio
    async def test_a_range_can_start_on_the_other_side(self, gi: GitHubIntegration):
        responses = _inline_responses()
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.add_inline_pr_comment("o", "r", 5, "app.py", 9, "spans", start_line=4, start_side="LEFT")
        assert self._posted(gi)["start_side"] == "LEFT"

    @pytest.mark.anyio
    async def test_a_backwards_range_is_refused_before_github_sees_it(self, gi: GitHubIntegration):
        responses = _inline_responses()
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        with pytest.raises(GitHubValidationError, match="must come before"):
            await gi.add_inline_pr_comment("o", "r", 5, "app.py", 4, "backwards", start_line=9)
        gi._http.request.assert_not_awaited()

    @pytest.mark.anyio
    async def test_a_line_outside_the_diff_names_the_path_and_line(self, gi: GitHubIntegration):
        """GitHub rejects a line outside every hunk. The message has to say which
        line, since the caller picked it from a diff it read separately."""
        responses = iter(
            [
                mock_response(json_data={"head": {"sha": "abc123"}}),
                mock_response(
                    status_code=422,
                    json_data={
                        "message": "Validation Failed",
                        "errors": [{"resource": "PullRequestReviewComment", "field": "line", "code": "invalid"}],
                    },
                    reason_phrase="Unprocessable Entity",
                ),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        with pytest.raises(ToolError, match=r"app\.py:999"):
            await gi.add_inline_pr_comment("o", "r", 5, "app.py", 999, "out of hunk")

    @pytest.mark.anyio
    async def test_a_failed_range_names_both_ends(self, gi: GitHubIntegration):
        responses = iter(
            [
                mock_response(json_data={"head": {"sha": "abc123"}}),
                mock_response(status_code=422, json_data={"message": "Validation Failed"}),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        with pytest.raises(ToolError, match=r"app\.py:4-9"):
            await gi.add_inline_pr_comment("o", "r", 5, "app.py", 9, "range", start_line=4)
