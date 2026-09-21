"""Tests for per-user tool preferences and the middleware that applies them.
See #451."""

from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.exceptions import NotFoundError

from mcp_github.preferences import (
    AUDIT_LIMIT,
    RECORD_VERSION,
    ToolPreferences,
    bump_epoch,
    current_subject,
    read_actions,
    read_disabled,
    read_record,
    record_action,
    write_disabled,
)

SUBJECT = "584221"


@contextmanager
def _memory_mode():
    """No Redis and no DynamoDB, so the preference store is in process."""
    with ExitStack() as stack:
        stack.enter_context(patch("mcp_github.auth.REDIS_HOST_PORT", None))
        stack.enter_context(patch("mcp_github.auth.DYNAMODB_TABLE_ARN", None))
        yield


def _grant(subject):
    """A request carrying a grant for this GitHub account, or none at all."""
    token = None
    if subject is not None:
        token = MagicMock(subject=subject, claims={"sub": subject, "login": "octocat"})
    return patch("mcp_github.preferences.get_access_token", return_value=token)


def _tool(name):
    """MagicMock reserves name, so the stand-ins are plain namespaces."""
    return SimpleNamespace(name=name, description="")


def _call(name):
    return SimpleNamespace(message=SimpleNamespace(name=name))


class TestCurrentSubject:
    """Which GitHub account the request is acting as."""

    def test_the_subject_comes_off_the_access_token(self):
        with _grant(SUBJECT):
            assert current_subject() == SUBJECT

    def test_no_grant_names_no_account(self):
        with _grant(None):
            assert current_subject() is None

    def test_the_sub_claim_stands_in_for_a_missing_subject(self):
        token = MagicMock(subject=None, claims={"sub": SUBJECT})
        with patch("mcp_github.preferences.get_access_token", return_value=token):
            assert current_subject() == SUBJECT


class TestRecord:
    """What is stored per user, and what an unseen user reads as."""

    @pytest.mark.anyio
    async def test_an_unseen_user_has_nothing_disabled(self):
        with _memory_mode():
            assert await read_disabled(SUBJECT) == set()

    @pytest.mark.anyio
    async def test_a_written_set_reads_back(self):
        with _memory_mode():
            await write_disabled(SUBJECT, {"github_create_issue", "github_merge_pr"})
            assert await read_disabled(SUBJECT) == {"github_create_issue", "github_merge_pr"}

    @pytest.mark.anyio
    async def test_a_write_stamps_the_record_version(self):
        with _memory_mode():
            await write_disabled(SUBJECT, set())
            assert (await read_record(SUBJECT))["version"] == RECORD_VERSION

    @pytest.mark.anyio
    async def test_a_write_leaves_the_epoch_alone(self):
        with _memory_mode():
            await bump_epoch(SUBJECT)
            await write_disabled(SUBJECT, {"github_merge_pr"})
            assert (await read_record(SUBJECT))["epoch"] == 1

    @pytest.mark.anyio
    async def test_the_epoch_climbs(self):
        with _memory_mode():
            assert await bump_epoch(SUBJECT) == 1
            assert await bump_epoch(SUBJECT) == 2

    @pytest.mark.anyio
    async def test_one_users_choice_leaves_another_alone(self):
        with _memory_mode():
            await write_disabled(SUBJECT, {"github_merge_pr"})
            assert await read_disabled("999") == set()


class TestAudit:
    """A bounded trail per user, since the store rewrites the whole value."""

    @pytest.mark.anyio
    async def test_an_unseen_user_has_no_trail(self):
        with _memory_mode():
            assert await read_actions(SUBJECT) == []

    @pytest.mark.anyio
    async def test_entries_come_back_oldest_first(self):
        with _memory_mode():
            await record_action(SUBJECT, "save-preferences", "1 disabled")
            await record_action(SUBJECT, "sign-out-everywhere")
            assert [entry["action"] for entry in await read_actions(SUBJECT)] == [
                "save-preferences",
                "sign-out-everywhere",
            ]

    @pytest.mark.anyio
    async def test_the_trail_keeps_only_the_last_fifty(self):
        with _memory_mode():
            for index in range(AUDIT_LIMIT + 10):
                await record_action(SUBJECT, "save-preferences", str(index))
            entries = await read_actions(SUBJECT)

        assert len(entries) == AUDIT_LIMIT
        assert entries[-1]["detail"] == str(AUDIT_LIMIT + 9)
        assert entries[0]["detail"] == "10"

    @pytest.mark.anyio
    async def test_one_users_trail_is_their_own(self):
        with _memory_mode():
            await record_action(SUBJECT, "save-preferences")
            assert await read_actions("999") == []


class TestListFiltering:
    """A tool turned off is not offered."""

    @pytest.mark.anyio
    async def test_a_disabled_tool_is_dropped(self):
        tools = [_tool("github_get_issue"), _tool("github_merge_pr")]
        with _memory_mode():
            await write_disabled(SUBJECT, {"github_merge_pr"})
            with _grant(SUBJECT):
                served = await ToolPreferences().on_list_tools(MagicMock(), AsyncMock(return_value=tools))

        assert [tool.name for tool in served] == ["github_get_issue"]

    @pytest.mark.anyio
    async def test_nothing_disabled_serves_the_whole_list(self):
        tools = [_tool("github_get_issue"), _tool("github_merge_pr")]
        with _memory_mode(), _grant(SUBJECT):
            served = await ToolPreferences().on_list_tools(MagicMock(), AsyncMock(return_value=tools))

        assert served == tools

    @pytest.mark.anyio
    async def test_a_request_with_no_grant_serves_the_whole_list(self):
        """Over stdio there is no account to have a preference."""
        tools = [_tool("github_merge_pr")]
        with _memory_mode(), _grant(None):
            served = await ToolPreferences().on_list_tools(MagicMock(), AsyncMock(return_value=tools))

        assert served == tools

    @pytest.mark.anyio
    async def test_an_unreadable_store_serves_the_whole_list(self):
        """A store that cannot be read must not take the tools down with it."""
        tools = [_tool("github_merge_pr")]
        with _grant(SUBJECT), patch("mcp_github.preferences.read_disabled", side_effect=RuntimeError("down")):
            served = await ToolPreferences().on_list_tools(MagicMock(), AsyncMock(return_value=tools))

        assert served == tools


class TestCallRefusal:
    """Hiding a tool from the list is not enough, because a client caches the list."""

    @pytest.mark.anyio
    async def test_a_disabled_tool_is_refused_when_called(self):
        context = _call("github_merge_pr")
        with _memory_mode():
            await write_disabled(SUBJECT, {"github_merge_pr"})
            with _grant(SUBJECT), pytest.raises(NotFoundError, match="github_merge_pr"):
                await ToolPreferences().on_call_tool(context, AsyncMock())

    @pytest.mark.anyio
    async def test_an_enabled_tool_runs(self):
        context = _call("github_get_issue")
        call_next = AsyncMock(return_value="done")
        with _memory_mode():
            await write_disabled(SUBJECT, {"github_merge_pr"})
            with _grant(SUBJECT):
                assert await ToolPreferences().on_call_tool(context, call_next) == "done"

        call_next.assert_awaited_once()
