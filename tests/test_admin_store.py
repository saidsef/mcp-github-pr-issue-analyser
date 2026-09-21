"""Tests for the shared token store and the encrypted view over it - one instance
for every consumer, and what a rotated key does to what it wrote. See #451."""

from contextlib import ExitStack, contextmanager
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from key_value.aio.stores.memory import MemoryStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

from mcp_github import auth
from mcp_github.auth import (
    ADMIN_STORE_SALT,
    JWT_SIGNING_SALT,
    _derive_jwt_signing_key,
    aclose_token_store,
    admin_secret_store,
    get_token_store,
)
from mcp_github.github_integration import GitHubIntegration

CLIENT_KEY = "an-oauth-client-key"


@contextmanager
def _memory_mode():
    """A deployment with no Redis and no DynamoDB, so the store is in process."""
    with ExitStack() as stack:
        stack.enter_context(patch("mcp_github.auth.REDIS_HOST_PORT", None))
        stack.enter_context(patch("mcp_github.auth.DYNAMODB_TABLE_ARN", None))
        stack.enter_context(patch("mcp_github.auth.GITHUB_OAUTH_CLIENT_SECRET", CLIENT_KEY))
        stack.enter_context(patch("mcp_github.auth.JWT_SIGNING_KEY", None))
        yield


class TestSharedStore:
    """Everything that needs the store has to get the same one."""

    def test_the_same_instance_comes_back(self):
        with patch("mcp_github.auth.REDIS_HOST_PORT", None), patch("mcp_github.auth.DYNAMODB_TABLE_ARN", None):
            assert get_token_store() is get_token_store()

    def test_the_factory_still_builds_a_new_one_each_call(self):
        """build_token_store stays a factory, which is what the prefix tests rely on."""
        with patch("mcp_github.auth.REDIS_HOST_PORT", None), patch("mcp_github.auth.DYNAMODB_TABLE_ARN", None):
            assert auth.build_token_store() is not auth.build_token_store()

    @pytest.mark.anyio
    async def test_a_second_consumer_sees_what_the_first_wrote(self):
        """In memory mode a second MemoryStore would be empty, which is the bug."""
        with patch("mcp_github.auth.REDIS_HOST_PORT", None), patch("mcp_github.auth.DYNAMODB_TABLE_ARN", None):
            await get_token_store().put(key="k", value={"v": 1}, collection="c")
            assert await get_token_store().get(key="k", collection="c") == {"v": 1}

    @pytest.mark.anyio
    async def test_aclose_drops_every_cached_view(self):
        with patch("mcp_github.auth.REDIS_HOST_PORT", None), patch("mcp_github.auth.DYNAMODB_TABLE_ARN", None):
            get_token_store()
        with patch("mcp_github.auth.GITHUB_OAUTH_CLIENT_SECRET", CLIENT_KEY):
            admin_secret_store()
        await aclose_token_store()
        assert auth._token_store is None
        assert auth._shared_store is None
        assert auth._admin_store is None


class TestVerifierIsBuiltOnce:
    """A provider per read would build a store consumer per read."""

    def test_repeated_reads_build_one_provider(self):
        integration = GitHubIntegration()
        with patch("mcp_github.github_integration.get_oauth_verifier") as build:
            build.return_value = MagicMock()
            first, second = integration._oauth_verifier, integration._oauth_verifier

        assert first is second
        build.assert_called_once()


class TestSigningKey:
    """Each consumer derives under its own salt."""

    def test_the_admin_salt_gives_a_different_key(self):
        with _memory_mode():
            assert _derive_jwt_signing_key(JWT_SIGNING_SALT) != _derive_jwt_signing_key(ADMIN_STORE_SALT)

    def test_the_default_salt_is_the_providers_own(self):
        with _memory_mode():
            assert _derive_jwt_signing_key() == _derive_jwt_signing_key(JWT_SIGNING_SALT)

    def test_no_signing_material_is_refused(self):
        with patch("mcp_github.auth.JWT_SIGNING_KEY", None), patch(
            "mcp_github.auth.GITHUB_OAUTH_CLIENT_SECRET", None
        ), pytest.raises(ValueError, match="JWT_SIGNING_KEY or GITHUB_OAUTH_CLIENT_SECRET"):
            _derive_jwt_signing_key()

    def test_the_derived_key_is_a_usable_fernet_key(self):
        """derive_jwt_key already returns Fernet's own format, so no second KDF runs."""
        with _memory_mode():
            assert Fernet(key=_derive_jwt_signing_key(ADMIN_STORE_SALT)) is not None


class TestAdminSecretStore:
    """The encrypted view, and what a rotated key does to what it wrote."""

    @pytest.mark.anyio
    async def test_it_round_trips_a_value(self):
        with _memory_mode():
            await admin_secret_store().put(key="k", value={"token": "t"}, collection="admin-sessions")
            assert await admin_secret_store().get(key="k", collection="admin-sessions") == {"token": "t"}

    @pytest.mark.anyio
    async def test_the_stored_value_is_not_the_plain_one(self):
        with _memory_mode():
            await admin_secret_store().put(key="k", value={"token": "t"}, collection="admin-sessions")
            raw = await get_token_store().get(key="k", collection="admin-sessions")

        assert raw is not None
        assert "token" not in raw
        assert "__encrypted_data__" in raw

    @pytest.mark.anyio
    async def test_a_different_key_reads_as_a_miss(self):
        """A rotated signing key signs an admin out rather than raising on every read."""
        with _memory_mode():
            await admin_secret_store().put(key="k", value={"token": "t"}, collection="admin-sessions")
            rotated = FernetEncryptionWrapper(
                key_value=get_token_store(),
                fernet=Fernet(key=Fernet.generate_key()),
                raise_on_decryption_error=False,
            )
            assert await rotated.get(key="k", collection="admin-sessions") is None

    def test_the_same_instance_comes_back(self):
        with _memory_mode():
            assert admin_secret_store() is admin_secret_store()

    def test_it_wraps_the_shared_store(self):
        with _memory_mode():
            store = admin_secret_store()
            assert isinstance(store, FernetEncryptionWrapper)
            assert store.key_value is get_token_store()
            assert isinstance(store.key_value, MemoryStore)
