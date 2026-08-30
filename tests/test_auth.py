"""Tests for auth.py - Redis client construction, token store selection, and the
DynamoDB table ARN and startup setup."""

import asyncio
import hashlib
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from key_value.aio.errors import StoreSetupError
from key_value.aio.stores.memory import MemoryStore

from mcp_github import auth
from mcp_github.auth import (
    _build_dynamodb_store,
    _build_redis_client,
    _check_caller_account,
    _parse_table_arn,
    _setup_store,
    aclose_token_store,
    build_token_store,
    setup_token_store,
)

TABLE_ARN = "arn:aws:dynamodb:eu-west-1:123456789012:table/oauth-state"


def _setup_error(code):
    """A StoreSetupError wrapping an AWS error, the shape the library raises."""
    error = StoreSetupError(message=f"Failed to setup key value store: {code}")
    error.__cause__ = ClientError({"Error": {"Code": code, "Message": code}}, "CreateTable")
    return error


def _sts_session(account=None, error=None):
    """An aioboto3 session whose STS client answers get_caller_identity."""
    sts = MagicMock()
    sts.get_caller_identity = AsyncMock(return_value={"Account": account}, side_effect=error)
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=sts)
    client.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.client.return_value = client
    return session


@pytest.fixture(autouse=True)
def _reset_token_store():
    """build_token_store records what it built, so drop it between tests."""
    yield
    auth._token_store = None


class TestBuildRedisClient:
    """URI parsing and AsyncRedis constructor kwargs."""

    def _kwargs(self, uri, redis_password=None):
        with patch("mcp_github.auth.AsyncRedis") as mock_cls, patch("mcp_github.auth.REDIS_PASSWORD", redis_password):
            _build_redis_client(uri)
            return mock_cls.call_args.kwargs

    def test_bare_host_port_defaults(self):
        kw = self._kwargs("localhost:6379")
        assert kw == {
            "host": "localhost",
            "port": 6379,
            "db": 0,
            "password": None,
            "ssl": False,
            "decode_responses": True,
        }

    def test_redis_uri_host_and_port(self):
        kw = self._kwargs("redis://myhost:6380")
        assert kw["host"] == "myhost"
        assert kw["port"] == 6380
        assert kw["ssl"] is False

    def test_db_read_from_uri_path(self):
        kw = self._kwargs("redis://localhost:6379/3")
        assert kw["db"] == 3

    def test_rediss_enables_ssl(self):
        kw = self._kwargs("rediss://localhost:6380")
        assert kw["ssl"] is True

    def test_password_embedded_in_uri(self):
        kw = self._kwargs("redis://:secret@localhost:6379")
        assert kw["password"] == "secret"

    def test_redis_password_env_fallback(self):
        kw = self._kwargs("redis://localhost:6379", redis_password="envpass")
        assert kw["password"] == "envpass"

    def test_uri_password_takes_precedence_over_env(self):
        kw = self._kwargs("redis://:uripass@localhost:6379", redis_password="envpass")
        assert kw["password"] == "uripass"

    def test_invalid_db_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid Redis database"):
            _build_redis_client("redis://localhost:6379/abc")

    def test_empty_path_defaults_db_to_zero(self):
        kw = self._kwargs("redis://localhost:6379/")
        assert kw["db"] == 0


