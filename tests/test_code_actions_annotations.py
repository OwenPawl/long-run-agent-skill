from __future__ import annotations

import json

from long_run_agent_skill.compiler import operation
from long_run_agent_skill.materialized_views import resolve_entity_view

from common import MissionCase, evidence_ref


class CodeActionAndAnnotationTests(MissionCase):
    def _claims_for_relations(self) -> None:
        self.evidence("relation_ground")
        self.claim("relation_left", ["relation_ground"])
        self.claim("relation_right", ["relation_ground"])

    def test_code_action_previews_and_applies_through_epistemic_delta(self) -> None:
        write = self.compiler.assert_claim(
            {
                "id": "unsupported_claim",
                "proposition": "This semantic assertion currently lacks support.",
            }
        )
        diagnostic = next(
            item
            for item in write["diagnostics"]["items"]
            if item["code"] == "UNSUPPORTED_ASSERTION"
            and "unsupported_claim" in item["affected_entities"]
        )
        action = diagnostic["fixits"][0]
        self.assertEqual(action["schema_version"], "epistemic-code-action.v1")
        self.assertTrue(action["preconditions"])
        self.assertTrue(action["expected_consequences"])
        self.assertTrue(action["semantic_input_required"])
        self.assertFalse(action["authoritative"])
        self.assertTrue(action["proposed_EpistemicDelta"]["operations"])

        generation = self.compiler.store.ledger_head()["generation"]
        preview = self.compiler.preview(action["proposed_EpistemicDelta"])
        self.assertEqual(preview["committed"]["status"], "not_committed")
        self.assertEqual(self.compiler.store.ledger_head()["generation"], generation)
        self.assertEqual(
            self.compiler.state()["claims"]["unsupported_claim"]["commitment"],
            "asserted",
        )

        applied = self.compiler.apply(
            action["proposed_EpistemicDelta"], expected_generation=generation
        )
        self.assertEqual(applied["committed"]["status"], "committed")
        self.assertEqual(
            applied["committed"]["semantic_operations"][0]["type"],
            "claim.withdrawn",
        )
        self.assertEqual(
            self.compiler.state()["claims"]["unsupported_claim"]["commitment"],
            "withdrawn",
        )

    def test_relation_rationale_is_optional_structured_and_append_only_revisable(self) -> None:
        self._claims_for_relations()
        mechanical = self.compiler.apply(
            {
                "operations": [
                    operation(
                        "relation.asserted",
                        id="mechanical_relation",
                        source={"type": "claim", "id": "relation_left"},
                        target={"type": "claim", "id": "relation_right"},
                        relation="references",
                    )
                ]
            }
        )
        self.assertEqual(mechanical["committed"]["status"], "committed")
        self.assertNotIn(
            "rationale", self.compiler.state()["relations"]["mechanical_relation"]
        )

        self.compiler.apply(
            {
                "operations": [
                    operation(
                        "relation.asserted",
                        id="semantic_relation",
                        source={"type": "claim", "id": "relation_left"},
                        target={"type": "claim", "id": "relation_right"},
                        relation="supersedes",
                        basis=[{"type": "evidence", "id": "relation_ground"}],
                        rationale="The first rationale compresses the evidence basis.",
                    )
                ]
            }
        )
        self.compiler.apply(
            {
                "operations": [
                    operation(
                        "annotation.revised",
                        id="relation_rationale_revision",
                        entity_type="relation",
                        entity_id="semantic_relation",
                        rationale="The corrected rationale describes the same relation.",
                        reason="The original wording was misleading.",
                    )
                ]
            }
        )
        relation = self.compiler.state()["relations"]["semantic_relation"]
        self.assertEqual(
            relation["basis"], [{"type": "evidence", "id": "relation_ground"}]
        )
        self.assertEqual(
            relation["rationale"],
            "The corrected rationale describes the same relation.",
        )
        compact_history = self.compiler.inspect(
            "semantic_relation", facets=["history"]
        )["history"]
        self.assertTrue(
            any(item["type"] == "annotation.revised" for item in compact_history)
        )
        history = json.loads(
            resolve_entity_view(self.root, "semantic_relation").read_text(
                encoding="utf-8"
            )
        )["history"]
        asserted = next(item for item in history if item["type"] == "relation.asserted")
        self.assertEqual(
            asserted["data"]["rationale"],
            "The first rationale compresses the evidence basis.",
        )
        self.assertTrue(any(item["type"] == "annotation.revised" for item in history))

        self.compiler.close("relation history complete")
        self.compiler.start("current relation explanation", run_id="relation_current")
        recall = self.compiler.assert_claim(
            {
                "id": "relation_current_claim",
                "proposition": "Current work revisits the relation basis.",
                "premises": [evidence_ref("relation_ground")],
                "warrant": {
                    "statement": "The recorded observations warrant this scoped proposition."
                },
                "argument_id": "relation_current_argument",
            }
        )
        corrective = next(
            item
            for item in recall["recall"]["capsules"]
            if item["entity_id"] == "relation_right"
        )
        self.assertEqual(
            corrective["why_no_longer_current"],
            "The corrected rationale describes the same relation.",
        )

    def test_annotation_maintenance_is_opportunistic_and_non_authoritative(self) -> None:
        self._claims_for_relations()
        self.compiler.apply(
            {
                "operations": [
                    operation(
                        "relation.asserted",
                        id="active_relation",
                        source={"type": "claim", "id": "relation_left"},
                        target={"type": "claim", "id": "relation_right"},
                        relation="undercuts",
                        basis=[{"type": "evidence", "id": "relation_ground"}],
                        rationale="Existing rationale remains epistemic presentation only.",
                    )
                ]
            }
        )
        cold = self.evidence("unrelated_cold_write", subject="unrelated")
        self.assertNotIn(
            "active_relation",
            {
                item["entity_id"]
                for item in cold["suggested"]["annotation_revisions"]
            },
        )

        self.compiler.inspect("active_relation")
        active = self.compiler.apply(
            {
                "operations": [
                    operation(
                        "relation.asserted",
                        id="relation_using_active_relation",
                        source={"type": "relation", "id": "active_relation"},
                        target={"type": "claim", "id": "relation_right"},
                        relation="references",
                    )
                ]
            }
        )
        opportunity = next(
            item
            for item in active["suggested"]["annotation_revisions"]
            if item["entity_id"] == "active_relation"
        )
        self.assertFalse(opportunity["authoritative"])
        self.assertEqual(opportunity["field"], "rationale")
        self.assertIn("inspected", " ".join(opportunity["reasons"]))
        self.assertIn("structural", " ".join(opportunity["reasons"]))
        self.assertEqual(
            self.compiler.state()["relations"]["active_relation"]["rationale"],
            "Existing rationale remains epistemic presentation only.",
        )
        self.assertFalse(
            any(
                item["type"] == "annotation.revised"
                and item["data"].get("entity_id") == "active_relation"
                for item in self.compiler.store.operations()
            )
        )
