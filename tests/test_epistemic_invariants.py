from __future__ import annotations

import json

from long_run_agent_skill.compiler import operation
from long_run_agent_skill.errors import ConflictError, LedgerError, SemanticError

from common import MissionCase, claim_ref, evidence_ref


class EpistemicInvariantTests(MissionCase):
    def test_content_identity_does_not_merge_distinct_observations(self) -> None:
        self.artifact("artifact_a", "sha256:same")
        duplicate = self.artifact("artifact_b", "sha256:same")
        self.assertEqual(
            duplicate["committed"]["deduplicated"][0]["canonical_id"], "artifact_a"
        )
        self.evidence("observation_1", artifact_id="artifact_a", source_run="run_1")
        self.evidence("observation_2", artifact_id="artifact_a", source_run="run_2")
        self.compiler.assert_claim(
            {
                "id": "claim_identity",
                "name": "Identity behavior",
                "description": "Two observations support one scoped behavior claim",
                "proposition": "The observed identity behavior is stable.",
                "premises": [evidence_ref("observation_1")],
                "warrant": {"statement": "The first observation warrants the claim."},
                "argument_id": "argument_1",
            }
        )
        self.compiler.assert_argument(
            {
                "id": "argument_2",
                "name": "Second correlated argument",
                "premises": [evidence_ref("observation_2")],
                "warrant": {"statement": "The second observation warrants the same claim."},
                "conclusion": {"type": "claim", "id": "claim_identity", "polarity": "support"},
            }
        )
        state = self.compiler.state()
        self.assertEqual(set(state["evidence"]), {"observation_1", "observation_2"})
        independence = state["claims"]["claim_identity"]["derived"]["independence"]
        self.assertEqual(independence["independent_group_count"], 1)
        reasons = independence["pairs"][0]["dependence_reasons"]
        self.assertIn("same_artifact", reasons)
        self.assertIn(
            "NONINDEPENDENT_SUPPORT", {item["code"] for item in state["diagnostics"]}
        )

    def test_ungrounded_support_cycle_is_rejected_by_least_fixed_point(self) -> None:
        self.compiler.assert_claim(
            {"id": "claim_x", "name": "Claim X", "description": "Proposition X", "proposition": "X"}
        )
        self.compiler.assert_claim(
            {"id": "claim_y", "name": "Claim Y", "description": "Proposition Y", "proposition": "Y"}
        )
        self.compiler.apply(
            {
                "operations": [
                    operation(
                        "argument.asserted",
                        id="argument_x",
                        name="Y implies X",
                        premises=[claim_ref("claim_y")],
                        warrant={"statement": "Explicit cyclic rule Y to X"},
                        conclusion={"type": "claim", "id": "claim_x", "polarity": "support"},
                    ),
                    operation(
                        "argument.asserted",
                        id="argument_y",
                        name="X implies Y",
                        premises=[claim_ref("claim_x")],
                        warrant={"statement": "Explicit cyclic rule X to Y"},
                        conclusion={"type": "claim", "id": "claim_y", "polarity": "support"},
                    ),
                ]
            }
        )
        state = self.compiler.state()
        self.assertFalse(state["arguments"]["argument_x"]["active"])
        self.assertFalse(state["arguments"]["argument_y"]["active"])
        self.assertEqual(state["claims"]["claim_x"]["derived"]["support_state"], "unsupported")
        self.assertEqual(state["claims"]["claim_y"]["derived"]["support_state"], "unsupported")
        self.assertEqual(state["circular_entity_ids"], ["claim_x", "claim_y"])
        self.assertIn("CIRCULAR_JUSTIFICATION", {item["code"] for item in state["diagnostics"]})

    def test_warranted_attack_can_deactivate_and_reactivate(self) -> None:
        self.evidence("support")
        self.evidence("defeater")
        self.claim("claim_a", ["support"], argument_id="argument_a")
        self.compiler.observe(
            "dependency",
            {"id": "attack_environment", "name": "Attack environment", "value": "v1", "status": "current"},
        )
        self.compiler.attack(
            {
                "id": "attack_a",
                "name": "Scoped undercutter",
                "attack_type": "undercut",
                "target": {"type": "argument", "id": "argument_a"},
                "grounds": [evidence_ref("defeater")],
                "warrant": {"statement": "The defeater invalidates this inference in v1."},
                "dependencies": [{"dependency_id": "attack_environment"}],
            }
        )
        self.assertFalse(self.compiler.state()["arguments"]["argument_a"]["active"])
        self.compiler.observe(
            "dependency",
            {"id": "attack_environment", "name": "Attack environment", "value": "v2", "status": "current"},
        )
        state = self.compiler.state()
        self.assertFalse(state["attacks"]["attack_a"]["active"])
        self.assertTrue(state["arguments"]["argument_a"]["active"])
        self.assertEqual(state["claims"]["claim_a"]["derived"]["support_state"], "supported")

    def test_rebut_contests_and_undermine_attacks_a_premise(self) -> None:
        self.evidence("support")
        self.evidence("contrary")
        self.claim("claim_a", ["support"], argument_id="argument_a")
        self.compiler.attack(
            {
                "id": "rebut_a",
                "name": "Contrary conclusion attack",
                "attack_type": "rebut",
                "target": {"type": "claim", "id": "claim_a"},
                "grounds": [evidence_ref("contrary")],
                "warrant": {"statement": "The contrary observation warrants opposition."},
            }
        )
        self.assertEqual(self.compiler.state()["claims"]["claim_a"]["derived"]["support_state"], "contested")
        self.compiler.attack(
            {
                "id": "undermine_support",
                "name": "Premise reliability attack",
                "attack_type": "undermine",
                "target": {"type": "evidence", "id": "support"},
                "grounds": [evidence_ref("contrary")],
                "warrant": {"statement": "The audit observation undermines the premise."},
            }
        )
        state = self.compiler.state()
        self.assertFalse(state["arguments"]["argument_a"]["active"])
        self.assertIn("PREMISE_UNDERMINED", {item["code"] for item in state["diagnostics"]})

    def test_assumption_is_distinct_from_dependency_and_can_be_withdrawn(self) -> None:
        self.evidence("support")
        self.compiler.assert_assumption(
            {
                "id": "assumption_scope",
                "name": "Fixture representativeness",
                "description": "The fixture represents the deployment scope",
                "proposition": "The fixture represents production behavior.",
            }
        )
        self.compiler.observe(
            "dependency",
            {"id": "repo_head", "name": "Repository head", "value": "abc", "status": "current"},
        )
        self.claim(
            "claim_conditioned",
            ["support"],
            assumptions=[{"assumption_id": "assumption_scope"}],
            dependencies=[{"dependency_id": "repo_head"}],
        )
        self.assertTrue(self.compiler.state()["arguments"]["arg_claim_conditioned"]["active"])
        self.compiler.apply(
            {"operations": [operation("assumption.withdrawn", id="assumption_scope", reason="scope rejected")]}
        )
        state = self.compiler.state()
        self.assertFalse(state["arguments"]["arg_claim_conditioned"]["active"])
        self.assertIn("assumption_scope", state["arguments"]["arg_claim_conditioned"]["blockers"][0])

    def test_verification_receipt_tracks_inputs_and_dependency_validity(self) -> None:
        self.evidence("verification_input")
        self.compiler.observe(
            "dependency",
            {"id": "verifier_version", "name": "Verifier version", "value": "1", "status": "current"},
        )
        self.compiler.observe(
            "verification",
            {
                "id": "receipt_1",
                "name": "Verifier receipt",
                "verifier": "test-verifier",
                "verifier_version": "1",
                "specification": "run deterministic check",
                "inputs": [evidence_ref("verification_input")],
                "output_hashes": ["sha256:output"],
                "outcome": "pass",
                "dependencies": [{"dependency_id": "verifier_version"}],
            },
        )
        self.compiler.assert_claim(
            {
                "id": "verified_claim",
                "name": "Verified behavior",
                "description": "Behavior checked by the verifier",
                "proposition": "The verifier accepted the behavior.",
                "premises": [{"type": "verification", "id": "receipt_1"}],
                "warrant": {"statement": "A passing receipt warrants this verification claim."},
                "argument_id": "verified_argument",
            }
        )
        state = self.compiler.state()
        self.assertEqual(state["claims"]["verified_claim"]["derived"]["verification_state"], "verified")
        self.assertEqual(state["arguments"]["verified_argument"]["root_evidence"], ["verification_input"])
        self.compiler.observe(
            "dependency",
            {"id": "verifier_version", "name": "Verifier version", "value": "2", "status": "current"},
        )
        self.assertFalse(self.compiler.state()["verifications"]["receipt_1"]["valid"])
        self.assertEqual(
            self.compiler.state()["claims"]["verified_claim"]["derived"]["applicability_state"],
            "revalidation_required",
        )

    def test_preview_is_non_authoritative_and_generation_preconditions_are_enforced(self) -> None:
        generation = self.compiler.store.ledger_head()["generation"]
        preview = self.compiler.observe(
            "evidence",
            {"id": "preview_evidence", "name": "Preview observation", "description": "Not committed"},
            preview=True,
        )
        self.assertEqual(preview["committed"]["status"], "not_committed")
        self.assertEqual(self.compiler.store.ledger_head()["generation"], generation)
        with self.assertRaises(ConflictError):
            self.compiler.apply(
                {
                    "operations": [
                        operation(
                            "evidence.registered",
                            id="conflict_evidence",
                            name="Conflict observation",
                            description="Conflicting write",
                        )
                    ]
                },
                expected_generation=generation - 1,
            )

    def test_attack_without_grounds_or_warrant_is_rejected(self) -> None:
        self.evidence("support")
        self.claim("claim_a", ["support"], argument_id="argument_a")
        with self.assertRaises(SemanticError):
            self.compiler.attack(
                {
                    "id": "invalid_attack",
                    "attack_type": "undercut",
                    "target": {"type": "argument", "id": "argument_a"},
                }
            )

    def test_hash_chain_tampering_is_detected(self) -> None:
        self.evidence("tamper_target")
        path = self.root / ".agent" / "ledger.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        transaction = json.loads(lines[-1])
        transaction["operations"][0]["data"]["description"] = "tampered"
        lines[-1] = json.dumps(transaction, sort_keys=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaises(LedgerError):
            self.compiler.validate()
