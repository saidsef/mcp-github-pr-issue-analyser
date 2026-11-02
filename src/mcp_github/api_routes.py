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

"""Paths and metric labels for github_api_read and github_api_write.

Both tools send a caller's path to api.github.com whatever route it names, and
GitHub's own permission check decides what the token may do. The route list
beside this module comes from GitHub's OpenAPI description and only labels the
calls for metrics. Running the module regenerates it:

    uv run python -m mcp_github.api_routes

See #543.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx

from .exceptions import GitHubValidationError
from .graphql_client import API

ROUTES_FILE = Path(__file__).parent / "rest_routes.txt"

DESCRIPTION_URL = (
    "https://raw.githubusercontent.com/github/rest-api-description/main/descriptions/api.github.com/api.github.com.json"
)

_METHODS = ("get", "post", "put", "patch", "delete")

_PARAM = re.compile(r"\{(\+?)([^}]+)\}")

# A block string, a string or a comment, in that order, since each can hold the others' delimiters.
_GRAPHQL_NOISE = re.compile(r'"""(?:\\"""|[\s\S])*?"""|"(?:\\.|[^"\\\n\r])*"|#[^\n\r]*')

_MUTATION = re.compile(r"\bmutation\b")


def _pattern(template: str) -> re.Pattern[str]:
    """{name} matches one path segment and {+name} matches several, which is how
    GitHub takes a file path or a branch name with a slash in it."""
    parts, end = [], 0
    for param in _PARAM.finditer(template):
        parts += [re.escape(template[end : param.start()]), ".+" if param.group(1) else "[^/]+"]
        end = param.end()
    return re.compile("".join(parts) + re.escape(template[end:]))


@functools.cache
def _routes() -> dict[str, list[tuple[str, re.Pattern[str]]]]:
    """Every route by method, fewest parameters first, so pulls/comments matches its
    own route before pulls/{pull_number}, as it does on GitHub."""
    routes: dict[str, list[tuple[str, re.Pattern[str]]]] = {}
    for line in ROUTES_FILE.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            method, template = line.split(" ", 1)
            routes.setdefault(method, []).append((template, _pattern(template)))
    for entries in routes.values():
        entries.sort(key=lambda entry: (entry[0].count("{+"), entry[0].count("{")))
    return routes


def api_url(path: str) -> str:
    """The URL a relative path names on api.github.com. httpx drops a fragment,
    resolves dot segments and replaces a query with params, so a path holding any
    of them is refused rather than sent somewhere other than it says."""
    url = httpx.URL(f"{API}/{path.lstrip('/')}")
    if "//" in path or ".." in unquote(path).split("/") or url.query or url.fragment:
        raise GitHubValidationError(
            f"{path!r} is not a path under {API}. Pass the path alone, such as"
            " repos/octocat/hello-world/collaborators, and put query parameters in params."
        )
    return str(url)


def route_label(method: Any, path: Any, graphql: Any) -> tuple[str, str]:
    """The method and route template a call named, for a metric label. The arguments
    arrive unvalidated, so a call that matches no documented route is labelled
    "unmatched", which stops a caller minting a series per path."""
    if graphql:
        return "POST", "/graphql"
    try:
        sent = httpx.URL(api_url(str(path))).raw_path.decode()
    except GitHubValidationError:
        return "unmatched", "unmatched"
    routes = _routes().get(str(method), [])
    template = next((template for template, pattern in routes if pattern.fullmatch(sent)), None)
    return (str(method), template) if template else ("unmatched", "unmatched")


def is_mutation(document: str) -> bool:
    """True when a GraphQL document holds a mutation. Strings and comments are blanked
    first, so a search for the word does not trip it and a block string cannot hide one."""
    return _MUTATION.search(_GRAPHQL_NOISE.sub(" ", document)) is not None


def _template(description: dict[str, Any], path: str, parameters: list[dict[str, Any]]) -> str:
    """The path with {name} rewritten to {+name} where GitHub marks the parameter
    x-multi-segment."""
    shared = description["components"]["parameters"]
    resolved = (shared[p["$ref"].rsplit("/", 1)[-1]] if "$ref" in p else p for p in parameters)
    multi = {p["name"] for p in resolved if p.get("in") == "path" and p.get("x-multi-segment")}
    return _PARAM.sub(lambda m: f"{{+{m.group(2)}}}" if m.group(2) in multi else m.group(0), path)


def write_routes() -> None:
    """Rewrite ROUTES_FILE from the description GitHub publishes for api.github.com."""
    description = httpx.get(DESCRIPTION_URL, timeout=60, follow_redirects=True).raise_for_status().json()
    routes = sorted(
        (_template(description, path, [*item.get("parameters", []), *op.get("parameters", [])]), method.upper())
        for path, item in description["paths"].items()
        for method in _METHODS
        if (op := item.get(method))
    )
    header = (
        "# Every REST route on api.github.com, from GitHub's OpenAPI description.\n"
        "# {+name} marks a parameter that spans several path segments.\n"
        "# Regenerate with: uv run python -m mcp_github.api_routes\n"
    )
    ROUTES_FILE.write_text(header + "".join(f"{method} {template}\n" for template, method in routes), encoding="utf-8")


if __name__ == "__main__":
    write_routes()
