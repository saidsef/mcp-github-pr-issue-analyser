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

"""The skill each tool belongs to, carried on the tool itself.

A client reads a tool's description at the moment it decides to call it. Until
now nothing in that payload said a skill covering the call existed: the pointer
lived only in the server instructions, which a client is free to ignore. This
transform puts it on every tool a skill documents.

The mapping is read from the SKILL.md files rather than declared beside each
tool, so the skill stays the one place a tool is claimed and the two cannot
drift apart.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from fastmcp.server.transforms import Transform
from fastmcp.tools import Tool

from .skills_access import _skill_paths

logger = logging.getLogger(__name__)

_TOOL_HEADING = re.compile(r"^#{2,3} `([a-z0-9_]+)`\s*$", re.M)


def skill_owners() -> dict[str, str]:
    """The skill documenting each tool, keyed by registered tool name, read from the
    `tool_name` headings in every SKILL.md. A tool claimed twice keeps the first
    skill in path order and is logged, since picking one beats refusing to start."""
    owners: dict[str, str] = {}
    for path in _skill_paths():
        skill = path.parent.name
        for tool in _TOOL_HEADING.findall(path.read_text(encoding="utf-8")):
            if tool in owners:
                logger.warning(f"Tool {tool} is documented by {owners[tool]} and {skill}; keeping {owners[tool]}")
                continue
            owners[tool] = skill
    return owners


def pointer(skill: str) -> str:
    """The line carried on a tool's description. Names the tool rather than the
    skill:// URI, since every client has tools and only some read resources."""
    return f"Read github_get_skill('{skill}') before calling this tool."


class SkillPointer(Transform):
    """Names the documenting skill on each tool's description and records it under
    the skill key of the tool's meta. A tool annotated as writing leads with the
    pointer, since a footer under a long schema is what a client skims past, and any
    other tool keeps it at the end. Server-level transforms run after the providers
    are aggregated, so the tools FastMCP registers for itself are covered too."""

    def __init__(self) -> None:
        self.owners = skill_owners()

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [self._point(tool) for tool in tools]

    def _point(self, tool: Tool) -> Tool:
        skill = self.owners.get(tool.name)
        if skill is None:
            return tool
        line = pointer(skill)
        writes = bool(tool.annotations) and not tool.annotations.read_only_hint
        if not tool.description:
            description = line
        elif writes:
            description = f"{line} {tool.description}"
        else:
            description = f"{tool.description}\n\n{line}"
        return tool.model_copy(
            update={
                "description": description,
                "meta": {**(tool.meta or {}), "skill": f"skill://{skill}/SKILL.md"},
            }
        )
