from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "scripts"
CLI = SCRIPT_DIR / "mission_harness.py"
sys.path.insert(0, str(SCRIPT_DIR))

from mission_live_state import LiveStateError, compact_live_snapshot


class CompactLiveTests(unittest.TestCase):
    def run_cli(
        self,
        root: pathlib.Path,
        *args: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(CLI), "--root", str(root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if check and result.returncode != 0:
            self.fail(f"command failed: {args}\nstdout={result.stdout}\nstderr={result.stderr}")
        return result

    @staticmethod
    def replace_entry(path: pathlib.Path, section: str, value: str) -> None:
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            f"## {section}\n- None recorded yet.",
            f"## {section}\n- {value}",
        )
        path.write_text(text, encoding="utf-8")

    def test_compact_then_close_recovers_archived_verification_and_references(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.run_cli(root, "init")
            run_id = json.loads(
                self.run_cli(root, "run", "start", "--goal", "compact close").stdout
            )["run_id"]
            self.run_cli(
                root,
                "claim",
                "add",
                "--id",
                "claim_compact",
                "--claim",
                "compaction preserves closeout evidence",
                "--source-path",
                "tests/test_compact_live.py",
                "--source-kind",
                "test",
                "--status",
                "tested",
            )
            self.run_cli(
                root,
                "artifact",
                "add",
                "--id",
                "artifact_compact",
                "--run-id",
                run_id,
                "--path",
                ".agent/archive",
                "--kind",
                "directory",
                "--description",
                "compact-live archives",
            )
            self.run_cli(
                root,
                "friction",
                "add",
                "--id",
                "friction_compact",
                "--run-id",
                run_id,
                "--category",
                "context_packing_gap",
                "--description",
                "live state required compaction",
                "--impact",
                "control state grew too large",
                "--proposed-harness-need",
                "archive-aware closeout",
            )
            live_path = root / ".agent" / "live.md"
            for section, value in (
                ("Commands Run", "python3 compact-check.py"),
                ("Tests / Verification", "compact verification passed"),
                ("Claims Touched", "compaction preserves closeout evidence"),
                ("Artifacts Produced", "`.agent/archive`"),
                ("Friction Observed", "live state required compaction"),
            ):
                self.replace_entry(live_path, section, value)

            compact = json.loads(self.run_cli(root, "state", "compact-live").stdout)
            compacted = live_path.read_text(encoding="utf-8")
            self.assertIn("`mission-harness.live-archive.v1`", compacted)
            self.assertNotIn("python3 compact-check.py", compacted)
            self.assertIn(
                "python3 compact-check.py",
                pathlib.Path(compact["archive"]).read_text(encoding="utf-8"),
            )

            self.run_cli(root, "run", "close", "--outcome", "compacted close")
            close = json.loads((root / ".agent" / "runs.jsonl").read_text().splitlines()[-1])
            self.assertEqual(close["commands_run"], ["python3 compact-check.py"])
            self.assertEqual(close["tests_run"], ["compact verification passed"])
            self.assertEqual(
                close["claims_touched"],
                ["compaction preserves closeout evidence"],
            )
            self.assertEqual(close["artifacts_produced"], ["`.agent/archive`"])

    def test_empty_compacted_verification_does_not_satisfy_close_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.run_cli(root, "init")
            self.run_cli(root, "run", "start", "--goal", "empty verification")
            self.run_cli(root, "state", "compact-live")

            close = self.run_cli(
                root,
                "run",
                "close",
                "--outcome",
                "must fail",
                check=False,
            )
            self.assertNotEqual(close.returncode, 0)
            self.assertIn("requires --command/--test entries", close.stderr)
            records = (root / ".agent" / "runs.jsonl").read_text().splitlines()
            self.assertEqual(len(records), 1)

    def test_compaction_rejects_missing_durable_references_before_archive(self) -> None:
        cases = (
            ("Claims Touched", "missing_claim"),
            ("Artifacts Produced", "missing_artifact"),
            ("Friction Observed", "missing_friction"),
        )
        for section, value in cases:
            with self.subTest(section=section), tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp)
                self.run_cli(root, "init")
                self.run_cli(root, "run", "start", "--goal", "missing durable reference")
                live_path = root / ".agent" / "live.md"
                self.replace_entry(live_path, section, value)
                original = live_path.read_text(encoding="utf-8")

                compact = self.run_cli(root, "state", "compact-live", check=False)
                self.assertNotEqual(compact.returncode, 0)
                self.assertIn(section, compact.stderr)
                self.assertEqual(live_path.read_text(encoding="utf-8"), original)
                archive = root / ".agent" / "archive"
                self.assertFalse(archive.exists())

    def test_repeated_compaction_uses_collision_suffix_and_recovers_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.run_cli(root, "init")
            self.run_cli(root, "run", "start", "--goal", "repeated compaction")
            live_path = root / ".agent" / "live.md"
            self.replace_entry(live_path, "Commands Run", "first archived command")
            self.replace_entry(live_path, "Tests / Verification", "first archived test")
            directory = root / ".agent"

            first = compact_live_snapshot(
                directory,
                live_path,
                live_path.read_text(encoding="utf-8"),
                "20260829T120000Z",
            )
            second = compact_live_snapshot(
                directory,
                live_path,
                live_path.read_text(encoding="utf-8"),
                "20260829T120000Z",
            )
            self.assertEqual(first.name, "live-20260829T120000Z.md")
            self.assertEqual(second.name, "live-20260829T120000Z-2.md")

            self.run_cli(root, "run", "close", "--outcome", "chain recovered")
            close = json.loads((directory / "runs.jsonl").read_text().splitlines()[-1])
            self.assertEqual(close["commands_run"], ["first archived command"])
            self.assertEqual(close["tests_run"], ["first archived test"])

    def test_compaction_cas_rejects_concurrent_live_edit_before_archiving(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.run_cli(root, "init")
            live_path = root / ".agent" / "live.md"
            expected = live_path.read_text(encoding="utf-8")
            updated = expected.replace("- None recorded yet.", "- concurrent operator edit", 1)
            live_path.write_text(updated, encoding="utf-8")

            with self.assertRaisesRegex(LiveStateError, "changed during compaction"):
                compact_live_snapshot(
                    root / ".agent",
                    live_path,
                    expected,
                    "20260829T130000Z",
                )
            self.assertEqual(live_path.read_text(encoding="utf-8"), updated)
            self.assertFalse((root / ".agent" / "archive").exists())

    def test_legacy_archive_marker_remains_close_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.run_cli(root, "init")
            self.run_cli(root, "run", "start", "--goal", "legacy archive")
            live_path = root / ".agent" / "live.md"
            archived = live_path.read_text(encoding="utf-8")
            archived = archived.replace(
                "## Commands Run\n- None recorded yet.",
                "## Commands Run\n- legacy command",
            ).replace(
                "## Tests / Verification\n- None recorded yet.",
                "## Tests / Verification\n- legacy test",
            )
            archive_path = root / ".agent" / "archive" / "live-legacy.md"
            archive_path.parent.mkdir()
            archive_path.write_text(archived, encoding="utf-8")
            current = live_path.read_text(encoding="utf-8")
            legacy = "Compacted history archived at `.agent/archive/live-legacy.md`."
            current = current.replace(
                "## Commands Run\n- None recorded yet.",
                f"## Commands Run\n- {legacy}",
            ).replace(
                "## Tests / Verification\n- None recorded yet.",
                f"## Tests / Verification\n- {legacy}",
            )
            live_path.write_text(current, encoding="utf-8")

            self.run_cli(root, "run", "close", "--outcome", "legacy recovered")
            close = json.loads((root / ".agent" / "runs.jsonl").read_text().splitlines()[-1])
            self.assertEqual(close["commands_run"], ["legacy command"])
            self.assertEqual(close["tests_run"], ["legacy test"])


if __name__ == "__main__":
    unittest.main()
