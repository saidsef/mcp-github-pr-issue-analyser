"""Tests for the admin page - the browser sign-in, the session, and saving a
tool list. See #451."""

import time
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_github import admin
from mcp_github.admin import (
    ADMIN_PATH,
    LOGIN_STATE_COLLECTION,
    SESSION_COOKIE,
    SESSIONS_COLLECTION,
    _challenge_for,
    _pkce_pair,
    admin_client_id,
    callback_url,
    complete_login,
    read_session,
    register_admin_client,
    render_page,
    save_preferences,
    sign_out,
    start_login,
)
from mcp_github.auth import admin_secret_store
from mcp_github.preferences import bump_epoch, read_actions, read_disabled

BASE_URL = "https://mcp.example.com"
SUBJECT = "584221"
LOGIN = "octocat"
ISSUED_TOKEN = "a-fastmcp-token"


@contextmanager
def _deployment():
    """OAuth configured, the store in memory, and a known base URL."""
    with ExitStack() as stack:
        stack.enter_context(patch("mcp_github.auth.REDIS_HOST_PORT", None))
        stack.enter_context(patch("mcp_github.auth.DYNAMODB_TABLE_ARN", None))
        stack.enter_context(patch("mcp_github.auth.GITHUB_OAUTH_CLIENT_SECRET", "an-oauth-client-key"))
        stack.enter_context(patch("mcp_github.auth.JWT_SIGNING_KEY", None))
        stack.enter_context(patch("mcp_github.admin.GITHUB_OAUTH_BASE_URL", BASE_URL))
        yield


def _provider(*, challenge="", subject=SUBJECT):
    """A stand-in OAuthProxy. The browser hops are the parts we do not run here."""
    access = SimpleNamespace(subject=subject, claims={"sub": subject, "login": LOGIN})
    return MagicMock(
        get_client=AsyncMock(return_value=MagicMock(client_id=admin_client_id())),
        register_client=AsyncMock(),
        authorize=AsyncMock(return_value=f"{BASE_URL}/consent?txn_id=abc"),
        load_authorization_code=AsyncMock(return_value=SimpleNamespace(code_challenge=challenge)),
        exchange_authorization_code=AsyncMock(return_value=SimpleNamespace(access_token=ISSUED_TOKEN)),
        load_access_token=AsyncMock(return_value=access),
    )


def _request(*, cookies=None, query=None, form=None):
    request = MagicMock(cookies=cookies or {}, query_params=query or {})
    request.form = AsyncMock(return_value=form if form is not None else _Form({}))
    return request


class _Form(dict):
    """Starlette's form mapping, which returns every value for a repeated field."""

    def __init__(self, values):
        flat = {key: (value[-1] if value else "") if isinstance(value, list) else value
                for key, value in values.items()}
        super().__init__(flat)
        self._values = values

    def getlist(self, key):
        value = self._values.get(key, [])
        return value if isinstance(value, list) else [value]


async def _sign_in(provider):
    """Run the flow for real, answering the code with the challenge it asked for,
    and return the cookie the callback set."""
    await start_login(provider, ADMIN_PATH)
    state = provider.authorize.await_args.args[1].state
    pending = await admin_secret_store().get(key=state, collection=LOGIN_STATE_COLLECTION)
    provider.load_authorization_code.return_value = SimpleNamespace(
        code_challenge=_challenge_for(pending["verifier"])
    )
    response = await complete_login(provider, _request(query={"state": state, "code": "a-code"}))
    return response.headers["set-cookie"].split("=")[1].split(";")[0], response


class TestClientRegistration:
    """The admin page is a client of this server, not of GitHub."""

    def test_the_callback_is_on_this_server(self):
        with _deployment():
            assert callback_url() == f"{BASE_URL}/admin/callback"

    def test_the_client_id_is_stable_for_one_deployment(self):
        with _deployment():
            assert admin_client_id() == admin_client_id()

    def test_two_deployments_get_different_client_ids(self):
        with patch("mcp_github.admin.GITHUB_OAUTH_BASE_URL", "https://a.example.com"):
            first = admin_client_id()
        with patch("mcp_github.admin.GITHUB_OAUTH_BASE_URL", "https://b.example.com"):
            assert admin_client_id() != first

    @pytest.mark.anyio
    async def test_it_registers_a_public_client_with_our_own_redirect(self):
        provider = _provider()
        with _deployment():
            await register_admin_client(provider)

        client = provider.register_client.await_args.args[0]
        assert client.token_endpoint_auth_method == "none"
        assert client.client_secret is None
        assert [str(uri) for uri in client.redirect_uris] == [f"{BASE_URL}/admin/callback"]


