#!/usr/bin/env python3

# /*
#  * Copyright Said Sef
#  *
#  * Licensed under the Apache License, Version 2.0 (the "License");
#  * you may not use this file except in compliance with the License.
#  * You may obtain a copy of the License at
#  *
#  *      https://www.apache.org/licenses/LICENSE-2.0
#  *
#  * Unless required by applicable law or agreed to in writing, software
#  * distributed under the License is distributed on an "AS IS" BASIS,
#  * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  * See the License for the specific language governing permissions and
#  * limitations under the License.
#  */

"""The admin page: a browser sign-in and a per-user tool list. See #451.

The page is a client of this server rather than of GitHub. OAuthProxy always sends
GitHub its own fixed redirect_uri and returns the browser to whichever redirect the
client registered, so the deployment keeps one GitHub OAuth App and the admin module
holds no client secret.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import logging
import secrets
import time
from base64 import urlsafe_b64encode
from typing import Any

from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from .auth import GITHUB_OAUTH_BASE_URL, GITHUB_SCOPES, admin_secret_store
from .preferences import bump_epoch, read_record, record_action, write_disabled

LOGIN_STATE_COLLECTION = "admin-login-state"
SESSIONS_COLLECTION = "admin-sessions"

SESSION_COOKIE = "mcp_admin_session"
SESSION_SECONDS = 8 * 60 * 60
LOGIN_STATE_SECONDS = 10 * 60

ADMIN_PATH = "/admin"
CALLBACK_PATH = "/admin/callback"

# A public client authenticates with PKCE rather than a secret, which is what OAuth
# spells "none".
PUBLIC_CLIENT = "none"

UNREGISTERED = "unregistered"
INCOMPLETE = "incomplete"
EXPIRED = "expired"
BAD_CODE = "bad-code"
UNKNOWN_USER = "unknown-user"
STALE_FORM = "stale-form"
UNEXPECTED = "unexpected"

ERRORS = {
    UNREGISTERED: "The admin client is not registered.",
    INCOMPLETE: "The sign-in reply was incomplete.",
    EXPIRED: "The sign-in attempt expired. Try again.",
    BAD_CODE: "That sign-in code is not valid.",
    UNKNOWN_USER: "GitHub did not confirm who you are.",
    STALE_FORM: "That form was stale. Reload the page and try again.",
    UNEXPECTED: "Something went wrong. Try again.",
}

logger = logging.getLogger(__name__)


def _base_url() -> str:
    """The deployment's own base URL without a trailing slash."""
    return (GITHUB_OAUTH_BASE_URL or "").rstrip("/")


def callback_url() -> str:
    """Where the authorisation code comes back to. Registered on this server's own
    client record, never on the GitHub OAuth App."""
    return f"{_base_url()}{CALLBACK_PATH}"


def admin_client_id() -> str:
    """A stable client id for this deployment, so restarting reuses one record."""
    return "mcp-admin-" + hashlib.sha256(_base_url().encode()).hexdigest()[:16]


