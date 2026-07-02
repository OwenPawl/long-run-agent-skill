from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "scripts"
CLI = REPO_ROOT / "scripts" / "mission_harness.py"
ARTIFACT_MATERIALIZE = REPO_ROOT / "scripts" / "mission_artifact_materialize.py"
RECORDS_RECENT = REPO_ROOT / "scripts" / "mission_records_recent.py"
sys.path.insert(0, str(SCRIPT_DIR))

from mission_state_preflight import READ_TIMEOUT_SECONDS, READ_VERIFY_ATTEMPTS, read_verification_error


class MissionHarnessTests(unittest.TestCase):
    def run_cli(self, root: pathlib.Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
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

    def run_materialize(self, root: pathlib.Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(ARTIFACT_MATERIALIZE), "--root", str(root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if check and result.returncode != 0:
            self.fail(f"materialize failed: {args}\nstdout={result.stdout}\nstderr={result.stderr}")
        return result

    def run_recent(self, root: pathlib.Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(RECORDS_RECENT), "--root", str(root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if check and result.returncode != 0:
            self.fail(f"recent failed: {args}\nstdout={result.stdout}\nstderr={result.stderr}")
        return result

    def test_init_creates_valid_agent_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.run_cli(root, "init")
            agent = root / ".agent"
            for name in [
                "live.md",
                "current_state.md",
                "constitution.md",
                "known_failures.md",
                "decisions.md",
                "runs.jsonl",
                "claims.json",
                "artifacts.json",
                "friction.jsonl",
            ]:
                self.assertTrue((agent / name).exists(), name)
            live = (agent / "live.md").read_text(encoding="utf-8")
            self.assertIn("## User Updates", live)
            self.assertIn("## Next Actions", live)
            self.run_cli(root, "state", "summarize")
            self.run_cli(root, "validate")

    def test_state_preflight_is_available_without_cloud_backing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.run_cli(root, "init")
            payload = json.loads(self.run_cli(root, "state", "preflight").stdout)
            self.assertTrue(payload["ok"])
            self.assertIn(payload["status"], {"not_applicable", "ready", "readable_with_dataless_flags_remaining"})
            if sys.platform == "darwin":
                self.assertEqual(len(payload["read_verified"]), 9)

    def test_state_read_verification_timeout_is_bounded(self) -> None:
        with (
            mock.patch(
                "mission_state_preflight.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="read probe", timeout=READ_TIMEOUT_SECONDS),
            ),
            mock.patch("mission_state_preflight.time.sleep"),
        ):
            error = read_verification_error(pathlib.Path("/blocked/live.md"))

        self.assertEqual(
            error,
            f"read verification timed out after {READ_VERIFY_ATTEMPTS} attempts of {READ_TIMEOUT_SECONDS}s",
        )

    def test_state_read_verification_retries_transient_timeout(self) -> None:
        success = subprocess.CompletedProcess(args=["probe"], returncode=0, stdout="", stderr="")
        with (
            mock.patch(
                "mission_state_preflight.subprocess.run",
                side_effect=[
                    subprocess.TimeoutExpired(cmd="read probe", timeout=READ_TIMEOUT_SECONDS),
                    success,
                ],
            ) as run_probe,
            mock.patch("mission_state_preflight.time.sleep") as sleep,
        ):
            error = read_verification_error(pathlib.Path("/slow/live.md"))

        self.assertEqual(error, "")
        self.assertEqual(run_probe.call_count, 2)
        sleep.assert_called_once()

    def test_artifact_materialize_resolves_recorded_readable_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            evidence = root / "evidence.json"
            evidence.write_text('{"observed": true}\n', encoding="utf-8")
            self.run_cli(root, "init")
            self.run_cli(
                root,
                "artifact",
                "add",
                "--id",
                "artifact_materialize_smoke",
                "--path",
                "evidence.json",
                "--kind",
                "test",
                "--description",
                "materialization smoke evidence",
            )
            payload = json.loads(self.run_materialize(root, "--id", "artifact_materialize_smoke").stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["path"], str(evidence))
            self.assertTrue(payload["read_verified"])

    def test_artifact_materialize_rejects_unknown_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.run_cli(root, "init")
            result = self.run_materialize(root, "--id", "missing_artifact", check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("artifact id not found", result.stdout)

    def test_artifact_materialize_accepts_unregistered_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            evidence = root / "new-evidence.log"
            evidence.write_text("runtime evidence pending registration\n", encoding="utf-8")
            self.run_cli(root, "init")

            payload = json.loads(self.run_materialize(root, "--path", "new-evidence.log").stdout)

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["artifact_id"], "")
            self.assertEqual(payload["path"], str(evidence))
            self.assertTrue(payload["read_verified"])

    def test_artifact_materialize_stages_verified_regular_file_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            evidence = root / "evidence.json"
            evidence.write_text('{"observed": true}\n', encoding="utf-8")
            self.run_cli(root, "init")

            payload = json.loads(
                self.run_materialize(root, "--path", "evidence.json", "--stage-copy", "staged/input.json").stdout
            )

            staged = root / "staged" / "input.json"
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["staged_copy"]["path"], str(staged))
            self.assertTrue(payload["staged_copy"]["read_verified"])
            self.assertEqual(staged.read_text(encoding="utf-8"), evidence.read_text(encoding="utf-8"))

    def test_artifact_materialize_does_not_overwrite_existing_staged_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            evidence = root / "evidence.json"
            staged = root / "staged.json"
            evidence.write_text('{"new": true}\n', encoding="utf-8")
            staged.write_text('{"preserved": true}\n', encoding="utf-8")
            self.run_cli(root, "init")

            result = self.run_materialize(root, "--path", "evidence.json", "--stage-copy", "staged.json", check=False)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("stage-copy destination already exists", result.stdout)
            self.assertEqual(staged.read_text(encoding="utf-8"), '{"preserved": true}\n')

    def test_run_lifecycle_records_claim_artifact_friction_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.run_cli(root, "init")
            start = self.run_cli(root, "run", "start", "--goal", "bootstrap harness smoke").stdout
            run_id = json.loads(start)["run_id"]
            self.run_cli(
                root,
                "claim",
                "add",
                "--id",
                "claim_smoke",
                "--claim",
                "validate catches malformed JSONL",
                "--source-path",
                "tests/test_mission_harness.py",
                "--source-kind",
                "test",
                "--status",
                "tested",
                "--verification-command",
                "python3 -m unittest discover -s tests",
                "--last-checked-at",
                "2026-05-22T00:00:00Z",
                "--confidence",
                "verified",
            )
            self.run_cli(
                root,
                "artifact",
                "add",
                "--id",
                "artifact_smoke",
                "--run-id",
                run_id,
                "--path",
                ".agent/current_state.md",
                "--kind",
                "markdown",
                "--description",
                "generated current state",
                "--verification-command",
                "python3 scripts/mission_harness.py validate",
            )
            self.run_cli(
                root,
                "friction",
                "add",
                "--run-id",
                run_id,
                "--category",
                "verification_gap",
                "--description",
                "bootstrap needed a malformed-state negative test",
                "--impact",
                "validate could otherwise overclaim",
                "--proposed-harness-need",
                "keep negative validation tests",
                "--severity",
                "medium",
            )
            self.run_cli(
                root,
                "run",
                "close",
                "--run-id",
                run_id,
                "--outcome",
                "smoke run closed",
                "--command",
                "python3 scripts/mission_harness.py validate",
                "--test",
                "python3 -m unittest discover -s tests",
                "--file-changed",
                "scripts/mission_harness.py",
                "--claim",
                "claim_smoke",
                "--artifact",
                "artifact_smoke",
                "--decision",
                "keep v0.1 file-based",
                "--next-action",
                "dogfood on a bounded project run",
            )
            self.run_cli(root, "validate")
            state = (root / ".agent" / "current_state.md").read_text(encoding="utf-8")
            self.assertIn("smoke run closed", state)
            self.assertIn("dogfood on a bounded project run", state)
            runs = [line for line in (root / ".agent" / "runs.jsonl").read_text(encoding="utf-8").splitlines() if line]
            self.assertEqual(len(runs), 2)

    def test_recent_records_lists_authoritative_state_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.run_cli(root, "init")
            start = self.run_cli(root, "run", "start", "--goal", "recent records").stdout
            run_id = json.loads(start)["run_id"]
            self.run_cli(
                root,
                "claim",
                "add",
                "--id",
                "claim_recent",
                "--claim",
                "recent command reads claims.json shape",
                "--source-path",
                "tests/test_mission_harness.py",
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
                "artifact_recent",
                "--run-id",
                run_id,
                "--path",
                ".agent/claims.json",
                "--kind",
                "json",
                "--description",
                "claim state",
            )
            self.run_cli(
                root,
                "friction",
                "add",
                "--id",
                "friction_recent",
                "--run-id",
                run_id,
                "--category",
                "artifact_discovery_gap",
                "--description",
                "manual record inspection guessed the JSON shape",
                "--impact",
                "agent hit a schema error instead of seeing recent claims",
                "--proposed-harness-need",
                "recent-record listing command",
            )

            claims = json.loads(self.run_recent(root, "claims", "--limit", "1").stdout)
            artifacts = json.loads(self.run_recent(root, "artifacts", "--limit", "1").stdout)
            friction = json.loads(self.run_recent(root, "friction", "--limit", "1").stdout)
            runs = json.loads(self.run_recent(root, "runs", "--limit", "1").stdout)

            self.assertEqual(claims["records"][0]["id"], "claim_recent")
            self.assertEqual(artifacts["records"][0]["id"], "artifact_recent")
            self.assertEqual(friction["records"][0]["id"], "friction_recent")
            self.assertEqual(runs["records"][0]["run_id"], run_id)

    def test_run_start_clears_per_run_live_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.run_cli(root, "init")
            start = self.run_cli(root, "run", "start", "--goal", "first run").stdout
            run_id = json.loads(start)["run_id"]
            self.run_cli(
                root,
                "run",
                "close",
                "--run-id",
                run_id,
                "--outcome",
                "first closed",
                "--command",
                "old command",
                "--test",
                "old test",
                "--failure",
                "old failure",
                "--claim",
                "old claim",
                "--artifact",
                "old artifact",
                "--next-action",
                "old next",
            )
            self.run_cli(root, "run", "start", "--goal", "second run")
            live = (root / ".agent" / "live.md").read_text(encoding="utf-8")
            for stale in ["old command", "old test", "old failure", "old claim", "old artifact", "old next"]:
                self.assertNotIn(stale, live)
            self.assertIn("## Commands Run\n- None recorded yet.", live)
            self.assertIn("## Claims Touched\n- None recorded yet.", live)

    def test_run_close_and_friction_accept_observed_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.run_cli(root, "init")
            start = self.run_cli(root, "run", "start", "--goal", "alias smoke").stdout
            run_id = json.loads(start)["run_id"]

            self.run_cli(
                root,
                "friction",
                "add",
                "--source-run-id",
                run_id,
                "--category",
                "user_control_gap",
                "--description",
                "source-run-id alias records the source run",
                "--impact",
                "manual smoke commands do not fail on natural flag spelling",
                "--proposed-harness-need",
                "keep compatibility aliases for common operator wording",
            )
            self.run_cli(
                root,
                "run",
                "close",
                "--run-id",
                run_id,
                "--outcome",
                "alias smoke closed",
                "--command",
                "alias command",
                "--test",
                "alias test",
                "--changed-file",
                "README.md",
            )

            runs = [json.loads(line) for line in (root / ".agent" / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
            friction = [
                json.loads(line)
                for line in (root / ".agent" / "friction.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(runs[-1]["files_changed"], ["README.md"])
            self.assertEqual(friction[-1]["source_run_id"], run_id)

    def test_friction_settle_preserves_evidence_and_reports_effective_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.run_cli(root, "init")
            start = self.run_cli(root, "run", "start", "--goal", "settle friction").stdout
            run_id = json.loads(start)["run_id"]
            self.run_cli(
                root,
                "friction",
                "add",
                "--id",
                "friction_duplicate_ci",
                "--run-id",
                run_id,
                "--category",
                "verification_gap",
                "--description",
                "hosted CI failed before job steps were visible",
                "--impact",
                "operator could mistake infrastructure noise for a release blocker",
                "--proposed-harness-need",
                "release report separates evidence from effective root causes",
            )

            failing = self.run_cli(
                root,
                "friction",
                "report",
                "--fail-on-ambiguous-open",
                check=False,
            )
            self.assertNotEqual(failing.returncode, 0)
            self.assertEqual(json.loads(failing.stdout)["ambiguous_open_count"], 1)

            self.run_cli(
                root,
                "friction",
                "settle",
                "--id",
                "friction_duplicate_ci",
                "--status",
                "external-blocker",
                "--root-cause-id",
                "root_hosted_ci_pre_steps",
                "--release-disposition",
                "external",
                "--rationale",
                "latest hosted CI verification is green; historical failures are infrastructure evidence",
                "--verification-command",
                "gh run view <latest> --json conclusion",
            )
            report_path = root / "friction-report.md"
            report = json.loads(
                self.run_cli(
                    root,
                    "friction",
                    "report",
                    "--fail-on-ambiguous-open",
                    "--output",
                    str(report_path),
                ).stdout
            )

            self.assertEqual(report["raw_record_count"], 2)
            self.assertEqual(report["effective_item_count"], 1)
            self.assertEqual(report["ambiguous_open_count"], 0)
            self.assertEqual(report["status_counts"]["external-blocker"], 1)
            self.assertIn("root_hosted_ci_pre_steps", report_path.read_text(encoding="utf-8"))
            self.run_cli(root, "state", "summarize")
            state = (root / ".agent" / "current_state.md").read_text(encoding="utf-8")
            self.assertIn("2 raw / 1 effective", state)
            self.run_cli(root, "validate")

    def test_run_close_explicit_next_actions_replace_stale_live_next_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.run_cli(root, "init")
            start = self.run_cli(root, "run", "start", "--goal", "next action close").stdout
            run_id = json.loads(start)["run_id"]
            live_path = root / ".agent" / "live.md"
            live = live_path.read_text(encoding="utf-8")
            live = live.replace("## Next Actions\n- None recorded yet.", "## Next Actions\n- stale live next")
            live_path.write_text(live, encoding="utf-8")

            self.run_cli(
                root,
                "run",
                "close",
                "--run-id",
                run_id,
                "--outcome",
                "closed",
                "--command",
                "command",
                "--test",
                "test",
                "--next-action",
                "explicit next",
            )

            runs = [json.loads(line) for line in (root / ".agent" / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
            close = runs[-1]
            self.assertEqual(close["next_actions"], ["explicit next"])
            state = (root / ".agent" / "current_state.md").read_text(encoding="utf-8")
            self.assertIn("- explicit next", state)
            self.assertNotIn("stale live next", state)

    def test_run_close_deduplicates_live_and_explicit_markdown_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.run_cli(root, "init")
            start = self.run_cli(root, "run", "start", "--goal", "dedupe close").stdout
            run_id = json.loads(start)["run_id"]
            live_path = root / ".agent" / "live.md"
            live = live_path.read_text(encoding="utf-8")
            live = live.replace("## Commands Run\n- None recorded yet.", "## Commands Run\n- `command one`")
            live = live.replace("## Tests / Verification\n- None recorded yet.", "## Tests / Verification\n- test one")
            live = live.replace("## Failures / Blockers\n- None recorded yet.", "## Failures / Blockers\n- `failure_one`")
            live = live.replace("## Claims Touched\n- None recorded yet.", "## Claims Touched\n- `claim_one`")
            live = live.replace("## Artifacts Produced\n- None recorded yet.", "## Artifacts Produced\n- `artifact_one`")
            live_path.write_text(live, encoding="utf-8")

            self.run_cli(
                root,
                "run",
                "close",
                "--run-id",
                run_id,
                "--outcome",
                "closed",
                "--command",
                "command one",
                "--test",
                "test one",
                "--failure",
                "failure_one",
                "--claim",
                "claim_one",
                "--artifact",
                "artifact_one",
            )

            runs = [json.loads(line) for line in (root / ".agent" / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
            close = runs[-1]
            self.assertEqual(close["commands_run"], ["command one"])
            self.assertEqual(close["tests_run"], ["test one"])
            self.assertEqual(close["failures"], ["failure_one"])
            self.assertEqual(close["claims_touched"], ["claim_one"])
            self.assertEqual(close["artifacts_produced"], ["artifact_one"])

    def test_state_summarize_prefers_live_sections_during_open_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.run_cli(root, "init")
            self.run_cli(root, "run", "start", "--goal", "stale start goal")
            live_path = root / ".agent" / "live.md"
            live = live_path.read_text(encoding="utf-8")
            live = live.replace("- stale start goal", "- active corrected goal")
            live = live.replace("## Failures / Blockers\n- None recorded yet.", "## Failures / Blockers\n- live blocker")
            live = live.replace("## Next Actions\n- None recorded yet.", "## Next Actions\n- active next action")
            live_path.write_text(live, encoding="utf-8")

            self.run_cli(root, "state", "summarize")
            state = (root / ".agent" / "current_state.md").read_text(encoding="utf-8")
            self.assertIn("- Goal: active corrected goal", state)
            self.assertIn("- active next action", state)
            self.assertIn("- live blocker", state)
            self.assertNotIn("- Goal: stale start goal", state)

    def test_watch_live_file_creates_live_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            live_path = root / ".agent" / "live.md"
            result = subprocess.run(
                [sys.executable, str(SCRIPT_DIR / "watch_live_file.py"), str(live_path), "--create", "--once"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
            )

            self.assertIn("LIVE FILE INITIAL", result.stdout)
            live = live_path.read_text(encoding="utf-8")
            self.assertTrue(live.startswith("# Live Control"))
            self.assertIn("## Agent Status", live)

    def test_state_compact_live_archives_verbose_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.run_cli(root, "init")
            self.run_cli(root, "run", "start", "--goal", "compact active evidence")
            live_path = root / ".agent" / "live.md"
            live = live_path.read_text(encoding="utf-8")
            live = live.replace("## Commands Run\n- None recorded yet.", "## Commands Run\n- verbose old command")
            live = live.replace("## Tests / Verification\n- None recorded yet.", "## Tests / Verification\n- verbose old test")
            live_path.write_text(live, encoding="utf-8")

            payload = json.loads(self.run_cli(root, "state", "compact-live").stdout)
            archive = pathlib.Path(payload["archive"])
            self.assertIn("verbose old command", archive.read_text(encoding="utf-8"))
            compacted = live_path.read_text(encoding="utf-8")
            self.assertIn("compact active evidence", compacted)
            self.assertIn(".agent/archive/live-", compacted)
            self.assertNotIn("verbose old command", compacted)
            self.assertNotIn("verbose old test", compacted)
            self.run_cli(root, "validate")

    def test_index_rebuild_status_and_search_cover_mission_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.run_cli(root, "init")
            start = self.run_cli(root, "run", "start", "--goal", "index previous work").stdout
            run_id = json.loads(start)["run_id"]
            self.run_cli(
                root,
                "claim",
                "add",
                "--id",
                "claim_index_smoke",
                "--claim",
                "native zero hit targets remain searchable",
                "--source-path",
                "phase41/frida-runtime-recheck.json",
                "--source-kind",
                "runtime evidence",
                "--status",
                "tested",
                "--verification-command",
                "python3 scripts/mission_harness.py --root . index search native",
                "--confidence",
                "verified",
            )
            self.run_cli(
                root,
                "artifact",
                "add",
                "--id",
                "artifact_index_smoke",
                "--run-id",
                run_id,
                "--path",
                "phase41/frida-runtime-recheck.json",
                "--kind",
                "json",
                "--description",
                "runtime recheck evidence",
            )
            self.run_cli(
                root,
                "artifact",
                "add",
                "--id",
                "artifact_index_smoke",
                "--run-id",
                run_id,
                "--path",
                "phase41/duplicate-friction.json",
                "--kind",
                "json",
                "--description",
                "duplicate id should still be indexable",
            )
            self.run_cli(
                root,
                "friction",
                "add",
                "--run-id",
                run_id,
                "--category",
                "memory_loss",
                "--description",
                "previous work needed a searchable index",
                "--impact",
                "resumption required manual grep",
                "--proposed-harness-need",
                "derived SQLite index",
            )
            self.run_cli(
                root,
                "friction",
                "add",
                "--id",
                "friction_index_broad_query",
                "--run-id",
                run_id,
                "--category",
                "claim_drift",
                "--description",
                "native missing overclaim guard",
                "--impact",
                "blocked timeout could be misread as export absence",
                "--proposed-harness-need",
                "verification path for negative evidence",
            )
            self.run_cli(
                root,
                "run",
                "close",
                "--run-id",
                run_id,
                "--outcome",
                "index smoke closed",
                "--command",
                "python3 scripts/mission_harness.py --root . index rebuild",
                "--test",
                "python3 -m unittest discover -s tests",
                "--claim",
                "claim_index_smoke",
                "--artifact",
                "artifact_index_smoke",
                "--next-action",
                "dogfood index on real run state",
            )

            rebuild = json.loads(self.run_cli(root, "index", "rebuild").stdout)
            self.assertTrue(rebuild["ok"])
            self.assertGreaterEqual(rebuild["record_count"], 9)
            self.assertTrue((root / ".agent" / "mission_index.sqlite").exists())

            status = json.loads(self.run_cli(root, "index", "status").stdout)
            self.assertTrue(status["exists"])
            self.assertEqual(status["record_count"], rebuild["record_count"])

            db_path = root / ".agent" / "mission_index.sqlite"
            db_path.write_text("not a sqlite database", encoding="utf-8")
            damaged_status = json.loads(self.run_cli(root, "index", "status").stdout)
            self.assertFalse(damaged_status["ok"])
            self.assertEqual(damaged_status["repair_command"], "index rebuild")

            repaired_search = json.loads(self.run_cli(root, "index", "search", "native zero hit").stdout)
            self.assertTrue(repaired_search["ok"])
            self.assertTrue(repaired_search["rebuilt_after_error"])
            self.assertTrue(any(result["source_id"] == "claim_index_smoke" for result in repaired_search["results"]))

            search = json.loads(self.run_cli(root, "index", "search", "native zero hit").stdout)
            self.assertTrue(search["ok"])
            self.assertTrue(any(result["source_id"] == "claim_index_smoke" for result in search["results"]))

            claim_id_search = json.loads(self.run_cli(root, "index", "search", "claim_index_smoke").stdout)
            self.assertTrue(claim_id_search["ok"])
            self.assertTrue(claim_id_search["used_fts"])
            self.assertTrue(any(result["source_id"] == "claim_index_smoke" for result in claim_id_search["results"]))

            artifact_id_search = json.loads(self.run_cli(root, "index", "search", "artifact_index_smoke").stdout)
            self.assertTrue(artifact_id_search["ok"])
            self.assertTrue(artifact_id_search["used_fts"])
            self.assertTrue(
                any(result["source_id"] == "artifact_index_smoke" for result in artifact_id_search["results"])
            )

            friction_id_search = json.loads(self.run_cli(root, "index", "search", "friction_index_broad_query").stdout)
            self.assertTrue(friction_id_search["ok"])
            self.assertTrue(friction_id_search["used_fts"])
            self.assertTrue(
                any(result["source_id"] == "friction_index_broad_query" for result in friction_id_search["results"])
            )

            broad = json.loads(
                self.run_cli(
                    root,
                    "index",
                    "search",
                    "--kind",
                    "friction",
                    "native missing overclaim claim drift verification gap",
                ).stdout
            )
            self.assertTrue(broad["ok"])
            self.assertFalse(broad["used_fts"])
            self.assertTrue(any(result["source_id"] == "friction_index_broad_query" for result in broad["results"]))

    def test_parallel_claim_writes_remain_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.run_cli(root, "init")
            processes = []
            for index in range(12):
                processes.append(
                    subprocess.Popen(
                        [
                            sys.executable,
                            str(CLI),
                            "--root",
                            str(root),
                            "claim",
                            "add",
                            "--id",
                            f"claim_{index}",
                            "--claim",
                            f"parallel claim {index}",
                            "--source-path",
                            "test",
                            "--source-kind",
                            "test",
                            "--status",
                            "tested",
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                )
            for process in processes:
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, f"stdout={stdout}\nstderr={stderr}")
            self.run_cli(root, "validate")
            claims = json.loads((root / ".agent" / "claims.json").read_text(encoding="utf-8"))["claims"]
            self.assertEqual({claim["id"] for claim in claims}, {f"claim_{index}" for index in range(12)})
            self.assertFalse((root / ".agent" / ".claims.json.lockdir").exists())

    def test_validate_rejects_malformed_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.run_cli(root, "init")
            with (root / ".agent" / "runs.jsonl").open("a", encoding="utf-8") as handle:
                handle.write("{not json}\n")
            result = self.run_cli(root, "validate", check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid JSONL", result.stderr)


if __name__ == "__main__":
    unittest.main()