class TestBuildTokenStore:
    """Storage backend selection based on env vars."""

    def test_returns_memory_store_when_no_backend_configured(self):
        with (
            patch("mcp_github.auth.REDIS_HOST_PORT", None),
            patch("mcp_github.auth.DYNAMODB_TABLE_ARN", None),
        ):
            result = build_token_store()
        assert isinstance(result, MemoryStore)

    def test_redis_store_constructed_with_correct_client(self):
        mock_client = MagicMock()
        with (
            patch("mcp_github.auth.REDIS_HOST_PORT", "redis://localhost:6379"),
            patch("mcp_github.auth.GITHUB_OAUTH_BASE_URL", None),
            patch("mcp_github.auth._build_redis_client", return_value=mock_client) as mock_build,
            patch("mcp_github.auth.RedisStore") as mock_store_cls,
        ):
            build_token_store()
            mock_build.assert_called_once_with("redis://localhost:6379")
            mock_store_cls.assert_called_once_with(client=mock_client)

    def test_prefix_wrapper_applied_when_base_url_set(self):
        url = "https://example.com"
        expected_prefix = hashlib.sha256(url.encode()).hexdigest()[:12]
        with (
            patch("mcp_github.auth.REDIS_HOST_PORT", "redis://localhost:6379"),
            patch("mcp_github.auth.GITHUB_OAUTH_BASE_URL", url),
            patch("mcp_github.auth._build_redis_client", return_value=MagicMock()),
            patch("mcp_github.auth.RedisStore") as mock_store_cls,
            patch("mcp_github.auth.PrefixCollectionsWrapper") as mock_wrapper,
        ):
            build_token_store()
            mock_wrapper.assert_called_once_with(mock_store_cls.return_value, prefix=expected_prefix)

    def test_no_prefix_wrapper_when_base_url_absent(self):
        with (
            patch("mcp_github.auth.REDIS_HOST_PORT", "redis://localhost:6379"),
            patch("mcp_github.auth.GITHUB_OAUTH_BASE_URL", None),
            patch("mcp_github.auth._build_redis_client", return_value=MagicMock()),
            patch("mcp_github.auth.RedisStore"),
            patch("mcp_github.auth.PrefixCollectionsWrapper") as mock_wrapper,
        ):
            build_token_store()
            mock_wrapper.assert_not_called()

    def test_prefix_is_stable_for_same_url(self):
        url = "https://example.com"
        prefixes = []

        def capture_wrapper(store, prefix):
            prefixes.append(prefix)
            return MagicMock()

        with (
            patch("mcp_github.auth.REDIS_HOST_PORT", "redis://localhost:6379"),
            patch("mcp_github.auth.GITHUB_OAUTH_BASE_URL", url),
            patch("mcp_github.auth._build_redis_client", return_value=MagicMock()),
            patch("mcp_github.auth.RedisStore"),
            patch("mcp_github.auth.PrefixCollectionsWrapper", side_effect=capture_wrapper),
        ):
            build_token_store()
            build_token_store()

        assert len(prefixes) == 2
        assert prefixes[0] == prefixes[1]

    def test_prefix_differs_for_different_urls(self):
        prefixes = []

        def capture_wrapper(store, prefix):
            prefixes.append(prefix)
            return MagicMock()

        for url in ("https://server-a.example.com", "https://server-b.example.com"):
            with (
                patch("mcp_github.auth.REDIS_HOST_PORT", "redis://localhost:6379"),
                patch("mcp_github.auth.GITHUB_OAUTH_BASE_URL", url),
                patch("mcp_github.auth._build_redis_client", return_value=MagicMock()),
                patch("mcp_github.auth.RedisStore"),
                patch("mcp_github.auth.PrefixCollectionsWrapper", side_effect=capture_wrapper),
            ):
                build_token_store()

        assert prefixes[0] != prefixes[1]


