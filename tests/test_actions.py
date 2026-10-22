"""Tests for re-running an Actions job and dispatching a workflow."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from fastmcp.exceptions import ToolError

from mcp_github.exceptions import GitHubValidationError
from mcp_github.github_integration import GitHubIntegration
from tests.support import mock_response


class TestRerunJob:
    @pytest.mark.anyio
    async def test_posts_the_debug_flag_and_reads_no_body(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(status_code=201))
        gi._http.request.return_value.json.side_effect = ValueError("no body")
        result = await gi.rerun_job("o", "r", 4242, enable_debug_logging=True)
        call = gi._http.request.call_args
        assert call.args == ("POST", "https://api.github.com/repos/o/r/actions/jobs/4242/rerun")
        assert call.kwargs["json"] == {"enable_debug_logging": True}
        assert result == {"job_id": 4242, "status": "requested"}

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


class TestDispatchWorkflow:
    _RUN = {
        "workflow_run_id": 9001,
        "run_url": "https://api.github.com/repos/o/r/actions/runs/9001",
        "html_url": "https://github.com/o/r/actions/runs/9001",
    }

    @pytest.mark.anyio
    async def test_sends_ref_and_inputs_and_returns_the_run(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock(return_value=mock_response(json_data=self._RUN))
        result = await gi.dispatch_workflow("o", "r", "ci.yml", "main", inputs={"target": "staging"})
        call = gi._http.request.call_args
        assert call.args == ("POST", "https://api.github.com/repos/o/r/actions/workflows/ci.yml/dispatches")
        assert call.kwargs["json"] == {"ref": "main", "return_run_details": True, "inputs": {"target": "staging"}}
        assert result == self._RUN

    @pytest.mark.anyio
    async def test_twenty_six_inputs_are_refused_before_any_request(self, gi: GitHubIntegration):
        gi._http.request = AsyncMock()
        with pytest.raises(GitHubValidationError, match="at most 25 inputs, and 26 were passed"):
            await gi.dispatch_workflow("o", "r", "ci.yml", "main", inputs={f"i{n}": "x" for n in range(26)})
        gi._http.request.assert_not_called()
