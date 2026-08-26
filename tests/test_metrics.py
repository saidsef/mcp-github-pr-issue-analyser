"""Tests for the Prometheus metrics middleware and /metrics route."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
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
        assert _sample("mcp_tool_invocations_total", {"tool_name": "get_pr_diff"}) == 1.0
        assert _sample("mcp_tool_duration_seconds_count", {"tool_name": "get_pr_diff"}) == 1.0
        assert _sample("mcp_tool_in_progress", {}) == 0.0

    @pytest.mark.anyio
    async def test_failed_call_is_not_counted_but_gauge_is_released(self):
        async def call_next(_ctx):
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            await MetricsMiddleware().on_call_tool(_ctx("create_issue"), call_next)
        assert _sample("mcp_tool_invocations_total", {"tool_name": "create_issue"}) is None
        assert _sample("mcp_tool_in_progress", {}) == 0.0

    @pytest.mark.anyio
    async def test_missing_tool_name_falls_back_to_unknown(self):
        async def call_next(_ctx):
            return None

        await MetricsMiddleware().on_call_tool(SimpleNamespace(message=object()), call_next)
        assert _sample("mcp_tool_invocations_total", {"tool_name": "unknown"}) == 1.0


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
