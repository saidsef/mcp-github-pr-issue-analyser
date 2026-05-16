"""Tests for auth.py — Redis client construction and token store selection."""

import hashlib
from unittest.mock import MagicMock, patch

import pytest
from key_value.aio.stores.memory import MemoryStore

from mcp_github.auth import _build_redis_client, build_token_store


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

    def test_returns_memory_store_when_redis_not_configured(self):
        with patch("mcp_github.auth.REDIS_HOST_PORT", None):
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
