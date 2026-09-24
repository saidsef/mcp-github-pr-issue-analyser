"""Tests for reading a repository's files, tree and listing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.exceptions import ToolError

from mcp_github.exceptions import GitHubNotFoundError, GitHubValidationError
from mcp_github.github_integration import GitHubIntegration
from tests.support import mock_response, repo_payload


def _raw(content: bytes) -> MagicMock:
    r = mock_response(text="")
    r.content = content
    r.headers = {"content-type": "application/vnd.github.raw; charset=utf-8"}
    return r


class TestGetRepositoryFile:
    @pytest.mark.anyio
    async def test_reads_a_text_file_whole(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_raw(b"print('hi')\n"))
        result = await gi.get_repository_file("o", "r", "app.py")
        assert result["content"] == "print('hi')\n"
        assert result["binary"] is False
        assert result["truncated"] is False
        assert result["next_offset"] is None

    @pytest.mark.anyio
    async def test_asks_for_the_raw_media_type(self, gi: GitHubIntegration):
        """The base64 body stops carrying content over 1 MB. Raw does not."""
        gi._http.request = AsyncMock(return_value=_raw(b"x"))
        await gi.get_repository_file("o", "r", "app.py")
        assert gi._http.request.call_args.kwargs["headers"]["Accept"] == "application/vnd.github.raw"

    @pytest.mark.anyio
    async def test_a_ref_is_sent_and_a_leading_slash_is_dropped(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_raw(b"x"))
        await gi.get_repository_file("o", "r", "/src/app.py", ref="feature/x")
        url = gi._http.request.call_args.args[1]
        assert url.endswith("/contents/src/app.py?ref=feature%2Fx")

    @pytest.mark.anyio
    async def test_a_window_says_where_to_carry_on(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_raw(b"abcdefghij"))
        result = await gi.get_repository_file("o", "r", "app.py", offset=2, limit=3)
        assert result["content"] == "cde"
        assert result["bytes_returned"] == 3
        assert result["bytes_total"] == 10
        assert result["truncated"] is True
        assert result["next_offset"] == 5

    @pytest.mark.anyio
    async def test_the_last_window_is_not_truncated(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_raw(b"abcdefghij"))
        result = await gi.get_repository_file("o", "r", "app.py", offset=5, limit=5)
        assert result["truncated"] is False
        assert result["next_offset"] is None

    @pytest.mark.anyio
    async def test_a_zero_limit_reports_the_size_without_the_content(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_raw(b"abcdefghij"))
        result = await gi.get_repository_file("o", "r", "app.py", limit=0)
        assert result["content"] == ""
        assert result["bytes_total"] == 10
        assert result["truncated"] is True

    @pytest.mark.anyio
    async def test_a_binary_file_is_named_rather_than_decoded(self, gi: GitHubIntegration):
        """Decoding a blob with a NUL in it returns mangled text under a name
        that promises the file."""
        gi._http.request = AsyncMock(return_value=_raw(b"\x89PNG\r\n\x1a\n\x00\x00"))
        result = await gi.get_repository_file("o", "r", "logo.png")
        assert result["binary"] is True
        assert result["content"] == ""
        assert result["bytes_total"] == 10

    @pytest.mark.anyio
    async def test_a_directory_points_at_the_tree_tool(self, gi: GitHubIntegration):
        """A directory answers as JSON however the Accept header is set."""
        listing = mock_response(json_data=[{"name": "app.py", "type": "file"}])
        listing.headers = {"content-type": "application/json; charset=utf-8"}
        gi._http.request = AsyncMock(return_value=listing)
        with pytest.raises(GitHubValidationError, match="list_repository_tree"):
            await gi.get_repository_file("o", "r", "src")

    @pytest.mark.anyio
    async def test_a_missing_path_is_not_found(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(status_code=404, json_data={"message": "Not Found"}))
        with pytest.raises(ToolError, match="nope.txt"):
            await gi.get_repository_file("o", "r", "nope.txt")

    @pytest.mark.anyio
    async def test_a_bad_ref_is_not_found(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(
            return_value=mock_response(status_code=404, json_data={"message": "No commit found for the ref nope"})
        )
        with pytest.raises(ToolError, match="nope"):
            await gi.get_repository_file("o", "r", "README.md", ref="nope")

    @pytest.mark.anyio
    async def test_a_negative_window_is_refused(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=_raw(b"x"))
        with pytest.raises(GitHubValidationError, match="negative"):
            await gi.get_repository_file("o", "r", "app.py", offset=-1)
        gi._http.request.assert_not_awaited()


class TestListRepositoryTree:
    @staticmethod
    def _tree(**kw) -> MagicMock:
        return mock_response(
            json_data={
                "sha": "t1",
                "tree": [{"path": "app.py", "mode": "100644", "type": "blob", "size": 12, "sha": "b1"}],
                "truncated": False,
                **kw,
            }
        )

    @pytest.mark.anyio
    async def test_lists_the_root_at_head_by_default(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=self._tree())
        result = await gi.list_repository_tree("o", "r")
        assert gi._http.request.call_args.args[1].endswith("/git/trees/HEAD")
        assert result["total"] == 1
        assert result["entries"][0] == {"path": "app.py", "mode": "100644", "type": "blob", "size": 12, "sha": "b1"}

    @pytest.mark.anyio
    async def test_a_subdirectory_uses_the_ref_colon_path_form(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=self._tree())
        await gi.list_repository_tree("o", "r", path="src", ref="main")
        assert gi._http.request.call_args.args[1].endswith("/git/trees/main%3Asrc")

    @pytest.mark.anyio
    async def test_recursive_is_omitted_rather_than_sent_as_false(self, gi: GitHubIntegration):
        """GitHub reads any value of recursive as on, "false" and "0" included,
        so sending it at all would recurse."""
        gi._http.request = AsyncMock(return_value=self._tree())
        await gi.list_repository_tree("o", "r", recursive=False)
        assert "recursive" not in gi._http.request.call_args.args[1]

    @pytest.mark.anyio
    async def test_recursive_asks_for_every_level(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=self._tree())
        await gi.list_repository_tree("o", "r", recursive=True)
        assert "recursive=1" in gi._http.request.call_args.args[1]

    @pytest.mark.anyio
    async def test_a_capped_tree_says_so(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=self._tree(truncated=True))
        assert (await gi.list_repository_tree("o", "r"))["truncated"] is True

    @pytest.mark.anyio
    async def test_a_bad_ref_is_not_found(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(status_code=404, json_data={"message": "Not Found"}))
        with pytest.raises(ToolError, match="nope"):
            await gi.list_repository_tree("o", "r", ref="nope")


class TestListRepos:
    @pytest.mark.anyio
    async def test_a_person_uses_the_users_endpoint(self, gi: GitHubIntegration):
        responses = iter(
            [
                mock_response(json_data={"login": "someone", "type": "User"}),
                mock_response(json_data=[repo_payload()]),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.list_repos("someone")
        assert gi._http.request.call_args.args[1] == "https://api.github.com/users/someone/repos"

    @pytest.mark.anyio
    async def test_an_organisation_uses_the_orgs_endpoint(self, gi: GitHubIntegration):
        responses = iter(
            [
                mock_response(json_data={"login": "acme", "type": "Organization"}),
                mock_response(json_data=[repo_payload()]),
            ]
        )
        gi._http.request = AsyncMock(side_effect=lambda *a, **kw: next(responses))
        await gi.list_repos("acme")
        assert gi._http.request.call_args.args[1] == "https://api.github.com/orgs/acme/repos"

    @pytest.mark.anyio
    async def test_no_owner_reads_the_callers_own(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=[repo_payload(private=True)]))
        result = await gi.list_repos()
        assert gi._http.request.call_count == 1
        assert gi._http.request.call_args.args[1] == "https://api.github.com/user/repos"
        assert result["repos"][0]["private"] is True

    @pytest.mark.anyio
    async def test_results_are_trimmed(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=[repo_payload()]))
        result = await gi.list_repos()
        assert result == {
            "count": 1,
            "has_more": False,
            "repos": [
                {
                    "name": "toolbox",
                    "owner": "octocat",
                    "description": "a repo",
                    "default_branch": "main",
                    "private": False,
                    "fork": False,
                    "archived": False,
                    "pushed_at": "2026-08-01T00:00:00Z",
                    "html_url": "https://github.com/acme/toolbox",
                }
            ],
        }

    @pytest.mark.anyio
    async def test_sort_and_paging_go_out_as_params(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=[]))
        await gi.list_repos(sort="full_name", per_page=100, page=3)
        assert gi._http.request.call_args.kwargs["params"] == {"sort": "full_name", "per_page": 100, "page": 3}

    @pytest.mark.anyio
    async def test_an_owner_that_is_neither_names_itself_in_the_error(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(status_code=404, json_data={}))
        with pytest.raises(GitHubNotFoundError, match="No user or organisation named 'nope'"):
            await gi.list_repos("nope")
        assert gi._http.request.call_count == 1