class TestParseTableArn:
    """What a DynamoDB table ARN has to look like."""

    def test_region_table_name_and_account_read_from_the_arn(self):
        assert _parse_table_arn(TABLE_ARN) == ("eu-west-1", "oauth-state", "123456789012")

    def test_table_name_may_contain_dots_and_dashes(self):
        arn = "arn:aws:dynamodb:us-east-1:123456789012:table/mcp.github-oauth_state"
        assert _parse_table_arn(arn)[1] == "mcp.github-oauth_state"

    @pytest.mark.parametrize(
        "arn",
        [
            "oauth-state",
            "arn:aws:dynamodb:eu-west-1:123456789012",
            "arn:aws:s3:::my-bucket",
            "arn:aws:dynamodb:eu-west-1:123456789012:stream/oauth-state",
            "aws:aws:dynamodb:eu-west-1:123456789012:table/oauth-state",
        ],
    )
    def test_anything_but_a_table_arn_is_refused(self, arn):
        with pytest.raises(ValueError, match="Not a DynamoDB table ARN"):
            _parse_table_arn(arn)

    @pytest.mark.parametrize(
        "arn",
        [
            "arn:aws:dynamodb:eu-west-1:123456789012:table/oauth-state/index/by-client",
            "arn:aws:dynamodb:eu-west-1:123456789012:table/oauth-state/stream/2026-08-30T00:00:00.000",
        ],
    )
    def test_an_index_or_stream_arn_is_refused(self, arn):
        with pytest.raises(ValueError, match="names an index or a stream"):
            _parse_table_arn(arn)

    @pytest.mark.parametrize(
        "arn",
        [
            "arn:aws:dynamodb::123456789012:table/oauth-state",
            "arn:aws:dynamodb:eu-west-1::table/oauth-state",
            "arn:aws:dynamodb:eu-west-1:123456789012:table/",
        ],
    )
    def test_empty_segments_are_refused(self, arn):
        with pytest.raises(ValueError, match="has no region, account or table name"):
            _parse_table_arn(arn)


class TestDynamoDBTokenStore:
    """DynamoDB selection, precedence over Redis, and shutdown."""

    def test_dynamodb_store_built_from_the_arn(self):
        with (
            patch("mcp_github.auth.DYNAMODB_TABLE_ARN", TABLE_ARN),
            patch("mcp_github.auth.GITHUB_OAUTH_BASE_URL", None),
            patch("mcp_github.auth.DynamoDBStore") as mock_store_cls,
        ):
            result = build_token_store()
        mock_store_cls.assert_called_once_with(table_name="oauth-state", region_name="eu-west-1")
        assert result is mock_store_cls.return_value

    def test_a_bad_arn_is_refused_when_the_store_is_built(self):
        with (
            patch("mcp_github.auth.DYNAMODB_TABLE_ARN", "arn:aws:s3:::my-bucket"),
            patch("mcp_github.auth.DynamoDBStore"),
            pytest.raises(ValueError, match="Not a DynamoDB table ARN"),
        ):
            build_token_store()

    def test_dynamodb_wins_when_both_backends_are_set(self):
        with (
            patch("mcp_github.auth.DYNAMODB_TABLE_ARN", TABLE_ARN),
            patch("mcp_github.auth.REDIS_HOST_PORT", "redis://localhost:6379"),
            patch("mcp_github.auth.GITHUB_OAUTH_BASE_URL", None),
            patch("mcp_github.auth.DynamoDBStore") as mock_store_cls,
            patch("mcp_github.auth.RedisStore") as mock_redis_cls,
        ):
            result = build_token_store()
        assert result is mock_store_cls.return_value
        mock_redis_cls.assert_not_called()

    def test_prefix_wrapper_applied_to_dynamodb_too(self):
        url = "https://example.com"
        expected_prefix = hashlib.sha256(url.encode()).hexdigest()[:12]
        with (
            patch("mcp_github.auth.DYNAMODB_TABLE_ARN", TABLE_ARN),
            patch("mcp_github.auth.GITHUB_OAUTH_BASE_URL", url),
            patch("mcp_github.auth.DynamoDBStore") as mock_store_cls,
            patch("mcp_github.auth.PrefixCollectionsWrapper") as mock_wrapper,
        ):
            build_token_store()
        mock_wrapper.assert_called_once_with(mock_store_cls.return_value, prefix=expected_prefix)

    @pytest.mark.anyio
    async def test_aclose_closes_the_store_once(self):
        store = MagicMock()
        store.close = AsyncMock()
        with (
            patch("mcp_github.auth.DYNAMODB_TABLE_ARN", TABLE_ARN),
            patch("mcp_github.auth.GITHUB_OAUTH_BASE_URL", None),
            patch("mcp_github.auth.DynamoDBStore", return_value=store),
        ):
            build_token_store()
        await aclose_token_store()
        await aclose_token_store()
        store.close.assert_awaited_once()

    @pytest.mark.anyio
    async def test_aclose_is_a_no_op_for_the_memory_store(self):
        with (
            patch("mcp_github.auth.REDIS_HOST_PORT", None),
            patch("mcp_github.auth.DYNAMODB_TABLE_ARN", None),
        ):
            build_token_store()
        await aclose_token_store()
        assert auth._token_store is None


