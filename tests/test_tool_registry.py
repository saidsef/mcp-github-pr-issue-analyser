"""The registered tool names are a contract. See #436.

A client caches the tool list when it connects, so a name that disappears breaks
callers that are already running. Nothing noticed when #430 renamed all 49 tools,
because no test reads the list as a whole. This one does.

Run this file to regenerate the snapshot after a deliberate rename:

    uv run python tests/test_tool_registry.py
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

SNAPSHOT = Path(__file__).parent / "registered_tools.txt"

PROVIDED = {"choose", "github_pr_issue_analyser_ui", "github_search_prefab_components"}


async def _registered() -> set[str]:
    """Every tool name the server registers, the provider tools aside."""
    from mcp_github.issues_pr_analyser import PRIssueAnalyser

    with ExitStack() as stack:
        stack.enter_context(patch("mcp_github.github_integration.GITHUB_TOKEN", "test-token"))
        stack.enter_context(patch("mcp_github.issues_pr_analyser.MCP_ENABLE_REMOTE", False))
        stack.enter_context(patch("mcp_github.auth.REDIS_HOST_PORT", None))
        stack.enter_context(patch("mcp_github.auth.DYNAMODB_TABLE_ARN", None))
        analyser = PRIssueAnalyser()
    tools = await analyser.mcp.list_tools(run_middleware=False)
    return {tool.name for tool in tools} - PROVIDED


def gone_missing(recorded: set[str], registered: set[str]) -> list[str]:
    """Names the snapshot holds that the server no longer registers. Additions are
    not flagged, since a growing surface breaks no existing caller."""
    return sorted(recorded - registered)


def _recorded() -> set[str]:
    lines = SNAPSHOT.read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


class TestTheComparison:
    """The rule itself, on synthetic sets, so both directions are covered without
    renaming anything for real."""

    def test_a_name_that_disappears_is_reported(self):
        assert gone_missing({"a", "b"}, {"a"}) == ["b"]

    def test_a_rename_reads_as_the_old_name_disappearing(self):
        assert gone_missing({"create_issue"}, {"github_create_issue"}) == ["create_issue"]

    def test_a_new_tool_is_not_flagged(self):
        """Adding to the surface breaks no caller, so the snapshot is a floor."""
        assert gone_missing({"a"}, {"a", "b"}) == []

    def test_an_unchanged_registry_is_quiet(self):
        assert gone_missing({"a", "b"}, {"a", "b"}) == []


class TestRegisteredTools:
    @pytest.mark.anyio
    async def test_no_tool_has_stopped_being_registered(self):
        """Renaming or removing a tool breaks a client that already holds the old
        name. Landing one has to be a decision somebody makes, not a surprise."""
        gone = sorted(_recorded() - await _registered())

        assert gone == [], (
            f"these tools are no longer registered: {', '.join(gone)}. "
            "If that was deliberate, regenerate the snapshot with "
            "`uv run python tests/test_tool_registry.py` and make sure the previous "
            "name still reaches the tool it was renamed to, per #435."
        )

    @pytest.mark.anyio
    async def test_the_check_reads_the_real_registry(self):
        """Pinned separately from the comparison, so neither half can pass on its own."""
        registered = await _registered()

        assert "github_create_issue" in registered
        assert len(registered) > 40

    @pytest.mark.anyio
    async def test_the_snapshot_is_not_empty_or_stale_beyond_recognition(self):
        """A guard that matched nothing would pass silently."""
        recorded, registered = _recorded(), await _registered()

        assert len(recorded) > 40
        assert recorded & registered


def write_snapshot() -> None:
    """Rewrite the snapshot from the server as it stands."""
    names = sorted(asyncio.run(_registered()))
    header = (
        "# Every tool this repository registers, one per line.\n"
        "# A name removed from here breaks clients holding a cached tool list.\n"
        "# Regenerate with: uv run python tests/test_tool_registry.py\n"
    )
    SNAPSHOT.write_text(header + "\n".join(names) + "\n", encoding="utf-8")
    print(f"wrote {len(names)} tool names to {SNAPSHOT.name}")


if __name__ == "__main__":
    write_snapshot()
