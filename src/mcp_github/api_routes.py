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

"""Paths, metric labels and the route search for the github_api_* tools.

The request tools send a caller's path to api.github.com whatever route it names,
and GitHub's own permission check decides what the token may do. The route list
beside this module comes from GitHub's OpenAPI description. It labels those calls
for metrics, and github_search_api_routes searches it so an agent can find the
method and path for a task. Running the module regenerates it:

    uv run python -m mcp_github.api_routes

See #543.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Annotated, Any, Literal, NamedTuple, TypedDict
from urllib.parse import unquote

import httpx

from .exceptions import GitHubValidationError
from .graphql_client import API
from .tool_annotations import _read_only

ROUTES_FILE = Path(__file__).parent / "rest_routes.txt"

DESCRIPTION_URL = (
    "https://raw.githubusercontent.com/github/rest-api-description/main/descriptions/api.github.com/api.github.com.json"
)

DOCS_URL = "https://docs.github.com/rest/"

# The tool that sends each method, so a search result names the one to call.
_TOOL_FOR = {"GET": "github_api_read", "DELETE": "github_api_delete"}

_METHODS = ("get", "post", "put", "patch", "delete")

_Method = Literal["GET", "POST", "PATCH", "PUT", "DELETE"]

_PARAM = re.compile(r"\{(\+?)([^}]+)\}")

# A block string, a string or a comment, in that order, since each can hold the others' delimiters.
_GRAPHQL_NOISE = re.compile(r'"""(?:\\"""|[\s\S])*?"""|"(?:\\.|[^"\\\n\r])*"|#[^\n\r]*')

_MUTATION = re.compile(r"\bmutation\b")

# GitHub names a mutation that takes something away delete... or remove...
_DELETION = re.compile(r"\b(?:delete|remove)[A-Z]\w*")


class Route(NamedTuple):
    method: str
    raw: str
    summary: str
    docs: str
    pattern: re.Pattern[str]

    @property
    def template(self) -> str:
        """The path as GitHub's docs write it, without the {+name} marker."""
        return self.raw.replace("{+", "{")


class RouteSearchResult(TypedDict):
    total: int
    routes: list[dict[str, str]]


def _pattern(template: str) -> re.Pattern[str]:
    """{name} matches one path segment and {+name} matches several, which is how
    GitHub takes a file path or a branch name with a slash in it."""
    parts, end = [], 0
    for param in _PARAM.finditer(template):
        parts += [re.escape(template[end : param.start()]), ".+" if param.group(1) else "[^/]+"]
        end = param.end()
    return re.compile("".join(parts) + re.escape(template[end:]))


@functools.cache
def _catalogue() -> list[Route]:
    """Every route in ROUTES_FILE, in file order."""
    routes = []
    for line in ROUTES_FILE.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            method, raw, summary, docs = line.split("\t")
            docs = docs if docs.startswith("https://") else DOCS_URL + docs
            routes.append(Route(method, raw, summary, docs, _pattern(raw)))
    return routes


@functools.cache
def _routes() -> dict[str, list[Route]]:
    """Every route by method, fewest parameters first, so pulls/comments matches its
    own route before pulls/{pull_number}, as it does on GitHub."""
    routes: dict[str, list[Route]] = {}
    for route in _catalogue():
        routes.setdefault(route.method, []).append(route)
    for entries in routes.values():
        entries.sort(key=lambda route: (route.raw.count("{+"), route.raw.count("{")))
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
    template = next((route.template for route in routes if route.pattern.fullmatch(sent)), None)
    return (str(method), template) if template else ("unmatched", "unmatched")


def _code(document: str) -> str:
    """A GraphQL document with its strings and comments blanked, so a keyword search
    neither trips on them nor misses what a block string hides."""
    return _GRAPHQL_NOISE.sub(" ", document)


def is_mutation(document: str) -> bool:
    """True when a GraphQL document holds a mutation."""
    return _MUTATION.search(_code(document)) is not None


def deletes(document: str) -> bool:
    """True when a GraphQL document calls a field named delete... or remove..."""
    return _DELETION.search(_code(document)) is not None


class APIRoutesMixin:
    """Searches GitHub's REST route list without going near GitHub."""

    @_read_only
    async def search_api_routes(
        self,
        query: Annotated[str, "Words for what the call should do, such as 'add collaborator' or 'ruleset'"],
        method: Annotated[_Method | None, "Only routes sent with this method"] = None,
        limit: Annotated[int, "Most routes to return"] = 20,
    ) -> RouteSearchResult:
        """Finds REST routes whose summary or path holds every word of the query, each
        with its method, path, summary, docs link and the request tool that sends it.
        The routes come from GitHub's OpenAPI description. See #543."""
        words = [word.removesuffix("s") for word in query.lower().split()]
        found = [
            route
            for route in _catalogue()
            if (method is None or route.method == method)
            and all(word in f"{route.summary} {route.template}".lower() for word in words)
        ]
        return {
            "total": len(found),
            "routes": [
                {
                    "method": route.method,
                    "path": route.template,
                    "summary": route.summary,
                    "docs": route.docs,
                    "tool": _TOOL_FOR.get(route.method, "github_api_write"),
                }
                for route in found[: max(limit, 0)]
            ],
        }


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
        (
            _template(description, path, [*item.get("parameters", []), *op.get("parameters", [])]),
            method.upper(),
            " ".join(op["summary"].split()),
            op["externalDocs"]["url"].removeprefix(DOCS_URL),
        )
        for path, item in description["paths"].items()
        for method in _METHODS
        if (op := item.get(method))
    )
    header = (
        "# Every REST route on api.github.com, from GitHub's OpenAPI description.\n"
        f"# Tab-separated: method, path, summary, and the docs page under {DOCS_URL}\n"
        "# {+name} marks a parameter that spans several path segments.\n"
        "# Regenerate with: uv run python -m mcp_github.api_routes\n"
    )
    lines = "".join(f"{method}\t{raw}\t{summary}\t{docs}\n" for raw, method, summary, docs in routes)
    ROUTES_FILE.write_text(header + lines, encoding="utf-8")


if __name__ == "__main__":
    write_routes()
