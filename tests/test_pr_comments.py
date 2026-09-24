"""Tests for the comments and reviews on a pull request."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastmcp.exceptions import ToolError

from mcp_github.exceptions import GitHubValidationError
from mcp_github.github_integration import GitHubIntegration
from tests.support import NOISE_REACTIONS, NOISE_USER, mock_response, review_payload


class TestCommentTrimming:
    @pytest.mark.anyio
    async def test_add_pr_comments_returns_trimmed_comment(self, gi: GitHubIntegration):
        payload = {
            "id": 11,
            "node_id": "IC_abc",
            "url": "https://api.github.com/repos/o/r/issues/comments/11",
            "html_url": "https://github.com/o/r/pull/5#issuecomment-11",
            "body": "hello",
            "user": NOISE_USER,
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": "2026-07-01T00:00:00Z",
            "issue_url": "https://api.github.com/repos/o/r/issues/5",
            "author_association": "OWNER",
            "reactions": NOISE_REACTIONS,
        }
        gi._http.request = AsyncMock(return_value=mock_response(json_data=payload))
        result = await gi.add_pr_comments("o", "r", 5, "hello")
        assert result == {
            "id": 11,
            "body": "hello",
            "author": "octocat",
            "html_url": "https://github.com/o/r/pull/5#issuecomment-11",
            "created_at": "2026-07-01T00:00:00Z",
        }

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

    @pytest.mark.anyio
    async def test_update_reviews_returns_trimmed_review(self, gi: GitHubIntegration):
        payload = {
            "id": 80,
            "node_id": "PRR_abc",
            "user": NOISE_USER,
            "body": "LGTM",
            "state": "APPROVED",
            "html_url": "https://github.com/o/r/pull/5#pullrequestreview-80",
            "pull_request_url": "https://api.github.com/repos/o/r/pulls/5",
            "_links": {"html": {"href": "https://github.com/x"}},
            "submitted_at": "2026-07-01T00:00:00Z",
            "commit_id": "abc123",
            "author_association": "OWNER",
        }
        gi._http.request = AsyncMock(return_value=mock_response(json_data=payload))
        result = await gi.update_reviews("o", "r", 5, "APPROVE", "LGTM")
        assert result == {
            "id": 80,
            "state": "APPROVED",
            "body": "LGTM",
            "html_url": "https://github.com/o/r/pull/5#pullrequestreview-80",
            "submitted_at": "2026-07-01T00:00:00Z",
        }


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

    @pytest.mark.anyio
    async def test_the_read_side_reports_where_a_comment_sits(self, gi: GitHubIntegration):
        """list_pr_comments has to carry the fields the write side can now set,
        or a second review cannot tell what the first said about a range."""
        gi._http.request = AsyncMock(
            return_value=mock_response(
                json_data=[
                    {
                        "id": 1,
                        "body": "b",
                        "user": NOISE_USER,
                        "html_url": "https://github.com/o/r/pull/5#discussion_r1",
                        "created_at": "2026-07-01T00:00:00Z",
                        "path": "app.py",
                        "line": 9,
                        "side": "RIGHT",
                        "start_line": 4,
                        "start_side": "RIGHT",
                    }
                ]
            )
        )
        comment = (await gi.list_pr_comments("o", "r", 5, kind="inline"))["comments"][0]
        assert comment["side"] == "RIGHT"
        assert comment["start_line"] == 4
        assert comment["start_side"] == "RIGHT"


class TestPRComments:
    @pytest.mark.anyio
    async def test_conversation_comments_come_from_the_issues_path(self, gi: GitHubIntegration):
        payload = [
            {
                "id": 11,
                "body": "hello",
                "user": NOISE_USER,
                "html_url": "https://github.com/o/r/pull/5#issuecomment-11",
                "created_at": "2026-07-01T00:00:00Z",
            }
        ]
        gi._http.request = AsyncMock(return_value=mock_response(json_data=payload))
        result = await gi.list_pr_comments("o", "r", 5)
        assert "/issues/5/comments" in gi._http.request.call_args.args[1]
        assert result["kind"] == "conversation"
        assert result["comments"] == [
            {
                "id": 11,
                "body": "hello",
                "author": "octocat",
                "html_url": "https://github.com/o/r/pull/5#issuecomment-11",
                "created_at": "2026-07-01T00:00:00Z",
            }
        ]

    @pytest.mark.anyio
    async def test_inline_comments_carry_the_file_and_line(self, gi: GitHubIntegration):
        payload = [
            {
                "id": 22,
                "body": "fix this",
                "user": NOISE_USER,
                "html_url": "https://github.com/o/r/pull/5#discussion_r22",
                "created_at": "2026-07-01T00:00:00Z",
                "path": "app.py",
                "line": 3,
                "in_reply_to_id": None,
                "diff_hunk": "@@ -1,3 +1,3 @@",
            }
        ]
        gi._http.request = AsyncMock(return_value=mock_response(json_data=payload))
        result = await gi.list_pr_comments("o", "r", 5, kind="inline")
        assert "/pulls/5/comments" in gi._http.request.call_args.args[1]
        assert result["comments"][0]["path"] == "app.py"
        assert result["comments"][0]["line"] == 3
        assert "diff_hunk" not in result["comments"][0]

    @pytest.mark.anyio
    async def test_paging_goes_out_as_params(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=[]))
        await gi.list_pr_comments("o", "r", 5, per_page=10, page=2)
        assert gi._http.request.call_args.kwargs["params"] == {"per_page": 10, "page": 2}

    @pytest.mark.anyio
    async def test_editing_an_inline_comment_uses_the_pulls_id_space(self, gi: GitHubIntegration):
        payload = {
            "id": 22,
            "body": "corrected",
            "user": NOISE_USER,
            "html_url": "https://github.com/o/r/pull/5#discussion_r22",
            "created_at": "2026-07-01T00:00:00Z",
        }
        gi._http.request = AsyncMock(return_value=mock_response(json_data=payload))
        result = await gi.update_pr_comment("o", "r", 22, "corrected", kind="inline")
        call = gi._http.request.call_args
        assert call.args[0] == "PATCH"
        assert call.args[1].endswith("/pulls/comments/22")
        assert result["body"] == "corrected"

    @pytest.mark.anyio
    async def test_editing_a_conversation_comment_uses_the_issues_id_space(self, gi: GitHubIntegration):
        payload = {
            "id": 11,
            "body": "corrected",
            "user": NOISE_USER,
            "html_url": "https://github.com/o/r/pull/5#issuecomment-11",
            "created_at": "2026-07-01T00:00:00Z",
        }
        gi._http.request = AsyncMock(return_value=mock_response(json_data=payload))
        await gi.update_pr_comment("o", "r", 11, "corrected")
        assert gi._http.request.call_args.args[1].endswith("/issues/comments/11")

    @pytest.mark.anyio
    async def test_reply_posts_onto_the_existing_thread(self, gi: GitHubIntegration):
        payload = {
            "id": 23,
            "body": "agreed",
            "user": NOISE_USER,
            "html_url": "https://github.com/o/r/pull/5#discussion_r23",
            "created_at": "2026-07-01T00:00:00Z",
            "path": "app.py",
            "line": 3,
            "in_reply_to_id": 22,
        }
        gi._http.request = AsyncMock(return_value=mock_response(json_data=payload))
        result = await gi.reply_to_review_comment("o", "r", 5, 22, "agreed")
        call = gi._http.request.call_args
        assert call.args[0] == "POST"
        assert call.args[1].endswith("/pulls/5/comments/22/replies")
        assert result["in_reply_to_id"] == 22

    @pytest.mark.anyio
    async def test_the_id_a_listing_returns_is_the_id_an_edit_takes(self, gi: GitHubIntegration):
        listing = [
            {
                "id": 22,
                "body": "fix this",
                "user": NOISE_USER,
                "html_url": "https://github.com/o/r/pull/5#discussion_r22",
                "created_at": "2026-07-01T00:00:00Z",
                "path": "app.py",
                "line": 3,
            }
        ]
        gi._http.request = AsyncMock(return_value=mock_response(json_data=listing))
        listed = await gi.list_pr_comments("o", "r", 5, kind="inline")
        comment_id = listed["comments"][0]["id"]
        gi._http.request = AsyncMock(return_value=mock_response(json_data=listing[0]))
        await gi.update_pr_comment("o", "r", comment_id, "corrected", kind="inline")
        assert gi._http.request.call_args.args[1].endswith("/pulls/comments/22")


class TestListPRReviews:
    @pytest.mark.anyio
    async def test_an_approval_carries_its_author_and_verdict(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=[review_payload()]))
        result = await gi.list_pr_reviews("o", "r", 5)
        assert result["reviews"] == [
            {
                "id": 80,
                "author": "octocat",
                "state": "APPROVED",
                "body": "LGTM",
                "html_url": "https://github.com/o/r/pull/5#pullrequestreview-80",
                "submitted_at": "2026-07-01T00:00:00Z",
                "commit_id": "abc123",
            }
        ]

    @pytest.mark.anyio
    @pytest.mark.parametrize("state", ["APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED"])
    async def test_every_verdict_comes_back_as_given(self, gi: GitHubIntegration, state: str):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=[review_payload(state=state)]))
        assert (await gi.list_pr_reviews("o", "r", 5))["reviews"][0]["state"] == state

    @pytest.mark.anyio
    async def test_an_unreviewed_pr_returns_nothing_rather_than_failing(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=[]))
        result = await gi.list_pr_reviews("o", "r", 5)
        assert result["reviews"] == []
        assert result["count"] == 0

    @pytest.mark.anyio
    async def test_a_pending_review_is_told_apart_by_a_missing_timestamp(self, gi: GitHubIntegration):
        """GitHub omits submitted_at on a review that was written but not sent."""
        payload = review_payload(state="PENDING")
        del payload["submitted_at"]
        gi._http.request = AsyncMock(return_value=mock_response(json_data=[payload]))
        assert (await gi.list_pr_reviews("o", "r", 5))["reviews"][0]["submitted_at"] is None

    @pytest.mark.anyio
    async def test_the_noise_is_trimmed_away(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=[review_payload()]))
        review = (await gi.list_pr_reviews("o", "r", 5))["reviews"][0]
        for noise in ("node_id", "_links", "pull_request_url", "author_association", "user"):
            assert noise not in review

    @pytest.mark.anyio
    async def test_paging_goes_out_as_params(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=[]))
        await gi.list_pr_reviews("o", "r", 5, per_page=100, page=2)
        call = gi._http.request.call_args
        assert call.args[1].endswith("/pulls/5/reviews")
        assert call.kwargs["params"] == {"per_page": 100, "page": 2}
