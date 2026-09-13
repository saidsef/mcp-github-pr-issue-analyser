"""Tests for auth.py - which credentials a deployment holds, what a request carrying
the static personal access token verifies as, and which token a tool acts with."""

from unittest.mock import patch

import pytest

from mcp_github import auth
from mcp_github.auth import (
    GITHUB_SCOPES,
    APIKeyVerifier,
    oauth_configured,
    resolve_token,
)


class TestOAuthConfigured:
    """The OAuth trio counts only when the whole of it is set."""

    def test_the_whole_trio_counts(self):
        with (
            patch("mcp_github.auth.GITHUB_OAUTH_CLIENT_ID", "Ov23liExample"),
            patch("mcp_github.auth.GITHUB_OAUTH_CLIENT_SECRET", "oauth-client-secret"),
            patch("mcp_github.auth.GITHUB_OAUTH_BASE_URL", "https://mcp.example.com"),
        ):
            assert oauth_configured() is True

    @pytest.mark.parametrize("missing", ["CLIENT_ID", "CLIENT_SECRET", "BASE_URL"])
    def test_any_one_of_them_missing_does_not(self, missing):
        with (
            patch("mcp_github.auth.GITHUB_OAUTH_CLIENT_ID", "Ov23liExample"),
            patch("mcp_github.auth.GITHUB_OAUTH_CLIENT_SECRET", "oauth-client-secret"),
            patch("mcp_github.auth.GITHUB_OAUTH_BASE_URL", "https://mcp.example.com"),
            patch(f"mcp_github.auth.GITHUB_OAUTH_{missing}", None),
        ):
            assert oauth_configured() is False


class TestAPIKeyVerifier:
    """What a request carrying the static personal access token verifies as."""

    @pytest.mark.anyio
    async def test_the_matching_token_is_accepted(self):
        verified = await APIKeyVerifier("pat").verify_token("pat")

        assert verified is not None
        assert verified.token == "pat"
        assert verified.client_id == "github_token"

    @pytest.mark.anyio
    async def test_it_reports_the_scopes_the_oauth_grant_reports(self):
        """MultiAuth checks every token against the OAuth provider's required scopes,
        so a narrower set here refuses the static token with a 403. See #389."""
        verified = await APIKeyVerifier("pat").verify_token("pat")

        assert verified is not None
        assert verified.scopes == GITHUB_SCOPES

    @pytest.mark.anyio
    async def test_any_other_token_is_refused(self):
        assert await APIKeyVerifier("pat").verify_token("other") is None


class TestResolveToken:
    """Which token a tool acts with. See #389."""

    @pytest.mark.anyio
    async def test_a_static_token_request_acts_with_the_token_it_arrived_with(self):
        arrived = await APIKeyVerifier("pat").verify_token("pat")
        with patch("mcp_github.auth.get_access_token", return_value=arrived):
            assert resolve_token("pat", oauth_mode=True) == "pat"

    def test_an_oauth_request_acts_with_its_own_grant(self):
        grant = auth.AccessToken(token="gho_user", client_id="1234", scopes=GITHUB_SCOPES, expires_at=None)
        with patch("mcp_github.auth.get_access_token", return_value=grant):
            assert resolve_token("pat", oauth_mode=True) == "gho_user"

    def test_the_static_token_serves_a_call_outside_any_request(self):
        with patch("mcp_github.auth.get_access_token", return_value=None):
            assert resolve_token("pat", oauth_mode=True) == "pat"

    def test_oauth_without_a_request_or_a_static_token_raises(self):
        with (
            patch("mcp_github.auth.get_access_token", return_value=None),
            pytest.raises(RuntimeError, match="no access token in request context"),
        ):
            resolve_token(None, oauth_mode=True)

    def test_no_credential_at_all_resolves_to_nothing(self):
        with patch("mcp_github.auth.get_access_token", return_value=None):
            assert resolve_token(None, oauth_mode=False) == ""

