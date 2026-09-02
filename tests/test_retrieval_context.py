from __future__ import annotations

from pathlib import Path

from long_run_agent_skill.compiler import operation

from common import MissionCase, evidence_ref


class RetrievalAndContextTests(MissionCase):
    def test_correlated_recall_uses_representatives_and_role_diversity(self) -> None:
        self.artifact("trace_artifact", "sha256:trace")
        for index in range(4):
            self.evidence(
                f"historical_e{index}",
                artifact_id="trace_artifact",
                source_run=f"historical_run_{index}",
                producer="runtime-tracer",
                subject="initializer",
            )
        self.claim(
            "historical_claim",
            ["historical_e0"],
            argument_id="historical_argument",
            subject="initializer",
        )
        self.compiler.close("historical records complete")
        self.compiler.start("current initializer work", run_id="run_current")
        write = self.evidence(
            "current_evidence",
            artifact_id="trace_artifact",
            source_run="current_run",
            producer="runtime-tracer",
            subject="initializer",
        )
        capsules = write["recall"]["capsules"]
        evidence_capsules = [item for item in capsules if item["entity_type"] == "evidence"]
        self.assertEqual(len(evidence_capsules), 1)
        self.assertGreaterEqual(
            evidence_capsules[0]["represents_family"]["related_candidate_count"], 2
        )
        self.assertIn("claim", {item["entity_type"] for item in capsules})
        event = self.compiler.state()["retrieval_events"][-1]
        self.assertEqual(event["event"], "retrieval.surfaced")
        self.assertTrue(all("features" in item for item in event["candidates"]))

    def test_relation_suggestion_lifecycle_does_not_silently_assert_relation(self) -> None:
        self.evidence("shared")
        self.claim("historical", ["shared"], argument_id="argument_h", subject="shared-subject")
        self.compiler.close("history complete")
        self.compiler.start("new work", run_id="run_new")
        write = self.compiler.assert_claim(
            {
                "id": "current",
                "name": "Current scoped behavior",
                "description": "Current observation-based behavior",
                "proposition": "The current behavior refines the historical behavior.",
                "subject": "shared-subject",
                "premises": [evidence_ref("shared")],
                "warrant": {"statement": "The shared observation warrants the current claim."},
                "argument_id": "argument_current",
            }
        )
        suggestion = write["suggested"]["relations"][0]
        self.assertFalse(suggestion["authoritative"])
        self.assertFalse(self.compiler.state()["relations"])
        self.assertEqual(self.compiler.state()["suggestions"][suggestion["id"]]["status"], "suggested")
        accepted = self.compiler.feedback(suggestion["id"], "historical", "accepted")
        self.assertEqual(accepted["suggestion_feedback"]["status"], "accepted")
        state = self.compiler.state()
        self.assertEqual(state["suggestions"][suggestion["id"]]["status"], "accepted")
        self.assertEqual(len(state["relations"]), 1)
        semantic_types = {item["type"] for item in self.compiler.store.operations()}
        self.assertNotIn("suggestion.created", semantic_types)
        self.assertIn("relation.asserted", semantic_types)

    def test_explicit_negative_feedback_is_contextual_not_global(self) -> None:
        self.evidence("historical_observation", subject="needle")
        self.compiler.close("history complete")
        self.compiler.start("current work", run_id="run_new")
        self.evidence("current_observation", subject="needle")
        surfaced = [
            item for item in self.compiler.state()["retrieval_events"] if item["event"] == "retrieval.surfaced"
        ][-1]
        self.compiler.feedback(surfaced["id"], "historical_observation", "dismissed")
        dismissal = self.compiler.state()["retrieval_events"][-1]
        self.assertEqual(dismissal["context_key"], surfaced["context_key"])
        self.assertEqual(dismissal["entity_ids"], ["historical_observation"])
        search = self.compiler.query("search", text="historical observation")
        self.assertIn("historical_observation", {item["entity_id"] for item in search["result"]})

    def test_bounded_context_and_query_have_explicit_continuations(self) -> None:
        for index in range(8):
            self.evidence(f"e{index}")
            self.claim(f"c{index}", [f"e{index}"], argument_id=f"a{index}")
        first = self.compiler.context(max_entities=2, max_chars=2000)
        self.assertFalse(first["complete"])
        self.assertEqual(len(first["entries"]), 2)
        self.assertTrue(first["warning"])
        second = self.compiler.context(
            cursor=first["continuation"], max_entities=2, max_chars=2000
        )
        self.assertNotEqual(
            {item.get("entity_id") for item in first["entries"]},
            {item.get("entity_id") for item in second["entries"]},
        )
        query = self.compiler.query("history", "c0", limit=1)
        self.assertFalse(query["complete"] if len(self.compiler.query("history", "c0")["result"]) > 1 else True)
        unknowns = self.compiler.query("unknowns", limit=1)
        self.assertFalse(unknowns["complete"])
        self.assertTrue(unknowns["continuation"])
        continued = self.compiler.query(
            "unknowns", limit=1, cursor=unknowns["continuation"]
        )
        self.assertNotEqual(unknowns["result"], continued["result"])

    def test_retrieval_telemetry_survives_derived_state_deletion(self) -> None:
        self.evidence("searchable_evidence", subject="uncommon-keyword")
        self.compiler.query("search", text="uncommon-keyword")
        self.compiler.context(max_entities=1, max_chars=1000)
        metrics = self.compiler.query("telemetry")["result"]
        self.assertEqual(metrics["agent_maintenance"]["manual_searches"], 1)
        self.assertEqual(metrics["context_efficiency"]["context_packs"], 1)
        self.assertEqual(metrics["context_efficiency"]["raw_evidence_avoided"], 1)
        self.assertFalse(
            metrics["agent_maintenance"]["state_administration_tokens"]["available"]
        )
        telemetry_path = self.root / ".agent" / "retrieval.jsonl"
        before = telemetry_path.read_text(encoding="utf-8")
        Path(self.root, ".agent", "state.sqlite").unlink()
        rebuilt = self.compiler.rebuild()
        after = telemetry_path.read_text(encoding="utf-8")
        self.assertEqual(before, after)
        self.assertEqual(rebuilt["model_calls"], 0)
        self.assertTrue(self.compiler.state()["retrieval_events"])
        rebuilt_metrics = self.compiler.query("telemetry")["result"]
        self.assertEqual(rebuilt_metrics["agent_maintenance"]["manual_searches"], 1)

    def test_annotations_are_revisable_without_overwriting_semantic_history(self) -> None:
        self.evidence("annotated")
        first_generation = self.compiler.store.ledger_head()["generation"]
        self.compiler.apply(
            {
                "operations": [
                    operation(
                        "annotation.revised",
                        id="annotation_1",
                        entity_type="evidence",
                        entity_id="annotated",
                        name="Runtime trace recording return identity",
                        description="A runtime observation of the returned object identity",
                        reason="Make the label descriptive rather than adjudicative",
                    )
                ]
            }
        )
        self.assertEqual(
            self.compiler.state()["evidence"]["annotated"]["name"],
            "Runtime trace recording return identity",
        )
        self.assertGreater(self.compiler.store.ledger_head()["generation"], first_generation)
        history = self.compiler.query("history", "annotated")["result"]
        self.assertTrue(any(item["type"] == "evidence.registered" for item in history))
        self.assertTrue(any(item["type"] == "annotation.revised" for item in history))
