"""Tests for re-running an Actions job."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from fastmcp.exceptions import ToolError

from mcp_github.github_integration import GitHubIntegration
from tests.support import mock_response


class TestRerunJob:
    @pytest.mark.anyio
    async def test_posts_the_debug_flag_and_reads_no_body(self, gi: GitHubIntegration):
        posted = mock_response(status_code=201)
        posted.json.side_effect = ValueError("no body")
        gi._http.request = AsyncMock(side_effect=[mock_response(json_data={"id": 4242, "run_id": 9001}), posted])
        result = await gi.rerun_job("o", "r", 4242, enable_debug_logging=True)
        call = gi._http.request.call_args
        assert call.args == ("POST", "https://api.github.com/repos/o/r/actions/jobs/4242/rerun")
        assert call.kwargs["json"] == {"enable_debug_logging": True}
        assert result == {"job_id": 4242, "workflow_run_id": 9001, "status": "requested"}

    @pytest.mark.anyio
    async def test_a_403_names_the_permission_github_wants(self, gi: GitHubIntegration):
        body = {"message": "Resource not accessible by personal access token"}
        gi._http.request = AsyncMock(
            return_value=mock_response(
                status_code=403,
                json_data=body,
                text=json.dumps(body),
                headers={"X-Accepted-GitHub-Permissions": "actions=write"},
            )
        )
        with pytest.raises(ToolError, match=r"Refused\. This endpoint needs the `actions=write` permission"):
            await gi.rerun_job("o", "r", 4242)
