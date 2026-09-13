"""Tests for reaching the bundled skills as tools. See #414."""

from __future__ import annotations

import pytest

from mcp_github.exceptions import GitHubNotFoundError
from mcp_github.skills_access import SKILLS_DIR, SkillsMixin, _description


@pytest.fixture
def skills() -> SkillsMixin:
    """The mixin on its own, since neither tool touches GitHub."""
    return SkillsMixin()


class TestListSkills:
    @pytest.mark.anyio
    async def test_every_bundled_skill_is_listed(self, skills: SkillsMixin):
        listed = await skills.list_skills()
        on_disk = {path.parent.name for path in SKILLS_DIR.glob("*/SKILL.md")}

        assert {entry["name"] for entry in listed["skills"]} == on_disk
        assert listed["total"] == len(on_disk)

    @pytest.mark.anyio
    async def test_each_entry_carries_a_description_and_its_uri(self, skills: SkillsMixin):
        for entry in (await skills.list_skills())["skills"]:
            assert entry["description"], entry["name"]
            assert entry["uri"] == f"skill://{entry['name']}/SKILL.md"

    @pytest.mark.anyio
    async def test_the_order_is_stable(self, skills: SkillsMixin):
        names = [entry["name"] for entry in (await skills.list_skills())["skills"]]

        assert names == sorted(names)

    def test_is_read_only(self, skills: SkillsMixin):
        assert skills.list_skills._mcp_annotations.read_only_hint is True


class TestGetSkill:
    @pytest.mark.anyio
    async def test_reads_a_skill_in_full(self, skills: SkillsMixin):
        result = await skills.get_skill("pr-review")

        assert result["name"] == "pr-review"
        assert result["uri"] == "skill://pr-review/SKILL.md"
        assert result["content"] == (SKILLS_DIR / "pr-review" / "SKILL.md").read_text(encoding="utf-8")

    @pytest.mark.anyio
    async def test_a_skill_uri_is_accepted_as_the_name(self, skills: SkillsMixin):
        """A client that saw the resource listing first will have the URI, not the name."""
        assert (await skills.get_skill("skill://pr-review/SKILL.md"))["name"] == "pr-review"

    @pytest.mark.anyio
    async def test_an_unknown_name_lists_the_ones_that_exist(self, skills: SkillsMixin):
        with pytest.raises(GitHubNotFoundError, match="pr-review"):
            await skills.get_skill("no-such-skill")

    @pytest.mark.anyio
    @pytest.mark.parametrize("name", ["../auth", "pr-review/../../auth", "/etc/passwd", ""])
    async def test_a_name_cannot_walk_out_of_the_skills_tree(self, skills: SkillsMixin, name: str):
        with pytest.raises(GitHubNotFoundError):
            await skills.get_skill(name)

    def test_is_read_only(self, skills: SkillsMixin):
        assert skills.get_skill._mcp_annotations.read_only_hint is True


class TestFrontMatter:
    def test_reads_the_description(self):
        assert _description("---\ndescription: Do a thing\n---\n\n# Title\n") == "Do a thing"

    def test_a_file_without_front_matter_has_no_description(self):
        assert _description("# Title\n") == ""

    def test_a_colon_in_the_description_survives(self):
        assert _description("---\ndescription: Read this: carefully\n---\n") == "Read this: carefully"