def _pkce_pair() -> tuple[str, str]:
    """A verifier and its S256 challenge, in the unpadded form the spec asks for."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, urlsafe_b64encode(digest).decode().rstrip("=")


def _challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return urlsafe_b64encode(digest).decode().rstrip("=")


def _key_for(cookie_value: str) -> str:
    """Sessions are keyed by a hash, so the store never holds the cookie itself."""
    return hashlib.sha256(cookie_value.encode()).hexdigest()


async def register_admin_client(provider: Any) -> None:
    """Record the admin page as a public client of this server.

    A public client with PKCE rather than a confidential one, because the page runs
    on the same server that issues the token and has no second secret to keep."""
    client = OAuthClientInformationFull(
        client_id=admin_client_id(),
        client_secret=None,
        redirect_uris=[AnyUrl(callback_url())],
        grant_types=["authorization_code"],
        response_types=["code"],
        scope=" ".join(GITHUB_SCOPES),
        token_endpoint_auth_method=PUBLIC_CLIENT,
        application_type="web",
        client_name="MCP GitHub admin page",
    )
    await provider.register_client(client)
    logger.info("Admin page registered as OAuth client %s", client.client_id)


async def _client(provider: Any) -> OAuthClientInformationFull | None:
    return await provider.get_client(admin_client_id())


async def read_session(request: Request) -> dict[str, Any] | None:
    """The signed-in session behind this request, or None.

    Refuses a session whose epoch the user has since raised, which is how signing
    out everywhere invalidates sessions the server cannot list."""
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return None
    session = await admin_secret_store().get(key=_key_for(cookie), collection=SESSIONS_COLLECTION)
    if not session:
        return None
    # The backend's own TTL is a tidy-up rather than the check, so the value carries
    # its expiry and that is what decides.
    if float(session.get("expires_at", 0)) <= time.time():
        await admin_secret_store().delete(key=_key_for(cookie), collection=SESSIONS_COLLECTION)
        return None
    record = await read_record(str(session.get("sub", "")))
    if int(session.get("epoch", 0)) < int(record.get("epoch", 0)):
        await admin_secret_store().delete(key=_key_for(cookie), collection=SESSIONS_COLLECTION)
        return None
    return session


async def _write_session(subject: str, login: str, token: str, epoch: int) -> str:
    """Store a new session and return the cookie value that finds it."""
    cookie = secrets.token_urlsafe(32)
    await admin_secret_store().put(
        key=_key_for(cookie),
        value={
            "sub": subject,
            "login": login,
            "token": token,
            "epoch": epoch,
            "csrf": secrets.token_urlsafe(32),
            "expires_at": time.time() + SESSION_SECONDS,
        },
        collection=SESSIONS_COLLECTION,
        ttl=SESSION_SECONDS,
    )
    return cookie


def _set_cookie(response: Response, cookie: str) -> Response:
    response.set_cookie(
        SESSION_COOKIE,
        cookie,
        max_age=SESSION_SECONDS,
        httponly=True,
        samesite="lax",
        secure=_base_url().startswith("https://"),
        path=ADMIN_PATH,
    )
    return response


async def start_login(provider: Any, return_path: str) -> Response:
    """Send the browser into this server's own authorisation flow."""
    client = await _client(provider)
    if client is None:
        return HTMLResponse(_error_page(UNREGISTERED), status_code=503)
    state = secrets.token_urlsafe(32)
    verifier, challenge = _pkce_pair()
    await admin_secret_store().put(
        key=state,
        value={"verifier": verifier, "return_path": return_path, "expires_at": time.time() + LOGIN_STATE_SECONDS},
        collection=LOGIN_STATE_COLLECTION,
        ttl=LOGIN_STATE_SECONDS,
    )
    params = AuthorizationParams(
        state=state,
        scopes=list(GITHUB_SCOPES),
        code_challenge=challenge,
        redirect_uri=AnyUrl(callback_url()),
        redirect_uri_provided_explicitly=True,
        resource=None,
    )
    return RedirectResponse(await provider.authorize(client, params), status_code=302)


async def _redeem_code(provider: Any, code: str, verifier: str) -> tuple[str, str, str] | str:
    """The subject, login and issued token behind a returned code, or the reason not.

    The exchange runs in process, which skips the SDK's token endpoint, so the PKCE
    verifier is checked here rather than being taken on trust."""
    client = await _client(provider)
    if client is None:
        return UNREGISTERED
    auth_code = await provider.load_authorization_code(client, code)
    if auth_code is None:
        return BAD_CODE
    if not hmac.compare_digest(_challenge_for(verifier), auth_code.code_challenge or ""):
        logger.warning("Admin sign-in refused: the PKCE verifier did not match the challenge")
        return BAD_CODE
    issued = await provider.exchange_authorization_code(client, auth_code)
    access = await provider.load_access_token(issued.access_token)
    if access is None:
        return UNKNOWN_USER
    claims = access.claims or {}
    subject = str(getattr(access, "subject", None) or claims.get("sub") or "")
    if not subject:
        return UNKNOWN_USER
    return subject, str(claims.get("login") or ""), issued.access_token


async def complete_login(provider: Any, request: Request) -> Response:
    """Turn the returned code into a session."""
    state = request.query_params.get("state", "")
    code = request.query_params.get("code", "")
    if not state or not code:
        return HTMLResponse(_error_page(INCOMPLETE), status_code=400)

    pending = await admin_secret_store().get(key=state, collection=LOGIN_STATE_COLLECTION)
    await admin_secret_store().delete(key=state, collection=LOGIN_STATE_COLLECTION)
    if not pending or float(pending.get("expires_at", 0)) <= time.time():
        return HTMLResponse(_error_page(EXPIRED), status_code=400)

    resolved = await _redeem_code(provider, code, str(pending.get("verifier", "")))
    if isinstance(resolved, str):
        return HTMLResponse(_error_page(resolved), status_code=400)

    subject, login, token = resolved
    record = await read_record(subject)
    cookie = await _write_session(subject, login, token, int(record.get("epoch", 0)))
    return _set_cookie(RedirectResponse(str(pending.get("return_path") or ADMIN_PATH), status_code=302), cookie)