class TestReplacedSettings:
    """The settings the ARN replaced, left behind on an upgrade."""

    def test_the_old_settings_are_called_out(self, caplog):
        with (
            patch("mcp_github.auth.DYNAMODB_TABLE_ARN", None),
            patch("mcp_github.auth.REDIS_HOST_PORT", None),
            patch.dict(os.environ, {"DYNAMODB_TABLE_NAME": "oauth-state", "DYNAMODB_REGION": "eu-west-1"}),
        ):
            build_token_store()
        assert "DYNAMODB_TABLE_NAME, DYNAMODB_REGION no longer configure the token store" in caplog.text

    def test_nothing_is_said_when_they_are_not_set(self, caplog):
        with (
            patch("mcp_github.auth.DYNAMODB_TABLE_ARN", None),
            patch("mcp_github.auth.REDIS_HOST_PORT", None),
            patch.dict(os.environ, {}, clear=True),
        ):
            build_token_store()
        assert "no longer configure" not in caplog.text


class TestCheckCallerAccount:
    """The ARN names an account, the credentials reach one, and they have to agree."""

    @pytest.mark.anyio
    async def test_matching_account_passes(self):
        with patch("mcp_github.auth.aioboto3.Session", return_value=_sts_session(account="123456789012")):
            await _check_caller_account("eu-west-1", "123456789012")

    @pytest.mark.anyio
    async def test_another_account_is_refused(self):
        with (
            patch("mcp_github.auth.aioboto3.Session", return_value=_sts_session(account="999999999999")),
            pytest.raises(ValueError, match="names account 123456789012.*account 999999999999"),
        ):
            await _check_caller_account("eu-west-1", "123456789012")

    @pytest.mark.anyio
    async def test_an_unreachable_sts_warns_and_carries_on(self, caplog):
        session = _sts_session(error=ClientError({"Error": {"Code": "AccessDenied"}}, "GetCallerIdentity"))
        with patch("mcp_github.auth.aioboto3.Session", return_value=session):
            await _check_caller_account("eu-west-1", "123456789012")
        assert "Could not read the caller's AWS account to check it against 123456789012" in caplog.text


class _RacingStore:
    """Two of these against one table behave the way DynamoDB does: both look before
    either creates, and whichever creates second is refused."""

    def __init__(self, tables, table_name):
        self.tables = tables
        self.table_name = table_name
        self.attempts = 0
        self.ready = False

    async def setup(self):
        self.attempts += 1
        existed = self.table_name in self.tables
        await asyncio.sleep(0)
        if not existed:
            if self.table_name in self.tables:
                raise _setup_error("ResourceInUseException")
            self.tables.add(self.table_name)
        self.ready = True


