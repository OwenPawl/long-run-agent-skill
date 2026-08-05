from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from long_run_agent_skill.mcp_runtime import (
    HarnessRunner,
    HarnessSettings,
    control_snapshot,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]


def settings() -> HarnessSettings:
    return HarnessSettings(
        python=__import__("sys").executable,
        harness_script=ROOT / "scripts" / "mission_harness.py",
        reveal_script=ROOT / "scripts" / "reveal_live_file.py",
    )


class MCPRuntimeTests(unittest.TestCase):
    def test_harness_runner_never_inherits_mcp_protocol_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "long_run_agent_skill.mcp_runtime.subprocess.run"
        ) as run:
            run.return_value = subprocess.CompletedProcess([], 0, "{}", "")
            HarnessRunner(settings()).run(tmp, ["validate"])
            self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_runner_initializes_and_validates_real_harness_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = HarnessRunner(settings())
            initialized = runner.run(tmp, ["init"])
            validated = runner.run(tmp, ["validate"])

            self.assertEqual(initialized["status"], "success")
            self.assertEqual(validated["status"], "success")
            self.assertTrue((pathlib.Path(tmp) / ".agent" / "live.md").is_file())

    def test_control_snapshot_omits_unchanged_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = HarnessRunner(settings())
            runner.run(tmp, ["init"])

            first = control_snapshot(tmp)
            second = control_snapshot(tmp, first["data"]["sha256"])

            self.assertTrue(first["data"]["changed"])
            self.assertIn("# Live Control", first["data"]["content"])
            self.assertFalse(second["data"]["changed"])
            self.assertEqual(second["data"]["content"], "")

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "MCP SDK is not installed")
    def test_server_registers_namespaced_mission_tools(self) -> None:
        from long_run_agent_skill.mcp_tools import register_mission_tools
        from mcp.server.fastmcp import FastMCP

        server = FastMCP("test")
        names = register_mission_tools(server, settings())
        registered = {tool.name for tool in server._tool_manager.list_tools()}

        self.assertEqual(set(names), registered)
        self.assertIn("mission_run_start", registered)
        self.assertIn("mission_control_read", registered)
        self.assertIn("mission_index_search", registered)

    def test_pyproject_exposes_stdio_entrypoint_and_sdk_pin(self) -> None:
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('long-run-agent-mcp = "long_run_agent_skill.mcp_server:main"', text)
        self.assertIn('"mcp>=1.29,<2"', text)

    def test_json_envelope_is_portable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = HarnessRunner(settings()).run(tmp, ["init"])
            encoded = json.dumps(result)
            self.assertEqual(json.loads(encoded)["status"], "success")
