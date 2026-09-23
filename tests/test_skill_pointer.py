"""Tests for the skill pointer each tool carries.

The mapping is read from the SKILL.md headings, so the guards here are what stops
a reformatted heading or a tool moved between skills from quietly emptying it.
"""

from __future__ import annotations

from contextlib import ExitStack
from typing import Any
from unittest.mock import patch

import pytest
from fastmcp import Client

from mcp_github.skill_pointer import _TOOL_HEADING, pointer, skill_owners
from mcp_github.skills_access import SKILLS_DIR, SkillsMixin

NO_SKILL = {"github_get_skill", "github_list_skills"}


def _analyser() -> Any:
    from mcp_github.issues_pr_analyser import PRIssueAnalyser

    with ExitStack() as stack:
        stack.enter_context(patch("mcp_github.github_integration.GITHUB_TOKEN", "test-token"))
        stack.enter_context(patch("mcp_github.issues_pr_analyser.MCP_ENABLE_REMOTE", False))
        stack.enter_context(patch("mcp_github.auth.REDIS_HOST_PORT", None))
        stack.enter_context(patch("mcp_github.auth.DYNAMODB_TABLE_ARN", None))
        return PRIssueAnalyser()


async def _tools() -> dict[str, Any]:
    """The served tool list, the scope gate aside, so every tool is seen."""
    tools = await _analyser().mcp.list_tools(run_middleware=False)
    return {tool.name: tool for tool in tools}


class TestTheOwnershipMap:
    def test_every_skill_named_is_a_skill_on_disk(self):
        on_disk = {path.parent.name for path in SKILLS_DIR.glob("*/SKILL.md")}

        assert set(skill_owners().values()) <= on_disk

    def test_the_map_is_not_empty(self):
        """A heading reformatted out of the pattern would empty this silently, and
        every tool would then ship with no pointer and no failure."""
        assert len(skill_owners()) > 40

    def test_no_tool_is_claimed_by_two_skills(self):
        """A second claim is dropped with a warning, so the map alone cannot show it."""
        claims = [
            tool
            for path in sorted(SKILLS_DIR.glob("*/SKILL.md"))
            for tool in _TOOL_HEADING.findall(path.read_text(encoding="utf-8"))
        ]

        assert sorted(claims) == sorted(set(claims))

    def test_the_pointer_names_the_tool_a_client_can_call(self):
        """A skill:// URI reaches only a client that reads resources."""
        assert pointer("pr-review") == "Read github_get_skill('pr-review') before calling this tool."


class TestTheServedToolList:
    @pytest.mark.anyio
    async def test_every_documented_tool_carries_its_pointer(self):
        owners = skill_owners()
        missing = {
            name: owners[name]
            for name, tool in (await _tools()).items()
            if name in owners and pointer(owners[name]) not in (tool.description or "")
        }

        assert missing == {}

    @pytest.mark.anyio
    async def test_the_two_skill_tools_point_at_no_skill(self):
        """Pointing github_get_skill at a skill would send a client in a circle."""
        tools = await _tools()

        for name in NO_SKILL:
            assert "github_get_skill('" not in (tools[name].description or "")

    @pytest.mark.anyio
    async def test_only_the_two_skill_tools_go_without_one(self):
        tools = await _tools()
        bare = {name for name, tool in tools.items() if "github_get_skill('" not in (tool.description or "")}

        assert bare == NO_SKILL
        assert len(tools) - len(bare) > 40

    @pytest.mark.anyio
    async def test_the_pointer_reaches_the_tools_fastmcp_registers_for_itself(self):
        """These three come from the Choice and GenerativeUI providers rather than
        this repo, which is why the pointer is a transform and not a description."""
        tools = await _tools()

        for name in ("choose", "github_pr_issue_analyser_ui", "github_search_prefab_components"):
            assert pointer("interactive-ui") in (tools[name].description or ""), name

    @pytest.mark.anyio
    async def test_a_writing_tool_leads_with_the_pointer(self):
        """The original text survives, and the pointer comes first."""
        description = (await _tools())["github_delete_tag"].description or ""

        assert description.startswith(pointer("release-management") + " Deletes a tag.")

    @pytest.mark.anyio
    @pytest.mark.parametrize(("name", "skill"), [("github_get_pr_diff", "pr-analysis"), ("choose", "interactive-ui")])
    async def test_a_tool_not_annotated_as_writing_ends_with_the_pointer(self, name: str, skill: str):
        description = (await _tools())[name].description or ""

        assert not description.startswith(pointer(skill))
        assert description.endswith(pointer(skill))

    @pytest.mark.anyio
    async def test_the_skill_uri_rides_along_in_meta(self):
        tools = await _tools()

        assert tools["github_merge_pr"].meta["skill"] == "skill://pr-management/SKILL.md"
        assert tools["github_add_inline_pr_comment"].meta["skill"] == "skill://pr-review/SKILL.md"

    @pytest.mark.anyio
    async def test_meta_a_tool_already_carried_survives(self):
        """The choose tool ships a ui key the renderer needs, and overwriting meta
        would take it with it."""
        meta = (await _tools())["choose"].meta

        assert meta["skill"] == "skill://interactive-ui/SKILL.md"
        assert "ui" in meta

    @pytest.mark.anyio
    async def test_the_description_and_the_meta_name_the_same_skill(self):
        disagreed = {
            name: (tool.description, tool.meta)
            for name, tool in (await _tools()).items()
            if (tool.meta or {}).get("skill")
            and pointer((tool.meta["skill"]).removeprefix("skill://").removesuffix("/SKILL.md"))
            not in (tool.description or "")
        }

        assert disagreed == {}


class TestThePointerResolves:
    @pytest.mark.anyio
    async def test_github_get_skill_accepts_every_name_a_pointer_gives(self):
        """A pointer naming a skill the tool refuses would be worse than none."""
        skills = SkillsMixin()

        for name in sorted(set(skill_owners().values())):
            assert (await skills.get_skill(name))["name"] == name

    @pytest.mark.anyio
    async def test_a_pointed_tool_still_dispatches(self):
        """The transform rewrites the listing, and a tool that no longer resolved
        by name would be a listing nobody could act on."""
        async with Client(_analyser().mcp) as client:
            result = await client.call_tool("github_list_skills", {})

        assert (result.structured_content or {})["total"] == len(list(SKILLS_DIR.glob("*/SKILL.md")))