class TestSetupStore:
    """Creating the table at startup, and the race between replicas doing it."""

    @pytest.mark.anyio
    async def test_setup_is_called_once_when_it_succeeds(self):
        store = MagicMock()
        store.setup = AsyncMock()
        await _setup_store(store, "oauth-state")
        store.setup.assert_awaited_once()

    @pytest.mark.anyio
    async def test_a_lost_create_race_is_retried(self):
        store = MagicMock()
        store.setup = AsyncMock(side_effect=[_setup_error("ResourceInUseException"), None])
        with patch("mcp_github.auth.DYNAMODB_SETUP_RETRY_SECONDS", 0):
            await _setup_store(store, "oauth-state")
        assert store.setup.await_count == 2

    @pytest.mark.anyio
    async def test_a_table_that_is_not_active_yet_is_retried(self):
        store = MagicMock()
        store.setup = AsyncMock(side_effect=[_setup_error("ResourceNotFoundException"), None])
        with patch("mcp_github.auth.DYNAMODB_SETUP_RETRY_SECONDS", 0):
            await _setup_store(store, "oauth-state")
        assert store.setup.await_count == 2

    @pytest.mark.anyio
    async def test_the_race_is_still_seen_when_releasing_the_client_also_failed(self):
        """That replaces the cause, leaving the AWS code only in the message."""
        error = StoreSetupError(message="Failed to setup key value store: An error occurred (ResourceInUseException)")
        error.__cause__ = RuntimeError("closing the client failed")
        store = MagicMock()
        store.setup = AsyncMock(side_effect=[error, None])
        with patch("mcp_github.auth.DYNAMODB_SETUP_RETRY_SECONDS", 0):
            await _setup_store(store, "oauth-state")
        assert store.setup.await_count == 2

    @pytest.mark.anyio
    async def test_a_role_that_cannot_create_the_table_stops_the_server(self):
        store = MagicMock()
        store.setup = AsyncMock(side_effect=_setup_error("AccessDeniedException"))
        with pytest.raises(StoreSetupError):
            await _setup_store(store, "oauth-state")
        store.setup.assert_awaited_once()

    @pytest.mark.anyio
    async def test_a_table_stuck_creating_gives_up_and_raises(self):
        store = MagicMock()
        store.setup = AsyncMock(side_effect=_setup_error("ResourceInUseException"))
        with patch("mcp_github.auth.DYNAMODB_SETUP_RETRY_SECONDS", 0):
            with pytest.raises(StoreSetupError):
                await _setup_store(store, "oauth-state")
        assert store.setup.await_count == auth.DYNAMODB_SETUP_ATTEMPTS

    @pytest.mark.anyio
    async def test_two_stores_creating_at_once_both_end_up_ready(self):
        tables = set()
        stores = [_RacingStore(tables, "oauth-state"), _RacingStore(tables, "oauth-state")]
        with patch("mcp_github.auth.DYNAMODB_SETUP_RETRY_SECONDS", 0):
            await asyncio.gather(*(_setup_store(store, "oauth-state") for store in stores))
        assert all(store.ready for store in stores)
        assert tables == {"oauth-state"}
        assert max(store.attempts for store in stores) >= 2


class TestSetupTokenStore:
    """What the lifespan calls before the server takes requests."""

    @pytest.mark.anyio
    async def test_no_op_without_a_dynamodb_store(self):
        with (
            patch("mcp_github.auth.DYNAMODB_TABLE_ARN", None),
            patch("mcp_github.auth.aioboto3.Session") as mock_session,
        ):
            await setup_token_store()
        mock_session.assert_not_called()

    @pytest.mark.anyio
    async def test_account_is_checked_and_the_table_created(self):
        store = MagicMock()
        store.setup = AsyncMock()
        auth._token_store = store
        with (
            patch("mcp_github.auth.DYNAMODB_TABLE_ARN", TABLE_ARN),
            patch(
                "mcp_github.auth.aioboto3.Session", return_value=_sts_session(account="123456789012")
            ) as mock_session,
        ):
            await setup_token_store()
        mock_session.assert_called_once_with(region_name="eu-west-1")
        store.setup.assert_awaited_once()

    @pytest.mark.anyio
    async def test_an_arn_for_another_account_stops_the_server(self):
        store = MagicMock()
        store.setup = AsyncMock()
        auth._token_store = store
        with (
            patch("mcp_github.auth.DYNAMODB_TABLE_ARN", TABLE_ARN),
            patch("mcp_github.auth.aioboto3.Session", return_value=_sts_session(account="999999999999")),
            pytest.raises(ValueError, match="names account 123456789012"),
        ):
            await setup_token_store()
        store.setup.assert_not_awaited()


