from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from long_run_agent_skill.mcp_tools import register_mission_tools


ROOT = Path(__file__).resolve().parents[1]


class FakeServer:
    def __init__(self) -> None:
        self.handlers = {}

    def tool(self, *, name: str):
        def register(handler):
            self.handlers[name] = handler
            return handler

        return register


class InterfaceAndPackageTests(unittest.TestCase):
    def run_cli(self, root: Path, *args: str) -> dict:
        result = subprocess.run(
            [sys.executable, "-m", "long_run_agent_skill", "--root", str(root), *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(result.stdout)

    def test_cli_exposes_semantic_surface_and_write_partitions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(self.run_cli(root, "init", "--goal", "CLI mission")["status"], "success")
            self.run_cli(root, "start", "--goal", "CLI mission", "--run-id", "run_cli")
            observed = self.run_cli(
                root,
                "observe",
                "evidence",
                "--data",
                json.dumps(
                    {
                        "id": "cli_evidence",
                        "annotation": {
                            "subject": "CLI observation",
                            "predicate": "records semantic input",
                        },
                    }
                ),
            )
            self.assertEqual(
                set(observed),
                {
                    "status",
                    "committed",
                    "derived",
                    "diagnostics",
                    "suggested",
                    "recall",
                    "telemetry",
                    "affected_questions",
                    "affected_decisions",
                },
            )
            self.run_cli(
                root,
                "assert",
                "--data",
                json.dumps(
                    {
                        "id": "cli_claim",
                        "annotation": {
                            "subject": "CLI",
                            "predicate": "records semantic claims",
                        },
                        "proposition": "The CLI records semantic claims.",
                        "premises": [{"type": "evidence", "id": "cli_evidence"}],
                        "warrant": {"statement": "The CLI observation warrants this claim."},
                        "argument_id": "cli_argument",
                        "argument_annotation": {
                            "subject": "CLI observation",
                            "predicate": "warrants the semantic claim",
                        },
                    }
                ),
            )
            inspected = self.run_cli(root, "inspect", "cli_claim")
            self.assertIn("supported", inspected["nodes"]["cli_claim"]["state"])
            searched = self.run_cli(root, "search", "semantic claims")
            self.assertEqual(searched["results"][0]["entity_id"], "cli_claim")
            self.assertEqual(self.run_cli(root, "validate")["status"], "success")

    def test_mcp_and_cli_have_matching_semantic_operations(self) -> None:
        server = FakeServer()
        names = set(register_mission_tools(server))
        expected = {
            "mission_init",
            "mission_start",
            "mission_context",
            "mission_observe",
            "mission_assert",
            "mission_assumption",
            "mission_argument",
            "mission_attack",
            "mission_ask",
            "mission_decide",
            "mission_update",
            "mission_search",
            "mission_inspect",
            "mission_feedback",
            "mission_checkpoint",
            "mission_resume",
            "mission_close",
            "mission_rebuild",
            "mission_validate",
            "mission_control_read",
        }
        self.assertEqual(names, expected)
        with tempfile.TemporaryDirectory() as tmp:
            initialized = server.handlers["mission_init"](tmp, "MCP mission")
            started = server.handlers["mission_start"](tmp, "MCP mission", run_id="run_mcp")
            context = server.handlers["mission_context"](tmp)
            self.assertEqual(initialized["status"], "success")
            self.assertEqual(started["status"], "success")
            self.assertEqual(context["data"]["schema_version"], "worker-context.v1")
            self.assertNotIn("mission_query", server.handlers)
            self.assertNotIn("mission_expand", server.handlers)

    def test_cli_and_mcp_search_inspect_behavior_match(self) -> None:
        server = FakeServer()
        register_mission_tools(server)
        with tempfile.TemporaryDirectory() as tmp:
            cli_root = Path(tmp) / "cli"
            mcp_root = Path(tmp) / "mcp"
            for root in (cli_root, mcp_root):
                if root == cli_root:
                    self.run_cli(root, "init", "--goal", "Parity mission")
                    self.run_cli(
                        root,
                        "start",
                        "--goal",
                        "Parity mission",
                        "--run-id",
                        "run_parity",
                    )
                else:
                    server.handlers["mission_init"](str(root), "Parity mission")
                    server.handlers["mission_start"](
                        str(root), "Parity mission", run_id="run_parity"
                    )
            delta = {
                "operations": [
                    {
                        "type": "evidence.registered",
                        "data": {
                            "id": "parity_evidence",
                            "annotation": {
                                "subject": "Parity trace",
                                "predicate": "records matching behavior",
                            },
                            "provenance_refs": [
                                {"type": "tool_event", "id": "T-parity"}
                            ],
                        },
                    }
                ]
            }
            self.run_cli(
                cli_root, "update", "--data", json.dumps(delta, sort_keys=True)
            )
            mcp_update = server.handlers["mission_update"](str(mcp_root), delta)
            self.assertEqual(mcp_update["status"], "success")

            cli_search = self.run_cli(cli_root, "search", "Parity T-parity")
            mcp_search = server.handlers["mission_search"](
                str(mcp_root), "Parity T-parity"
            )["data"]
            self.assertEqual(cli_search["results"], mcp_search["results"])
            cli_inspect = self.run_cli(cli_root, "inspect", "parity_evidence")
            mcp_inspect = server.handlers["mission_inspect"](
                str(mcp_root), "parity_evidence"
            )["data"]
            for field in ("nodes", "relations", "paths", "provenance"):
                self.assertEqual(cli_inspect.get(field), mcp_inspect.get(field))

    def test_cli_does_not_expose_removed_query_or_expand_commands(self) -> None:
        for command in ("query", "expand"):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as tmp:
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "long_run_agent_skill",
                        "--root",
                        tmp,
                        command,
                    ],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(completed.returncode, 2)
                self.assertIn("invalid choice", completed.stderr)

    def test_editable_package_install_and_entrypoints_work_without_source_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "site"
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(target), str(ROOT)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=True,
            )
            mission = Path(tmp) / "installed-mission"
            environment = {**os.environ, "PYTHONPATH": str(target)}
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "long_run_agent_skill",
                    "--root",
                    str(mission),
                    "init",
                    "--goal",
                    "Installed mission",
                ],
                cwd=tmp,
                env=environment,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(json.loads(result.stdout)["status"], "success")
            self.assertTrue((mission / ".agent" / "retrieval.jsonl").is_file())

    def test_skill_installer_copies_new_architecture_without_cache_or_git_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "skill"
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/install_skill.py",
                    "--json",
                    "--execute",
                    "--source",
                    str(ROOT),
                    "--target",
                    str(target),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=True,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["steps"][0]["status"], "installed")
            self.assertTrue((target / "long_run_agent_skill" / "compiler.py").is_file())
            self.assertTrue((target / "scripts" / "mission_harness.py").is_file())
            self.assertFalse((target / ".git").exists())
            self.assertFalse(list(target.rglob("*.pyc")))

    def test_all_code_files_stay_under_one_thousand_lines(self) -> None:
        oversized = []
        for path in ROOT.rglob("*"):
            if ".git" in path.parts or "__pycache__" in path.parts:
                continue
            if path.suffix not in {".py", ".sh", ".ps1"}:
                continue
            lines = len(path.read_text(encoding="utf-8").splitlines())
            if lines >= 1000:
                oversized.append(f"{path.relative_to(ROOT)}:{lines}")
        self.assertEqual(oversized, [])

    def test_distribution_metadata_declares_both_entrypoints(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('version = "1.0.0"', pyproject)
        self.assertIn('long-run-agent = "long_run_agent_skill.cli:main"', pyproject)
        self.assertIn('long-run-agent-mcp = "long_run_agent_skill.mcp_server:main"', pyproject)


if __name__ == "__main__":
    unittest.main()
