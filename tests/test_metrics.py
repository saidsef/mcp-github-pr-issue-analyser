"""Tests for the Prometheus metrics middleware and /metrics route."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastmcp.exceptions import NotFoundError
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY
from starlette.testclient import TestClient

from mcp_github.issues_pr_analyser import MetricsMiddleware, PRIssueAnalyser


def _ctx(name: str) -> SimpleNamespace:
    return SimpleNamespace(message=SimpleNamespace(name=name))


def _sample(name: str, labels: dict[str, str]) -> float | None:
    return REGISTRY.get_sample_value(name, labels)


class TestMetricsMiddleware:
    """Counter, histogram and in-progress gauge behaviour."""

    @pytest.mark.anyio
    async def test_successful_call_is_counted_and_timed(self):
        async def call_next(_ctx):
            return "result"

        assert await MetricsMiddleware().on_call_tool(_ctx("get_pr_diff"), call_next) == "result"
        labels = {"tool_name": "get_pr_diff", "outcome": "success"}
        assert _sample("mcp_tool_invocations_total", labels) == 1.0
        assert _sample("mcp_tool_duration_seconds_count", labels) == 1.0
        assert _sample("mcp_tool_in_progress", {}) == 0.0

    @pytest.mark.anyio
    async def test_failed_call_is_counted_as_an_error(self):
        async def call_next(_ctx):
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            await MetricsMiddleware().on_call_tool(_ctx("create_issue"), call_next)
        labels = {"tool_name": "create_issue", "outcome": "error"}
        assert _sample("mcp_tool_invocations_total", labels) == 1.0
        assert _sample("mcp_tool_duration_seconds_count", labels) == 1.0
        assert _sample("mcp_tool_invocations_total", {"tool_name": "create_issue", "outcome": "success"}) is None
        assert _sample("mcp_tool_in_progress", {}) == 0.0

    @pytest.mark.anyio
    async def test_unknown_tool_name_is_not_given_its_own_series(self):
        async def call_next(_ctx):
            raise NotFoundError("Unknown tool: 'made_up_tool'")

        with pytest.raises(NotFoundError):
            await MetricsMiddleware().on_call_tool(_ctx("made_up_tool"), call_next)
        assert _sample("mcp_tool_invocations_total", {"tool_name": "made_up_tool", "outcome": "error"}) is None
        assert _sample("mcp_tool_invocations_total", {"tool_name": "unknown", "outcome": "error"}) == 1.0
        assert _sample("mcp_tool_in_progress", {}) == 0.0

    @pytest.mark.anyio
    async def test_missing_tool_name_falls_back_to_unknown(self):
        async def call_next(_ctx):
            return None

        await MetricsMiddleware().on_call_tool(SimpleNamespace(message=object()), call_next)
        assert _sample("mcp_tool_invocations_total", {"tool_name": "unknown", "outcome": "success"}) == 1.0


class TestMetricsRoute:
    """The /metrics endpoint served alongside /mcp in HTTP mode."""

    def test_serves_prometheus_exposition(self):
        with patch("mcp_github.github_integration.GITHUB_TOKEN", "test-token"):
            app = PRIssueAnalyser().mcp.http_app(transport="http", stateless_http=True)
        with TestClient(app) as client:
            response = client.get("/metrics")

        assert response.status_code == 200
        assert response.headers["content-type"] == CONTENT_TYPE_LATEST
        assert "mcp_tool_in_progress" in response.text
        assert "python_info" in response.text
