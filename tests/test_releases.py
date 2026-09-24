"""Tests for commits, tags and releases."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastmcp.exceptions import ToolError

from mcp_github.exceptions import GitHubNotFoundError, GitHubValidationError
from mcp_github.github_integration import GitHubIntegration
from tests.support import NOISE_USER, mock_response, release_payload


class TestGetLatestShaAndCreateTag:
    @pytest.mark.anyio
    async def test_get_latest_sha_asks_for_one_commit(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=[{"sha": "abc123"}]))
        assert await gi.get_latest_sha("owner", "repo") == "abc123"
        assert "per_page=1" in gi._http.request.call_args.args[1]

    @pytest.mark.anyio
    async def test_get_latest_sha_empty_repo_returns_none(self, gi: GitHubIntegration):
        """GitHub answers an empty repository with 409, not an empty list, so the
        documented no-commits contract only holds by reading that status. See #411."""
        gi._http.request = AsyncMock(
            return_value=mock_response(status_code=409, json_data={"message": "Git Repository is empty."})
        )
        result = await gi.get_latest_sha("owner", "empty-repo")
        assert result is None

    @pytest.mark.anyio
    async def test_get_latest_sha_reads_the_default_branch_when_no_ref_is_given(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=[{"sha": "abc123"}]))
        await gi.get_latest_sha("owner", "repo")
        assert "sha=" not in gi._http.request.call_args.args[1]

    @pytest.mark.anyio
    async def test_get_latest_sha_reads_a_named_branch(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=[{"sha": "branchsha"}]))
        assert await gi.get_latest_sha("owner", "repo", ref="feature/x") == "branchsha"
        assert "sha=feature%2Fx" in gi._http.request.call_args.args[1]

    @pytest.mark.anyio
    async def test_get_latest_sha_reads_a_tag(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=[{"sha": "tagsha"}]))
        assert await gi.get_latest_sha("owner", "repo", ref="v1.2.3") == "tagsha"
        assert "sha=v1.2.3" in gi._http.request.call_args.args[1]

    @pytest.mark.anyio
    async def test_get_latest_sha_rejects_an_unknown_ref(self, gi: GitHubIntegration):
        """A ref GitHub cannot resolve is a 404, which is a failure rather than
        the no-commits answer."""
        gi._http.request = AsyncMock(
            return_value=mock_response(status_code=404, json_data={"message": "No commit found for SHA: nope"})
        )
        with pytest.raises(ToolError, match="nope"):
            await gi.get_latest_sha("owner", "repo", ref="nope")

    @pytest.mark.anyio
    async def test_create_tag_refuses_an_empty_repository(self, gi: GitHubIntegration):
        """The guard was unreachable while an empty repository raised instead of
        answering None."""
        gi._http.request = AsyncMock(
            return_value=mock_response(status_code=409, json_data={"message": "Git Repository is empty."})
        )
        with pytest.raises(GitHubNotFoundError, match="No commits found"):
            await gi.create_tag("owner", "empty-repo", "v1")

    @pytest.mark.anyio
    async def test_create_tag_uses_the_sha_it_is_given(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data={"ref": "refs/tags/v1"}))
        await gi.create_tag("o", "r", "v1", sha="deadbee")
        assert gi._http.request.call_count == 1
        assert gi._http.request.call_args.kwargs["json"]["sha"] == "deadbee"

    @pytest.mark.anyio
    async def test_create_tag_without_a_message_is_a_plain_ref(self, gi: GitHubIntegration):
        responses = iter(
            [
                mock_response(json_data=[{"sha": "abc123"}]),
                mock_response(json_data={"ref": "refs/tags/v1"}),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.create_tag("o", "r", "v1")
        assert gi._http.request.call_args.args[1].endswith("/git/refs")
        assert gi._http.request.call_args.kwargs["json"] == {"ref": "refs/tags/v1", "sha": "abc123"}

    @pytest.mark.anyio
    async def test_create_tag_with_a_message_creates_an_annotated_tag(self, gi: GitHubIntegration):
        responses = iter(
            [
                mock_response(json_data={"sha": "tagobj1"}),
                mock_response(json_data={"ref": "refs/tags/v1"}),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.create_tag("o", "r", "v1", message="ship it", sha="deadbee")
        calls = gi._http.request.call_args_list
        assert calls[0].args[1].endswith("/git/tags")
        assert calls[0].kwargs["json"] == {
            "tag": "v1",
            "message": "ship it",
            "object": "deadbee",
            "type": "commit",
        }
        assert calls[1].kwargs["json"]["sha"] == "tagobj1"

    @pytest.mark.anyio
    async def test_create_tag_empty_repo_raises_github_not_found(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=[]))
        with pytest.raises(GitHubNotFoundError, match="No commits found"):
            await gi.create_tag("owner", "empty-repo", "v1.0.0", "First tag")
        gi._http.request.assert_awaited_once()


class TestReleasesAndTags:
    @pytest.mark.anyio
    async def test_create_release_returns_trimmed_release(self, gi: GitHubIntegration):
        payload = {
            "id": 55,
            "node_id": "RE_abc",
            "url": "https://api.github.com/repos/o/r/releases/55",
            "assets_url": "https://api.github.com/repos/o/r/releases/55/assets",
            "upload_url": "https://uploads.github.com/repos/o/r/releases/55/assets{?name,label}",
            "html_url": "https://github.com/o/r/releases/tag/v1.0.0",
            "author": NOISE_USER,
            "tag_name": "v1.0.0",
            "target_commitish": "main",
            "name": "v1.0.0",
            "draft": False,
            "prerelease": False,
            "created_at": "2026-07-01T00:00:00Z",
            "published_at": "2026-07-01T00:00:00Z",
            "assets": [],
            "tarball_url": "https://api.github.com/repos/o/r/tarball/v1.0.0",
            "zipball_url": "https://api.github.com/repos/o/r/zipball/v1.0.0",
            "body": "Generated notes",
        }
        gi._http.request = AsyncMock(return_value=mock_response(json_data=payload))
        result = await gi.create_release("o", "r", "v1.0.0", "v1.0.0", "notes")
        assert result == {
            "id": 55,
            "tag_name": "v1.0.0",
            "name": "v1.0.0",
            "html_url": "https://github.com/o/r/releases/tag/v1.0.0",
            "draft": False,
            "prerelease": False,
            "body": "Generated notes",
            "updated": False,
        }

    @pytest.mark.anyio
    async def test_get_release_by_tag(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=release_payload()))
        result = await gi.get_release("o", "r", "v1.0.0")
        assert gi._http.request.call_args.args[1].endswith("/releases/tags/v1.0.0")
        assert result == {
            "id": 55,
            "tag_name": "v1.0.0",
            "name": "v1.0.0",
            "html_url": "https://github.com/o/r/releases/tag/v1.0.0",
            "draft": False,
            "prerelease": False,
            "body": "notes",
        }

    @pytest.mark.anyio
    async def test_get_release_without_a_tag_asks_for_the_latest(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=release_payload()))
        await gi.get_release("o", "r")
        assert gi._http.request.call_args.args[1].endswith("/releases/latest")

    @pytest.mark.anyio
    async def test_missing_release_raises_not_found(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(status_code=404, json_data={}))
        with pytest.raises(GitHubNotFoundError, match="No release found for tag 'v9'"):
            await gi.get_release("o", "r", "v9")

    @pytest.mark.anyio
    async def test_list_releases_is_trimmed(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=[release_payload()]))
        result = await gi.list_releases("o", "r")
        assert result["count"] == 1
        assert set(result["releases"][0]) == {
            "id",
            "tag_name",
            "name",
            "html_url",
            "draft",
            "prerelease",
            "body",
        }

    @pytest.mark.anyio
    async def test_list_tags_keeps_name_and_sha(self, gi: GitHubIntegration):
        payload = [{"name": "v1.0.0", "zipball_url": "z", "commit": {"sha": "abc123", "url": "u"}}]
        gi._http.request = AsyncMock(return_value=mock_response(json_data=payload))
        result = await gi.list_tags("o", "r")
        assert result == {"count": 1, "has_more": False, "tags": [{"name": "v1.0.0", "sha": "abc123"}]}

    @pytest.mark.anyio
    async def test_update_release_sends_only_the_fields_supplied(self, gi: GitHubIntegration):
        responses = iter(
            [
                mock_response(json_data=release_payload()),
                mock_response(json_data=release_payload(body="corrected")),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.update_release("o", "r", "v1.0.0", body="corrected")
        patch_call = gi._http.request.call_args_list[1]
        assert patch_call.args[0] == "PATCH"
        assert patch_call.args[1].endswith("/releases/55")
        assert patch_call.kwargs["json"] == {"body": "corrected"}

    @pytest.mark.anyio
    async def test_update_release_can_clear_the_draft_flag(self, gi: GitHubIntegration):
        responses = iter(
            [
                mock_response(json_data=release_payload(draft=True)),
                mock_response(json_data=release_payload()),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.update_release("o", "r", "v1.0.0", draft=False)
        assert gi._http.request.call_args_list[1].kwargs["json"] == {"draft": False}

    @pytest.mark.anyio
    async def test_update_release_rejects_a_call_with_nothing_to_change(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock()
        with pytest.raises(GitHubValidationError):
            await gi.update_release("o", "r", "v1.0.0")
        gi._http.request.assert_not_called()

    @pytest.mark.anyio
    async def test_create_release_refuses_a_tag_that_already_has_one(self, gi: GitHubIntegration):
        """A retry after a timeout used to land here and replace the published
        notes without saying so. See #401."""
        gi._http.request = AsyncMock(
            return_value=mock_response(status_code=422, json_data={"errors": [{"code": "already_exists"}]})
        )
        with pytest.raises(GitHubValidationError, match="update_release"):
            await gi.create_release("o", "r", "v1.0.0", "v1.0.0", "second attempt")
        assert gi._http.request.call_count == 1

    @pytest.mark.anyio
    async def test_create_release_updates_when_asked_to(self, gi: GitHubIntegration):
        responses = iter(
            [
                mock_response(status_code=422, json_data={"errors": [{"code": "already_exists"}]}),
                mock_response(json_data=release_payload()),
                mock_response(json_data=release_payload(body="second attempt")),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        result = await gi.create_release("o", "r", "v1.0.0", "v1.0.0", "second attempt", if_exists="update")
        calls = gi._http.request.call_args_list
        assert calls[0].args[0] == "POST"
        assert calls[2].args[0] == "PATCH"
        assert calls[2].kwargs["json"]["body"] == "second attempt"
        assert result["body"] == "second attempt"
        assert result["updated"] is True

    @pytest.mark.anyio
    async def test_create_release_still_raises_on_other_validation_errors(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(
            return_value=mock_response(status_code=422, json_data={"errors": [{"code": "invalid"}]})
        )
        with pytest.raises(GitHubValidationError):
            await gi.create_release("o", "r", "bad tag", "name", "notes")
        assert gi._http.request.call_count == 1

    @pytest.mark.anyio
    async def test_delete_release_leaves_the_tag_alone(self, gi: GitHubIntegration):
        responses = iter(
            [
                mock_response(json_data=release_payload()),
                mock_response(status_code=204),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        result = await gi.delete_release("o", "r", "v1.0.0")
        assert gi._http.request.call_count == 2
        assert gi._http.request.call_args.args[1].endswith("/releases/55")
        assert result["tag_deleted"] is False

    @pytest.mark.anyio
    async def test_delete_release_removes_the_tag_after_the_release(self, gi: GitHubIntegration):
        """The whole reason this path needs no force flag. Reverse the order and
        a failed tag delete would strand a release naming a ref nobody can fetch."""
        responses = iter(
            [
                mock_response(json_data=release_payload()),
                mock_response(status_code=204),
                mock_response(status_code=204),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        result = await gi.delete_release("o", "r", "v1.0.0", delete_tag=True)
        calls = [(c.args[0], c.args[1]) for c in gi._http.request.call_args_list]
        assert calls[0][0] == "GET"
        assert calls[1] == ("DELETE", "https://api.github.com/repos/o/r/releases/55")
        assert calls[2][0] == "DELETE"
        assert calls[2][1].endswith("/git/refs/tags/v1.0.0")
        assert result["tag_deleted"] is True

    @pytest.mark.anyio
    async def test_a_failed_release_delete_leaves_the_tag_alone(self, gi: GitHubIntegration):
        responses = iter(
            [
                mock_response(json_data=release_payload()),
                mock_response(status_code=500, reason_phrase="Server Error"),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        with pytest.raises(ToolError):
            await gi.delete_release("o", "r", "v1.0.0", delete_tag=True)
        assert gi._http.request.call_count == 2

    @pytest.mark.anyio
    async def test_delete_tag_refuses_a_tag_a_release_points_at(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=release_payload()))
        with pytest.raises(GitHubValidationError, match="force=True"):
            await gi.delete_tag("o", "r", "v1.0.0")
        assert gi._http.request.call_count == 1

    @pytest.mark.anyio
    async def test_delete_tag_proceeds_when_forced(self, gi: GitHubIntegration):
        responses = iter(
            [
                mock_response(json_data=release_payload()),
                mock_response(status_code=204),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        result = await gi.delete_tag("o", "r", "v1.0.0", force=True)
        assert gi._http.request.call_args.args[0] == "DELETE"
        assert result["release_still_published"] is True

    @pytest.mark.anyio
    async def test_delete_tag_without_a_release_needs_no_force(self, gi: GitHubIntegration):
        responses = iter(
            [
                mock_response(status_code=404, json_data={}),
                mock_response(status_code=204),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        result = await gi.delete_tag("o", "r", "v0.1.0")
        assert result == {"status": "deleted", "tag_name": "v0.1.0", "release_still_published": False}