DYNAMODB_TEST_ENDPOINT = os.getenv("DYNAMODB_TEST_ENDPOINT")

TEST_TABLE_ARN = "arn:aws:dynamodb:eu-west-1:000000000000:table/oauth-state-test"
RACE_TABLE_ARN = "arn:aws:dynamodb:eu-west-1:000000000000:table/oauth-state-race"


@pytest.mark.skipif(
    not DYNAMODB_TEST_ENDPOINT,
    reason="set DYNAMODB_TEST_ENDPOINT to a DynamoDB Local endpoint to run these",
)
class TestDynamoDBStoreEndToEnd:
    """Round trip against a real DynamoDB. Every other test here mocks the store,
    so this is the only one that proves the table and its TTL are usable.

    docker run --rm -p 8000:8000 amazon/dynamodb-local
    DYNAMODB_TEST_ENDPOINT=http://localhost:8000 uv run pytest tests/test_auth.py
    """

    @pytest.fixture
    def _aws_environment(self):
        """AWS_ENDPOINT_URL_DYNAMODB is the SDK's own, so reaching DynamoDB Local
        needs no setting this repository defines. The global one points STS at the
        same place, where the account check fails and is logged rather than raised."""
        with patch.dict(
            os.environ,
            {
                "AWS_ACCESS_KEY_ID": "dummy",
                "AWS_SECRET_ACCESS_KEY": "dummy",
                "AWS_ENDPOINT_URL": DYNAMODB_TEST_ENDPOINT,
                "AWS_ENDPOINT_URL_DYNAMODB": DYNAMODB_TEST_ENDPOINT,
            },
        ):
            yield

    @pytest.mark.anyio
    async def test_stores_reads_and_deletes_a_token(self, _aws_environment):
        with (
            patch("mcp_github.auth.DYNAMODB_TABLE_ARN", TEST_TABLE_ARN),
            patch("mcp_github.auth.GITHUB_OAUTH_BASE_URL", None),
        ):
            store = build_token_store()
            await setup_token_store()

            await store.put("client-1", {"client_secret": "abc"}, collection="oauth")
            assert await store.get("client-1", collection="oauth") == {"client_secret": "abc"}

            # The library writes the expiry to the table's TTL attribute, which is
            # what keeps expired tokens from piling up.
            await store.put("expiring", {"tok": "x"}, collection="oauth", ttl=60)
            _, remaining = await store.ttl("expiring", collection="oauth")
            assert remaining is not None and 0 < remaining <= 60

            assert await store.delete("client-1", collection="oauth") is True
            assert await store.get("client-1", collection="oauth") is None

            await aclose_token_store()
            assert auth._token_store is None

    @pytest.mark.anyio
    async def test_replicas_starting_together_all_get_the_table(self, _aws_environment):
        """Three stores creating at once. Every one but the winner used to raise
        StoreSetupError at whichever sign-in reached it."""
        with (
            patch("mcp_github.auth.DYNAMODB_TABLE_ARN", RACE_TABLE_ARN),
            patch("mcp_github.auth.GITHUB_OAUTH_BASE_URL", None),
            patch("mcp_github.auth.DYNAMODB_SETUP_RETRY_SECONDS", 0.2),
        ):
            stores = [_build_dynamodb_store() for _ in range(3)]
            try:
                await asyncio.gather(*(_setup_store(store, "oauth-state-race") for store in stores))
                for index, store in enumerate(stores):
                    await store.put(f"replica-{index}", {"tok": "x"}, collection="oauth")
                    assert await store.get(f"replica-{index}", collection="oauth") == {"tok": "x"}
            finally:
                for store in stores:
                    await store.close()