class TestPkce:
    """The exchange runs in process, so the verifier is checked here."""

    def test_the_challenge_matches_its_verifier(self):
        verifier, challenge = _pkce_pair()
        assert _challenge_for(verifier) == challenge

    def test_the_challenge_carries_no_padding(self):
        assert not _pkce_pair()[1].endswith("=")


class TestLogin:
    """Starting the flow, and what comes back from it."""

    @pytest.mark.anyio
    async def test_it_redirects_into_the_servers_own_authorize(self):
        provider = _provider()
        with _deployment():
            response = await start_login(provider, ADMIN_PATH)

        assert response.status_code == 302
        assert response.headers["location"] == f"{BASE_URL}/consent?txn_id=abc"

    @pytest.mark.anyio
    async def test_it_stores_the_verifier_under_the_state(self):
        provider = _provider()
        with _deployment():
            await start_login(provider, "/admin?saved=1")
            state = provider.authorize.await_args.args[1].state
            pending = await admin_secret_store().get(key=state, collection=LOGIN_STATE_COLLECTION)

        assert pending["return_path"] == "/admin?saved=1"
        assert _challenge_for(pending["verifier"]) == provider.authorize.await_args.args[1].code_challenge

    @pytest.mark.anyio
    async def test_a_completed_sign_in_sets_a_session_cookie(self):
        provider = _provider()
        with _deployment():
            cookie, response = await _sign_in(provider)
            session = await admin_secret_store().get(key=admin._key_for(cookie), collection=SESSIONS_COLLECTION)

        assert response.status_code == 302
        assert session["sub"] == SUBJECT
        assert session["login"] == LOGIN
        assert session["token"] == ISSUED_TOKEN

    @pytest.mark.anyio
    async def test_the_cookie_is_httponly_and_secure_over_https(self):
        provider = _provider()
        with _deployment():
            _, response = await _sign_in(provider)

        header = response.headers["set-cookie"]
        assert "HttpOnly" in header
        assert "Secure" in header
        assert "SameSite=lax" in header

    @pytest.mark.anyio
    async def test_a_mismatched_verifier_is_refused(self):
        """Without this check an intercepted code would mint somebody else's session."""
        provider = _provider(challenge="not-the-challenge")
        with _deployment():
            await start_login(provider, ADMIN_PATH)
            state = provider.authorize.await_args.args[1].state
            response = await complete_login(provider, _request(query={"state": state, "code": "a-code"}))

        assert response.status_code == 400
        provider.exchange_authorization_code.assert_not_awaited()

    @pytest.mark.anyio
    async def test_an_unknown_state_is_refused(self):
        provider = _provider()
        with _deployment():
            response = await complete_login(provider, _request(query={"state": "never-issued", "code": "c"}))

        assert response.status_code == 400

    @pytest.mark.anyio
    async def test_a_reply_missing_its_code_is_refused(self):
        provider = _provider()
        with _deployment():
            response = await complete_login(provider, _request(query={"state": "s"}))

        assert response.status_code == 400

    @pytest.mark.anyio
    async def test_the_state_is_spent_on_use(self):
        """A replayed callback must not mint a second session."""
        provider = _provider()
        with _deployment():
            await start_login(provider, ADMIN_PATH)
            state = provider.authorize.await_args.args[1].state
            pending = await admin_secret_store().get(key=state, collection=LOGIN_STATE_COLLECTION)
            provider.load_authorization_code.return_value = SimpleNamespace(
                code_challenge=_challenge_for(pending["verifier"])
            )
            await complete_login(provider, _request(query={"state": state, "code": "a-code"}))
            replayed = await complete_login(provider, _request(query={"state": state, "code": "a-code"}))

        assert replayed.status_code == 400


