"""Tests for the one token store every consumer shares, and for the provider being
built once per integration."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_github import auth
from mcp_github.auth import aclose_token_store, build_token_store, get_token_store
from mcp_github.github_integration import GitHubIntegration

TABLE_ARN = "arn:aws:dynamodb:eu-west-1:123456789012:table/oauth-state"


def _memory_mode():
    """No Redis and no DynamoDB, so the store is in process."""
    return patch.multiple("mcp_github.auth", REDIS_HOST_PORT=None, DYNAMODB_TABLE_ARN=None)


class TestSharedStore:
    """Everything that needs the store has to get the same one."""

    def test_the_same_instance_comes_back(self):
        with _memory_mode():
            assert get_token_store() is get_token_store()

    def test_the_factory_still_builds_a_new_one_each_call(self):
        """build_token_store stays a factory, which the prefix tests rely on."""
        with _memory_mode():
            assert build_token_store() is not build_token_store()

    @pytest.mark.anyio
    async def test_a_second_consumer_sees_what_the_first_wrote(self):
        """A second MemoryStore would read empty, which is the bug in memory mode."""
        with _memory_mode():
            await get_token_store().put(key="k", value={"v": 1}, collection="c")
            assert await get_token_store().get(key="k", collection="c") == {"v": 1}

    @pytest.mark.anyio
    async def test_one_backend_client_is_built_and_released(self):
        """Two reads used to build two clients and release only the second."""
        store = MagicMock(close=AsyncMock())
        with patch("mcp_github.auth.DYNAMODB_TABLE_ARN", TABLE_ARN), patch(
            "mcp_github.auth.GITHUB_OAUTH_BASE_URL", None
        ), patch("mcp_github.auth.DynamoDBStore", return_value=store) as build:
            get_token_store()
            get_token_store()

        assert build.call_count == 1
        await aclose_token_store()
        store.close.assert_awaited_once()

    @pytest.mark.anyio
    async def test_aclose_drops_the_shared_view(self):
        with _memory_mode():
            get_token_store()
        await aclose_token_store()
        assert auth._token_store is None
        assert auth._shared_store is None

    def test_a_later_call_rebuilds_after_shutdown(self):
        with _memory_mode():
            first = get_token_store()
            auth._shared_store = None
            assert get_token_store() is not first


class TestVerifierIsBuiltOnce:
    """A provider per read would take a store consumer per read with it."""

    def test_repeated_reads_build_one_provider(self):
        integration = GitHubIntegration()
        with patch("mcp_github.github_integration.get_oauth_verifier") as build:
            build.return_value = MagicMock()
            first, second = integration._oauth_verifier, integration._oauth_verifier

        assert first is second
        build.assert_called_once()
