from __future__ import annotations

from long_run_agent_skill.compiler import operation

from common import MissionCase, evidence_ref


class WorkerStateRefinementTests(MissionCase):
    def _historical_evidence(self, entity_id: str, subject: str) -> None:
        self.evidence(entity_id, subject=subject)
        self.compiler.close("historical setup complete")
        self.compiler.start("current work", run_id=f"current_{entity_id}")

    def test_surface_adds_weak_coverage_and_inhibits_unchanged_repeat(self) -> None:
        self._historical_evidence("historical_needle", "needle")
        search = self.compiler.query("search", text="historical needle")
        self.assertEqual(search["result"][0]["entity_id"], "historical_needle")

        coverage = self.compiler.state()["worker_state"]["historical_needle"]
        self.assertEqual(coverage["content_coverage"]["estimate"], 0.25)
        self.assertEqual(coverage["salience"]["estimate"], 0.45)
        self.assertEqual(coverage["structural_coverage"]["estimate"], 0.0)

        write = self.evidence("current_needle", subject="needle")
        recalled = {item["entity_id"] for item in write["recall"]["capsules"]}
        self.assertNotIn("historical_needle", recalled)

    def test_reference_structural_use_and_expansion_affect_separate_dimensions(self) -> None:
        self._historical_evidence("historical_signal", "signal")
        self.compiler.query("search", text="historical signal")

        reference_write = self.compiler.apply(
            {
                "operations": [
                    operation(
                        "annotation.revised",
                        id="annotation_signal",
                        entity_type="evidence",
                        entity_id="historical_signal",
                        name="Historical signal observation",
                        reason="Use a clearer active label",
                    )
                ]
            }
        )
        referenced_events = [
            item
            for item in reference_write["telemetry"]["events"]
            if item["type"] == "retrieval.referenced"
        ]
        self.assertEqual(referenced_events[0]["data"]["entity_ids"], ["historical_signal"])
        referenced = self.compiler.state()["worker_state"]["historical_signal"]
        self.assertEqual(referenced["salience"]["estimate"], 0.9)
        self.assertEqual(referenced["structural_coverage"]["estimate"], 0.0)
        self.assertLess(referenced["content_coverage"]["estimate"], 0.5)

        self.compiler.assert_claim(
            {
                "id": "claim_using_signal",
                "proposition": "The historical signal informs the current result.",
                "subject": "signal",
                "premises": [evidence_ref("historical_signal")],
                "warrant": {"statement": "The observation grounds the result."},
                "argument_id": "argument_using_signal",
            }
        )
        structural = self.compiler.state()["worker_state"]["historical_signal"]
        self.assertEqual(structural["structural_coverage"]["estimate"], 1.0)
        self.assertLess(structural["content_coverage"]["estimate"], 1.0)
        self.assertGreaterEqual(structural["retrieval_outcomes"]["referenced"], 1)
        self.assertGreaterEqual(
            structural["retrieval_outcomes"]["structurally_used"], 1
        )

        expanded = self.compiler.expand("historical_signal")
        self.assertEqual(
            expanded["worker_state_coverage"]["content_coverage"]["estimate"],
            1.0,
        )
        final = self.compiler.query("worker-state", "historical_signal")["result"][0]
        self.assertGreaterEqual(final["retrieval_outcomes"]["expanded"], 1)
        self.assertEqual(final["content_coverage"]["estimate"], 1.0)

    def test_dismissal_strength_is_contextual_and_non_use_is_neutral(self) -> None:
        self.evidence("weak_item", subject="weak-dismissal")
        self.evidence("strong_item", subject="strong-dismissal")
        self.evidence("neutral_item", subject="neutral-no-use")
        self.compiler.close("historical setup complete")
        self.compiler.start("current feedback work", run_id="feedback_current")

        self.compiler.query("search", text="weak item")
        weak_source = self.compiler.state()["retrieval_events"][-1]
        weak = self.compiler.feedback(weak_source["id"], "weak_item", "dismissed")
        self.assertEqual(weak["feedback_strength"], "weak")
        self.assertFalse(weak["globally_demoted"])

        self.compiler.query("search", text="strong item")
        strong_source = self.compiler.state()["retrieval_events"][-1]
        self.compiler.expand("strong_item")
        strong = self.compiler.feedback(
            strong_source["id"], "strong_item", "dismissed"
        )
        self.assertEqual(strong["feedback_strength"], "strong")
        self.assertFalse(strong["globally_demoted"])

        self.compiler.query("search", text="neutral item")
        state = self.compiler.state()
        weak_state = state["worker_state"]["weak_item"]
        strong_state = state["worker_state"]["strong_item"]
        neutral_state = state["worker_state"]["neutral_item"]
        self.assertEqual(weak_state["retrieval_outcomes"]["dismissed_weak"], 1)
        self.assertEqual(strong_state["retrieval_outcomes"]["dismissed_strong"], 1)
        self.assertEqual(neutral_state["retrieval_outcomes"]["dismissed_weak"], 0)
        self.assertEqual(neutral_state["retrieval_outcomes"]["dismissed_strong"], 0)
        self.assertIn(
            "weak_item",
            {
                item["entity_id"]
                for item in self.compiler.query("search", text="weak item")["result"]
            },
        )

    def test_material_change_overrides_recent_surface_inhibition(self) -> None:
        self.evidence("historical_root", subject="feature")
        self.claim(
            "historical_claim",
            ["historical_root"],
            argument_id="historical_argument",
            subject="feature",
        )
        self.compiler.close("historical setup complete")
        self.compiler.start("current reasoning", run_id="material_current")
        first = self.compiler.assert_claim(
            {
                "id": "current_claim",
                "proposition": "The current feature behavior is understood.",
                "subject": "feature",
                "premises": [evidence_ref("historical_root")],
                "warrant": {
                    "statement": "The recorded observations warrant this scoped proposition."
                },
                "argument_id": "current_argument",
            }
        )
        self.assertIn(
            "historical_claim",
            {item["entity_id"] for item in first["recall"]["capsules"]},
        )
        self.evidence("undercutter_ground", subject="feature")
        changed = self.compiler.attack(
            {
                "id": "historical_undercutter",
                "attack_type": "undercut",
                "target": {"type": "argument", "id": "historical_argument"},
                "grounds": [evidence_ref("undercutter_ground")],
                "warrant": {
                    "statement": "The recorded observations warrant this scoped proposition."
                },
                "subject": "feature",
            }
        )
        capsule = next(
            item
            for item in changed["recall"]["capsules"]
            if item["entity_id"] == "historical_claim"
        )
        self.assertTrue(capsule["material_change"]["changed"])
        self.assertEqual(capsule["role"], "corrective")
        self.assertIn("support_state", capsule["material_change"]["fields"])

    def test_accepted_relation_suggestion_is_inferred_positive_outcome(self) -> None:
        self.evidence("suggestion_root", subject="suggestion")
        self.claim(
            "historical_suggestion_claim",
            ["suggestion_root"],
            subject="suggestion",
        )
        self.compiler.close("historical setup complete")
        self.compiler.start("current suggestion work", run_id="suggestion_current")
        write = self.compiler.assert_claim(
            {
                "id": "current_suggestion_claim",
                "proposition": "The current claim refines historical suggestion work.",
                "subject": "suggestion",
                "premises": [evidence_ref("suggestion_root")],
                "warrant": {
                    "statement": "The recorded observations warrant this scoped proposition."
                },
                "argument_id": "current_suggestion_argument",
            }
        )
        suggestion = write["suggested"]["relations"][0]
        self.compiler.feedback(
            suggestion["id"], "historical_suggestion_claim", "accepted"
        )
        outcome = self.compiler.state()["worker_state"][
            "historical_suggestion_claim"
        ]["retrieval_outcomes"]
        self.assertEqual(outcome["relation_suggestion_accepted"], 1)