async def sign_out(request: Request, everywhere: bool) -> Response:
    """Drop this session, and raise the epoch when every session is to go."""
    session = await read_session(request)
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        await admin_secret_store().delete(key=_key_for(cookie), collection=SESSIONS_COLLECTION)
    if everywhere and session:
        await bump_epoch(str(session.get("sub", "")))
        await record_action(str(session.get("sub", "")), "sign-out-everywhere")
    response = RedirectResponse(ADMIN_PATH, status_code=302)
    response.delete_cookie(SESSION_COOKIE, path=ADMIN_PATH)
    return response


async def save_preferences(request: Request) -> Response:
    """Write the boxes the user left ticked, as the set they turned off."""
    session = await read_session(request)
    if session is None:
        return RedirectResponse(ADMIN_PATH, status_code=302)
    form = await request.form()
    if not hmac.compare_digest(str(form.get("csrf", "")), str(session.get("csrf", ""))):
        return HTMLResponse(_error_page(STALE_FORM), status_code=400)
    offered = {str(name) for name in form.getlist("tool")}
    enabled = {str(name) for name in form.getlist("enabled")}
    disabled = offered - enabled
    await write_disabled(str(session.get("sub", "")), disabled)
    await record_action(str(session.get("sub", "")), "save-preferences", f"{len(disabled)} disabled")
    return RedirectResponse(f"{ADMIN_PATH}?saved=1", status_code=302)


_STYLE = """
:root { color-scheme: light dark; }
body { font: 15px/1.5 system-ui, sans-serif; margin: 0 auto; max-width: 46rem; padding: 2rem 1rem; }
h1 { font-size: 1.4rem; margin-bottom: 0.25rem; }
.who { color: #666; margin-top: 0; }
ul { list-style: none; padding: 0; }
li { border-top: 1px solid #8884; padding: 0.6rem 0; }
label { display: flex; gap: 0.6rem; align-items: baseline; }
.desc { color: #666; font-size: 0.85rem; margin: 0.15rem 0 0 1.9rem; }
button { font: inherit; padding: 0.45rem 0.9rem; }
.bar { display: flex; gap: 0.75rem; align-items: center; margin-top: 1.5rem; }
.saved { background: #2e7d3222; border: 1px solid #2e7d3255; padding: 0.5rem 0.75rem; }
"""


def _error_page(reason: str) -> str:
    """The page shown when signing in or saving does not go through.

    Callers name a reason rather than passing a message, so the only strings that
    reach the markup are the ones written here."""
    parts = [
        "<!doctype html><title>Admin</title><style>",
        _STYLE,
        "</style><h1>Admin</h1><p>",
        ERRORS.get(reason, ERRORS[UNEXPECTED]),
        "</p>",
    ]
    return "".join(parts)


def render_page(login: str, tools: list[Any], disabled: set[str], csrf: str, saved: bool) -> str:
    """The page itself. Served as one self-contained response, the way the landing
    and metrics routes are, so the server takes on no templating dependency."""
    rows = []
    for tool in sorted(tools, key=lambda item: item.name):
        checked = "" if tool.name in disabled else " checked"
        description = (getattr(tool, "description", "") or "").strip().split("\n")[0]
        rows.append(
            f'<li><label><input type="checkbox" name="enabled" value="{html.escape(tool.name)}"{checked}>'
            f'<span>{html.escape(tool.name)}</span></label>'
            f'<input type="hidden" name="tool" value="{html.escape(tool.name)}">'
            + (f'<p class="desc">{html.escape(description)}</p>' if description else "")
            + "</li>"
        )
    notice = '<p class="saved">Preferences saved.</p>' if saved else ""
    return (
        "<!doctype html><title>MCP GitHub admin</title>"
        f"<style>{_STYLE}</style>"
        "<h1>Your MCP tools</h1>"
        f'<p class="who">Signed in as {html.escape(login or "an unknown account")}. '
        "These choices apply to your own sessions only.</p>"
        f"{notice}"
        f'<form method="post" action="{ADMIN_PATH}/preferences">'
        f'<input type="hidden" name="csrf" value="{html.escape(csrf)}">'
        f'<ul>{"".join(rows)}</ul>'
        '<div class="bar"><button type="submit">Save</button></div>'
        "</form>"
        f'<form method="post" action="{ADMIN_PATH}/logout" class="bar">'
        '<button type="submit">Sign out</button>'
        '<button type="submit" name="everywhere" value="1">Sign out everywhere</button>'
        "</form>"
    )
