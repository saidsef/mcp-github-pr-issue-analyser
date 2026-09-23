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

"""The skills, reachable as tools.

The server publishes every skill as a `skill://` resource. A client that speaks
only tools never issues `resources/list`, so the guidance stays invisible to it
however correctly it is served. These two tools are the path that needs no
resource support. See #414.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any, TypedDict

from .exceptions import GitHubNotFoundError
from .tool_annotations import _read_only

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent / "skills"


class SkillContent(TypedDict):
    name: str
    uri: str
    description: str
    content: str


def _skill_paths() -> list[Path]:
    """Every skill directory holding a SKILL.md, in a stable order."""
    return sorted(p for p in SKILLS_DIR.glob("*/SKILL.md") if p.is_file())


def _description(text: str) -> str:
    """The description from the YAML front matter, which is one line in every
    skill here. Returns an empty string where the file carries no front matter."""
    if not text.startswith("---"):
        return ""
    _, _, rest = text.partition("\n")
    body, _, _ = rest.partition("\n---")
    for line in body.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() == "description":
            return value.strip()
    return ""


class SkillsMixin:
    """Lists and reads the bundled skills without going near GitHub."""

    @_read_only
    async def list_skills(self) -> dict[str, Any]:
        """Lists the workflow guidance bundled with this server, each with the name
        github_get_skill takes and the skill:// URI the same content is served under. Read
        the one covering the task before starting it. See #414."""
        skills = [
            {
                "name": path.parent.name,
                "uri": f"skill://{path.parent.name}/SKILL.md",
                "description": _description(path.read_text(encoding="utf-8")),
            }
            for path in _skill_paths()
        ]
        return {"total": len(skills), "skills": skills}

    @_read_only
    async def get_skill(
        self,
        name: Annotated[str, "Skill name as github_list_skills reports it, e.g. pr-review"],
    ) -> SkillContent:
        """Reads one skill in full. The same content is served as a skill://
        resource, so a client that reads resources needs neither tool."""
        wanted = name.strip().removeprefix("skill://").removesuffix("/SKILL.md").strip("/")
        path = SKILLS_DIR / wanted / "SKILL.md"
        if "/" in wanted or "\\" in wanted or not path.is_file():
            known = ", ".join(p.parent.name for p in _skill_paths())
            raise GitHubNotFoundError(f"No skill named {name!r}. Available: {known}")
        text = path.read_text(encoding="utf-8")
        return {
            "name": wanted,
            "uri": f"skill://{wanted}/SKILL.md",
            "description": _description(text),
            "content": text,
        }