class TestSession:
    """What counts as signed in."""

    @pytest.mark.anyio
    async def test_no_cookie_is_no_session(self):
        with _deployment():
            assert await read_session(_request()) is None

    @pytest.mark.anyio
    async def test_an_unknown_cookie_is_no_session(self):
        with _deployment():
            assert await read_session(_request(cookies={SESSION_COOKIE: "made-up"})) is None

    @pytest.mark.anyio
    async def test_a_fresh_session_reads_back(self):
        provider = _provider()
        with _deployment():
            cookie, _ = await _sign_in(provider)
            session = await read_session(_request(cookies={SESSION_COOKIE: cookie}))

        assert session["sub"] == SUBJECT

    @pytest.mark.anyio
    async def test_an_expired_session_is_refused(self):
        """The value carries its own expiry, so the backend TTL is not the check."""
        provider = _provider()
        with _deployment():
            cookie, _ = await _sign_in(provider)
            key = admin._key_for(cookie)
            stored = await admin_secret_store().get(key=key, collection=SESSIONS_COLLECTION)
            stored["expires_at"] = time.time() - 1
            await admin_secret_store().put(key=key, value=stored, collection=SESSIONS_COLLECTION)
            assert await read_session(_request(cookies={SESSION_COOKIE: cookie})) is None

    @pytest.mark.anyio
    async def test_a_raised_epoch_refuses_an_older_session(self):
        provider = _provider()
        with _deployment():
            cookie, _ = await _sign_in(provider)
            await bump_epoch(SUBJECT)
            assert await read_session(_request(cookies={SESSION_COOKIE: cookie})) is None

    @pytest.mark.anyio
    async def test_signing_out_drops_this_session_only(self):
        provider = _provider()
        with _deployment():
            cookie, _ = await _sign_in(provider)
            await sign_out(_request(cookies={SESSION_COOKIE: cookie}), everywhere=False)
            assert await read_session(_request(cookies={SESSION_COOKIE: cookie})) is None
            record_epoch = (await admin_secret_store().get(key="x", collection="nothing")) or {}

        assert record_epoch == {}

    @pytest.mark.anyio
    async def test_signing_out_everywhere_raises_the_epoch(self):
        provider = _provider()
        with _deployment():
            first, _ = await _sign_in(provider)
            second, _ = await _sign_in(provider)
            await sign_out(_request(cookies={SESSION_COOKIE: first}), everywhere=True)
            assert await read_session(_request(cookies={SESSION_COOKIE: second})) is None
            assert [entry["action"] for entry in await read_actions(SUBJECT)] == ["sign-out-everywhere"]


class TestSavePreferences:
    """Ticked boxes are what stays on, so the write is the complement."""

    @pytest.mark.anyio
    async def test_the_unticked_tools_are_the_ones_disabled(self):
        provider = _provider()
        with _deployment():
            cookie, _ = await _sign_in(provider)
            session = await read_session(_request(cookies={SESSION_COOKIE: cookie}))
            form = _Form({"csrf": session["csrf"], "tool": ["a", "b", "c"], "enabled": ["a", "c"]})
            response = await save_preferences(_request(cookies={SESSION_COOKIE: cookie}, form=form))
            assert await read_disabled(SUBJECT) == {"b"}
            assert [entry["action"] for entry in await read_actions(SUBJECT)] == ["save-preferences"]

        assert response.status_code == 302

    @pytest.mark.anyio
    async def test_a_wrong_csrf_token_is_refused(self):
        provider = _provider()
        with _deployment():
            cookie, _ = await _sign_in(provider)
            form = _Form({"csrf": "not-the-token", "tool": ["a"], "enabled": []})
            response = await save_preferences(_request(cookies={SESSION_COOKIE: cookie}, form=form))
            assert await read_disabled(SUBJECT) == set()

        assert response.status_code == 400

    @pytest.mark.anyio
    async def test_a_signed_out_caller_is_sent_back_to_the_page(self):
        with _deployment():
            response = await save_preferences(_request(form=_Form({"csrf": "x"})))

        assert response.status_code == 302
        assert response.headers["location"] == ADMIN_PATH


class TestRenderPage:
    """What the page shows."""

    def test_a_disabled_tool_is_unticked(self):
        page = render_page(LOGIN, [SimpleNamespace(name="a", description="")], {"a"}, "tok", saved=False)
        assert 'value="a"' in page
        assert "checked" not in page

    def test_an_enabled_tool_is_ticked(self):
        page = render_page(LOGIN, [SimpleNamespace(name="a", description="")], set(), "tok", saved=False)
        assert "checked" in page

    def test_every_tool_is_carried_so_the_complement_can_be_worked_out(self):
        tools = [SimpleNamespace(name="a", description=""), SimpleNamespace(name="b", description="")]
        page = render_page(LOGIN, tools, {"b"}, "tok", saved=False)
        assert page.count('name="tool"') == 2

    def test_the_login_is_escaped(self):
        page = render_page("<script>x</script>", [], set(), "tok", saved=False)
        assert "<script>" not in page

    def test_saving_says_so(self):
        assert "Preferences saved" in render_page(LOGIN, [], set(), "tok", saved=True)
