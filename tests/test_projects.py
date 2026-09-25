"""Tests for the project board tools and the GraphQL errors they meet."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from mcp_github.exceptions import GitHubAPIError, GitHubAuthError, GitHubNotFoundError, GitHubValidationError
from mcp_github.github_integration import GitHubIntegration
from mcp_github.graphql_client import handle_graphql_errors

_NO_PROJECT: dict = {"repositoryOwner": {}}


def _project() -> dict:
    """A board with one plain field and one single select. Built per call so one
    test cannot mutate what the next one reads."""
    return {
        "id": "PVT_1",
        "number": 4,
        "title": "Backlog",
        "url": "https://github.com/users/o/projects/4",
        "fields": {
            "nodes": [
                {"id": "F_title", "name": "Title", "dataType": "TITLE"},
                {
                    "id": "F_status",
                    "name": "Status",
                    "dataType": "SINGLE_SELECT",
                    "options": [{"id": "opt_todo", "name": "Todo"}, {"id": "opt_doing", "name": "In Progress"}],
                },
                {},
            ]
        },
    }


def _owner(project: dict | None = None) -> dict:
    """A repositoryOwner payload, as either inline fragment resolves into one shape."""
    return {"repositoryOwner": {"projectV2": project if project is not None else _project()}}


def _issue_node(items: list[dict] | None = None) -> dict:
    return {
        "repository": {
            "issueOrPullRequest": {
                "id": "I_1",
                "number": 12,
                "title": "A bug",
                "url": "https://github.com/o/r/issues/12",
                "projectItems": {"nodes": items if items is not None else []},
            }
        }
    }


class TestProjectResolution:
    @pytest.mark.anyio
    async def test_a_project_is_looked_up_by_owner_and_number(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(return_value=_owner())
        result = await gi.get_project_fields("o", 4)
        query, variables = gi._execute_graphql.call_args.args
        assert "repositoryOwner" in query
        assert variables == {"owner": "o", "number": 4}
        assert result["title"] == "Backlog"

    @pytest.mark.anyio
    async def test_a_missing_project_names_what_was_not_found(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(return_value=_NO_PROJECT)
        with pytest.raises(GitHubNotFoundError, match="No project #4 for 'o'"):
            await gi.get_project_fields("o", 4)

    @pytest.mark.anyio
    async def test_an_invisible_project_points_at_the_scope(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(return_value=_NO_PROJECT)
        with pytest.raises(GitHubNotFoundError, match="read:project"):
            await gi.get_project_fields("o", 4)

    @pytest.mark.anyio
    async def test_fields_list_their_options(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(return_value=_owner())
        fields = (await gi.get_project_fields("o", 4))["fields"]
        assert [f["name"] for f in fields] == ["Title", "Status"]
        assert fields[1]["options"] == ["Todo", "In Progress"]
        assert fields[0]["options"] == []


class TestAddToProject:
    @pytest.mark.anyio
    async def test_the_content_node_id_is_what_is_added(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(
            side_effect=[_owner(), _issue_node(), {"addProjectV2ItemById": {"item": {"id": "PVTI_9"}}}]
        )
        result = await gi.add_to_project("o", 4, "o", "r", 12)
        assert gi._execute_graphql.call_args.args[1] == {"projectId": "PVT_1", "contentId": "I_1"}
        assert result["item_id"] == "PVTI_9"
        assert result["project_title"] == "Backlog"

    @pytest.mark.anyio
    async def test_a_missing_issue_is_named(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(side_effect=[_owner(), {"repository": {"issueOrPullRequest": None}}])
        with pytest.raises(GitHubNotFoundError, match="#12 in o/r"):
            await gi.add_to_project("o", 4, "o", "r", 12)

    @pytest.mark.anyio
    async def test_a_mutation_returning_no_item_is_an_error(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(side_effect=[_owner(), _issue_node(), {"addProjectV2ItemById": {}}])
        with pytest.raises(GitHubAPIError, match="no item id"):
            await gi.add_to_project("o", 4, "o", "r", 12)


class TestSetProjectField:
    @pytest.mark.anyio
    async def test_field_and_option_names_resolve_to_ids(self, gi: GitHubIntegration):
        on_board = [{"id": "PVTI_9", "project": {"id": "PVT_1", "number": 4}}]
        gi._execute_graphql = AsyncMock(side_effect=[_owner(), _issue_node(on_board), {}])
        result = await gi.set_project_field("o", 4, "o", "r", 12, "Status", "In Progress")
        assert gi._execute_graphql.call_args.args[1] == {
            "projectId": "PVT_1",
            "itemId": "PVTI_9",
            "fieldId": "F_status",
            "optionId": "opt_doing",
        }
        assert result["option"] == "In Progress"

    @pytest.mark.anyio
    async def test_names_are_matched_regardless_of_case(self, gi: GitHubIntegration):
        on_board = [{"id": "PVTI_9", "project": {"id": "PVT_1", "number": 4}}]
        gi._execute_graphql = AsyncMock(side_effect=[_owner(), _issue_node(on_board), {}])
        await gi.set_project_field("o", 4, "o", "r", 12, "status", "in progress")
        assert gi._execute_graphql.call_args.args[1]["optionId"] == "opt_doing"

    @pytest.mark.anyio
    async def test_an_issue_not_on_the_board_is_added_first(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(
            side_effect=[_owner(), _issue_node(), {"addProjectV2ItemById": {"item": {"id": "PVTI_new"}}}, {}]
        )
        result = await gi.set_project_field("o", 4, "o", "r", 12, "Status", "Todo")
        assert "addProjectV2ItemById" in gi._execute_graphql.call_args_list[2].args[0]
        assert result["item_id"] == "PVTI_new"

    @pytest.mark.anyio
    async def test_an_item_already_on_the_board_is_not_added_again(self, gi: GitHubIntegration):
        on_board = [{"id": "PVTI_9", "project": {"id": "PVT_1", "number": 4}}]
        gi._execute_graphql = AsyncMock(side_effect=[_owner(), _issue_node(on_board), {}])
        await gi.set_project_field("o", 4, "o", "r", 12, "Status", "Todo")
        assert all("addProjectV2ItemById" not in c.args[0] for c in gi._execute_graphql.call_args_list)

    @pytest.mark.anyio
    async def test_an_item_on_another_board_does_not_count(self, gi: GitHubIntegration):
        elsewhere = [{"id": "PVTI_other", "project": {"id": "PVT_2", "number": 7}}]
        gi._execute_graphql = AsyncMock(
            side_effect=[_owner(), _issue_node(elsewhere), {"addProjectV2ItemById": {"item": {"id": "PVTI_new"}}}, {}]
        )
        result = await gi.set_project_field("o", 4, "o", "r", 12, "Status", "Todo")
        assert result["item_id"] == "PVTI_new"

    @pytest.mark.anyio
    async def test_an_unknown_field_lists_the_ones_there(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(return_value=_owner())
        with pytest.raises(GitHubNotFoundError, match="No field named 'Stage'.*Status, Title"):
            await gi.set_project_field("o", 4, "o", "r", 12, "Stage", "Todo")

    @pytest.mark.anyio
    async def test_an_unknown_option_lists_the_ones_there(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(return_value=_owner())
        with pytest.raises(GitHubNotFoundError, match="No option named 'Blocked'.*Todo, In Progress"):
            await gi.set_project_field("o", 4, "o", "r", 12, "Status", "Blocked")

    @pytest.mark.anyio
    async def test_a_field_that_is_not_a_select_is_rejected(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(return_value=_owner())
        with pytest.raises(GitHubValidationError, match="TITLE field"):
            await gi.set_project_field("o", 4, "o", "r", 12, "Title", "Todo")

    @pytest.mark.anyio
    async def test_a_bad_name_fails_before_the_issue_is_looked_up(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(return_value=_owner())
        with pytest.raises(GitHubNotFoundError):
            await gi.set_project_field("o", 4, "o", "r", 12, "Stage", "Todo")
        assert gi._execute_graphql.call_count == 1


class TestRemoveFromProject:
    @pytest.mark.anyio
    async def test_the_item_on_that_board_is_the_one_deleted(self, gi: GitHubIntegration):
        on_board = [
            {"id": "PVTI_other", "project": {"id": "PVT_2", "number": 7}},
            {"id": "PVTI_9", "project": {"id": "PVT_1", "number": 4}},
        ]
        gi._execute_graphql = AsyncMock(
            side_effect=[_owner(), _issue_node(on_board), {"deleteProjectV2Item": {"deletedItemId": "PVTI_9"}}]
        )
        result = await gi.remove_from_project("o", 4, "o", "r", 12)
        assert gi._execute_graphql.call_args.args[1] == {"projectId": "PVT_1", "itemId": "PVTI_9"}
        assert result == {"status": "removed", "item_id": "PVTI_9", "project_number": 4, "issue_number": 12}

    @pytest.mark.anyio
    async def test_an_issue_not_on_the_board_is_not_added_to_delete_it(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(side_effect=[_owner(), _issue_node()])
        with pytest.raises(GitHubNotFoundError, match="is not on project #4"):
            await gi.remove_from_project("o", 4, "o", "r", 12)
        assert gi._execute_graphql.call_count == 2


class TestListProjectItems:
    @staticmethod
    def _items(nodes: list[dict], has_next: bool = False) -> dict:
        return _owner(
            {
                "id": "PVT_1",
                "number": 4,
                "title": "Backlog",
                "items": {
                    "totalCount": len(nodes),
                    "pageInfo": {"hasNextPage": has_next, "endCursor": "cur"},
                    "nodes": nodes,
                },
            }
        )

    @pytest.mark.anyio
    async def test_an_item_reports_its_content_and_field_values(self, gi: GitHubIntegration):
        node = {
            "id": "PVTI_9",
            "type": "ISSUE",
            "content": {
                "number": 12,
                "title": "A bug",
                "state": "OPEN",
                "url": "https://github.com/o/r/issues/12",
                "repository": {"nameWithOwner": "o/r"},
            },
            "fieldValues": {
                "nodes": [
                    {},
                    {"name": "In Progress", "field": {"name": "Status"}},
                    {"number": 3.0, "field": {"name": "Size"}},
                    {"text": "note", "field": {"name": "Notes"}},
                ]
            },
        }
        gi._execute_graphql = AsyncMock(return_value=self._items([node]))
        result = await gi.list_project_items("o", 4)
        assert result["items"] == [
            {
                "item_id": "PVTI_9",
                "type": "ISSUE",
                "number": 12,
                "title": "A bug",
                "state": "OPEN",
                "url": "https://github.com/o/r/issues/12",
                "repository": "o/r",
                "fields": {"Status": "In Progress", "Size": 3.0, "Notes": "note"},
            }
        ]

    @pytest.mark.anyio
    async def test_paging_arguments_reach_the_query(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(return_value=self._items([]))
        await gi.list_project_items("o", 4, per_page=10, after="prev")
        assert gi._execute_graphql.call_args.args[1] == {"owner": "o", "number": 4, "first": 10, "after": "prev"}

    @pytest.mark.anyio
    async def test_a_cursor_comes_back_only_when_there_is_another_page(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(return_value=self._items([], has_next=True))
        assert (await gi.list_project_items("o", 4))["next_cursor"] == "cur"
        gi._execute_graphql = AsyncMock(return_value=self._items([]))
        assert (await gi.list_project_items("o", 4))["next_cursor"] is None

    @pytest.mark.anyio
    async def test_a_draft_item_carries_a_title_and_no_number(self, gi: GitHubIntegration):
        node = {"id": "PVTI_d", "type": "DRAFT_ISSUE", "content": {"title": "Think about it"}}
        gi._execute_graphql = AsyncMock(return_value=self._items([node]))
        item = (await gi.list_project_items("o", 4))["items"][0]
        assert item["title"] == "Think about it"
        assert item["number"] is None
        assert item["fields"] == {}


class TestListProjects:
    @staticmethod
    def _boards(nodes: list[dict], has_next: bool = False) -> dict:
        return {
            "repositoryOwner": {
                "projectsV2": {
                    "totalCount": len(nodes),
                    "pageInfo": {"hasNextPage": has_next, "endCursor": "cur"},
                    "nodes": nodes,
                }
            }
        }

    @pytest.mark.anyio
    async def test_each_board_reports_its_number_title_state_and_url(self, gi: GitHubIntegration):
        nodes = [
            {"number": 4, "title": "Backlog", "closed": False, "url": "https://github.com/users/o/projects/4"},
            {"number": 2, "title": "Old", "closed": True, "url": "https://github.com/users/o/projects/2"},
        ]
        gi._execute_graphql = AsyncMock(return_value=self._boards(nodes))
        result = await gi.list_projects("o")
        query, variables = gi._execute_graphql.call_args.args
        assert "repositoryOwner" in query
        assert variables == {"owner": "o", "first": 50, "after": None}
        assert result["projects"] == [
            {"number": 4, "title": "Backlog", "state": "open", "url": "https://github.com/users/o/projects/4"},
            {"number": 2, "title": "Old", "state": "closed", "url": "https://github.com/users/o/projects/2"},
        ]
        assert result["project_owner"] == "o"
        assert result["total"] == 2
        assert result["count"] == 2

    @pytest.mark.anyio
    async def test_paging_arguments_reach_the_query(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(return_value=self._boards([]))
        await gi.list_projects("o", per_page=10, after="prev")
        assert gi._execute_graphql.call_args.args[1] == {"owner": "o", "first": 10, "after": "prev"}

    @pytest.mark.anyio
    async def test_a_cursor_comes_back_only_when_there_is_another_page(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(return_value=self._boards([], has_next=True))
        result = await gi.list_projects("o")
        assert result["has_more"] is True
        assert result["next_cursor"] == "cur"
        gi._execute_graphql = AsyncMock(return_value=self._boards([]))
        result = await gi.list_projects("o")
        assert result["has_more"] is False
        assert result["next_cursor"] is None

    @pytest.mark.anyio
    async def test_an_unknown_owner_is_named(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(return_value={"repositoryOwner": None})
        with pytest.raises(GitHubNotFoundError, match="No user or organisation named 'nobody'"):
            await gi.list_projects("nobody")

    @pytest.mark.anyio
    async def test_an_owner_with_no_boards_is_an_empty_list(self, gi: GitHubIntegration):
        gi._execute_graphql = AsyncMock(return_value=self._boards([]))
        result = await gi.list_projects("o")
        assert result["projects"] == []
        assert result["total"] == 0

    @pytest.mark.anyio
    async def test_a_missing_scope_keeps_its_hint(self, gi: GitHubIntegration):
        errors = [{"type": "INSUFFICIENT_SCOPES", "message": "The 'projectsV2' field requires ['read:project']."}]
        gi._execute_graphql = AsyncMock(side_effect=lambda *_: handle_graphql_errors(errors))
        with pytest.raises(GitHubAuthError, match="read:project"):
            await gi.list_projects("o")


class TestGraphQLScopeErrors:
    def test_a_missing_scope_is_an_auth_error_naming_the_scope(self):
        errors = [
            {
                "type": "INSUFFICIENT_SCOPES",
                "message": "The 'id' field requires one of the following scopes: ['read:project'].",
            }
        ]
        with pytest.raises(GitHubAuthError, match="read:project"):
            handle_graphql_errors(errors)

    def test_a_scope_error_does_not_advise_re_authenticating(self):
        errors = [{"type": "INSUFFICIENT_SCOPES", "message": "Missing scope."}]
        with pytest.raises(GitHubAuthError) as caught:
            handle_graphql_errors(errors)
        assert "re-authenticate" not in str(caught.value)

    def test_a_forbidden_error_still_advises_re_authenticating(self):
        with pytest.raises(GitHubAuthError, match="re-authenticate"):
            handle_graphql_errors([{"type": "FORBIDDEN", "message": "Nope."}])

    @pytest.mark.anyio
    async def test_guard_lets_an_auth_error_keep_its_class(self, gi: GitHubIntegration):
        with pytest.raises(GitHubAuthError):  # noqa: PT012 - the guard is what is under test
            async with gi._guard("do a thing"):
                raise GitHubAuthError("Missing scope.")
