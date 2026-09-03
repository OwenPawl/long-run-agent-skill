from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from long_run_agent_skill.call_tracker import track_public_call, tracker_log_path
from long_run_agent_skill.compiler import EpistemicCompiler
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


def parse_blocks(content: str) -> list[dict[str, object]]:
    blocks = []
    position = 0
    while position < len(content):
        start = content.find("=== SKILL CALL ", position)
        if start < 0:
            break
        call_line_end = content.index(" ===\n", start)
        call_id = content[start + len("=== SKILL CALL ") : call_line_end]
        end_marker = f"=== END {call_id} ===\n"
        end = content.index(end_marker, call_line_end) + len(end_marker)
        text = content[start:end]
        header, remainder = text.split("\n\n--- REQUEST ---\n", 1)
        request_text, response_part = remainder.split("\n\n--- RESPONSE ---\n", 1)
        response_text = response_part.rsplit(f"\n\n=== END {call_id} ===\n", 1)[0]
        fields = {}
        for line in header.splitlines()[1:]:
            key, value = line.split(": ", 1)
            fields[key] = value
        blocks.append(
            {
                "call_id": call_id,
                "fields": fields,
                "request_text": request_text,
                "response_text": response_text,
            }
        )
        position = end
    return blocks


class SkillCallTrackerTests(unittest.TestCase):
    def mcp(self) -> tuple[FakeServer, list[str]]:
        server = FakeServer()
        return server, register_mission_tools(server)

    def test_successful_mcp_call_records_one_complete_exact_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server, _ = self.mcp()
            response = server.handlers["mission_init"](tmp, "Tracked mission")
            content = tracker_log_path(tmp).read_text(encoding="utf-8")
            blocks = parse_blocks(content)
            self.assertEqual(len(blocks), 1)
            block = blocks[0]
            self.assertTrue(str(block["call_id"]).startswith("call_"))
            self.assertEqual(block["fields"]["operation"], "mission_init")
            self.assertEqual(block["fields"]["surface"], "mcp")
            self.assertEqual(block["fields"]["status"], "success")
            self.assertEqual(
                json.loads(str(block["request_text"])),
                {"goal": "Tracked mission", "root": tmp},
            )
            self.assertEqual(json.loads(str(block["response_text"])), response)
            self.assertRegex(str(block["fields"]["duration_ms"]), r"^\d+\.\d{3}$")
            self.assertGreater(int(block["fields"]["request_bytes"]), 0)
            self.assertGreater(int(block["fields"]["response_bytes"]), 0)
            self.assertEqual(
                block["fields"]["epistemic_generation_before"], "unavailable"
            )
            self.assertEqual(block["fields"]["epistemic_generation_after"], "0")
            self.assertEqual(content.count("=== END "), 1)

    def test_generation_and_run_identifier_are_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server, _ = self.mcp()
            server.handlers["mission_init"](tmp, "Tracked mission")
            server.handlers["mission_start"](
                tmp, "Tracked mission", run_id="run_tracker_test"
            )
            block = parse_blocks(tracker_log_path(tmp).read_text(encoding="utf-8"))[-1]
            self.assertEqual(block["fields"]["epistemic_generation_before"], "0")
            self.assertEqual(block["fields"]["epistemic_generation_after"], "1")
            self.assertIn(
                "run_tracker_test", str(block["fields"]["worker/run/session"])
            )

    def test_cli_logs_exact_argv_and_surfaced_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            argv = [
                sys.executable,
                "-m",
                "long_run_agent_skill",
                "--root",
                tmp,
                "init",
                "--goal",
                "CLI tracked mission",
            ]
            completed = subprocess.run(
                argv, cwd=ROOT, text=True, capture_output=True, check=True
            )
            block = parse_blocks(tracker_log_path(tmp).read_text(encoding="utf-8"))[0]
            self.assertEqual(block["fields"]["operation"], "init")
            self.assertEqual(block["fields"]["surface"], "cli")
            self.assertEqual(
                json.loads(str(block["request_text"])), {"argv": argv[3:]}
            )
            self.assertEqual(
                json.loads(str(block["response_text"])), json.loads(completed.stdout)
            )

    def test_error_call_closes_block_with_exact_surfaced_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "long_run_agent_skill",
                    "--root",
                    tmp,
                    "validate",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertTrue(completed.stderr)
            content = tracker_log_path(tmp).read_text(encoding="utf-8")
            block = parse_blocks(content)[0]
            self.assertEqual(block["fields"]["status"], "failed")
            self.assertEqual(block["fields"]["error_type"], "LedgerError")
            self.assertEqual(
                json.loads(str(block["response_text"])), json.loads(completed.stderr)
            )
            self.assertEqual(content.count(f"=== END {block['call_id']} ==="), 1)

    def test_mcp_failure_envelope_is_logged_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server, _ = self.mcp()
            response = server.handlers["mission_validate"](tmp)
            self.assertEqual(response["status"], "failed")
            content = tracker_log_path(tmp).read_text(encoding="utf-8")
            block = parse_blocks(content)[0]
            self.assertEqual(block["fields"]["status"], "failed")
            self.assertEqual(block["fields"]["surface"], "mcp")
            self.assertEqual(json.loads(str(block["response_text"])), response)
            self.assertEqual(content.count(f"=== END {block['call_id']} ==="), 1)

    def test_consecutive_concurrent_blocks_do_not_interleave(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            barrier = threading.Barrier(2)

            def invoke(index: int) -> dict[str, int]:
                def callback() -> dict[str, int]:
                    barrier.wait()
                    time.sleep(0.01 if index == 1 else 0)
                    return {"status": "success", "index": index}

                return track_public_call(
                    tmp,
                    f"concurrent_{index}",
                    {"index": index},
                    callback,
                    surface="test",
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(invoke, [1, 2]))
            self.assertEqual(
                responses,
                [
                    {"status": "success", "index": 1},
                    {"status": "success", "index": 2},
                ],
            )
            content = tracker_log_path(tmp).read_text(encoding="utf-8")
            blocks = parse_blocks(content)
            self.assertEqual(len(blocks), 2)
            self.assertEqual(content.count("=== SKILL CALL "), 2)
            self.assertEqual(content.count("=== END "), 2)
            self.assertEqual(
                {
                    json.loads(str(item["response_text"]))["index"]
                    for item in blocks
                },
                {1, 2},
            )

    def test_tracking_and_tracking_failure_do_not_change_return_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            expected = {"status": "success", "identity": object()}
            returned = track_public_call(
                tmp, "identity", {}, lambda: expected, surface="test"
            )
            self.assertIs(returned, expected)
            with patch(
                "long_run_agent_skill.call_tracker._append_block",
                side_effect=OSError("simulated log failure"),
            ):
                returned_after_failure = track_public_call(
                    tmp, "identity_failure", {}, lambda: expected, surface="test"
                )
            self.assertIs(returned_after_failure, expected)

    def test_tracker_is_not_epistemic_or_retrieval_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server, _ = self.mcp()
            server.handlers["mission_init"](tmp, "Authority boundary")
            server.handlers["mission_start"](
                tmp, "Authority boundary", run_id="run_authority_boundary"
            )
            compiler = EpistemicCompiler(tmp)
            ledger_before = compiler.store.paths.ledger.read_bytes()
            retrieval_before = compiler.store.paths.retrieval.read_bytes()
            generation_before = compiler.state()["generation"]
            response = server.handlers["mission_validate"](tmp)
            self.assertEqual(response["status"], "success")
            self.assertEqual(compiler.store.paths.ledger.read_bytes(), ledger_before)
            self.assertEqual(
                compiler.store.paths.retrieval.read_bytes(), retrieval_before
            )
            compiler.store.paths.state.unlink()
            rebuilt = compiler.rebuild()
            self.assertEqual(rebuilt["generation"], generation_before)
            self.assertNotIn("skill-calls.log", json.dumps(compiler.state()))

    def test_relative_path_override_is_mission_relative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                "os.environ", {"LONG_RUN_AGENT_SKILL_CALL_LOG": "logs/calls.log"}
            ):
                expected = Path(tmp).resolve() / "logs" / "calls.log"
                self.assertEqual(tracker_log_path(tmp), expected)
                track_public_call(
                    tmp,
                    "configured",
                    {},
                    lambda: {"status": "success"},
                    surface="test",
                )
                self.assertTrue(expected.is_file())

    def test_override_cannot_target_authority_ledgers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for configured in (".agent/ledger.jsonl", ".agent/retrieval.jsonl"):
                with self.subTest(configured=configured), patch.dict(
                    "os.environ", {"LONG_RUN_AGENT_SKILL_CALL_LOG": configured}
                ):
                    with self.assertRaisesRegex(ValueError, "cannot replace"):
                        tracker_log_path(tmp)


if __name__ == "__main__":
    unittest.main()
