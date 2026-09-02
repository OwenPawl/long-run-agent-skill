from __future__ import annotations

from long_run_agent_skill.compiler import operation

from common import MissionCase, evidence_ref


class CorrectiveRecallRefinementTests(MissionCase):
    def _superseded_history(self) -> None:
        self.evidence("E17", subject="initializer", producer="replacement-trace")
        self.compiler.assert_claim(
            {
                "id": "C4",
                "name": "Initializer always preserves receiver identity",
                "proposition": "Initializer always preserves receiver identity.",
                "subject": "initializer",
                "premises": [evidence_ref("E17")],
                "warrant": {"statement": "receiver-identity warrant"},
                "argument_id": "A7",
            }
        )
        self.compiler.assert_claim(
            {
                "id": "C8",
                "name": "Initializer may replace receiver identity",
                "proposition": "Initializer may replace receiver identity.",
                "subject": "initializer",
                "premises": [evidence_ref("E17")],
                "warrant": {"statement": "replacement-trace warrant"},
                "argument_id": "A8",
            }
        )
        self.compiler.apply(
            {
                "operations": [
                    operation(
                        "relation.asserted",
                        id="R_supersession",
                        source={"type": "claim", "id": "C8"},
                        target={"type": "claim", "id": "C4"},
                        relation="supersedes",
                        basis=[{"type": "evidence", "id": "E17"}],
                        rationale=(
                            "C4 depended on receiver preservation, which the "
                            "replacement trace invalidated."
                        ),
                    )
                ]
            }
        )
        self.compiler.close("historical correction recorded")
        self.compiler.start("current initializer reasoning", run_id="current_initializer")

    def test_noncurrent_claim_is_not_constructive_or_recalled_by_text_alone(self) -> None:
        self._superseded_history()
        write = self.compiler.assert_claim(
            {
                "id": "unrelated_current",
                "proposition": "Initializer always preserves receiver identity.",
                "subject": "different-subject",
            }
        )
        c4 = [
            item for item in write["recall"]["capsules"] if item["entity_id"] == "C4"
        ]
        self.assertEqual(c4, [])
        self.assertNotIn(
            "C4",
            {
                item["entity_id"]
                for item in write["recall"]["candidates"]
                if item["role"] == "constructive"
            },
        )

    def test_matching_abandoned_basis_yields_guarded_corrective_capsule(self) -> None:
        self._superseded_history()
        write = self.compiler.assert_claim(
            {
                "id": "C31",
                "name": "Current initializer reasoning",
                "proposition": "Current reasoning revisits receiver identity.",
                "subject": "initializer",
                "premises": [evidence_ref("E17")],
                "warrant": {"statement": "receiver-identity warrant"},
                "argument_id": "A31",
            }
        )
        capsule = next(
            item for item in write["recall"]["capsules"] if item["entity_id"] == "C4"
        )
        self.assertEqual(capsule["role"], "corrective")
        self.assertTrue(capsule["historical_noncurrent"])
        self.assertEqual(capsule["current_state"], "SUPERSEDED")
        self.assertTrue(capsule["obsolete_content_guard"])
        self.assertTrue(capsule["description"].startswith("Prior claim:"))
        self.assertIn("historical correction", " ".join(capsule["why_relevant_now"]))
        self.assertIn("invalidated", capsule["why_no_longer_current"])
        self.assertEqual(capsule["decisive_path"][0]["id"], "R_supersession")
        self.assertEqual(capsule["current_successor"]["id"], "C8")
        self.assertIn("[SUPERSEDED]", capsule["name"])
        context = self.compiler.context(max_entities=50, max_chars=30000)
        self.assertFalse(
            any(
                item.get("section") == "current_claims"
                and item.get("entity_id") == "C4"
                for item in context["entries"]
            )
        )
        historical = next(
            item
            for item in context["entries"]
            if item.get("section") == "historical_recall"
            and item.get("entity_id") == "C4"
        )
        self.assertIn("[SUPERSEDED]", historical["name"])

    def test_recall_is_novel_relative_to_current_state_and_remains_diverse(self) -> None:
        self.evidence("shared_root", subject="feature")
        self.claim("redundant_history", ["shared_root"], subject="feature")
        self.claim("abandoned_history", ["shared_root"], subject="feature")
        self.claim("replacement", ["shared_root"], subject="feature")
        self.compiler.apply(
            {
                "operations": [
                    operation(
                        "relation.asserted",
                        id="R_replace",
                        source={"type": "claim", "id": "replacement"},
                        target={"type": "claim", "id": "abandoned_history"},
                        relation="supersedes",
                        basis=[{"type": "evidence", "id": "shared_root"}],
                        rationale="The replacement corrects the abandoned reasoning path.",
                    )
                ]
            }
        )
        self.compiler.close("historical setup complete")
        self.compiler.start("current feature reasoning", run_id="novelty_current")
        write = self.compiler.assert_claim(
            {
                "id": "current_claim",
                "proposition": "Current feature reasoning uses the shared observation.",
                "subject": "feature",
                "premises": [evidence_ref("shared_root")],
                "warrant": {
                    "statement": "The recorded observations warrant this scoped proposition."
                },
                "argument_id": "current_argument",
            }
        )
        selected = {item["entity_id"]: item for item in write["recall"]["capsules"]}
        self.assertIn("abandoned_history", selected)
        self.assertEqual(selected["abandoned_history"]["role"], "corrective")
        redundant_candidate = next(
            (
                item
                for item in write["recall"]["candidates"]
                if item["entity_id"] == "redundant_history"
            ),
            None,
        )
        if redundant_candidate:
            self.assertGreater(
                redundant_candidate["features"]["current_state_redundancy"], 0
            )
            self.assertNotIn("redundant_history", selected)
        self.assertGreaterEqual(
            len({item["entity_type"] for item in write["recall"]["capsules"]}),
            2,
        )
        self.assertGreaterEqual(
            len({item["role"] for item in write["recall"]["capsules"]}),
            2,
        )
