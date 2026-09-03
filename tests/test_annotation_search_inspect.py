from __future__ import annotations

import json

from common import MissionCase, evidence_ref
from long_run_agent_skill.annotations import render_annotation
from long_run_agent_skill.errors import SemanticError


def annotation(subject: str, predicate: str, scope: str = "") -> dict[str, str]:
    value = {"subject": subject, "predicate": predicate}
    if scope:
        value["scope"] = scope
    return value


class AnnotationSearchInspectTests(MissionCase):
    def test_entities_use_canonical_annotations_without_synthetic_names(self) -> None:
        self.compiler.observe(
            "evidence",
            {
                "id": "E17",
                "annotation": annotation(
                    "Constructor trace",
                    "observed different returned-object identity",
                    "run R17",
                ),
                "producer": "identity-trace",
                "source_run": "R17",
            },
        )
        self.compiler.observe(
            "artifact",
            {
                "id": "artifact_parser",
                "annotation": annotation("Parser source", "contains identity check"),
                "intrinsic_name": "parser.py",
                "external_identity": "file:///tmp/parser.py",
            },
        )
        state = self.compiler.state()
        evidence = state["evidence"]["E17"]
        artifact = state["artifacts"]["artifact_parser"]
        self.assertEqual(
            evidence["annotation"],
            {
                "subject": "Constructor trace",
                "predicate": "observed different returned-object identity",
                "scope": "run R17",
            },
        )
        self.assertNotIn("name", evidence)
        self.assertNotIn("intrinsic_name", evidence)
        self.assertNotIn("extended_annotation", evidence)
        self.assertEqual(artifact["intrinsic_name"], "parser.py")
        self.assertNotIn("extended_annotation", artifact)
        self.assertEqual(
            render_annotation(evidence["annotation"]),
            "Constructor trace | observed different returned-object identity | run R17",
        )
        self.assertEqual(
            render_annotation({"predicate": "p", "subject": "s"}),
            "s | p",
        )
        with self.assertRaises(SemanticError):
            self.compiler.observe(
                "evidence",
                {"id": "bad", "annotation": {"subject": "missing predicate"}},
            )

    def test_annotation_revision_is_append_only_and_search_is_structured(self) -> None:
        self.compiler.observe(
            "evidence",
            {
                "id": "E17",
                "annotation": annotation(
                    "Constructor trace", "observed returned identity", "run R17"
                ),
                "extended_annotation": "Initial trace summary.",
                "producer": "identity-trace",
                "source_run": "R17",
                "measurement_process": "constructor-return-identity",
            },
        )
        self.compiler.update(
            {
                "operations": [
                    {
                        "type": "annotation.revised",
                        "data": {
                            "entity_type": "evidence",
                            "entity_id": "E17",
                            "annotation": annotation(
                                "Constructor trace",
                                "observed different returned-object identity",
                                "run R17",
                            ),
                            "extended_annotation": "The receiver and result identities differ.",
                            "reason": "Clarify the observed identity relationship.",
                        },
                    }
                ]
            }
        )
        state = self.compiler.state()
        self.assertEqual(
            state["evidence"]["E17"]["annotation"]["predicate"],
            "observed different returned-object identity",
        )
        history = self.compiler.inspect("E17", facets=["history"])["history"]
        self.assertEqual(
            [item["type"] for item in history],
            ["evidence.registered", "annotation.revised"],
        )

        first = self.compiler.search("constructor R17 identity")
        second = self.compiler.search("constructor R17 identity")
        self.assertEqual(first["results"], second["results"])
        result = first["results"][0]
        self.assertEqual(result["entity_id"], "E17")
        self.assertEqual(result["annotation"], state["evidence"]["E17"]["annotation"])
        self.assertIn("annotation.subject", result["match"]["fields"])
        self.assertIn("annotation.scope", result["match"]["fields"])
        self.assertNotIn("entity", result)
        self.assertLess(len(json.dumps(result)), 1200)

    def test_inspect_normalizes_nodes_relations_paths_and_provenance(self) -> None:
        self.compiler.observe(
            "dependency",
            {
                "id": "D3",
                "annotation": annotation(
                    "Receiver-replacement argument",
                    "requires WorkflowKit 26.6",
                    "tested implementation",
                ),
                "value": "26.6",
                "status": "current",
            },
        )
        self.compiler.observe(
            "evidence",
            {
                "id": "E17",
                "annotation": annotation(
                    "Constructor trace",
                    "observed different returned-object identity",
                    "run R17",
                ),
                "producer": "identity-trace",
                "source_run": "R17",
                "host_generation": "macOS-26.6-test-4",
                "measurement_process": "constructor-return-identity",
            },
        )
        self.compiler.assert_claim(
            {
                "id": "C8",
                "annotation": annotation(
                    "Initializer",
                    "may replace receiver identity",
                    "during construction",
                ),
                "extended_annotation": (
                    "Initialization may return an object whose identity differs from the receiver."
                ),
                "proposition": "An initializer may return a replacement object.",
                "premises": [evidence_ref("E17")],
                "dependencies": [{"dependency_id": "D3", "expected": "26.6"}],
                "warrant": {"statement": "A differing return identity warrants replacement."},
                "argument_id": "A4",
                "argument_annotation": annotation(
                    "Runtime identity observations",
                    "warrant receiver replacement",
                    "tested initializer path",
                ),
            }
        )
        result = self.compiler.inspect(
            "C8", facets=["why", "defeaters", "assumptions"]
        )
        self.assertEqual(result["target"], "C8")
        self.assertEqual(set(result["nodes"]), {"A4", "C8", "D3", "E17"})
        self.assertNotIn("id", result["nodes"]["C8"])
        self.assertEqual(
            result["nodes"]["C8"]["annotation"]["predicate"],
            "may replace receiver identity",
        )
        for relation in result["relations"]:
            self.assertIsInstance(relation["source"], str)
            self.assertIsInstance(relation["target"], str)
            self.assertNotIn("entity", relation)
        self.assertIn(["E17", "A4", "C8"], result["paths"]["support"])
        self.assertIn(["D3", "A4"], result["paths"]["condition"])
        provenance_ref = result["nodes"]["E17"]["provenance_ref"]
        self.assertIn(provenance_ref, result["provenance"])
        rendered = json.dumps(result, sort_keys=True)
        self.assertEqual(rendered.count("may replace receiver identity"), 1)
        self.assertNotIn(".agent/views", rendered)
        self.assertNotIn("view_ref", rendered)

    def test_typed_provenance_refs_bridge_external_activity_to_graph_entities(self) -> None:
        self.compiler.observe(
            "artifact",
            {
                "id": "A12",
                "annotation": annotation("Trace file", "contains constructor output"),
                "external_identity": "file:///outside/mission/trace-R17.json",
            },
        )
        self.compiler.observe(
            "evidence",
            {
                "id": "E17",
                "annotation": annotation("Constructor trace", "observed replacement"),
                "provenance_refs": [
                    {"type": "artifact", "id": "A12"},
                    {"type": "tool_event", "id": "T83"},
                    {"type": "run", "id": "R17"},
                ],
                "producer": "identity-trace",
            },
        )
        self.compiler.assert_claim(
            {
                "id": "C17",
                "annotation": annotation(
                    "Initializer", "may return a replacement object"
                ),
                "proposition": "The initializer may return a replacement object.",
                "premises": [evidence_ref("E17")],
                "warrant": {"statement": "The constructor trace warrants replacement."},
                "argument_id": "A17",
                "argument_annotation": annotation(
                    "Constructor trace", "warrants receiver replacement"
                ),
            }
        )
        inspected = self.compiler.inspect("E17")
        provenance = inspected["provenance"][
            inspected["nodes"]["E17"]["provenance_ref"]
        ]
        self.assertEqual(
            provenance["refs"],
            [
                {"type": "artifact", "id": "A12"},
                {"type": "run", "id": "R17"},
                {"type": "tool_event", "id": "T83"},
            ],
        )
        self.assertEqual(
            self.compiler.search("T83")["results"][0]["entity_id"], "E17"
        )
        self.assertIn("A17", inspected["nodes"])
        self.assertIn(
            {"source": "E17", "relation": "premise_of", "target": "A17"},
            inspected["relations"],
        )
        self.assertNotIn("tool_events", self.compiler.state())
        rendered = json.dumps(inspected, sort_keys=True)
        self.assertNotIn(".agent/views", rendered)
        self.assertNotIn("file:///outside/mission/trace-R17.json", rendered)

    def test_default_inspect_is_bounded_and_global_facets_replace_queries(self) -> None:
        for index in range(8):
            self.compiler.observe(
                "evidence",
                {
                    "id": f"E{index}",
                    "annotation": annotation(f"Trace {index}", "recorded a result"),
                },
            )
        self.compiler.assert_claim(
            {
                "id": "C",
                "annotation": annotation("Feature", "behaves consistently"),
                "proposition": "The feature behaves consistently.",
                "premises": [evidence_ref(f"E{index}") for index in range(8)],
                "warrant": {"statement": "The traces warrant the claim."},
                "argument_id": "A",
                "argument_annotation": annotation("Traces", "warrant behavior"),
            }
        )
        bounded = self.compiler.inspect("C", facets=["why"], limit=4)
        self.assertFalse(bounded["complete"])
        self.assertIn("continuation", bounded)
        self.assertLessEqual(len(bounded["nodes"]), 4)

        self.compiler.ask(
            {
                "id": "Q1",
                "annotation": annotation("Behavior", "remains unexplained"),
                "question": "Why does the behavior occur?",
            }
        )
        global_view = self.compiler.inspect(facets=["unknowns", "changes-since"], since_generation=0)
        self.assertIn("Q1", global_view["nodes"])
        self.assertTrue(
            any(item["entity_id"] == "Q1" for item in global_view["groups"]["unknowns"])
        )
        self.assertTrue(global_view["groups"]["changes_since"])

    def test_noncurrent_search_and_inspect_are_never_bare(self) -> None:
        self.compiler.assert_claim(
            {
                "id": "C4",
                "annotation": annotation(
                    "Initializer", "always preserves receiver identity", "construction"
                ),
                "proposition": "Initializer always preserves receiver identity.",
            }
        )
        self.compiler.update(
            {"operations": [{"type": "claim.withdrawn", "data": {"id": "C4"}}]}
        )
        search = self.compiler.search("preserves receiver identity")["results"][0]
        self.assertTrue(search["historical_noncurrent"])
        self.assertNotEqual(search["presentation_status"], "CURRENT")
        self.assertTrue(search["why_no_longer_current"])
        inspected = self.compiler.inspect("C4")
        self.assertTrue(inspected["presentation_guard"]["historical_noncurrent"])
        self.assertTrue(inspected["presentation_guard"]["why_no_longer_current"])
        self.assertEqual(inspected["nodes"]["C4"]["state"], inspected["presentation_guard"]["current_state"])

    def test_inspect_enables_opportunistic_annotation_revision_without_truth_change(self) -> None:
        self.compiler.observe(
            "evidence",
            {
                "id": "active",
                "annotation": annotation("Ambiguous trace", "records identity"),
            },
        )
        generation = self.compiler.state()["generation"]
        self.compiler.inspect("active")
        preview = self.compiler.update(
            {
                "operations": [
                    {
                        "type": "question.opened",
                        "data": {
                            "id": "Q",
                            "annotation": annotation("Trace", "needs interpretation"),
                            "question": "How should the trace be interpreted?",
                            "related_claim_ids": [],
                            "evidence_ids": ["active"],
                        },
                    }
                ]
            },
            preview=True,
        )
        opportunities = preview["suggested"]["annotation_revisions"]
        active = next(item for item in opportunities if item["entity_id"] == "active")
        self.assertIn("inspected", " ".join(active["reasons"]))
        self.assertEqual(self.compiler.state()["generation"], generation)
        self.assertFalse(
            any(item["entity_id"] == "cold" for item in opportunities)
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
