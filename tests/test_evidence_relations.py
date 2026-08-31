from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "scripts" / "mission_harness.py"
RECENT = REPO_ROOT / "scripts" / "mission_records_recent.py"


class EvidenceRelationshipTests(unittest.TestCase):
    def run_cli(
        self,
        root: pathlib.Path,
        *args: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(CLI), "--root", str(root), *args],
            text=True,
            capture_output=True,
            check=False,
        )
        if check and result.returncode != 0:
            self.fail(f"command failed: {args}\nstdout={result.stdout}\nstderr={result.stderr}")
        return result

    def init(self, root: pathlib.Path) -> None:
        self.run_cli(root, "init")

    def add_claim(self, root: pathlib.Path, claim_id: str, claim: str = "candidate behavior") -> dict:
        result = self.run_cli(
            root,
            "claim",
            "add",
            "--id",
            claim_id,
            "--claim",
            claim,
            "--source-path",
            "evidence/observation.json",
            "--source-kind",
            "observation",
            "--status",
            "tested",
            "--confidence",
            "verified",
        )
        return json.loads(result.stdout)

    def add_artifact(self, root: pathlib.Path, artifact_id: str) -> None:
        self.run_cli(
            root,
            "artifact",
            "add",
            "--id",
            artifact_id,
            "--path",
            f"evidence/{artifact_id}.json",
            "--kind",
            "runtime_trace",
            "--description",
            f"runtime evidence {artifact_id}",
        )

    def read_claims(self, root: pathlib.Path) -> dict:
        return json.loads((root / ".agent" / "claims.json").read_text(encoding="utf-8"))

    def read_relations(self, root: pathlib.Path) -> list[dict]:
        path = root / ".agent" / "evidence_relations.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def test_claim_add_appends_ordered_revisions_without_mutating_prior_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.init(root)
            first = self.add_claim(root, "claim_history", "first wording")
            first_record = self.read_claims(root)["claims"][0]
            second = self.add_claim(root, "claim_history", "corrected wording")
            claims = self.read_claims(root)

            self.assertEqual(claims["schema_version"], "claims.v2")
            self.assertEqual(claims["claims"][0], first_record)
            self.assertEqual([record["revision"] for record in claims["claims"]], [1, 2])
            self.assertEqual(claims["claims"][1]["supersedes_revision_id"], first["revision_id"])
            self.assertEqual(second["revision_id"], "claim_history:r2")
            self.run_cli(root, "validate")

    def test_legacy_claims_v1_upgrade_preserves_original_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.init(root)
            legacy = {
                "schema_version": "claims.v1",
                "claims": [
                    {
                        "id": "legacy_claim",
                        "claim": "legacy assertion",
                        "source_path": "legacy.log",
                        "source_kind": "log",
                        "status": "tested",
                        "verification_command": "verify legacy",
                        "last_checked_at": "2026-01-01T00:00:00Z",
                        "confidence": "verified",
                        "notes": "preserve me",
                    }
                ],
            }
            (root / ".agent" / "claims.json").write_text(json.dumps(legacy), encoding="utf-8")

            result = self.add_claim(root, "legacy_claim", "revised assertion")
            claims = self.read_claims(root)

            self.assertTrue(result["migrated_from_claims_v1"])
            self.assertEqual(claims["schema_version"], "claims.v2")
            self.assertEqual(claims["claims"][0]["claim"], "legacy assertion")
            self.assertTrue(claims["claims"][0]["legacy_revision"])
            self.assertEqual(claims["claims"][1]["supersedes_revision_id"], "legacy_claim:r1")
            self.run_cli(root, "validate")

    def test_legacy_mission_without_relation_ledger_is_extended_automatically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.init(root)
            relation_path = root / ".agent" / "evidence_relations.jsonl"
            relation_path.unlink()

            self.run_cli(root, "validate")

            self.assertTrue(relation_path.is_file())
            self.assertEqual(relation_path.read_text(encoding="utf-8"), "")

    def test_disprove_appends_revision_and_associates_refuting_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.init(root)
            original = self.add_claim(root, "claim_disproved")
            self.add_artifact(root, "artifact_static")
            self.add_artifact(root, "artifact_runtime")

            result = json.loads(
                self.run_cli(
                    root,
                    "claim",
                    "disprove",
                    "--id",
                    "claim_disproved",
                    "--evidence-artifact",
                    "artifact_static",
                    "--evidence-artifact",
                    "artifact_runtime",
                    "--notes",
                    "both independent observations contradict the assertion",
                ).stdout
            )
            claims = self.read_claims(root)["claims"]
            relations = self.read_relations(root)

            self.assertEqual(len(claims), 2)
            self.assertEqual(claims[-1]["status"], "disproved")
            self.assertEqual(claims[-1]["supersedes_revision_id"], original["revision_id"])
            self.assertEqual(set(claims[-1]["evidence_relation_ids"]), set(result["relation_ids"]))
            self.assertEqual({record["relation"] for record in relations}, {"refutes"})
            self.assertEqual(
                {record["target"]["id"] for record in relations},
                {original["revision_id"]},
            )
            self.assertEqual(
                {record["source"]["id"] for record in relations},
                {"artifact_static", "artifact_runtime"},
            )

            history = json.loads(
                self.run_cli(root, "claim", "history", "--id", "claim_disproved").stdout
            )
            self.assertEqual(history["revision_count"], 2)
            self.assertEqual(history["latest"]["status"], "disproved")
            self.assertEqual(len(history["relations"]), 2)
            self.run_cli(root, "validate")

    def test_disprove_is_idempotent_for_same_latest_evidence_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.init(root)
            self.add_claim(root, "claim_idempotent")
            self.add_artifact(root, "artifact_refutation")
            args = (
                "claim",
                "disprove",
                "--id",
                "claim_idempotent",
                "--evidence-artifact",
                "artifact_refutation",
            )

            first = json.loads(self.run_cli(root, *args).stdout)
            second = json.loads(self.run_cli(root, *args).stdout)

            self.assertTrue(first["appended"])
            self.assertFalse(second["appended"])
            self.assertTrue(second["existing"])
            self.assertEqual(len(self.read_claims(root)["claims"]), 2)
            self.assertEqual(len(self.read_relations(root)), 1)

    def test_disproved_status_requires_specialized_evidence_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.init(root)
            result = self.run_cli(
                root,
                "claim",
                "add",
                "--id",
                "invalid_disproof",
                "--claim",
                "cannot be disproved without evidence",
                "--source-path",
                "none",
                "--source-kind",
                "none",
                "--status",
                "disproved",
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("use 'claim disprove'", result.stderr)
            self.assertEqual(self.read_claims(root)["claims"], [])

    def test_relation_add_validates_endpoints_and_lists_typed_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.init(root)
            claim = self.add_claim(root, "claim_supported")
            self.add_artifact(root, "artifact_support")

            added = json.loads(
                self.run_cli(
                    root,
                    "relation",
                    "add",
                    "--source-type",
                    "artifact",
                    "--source-id",
                    "artifact_support",
                    "--relation",
                    "supports",
                    "--target-type",
                    "claim_revision",
                    "--target-id",
                    claim["revision_id"],
                    "--confidence",
                    "verified",
                ).stdout
            )
            listed = json.loads(
                self.run_cli(
                    root,
                    "relation",
                    "list",
                    "--endpoint-type",
                    "artifact",
                    "--endpoint-id",
                    "artifact_support",
                ).stdout
            )

            self.assertTrue(added["appended"])
            self.assertEqual(listed["count"], 1)
            self.assertEqual(listed["relations"][0]["relation"], "supports")

            self.add_artifact(root, "artifact_retraction")
            self.run_cli(
                root,
                "relation",
                "add",
                "--source-type",
                "artifact",
                "--source-id",
                "artifact_retraction",
                "--relation",
                "retracts",
                "--target-type",
                "relation",
                "--target-id",
                added["id"],
            )
            history = json.loads(
                self.run_cli(root, "claim", "history", "--id", "claim_supported").stdout
            )
            self.assertEqual({relation["relation"] for relation in history["relations"]}, {"supports", "retracts"})
            self.run_cli(root, "validate")

            invalid = self.run_cli(
                root,
                "relation",
                "add",
                "--source-type",
                "artifact",
                "--source-id",
                "missing",
                "--relation",
                "supports",
                "--target-type",
                "claim",
                "--target-id",
                "claim_supported",
                check=False,
            )
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("unknown artifact endpoint", invalid.stderr)

    def test_run_close_records_and_validates_relationship_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.init(root)
            run_id = json.loads(
                self.run_cli(root, "run", "start", "--goal", "record typed evidence").stdout
            )["run_id"]
            claim = self.add_claim(root, "claim_run_relation")
            self.add_artifact(root, "artifact_run_relation")
            relation = json.loads(
                self.run_cli(
                    root,
                    "relation",
                    "add",
                    "--source-type",
                    "artifact",
                    "--source-id",
                    "artifact_run_relation",
                    "--relation",
                    "supports",
                    "--target-type",
                    "claim_revision",
                    "--target-id",
                    claim["revision_id"],
                    "--run-id",
                    run_id,
                ).stdout
            )
            self.run_cli(
                root,
                "run",
                "close",
                "--run-id",
                run_id,
                "--outcome",
                "relationship recorded",
                "--test",
                "evidence relationship lifecycle test",
                "--relation",
                relation["id"],
            )

            runs = [
                json.loads(line)
                for line in (root / ".agent" / "runs.jsonl").read_text(encoding="utf-8").splitlines()
                if line
            ]
            self.assertEqual(runs[-1]["evidence_relations"], [relation["id"]])
            self.run_cli(root, "validate")

            runs[-1]["evidence_relations"] = ["missing_relation"]
            (root / ".agent" / "runs.jsonl").write_text(
                "".join(json.dumps(record) + "\n" for record in runs),
                encoding="utf-8",
            )
            invalid = self.run_cli(root, "validate", check=False)
            self.assertIn("unknown evidence relation", invalid.stderr)

    def test_duplicate_artifact_id_is_rejected_as_ambiguous_relation_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.init(root)
            claim = self.add_claim(root, "claim_ambiguous")
            self.add_artifact(root, "artifact_duplicate")
            self.add_artifact(root, "artifact_duplicate")

            result = self.run_cli(
                root,
                "relation",
                "add",
                "--source-type",
                "artifact",
                "--source-id",
                "artifact_duplicate",
                "--relation",
                "supports",
                "--target-type",
                "claim_revision",
                "--target-id",
                claim["revision_id"],
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ambiguous artifact endpoint", result.stderr)
            self.assertEqual(self.read_relations(root), [])

    def test_validate_rejects_disproved_revision_with_missing_relation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.init(root)
            self.add_claim(root, "claim_corrupt")
            claims = self.read_claims(root)
            corrupt = {
                **claims["claims"][0],
                "revision_id": "claim_corrupt:r2",
                "revision": 2,
                "supersedes_revision_id": "claim_corrupt:r1",
                "status": "disproved",
                "evidence_relation_ids": ["missing_relation"],
            }
            claims["claims"].append(corrupt)
            (root / ".agent" / "claims.json").write_text(json.dumps(claims), encoding="utf-8")

            result = self.run_cli(root, "validate", check=False)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unknown evidence relation", result.stderr)

    def test_recent_records_and_index_include_relations_and_historical_revisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.init(root)
            self.add_claim(root, "claim_search", "searchable initial assertion")
            self.add_artifact(root, "artifact_search")
            self.run_cli(
                root,
                "claim",
                "disprove",
                "--id",
                "claim_search",
                "--evidence-artifact",
                "artifact_search",
                "--notes",
                "runtime contradiction remains searchable",
            )

            recent = subprocess.run(
                [sys.executable, str(RECENT), "--root", str(root), "relations", "--limit", "1"],
                text=True,
                capture_output=True,
                check=True,
            )
            recent_payload = json.loads(recent.stdout)
            rebuild = json.loads(self.run_cli(root, "index", "rebuild").stdout)
            revision_search = json.loads(
                self.run_cli(root, "index", "search", "--kind", "claim_revision", "searchable initial").stdout
            )
            relation_search = json.loads(
                self.run_cli(root, "index", "search", "--kind", "evidence_relation", "runtime contradiction").stdout
            )

            self.assertEqual(recent_payload["count"], 1)
            self.assertTrue(rebuild["ok"])
            self.assertGreaterEqual(len(revision_search["results"]), 2)
            self.assertEqual(relation_search["results"][0]["kind"], "evidence_relation")

    def test_parallel_revisions_for_one_claim_form_a_complete_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.init(root)
            processes = []
            for index in range(8):
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
                            "claim_parallel",
                            "--claim",
                            f"parallel revision {index}",
                            "--source-path",
                            "parallel.log",
                            "--source-kind",
                            "test",
                            "--status",
                            "tested",
                        ],
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                )
            for process in processes:
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, f"stdout={stdout}\nstderr={stderr}")

            revisions = self.read_claims(root)["claims"]
            self.assertEqual([record["revision"] for record in revisions], list(range(1, 9)))
            self.assertEqual(revisions[0]["supersedes_revision_id"], "")
            for previous, current in zip(revisions, revisions[1:]):
                self.assertEqual(current["supersedes_revision_id"], previous["revision_id"])
            self.run_cli(root, "validate")


if __name__ == "__main__":
    unittest.main()
