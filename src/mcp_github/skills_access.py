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

from fastmcp.skills import Skill

from .exceptions import GitHubNotFoundError
from .tool_annotations import _read_only

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent / "skills"

# Read once at import, so a SKILL.md that breaks the Agent Skills spec stops the
# server at startup rather than on the first read.
SKILLS: dict[str, Skill] = {
    path.parent.name: Skill.from_directory(path.parent) for path in sorted(SKILLS_DIR.glob("*/SKILL.md"))
}


class SkillContent(TypedDict):
    name: str
    uri: str
    description: str
    content: str


def _skill_md(skill: Skill) -> str:
    """The SKILL.md text, front matter included."""
    return skill.files["SKILL.md"].decode("utf-8")


class SkillsMixin:
    """Lists and reads the bundled skills without going near GitHub."""

    @_read_only
    async def list_skills(self) -> dict[str, Any]:
        """Lists the workflow guidance bundled with this server, each with the name
        github_get_skill takes and the skill:// URI the same content is served under. Read
        the one covering the task before starting it. See #414."""
        skills = [
            {"name": name, "uri": f"skill://{name}/SKILL.md", "description": skill.description}
            for name, skill in SKILLS.items()
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
        skill = SKILLS.get(wanted)
        if skill is None:
            raise GitHubNotFoundError(f"No skill named {name!r}. Available: {', '.join(SKILLS)}")
        return {
            "name": wanted,
            "uri": f"skill://{wanted}/SKILL.md",
            "description": skill.description,
            "content": _skill_md(skill),
        }
