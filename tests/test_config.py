"""Tests for environment flag parsing in issues_pr_analyser."""

import subprocess
import sys
from unittest.mock import patch

import pytest

from mcp_github.issues_pr_analyser import MCP_ENABLE_REMOTE, _env_enabled


class TestEnvEnabled:
    """Which environment variable values switch a flag on."""

    @pytest.mark.parametrize("value", ["true", "TRUE", "True", "1", "yes", "on", " true "])
    def test_recognised_values_enable(self, value):
        with patch.dict("os.environ", {"FLAG": value}):
            assert _env_enabled("FLAG") is True

    @pytest.mark.parametrize("value", ["false", "FALSE", "False", "0", "no", "off", "", "   "])
    def test_negative_values_disable(self, value):
        with patch.dict("os.environ", {"FLAG": value}):
            assert _env_enabled("FLAG") is False

    def test_unrecognised_value_disables(self):
        with patch.dict("os.environ", {"FLAG": "maybe"}):
            assert _env_enabled("FLAG") is False

    def test_unset_disables(self):
        with patch.dict("os.environ", {}, clear=True):
            assert _env_enabled("FLAG") is False


class TestMcpEnableRemote:
    """The module constant the transport and auth choices read."""

    def test_is_a_bool(self):
        assert isinstance(MCP_ENABLE_REMOTE, bool)


class TestLoggingSetup:
    """Importing the package must leave the root logger alone. See #318."""

    def test_import_does_not_configure_the_root_logger(self):
        script = (
            "import logging;"
            "import mcp_github.github_integration, mcp_github.issues_pr_analyser;"
            "print(len(logging.getLogger().handlers))"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env={"GITHUB_TOKEN": "test-token", "PATH": "/usr/bin:/bin"},
            check=True,
        )
        assert result.stdout.strip() == "0"
