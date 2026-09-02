from __future__ import annotations

from pathlib import Path

from long_run_agent_skill.compiler import EpistemicCompiler, operation

from common import MissionCase, claim_ref, evidence_ref


class FirstLargeSliceScenarioTests(MissionCase):
    def test_required_twenty_step_scenario_and_fresh_offline_resume(self) -> None:
        # Steps 1-4: historical evidence and claim, then a fresh current run.
        self.artifact("artifact_correlated", "sha256:correlated")
        self.artifact("artifact_independent", "sha256:independent")
        self.evidence("e1", artifact_id="artifact_correlated", source_run="r1")
        self.evidence("e2", artifact_id="artifact_correlated", source_run="r2")
        self.evidence(
            "e3",
            artifact_id="artifact_independent",
            source_run="r3",
            producer="independent-verifier",
        )
        self.claim("historical_h", ["e1", "e2"], argument_id="argument_h", subject="feature")
        self.compiler.close("historical setup complete")
        self.compiler.start("current feature decision", run_id="run_current")
        self.compiler.observe(
            "dependency",
            {"id": "dependency_b", "name": "Repository revision", "value": "v1", "status": "current"},
        )

        # Steps 2-4: A supports C and the write recalls absent historical H.
        write_a = self.compiler.assert_claim(
            {
                "id": "claim_c",
                "name": "Current feature behavior",
                "description": "Observed feature behavior in the current scope",
                "proposition": "The feature works in the current repository revision.",
                "subject": "feature",
                "premises": [evidence_ref("e1"), evidence_ref("e2")],
                "warrant": {"statement": "Concordant runtime observations warrant the scoped behavior claim."},
                "argument_id": "argument_a",
            }
        )
        self.assertEqual(self.compiler.state()["claims"]["claim_c"]["derived"]["support_state"], "supported")
        recalled_ids = {item["entity_id"] for item in write_a["recall"]["capsules"]}
        self.assertIn("historical_h", recalled_ids)
        self.assertTrue(write_a["suggested"]["relations"])

        # Steps 5-6: expanding and structurally using H infer positive feedback.
        self.compiler.expand("historical_h")
        self.compiler.assert_claim(
            {
                "id": "claim_interpretation",
                "name": "Historical mechanism relevance",
                "description": "The historical mechanism informs current interpretation",
                "proposition": "The historical mechanism is relevant to the current interpretation.",
                "subject": "feature",
                "premises": [claim_ref("historical_h")],
                "warrant": {"statement": "The historical conclusion supplies the stated interpretive premise."},
                "argument_id": "argument_interpretation",
            }
        )
        retrieval_events = self.compiler.state()["retrieval_events"]
        self.assertTrue(any(item["event"] == "retrieval.expanded" for item in retrieval_events))
        structural = [item for item in retrieval_events if item["event"] == "retrieval.structurally_used"]
        self.assertIn("historical_h", structural[-1]["entity_ids"])

        # Step 9 is installed before the attack so the reducer can prove surviving support.
        self.compiler.assert_argument(
            {
                "id": "argument_b",
                "name": "Independent verifier argument",
                "premises": [evidence_ref("e3")],
                "dependencies": [{"dependency_id": "dependency_b"}],
                "warrant": {"statement": "An independent verifier observation warrants the same claim."},
                "conclusion": {"type": "claim", "id": "claim_c", "polarity": "support"},
                "subject": "feature",
            }
        )
        self.compiler.decide(
            {
                "id": "decision_d",
                "name": "Feature shipment decision",
                "choice": "Ship the feature.",
                "alternatives": ["Do not ship the feature."],
                "basis_claim_ids": ["claim_c"],
            }
        )
        self.compiler.ask(
            {
                "id": "question_q",
                "name": "Current feature status",
                "question": "Does the feature work in the current revision?",
                "related_claim_ids": ["claim_c"],
                "candidate_claim_ids": ["claim_c"],
            }
        )
        self.compiler.apply(
            {
                "operations": [
                    operation(
                        "question.resolved",
                        id="question_q",
                        answer="yes",
                        resolution_basis_claim_ids=["claim_c"],
                    )
                ]
            }
        )

        # Steps 7-10: warranted E4 undercuts A, while independent B preserves C.
        self.evidence(
            "e4",
            source_run="r4",
            producer="measurement-auditor",
            description="The first measurement path is unreliable.",
        )
        attack_write = self.compiler.attack(
            {
                "id": "attack_x",
                "name": "Measurement-path undercutter",
                "attack_type": "undercut",
                "target": {"type": "argument", "id": "argument_a"},
                "grounds": [evidence_ref("e4")],
                "warrant": {"statement": "An unreliable measurement path defeats that inference."},
            }
        )
        state = self.compiler.state()
        self.assertFalse(state["arguments"]["argument_a"]["active"])
        self.assertTrue(state["arguments"]["argument_b"]["active"])
        self.assertEqual(state["claims"]["claim_c"]["derived"]["support_state"], "supported")
        self.assertEqual(state["claims"]["claim_c"]["derived"]["applicability_state"], "applicable")
        self.assertNotIn("SUPPORT_LOST", {item["code"] for item in attack_write["diagnostics"]["items"]})

        # Steps 11-15: B loses scope, C needs revalidation, and D/Q are affected.
        lost_write = self.compiler.observe(
            "dependency",
            {"id": "dependency_b", "name": "Repository revision", "value": "v2", "status": "current"},
        )
        state = self.compiler.state()
        claim_state = state["claims"]["claim_c"]["derived"]
        self.assertFalse(state["arguments"]["argument_b"]["active"])
        self.assertEqual(claim_state["support_state"], "unsupported")
        self.assertTrue(claim_state["historical_support"])
        self.assertEqual(claim_state["applicability_state"], "revalidation_required")
        self.assertEqual(state["questions"]["question_q"]["derived_status"], "reopened")
        self.assertTrue(state["decisions"]["decision_d"]["basis_changed"])
        codes = {item["code"] for item in lost_write["diagnostics"]["items"]}
        self.assertTrue({"SUPPORT_LOST", "DECISION_BASIS_CHANGED", "QUESTION_REOPENED"} <= codes)
        self.assertEqual(claim_state["revalidation_plan"][0]["argument_id"], "argument_b")

        # Steps 16-20: checkpoint, delete derived state, and resume with zero model calls.
        self.compiler.checkpoint("checkpoint_release_scenario")
        expected_hash = self.compiler.rebuild()["state_hash"]
        Path(self.root, ".agent", "state.sqlite").unlink()
        fresh = EpistemicCompiler(self.root)
        resumed = fresh.resume()
        self.assertEqual(resumed["model_calls"], 0)
        self.assertEqual(resumed["rebuild"]["state_hash"], expected_hash)
        sections = {item["section"] for item in resumed["context"]["entries"]}
        self.assertTrue(
            {"current_claims", "open_questions", "affected_decisions", "diagnostics", "historical_recall"}
            <= sections
        )
        self.assertNotIn("raw_transcript", resumed["context"])
        self.assertLessEqual(
            resumed["context"]["budget"]["used_chars"],
            resumed["context"]["budget"]["max_chars"],
        )
        metrics = fresh.query("telemetry")["result"]
        self.assertGreaterEqual(metrics["resume"]["resume_events"], 1)
        self.assertGreaterEqual(metrics["resume"]["zero_model_call_resumes"], 1)
        self.assertEqual(fresh.validate()["status"], "success")
