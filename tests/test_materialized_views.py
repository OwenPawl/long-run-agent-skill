from __future__ import annotations

import hashlib
import json
from pathlib import Path

from common import MissionCase, evidence_ref
from long_run_agent_skill.materialized_views import resolve_entity_view


def annotation(subject: str, predicate: str, scope: str = "") -> dict[str, str]:
    value = {"subject": subject, "predicate": predicate}
    if scope:
        value["scope"] = scope
    return value


def file_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class MaterializedViewTests(MissionCase):
    def test_every_entity_has_a_deterministic_safe_full_view(self) -> None:
        self.compiler.observe(
            "artifact",
            {
                "id": "artifact:trace-R17",
                "annotation": annotation("Trace artifact", "contains constructor output"),
                "external_identity": "file:///tmp/trace-R17.json",
            },
        )
        self.compiler.observe(
            "evidence",
            {
                "id": "../E17",
                "annotation": annotation("Constructor trace", "observed replacement"),
                "artifact_ref": {"id": "artifact:trace-R17"},
                "source": "runtime harness",
                "producer": "identity-trace",
            },
        )
        views_root = (self.root / ".agent" / "views").resolve()
        for entity_id in ("artifact:trace-R17", "../E17"):
            first = resolve_entity_view(self.root, entity_id)
            second = resolve_entity_view(self.root, entity_id)
            self.assertEqual(first, second)
            self.assertEqual(first.parent.resolve(), views_root / "entities")
            self.assertTrue(first.is_file())
            payload = json.loads(first.read_text(encoding="utf-8"))
            self.assertEqual(payload["entity_id"], entity_id)
            self.assertIn("full_payload", payload)
            self.assertIn("history", payload)
            if entity_id == "../E17":
                self.assertNotIn("artifact_ref", payload["full_payload"])
                self.assertNotIn("producer", payload["full_payload"])
                self.assertEqual(
                    payload["provenance"]["refs"],
                    [{"id": "artifact:trace-R17", "type": "artifact"}],
                )
        self.assertFalse((self.root / "E17.json").exists())
        self.assertTrue((views_root / "current.md").is_file())
        self.assertTrue((views_root / "state.sqlite").is_file())
        self.assertFalse((self.root / ".agent" / "current.md").exists())
        self.assertFalse((self.root / ".agent" / "state.sqlite").exists())

    def test_view_availability_does_not_imply_surface_or_content_read(self) -> None:
        self.compiler.observe(
            "evidence",
            {
                "id": "E",
                "annotation": annotation("Trace", "records a result"),
            },
        )
        state = self.compiler.state()
        self.assertTrue(resolve_entity_view(self.root, "E").is_file())
        self.assertFalse(
            any(
                event.get("event") in {
                    "retrieval.search_result_surfaced",
                    "retrieval.inspect_topology_surfaced",
                    "retrieval.content_read",
                    "retrieval.expanded",
                }
                and "E" in event.get("entity_ids", [])
                for event in state["retrieval_events"]
            )
        )
        inspected = self.compiler.inspect("E")
        coverage = inspected["worker_state_after_inspect"]["E"]
        self.assertGreater(coverage["content_coverage"]["estimate"], 0)
        self.assertLess(coverage["content_coverage"]["estimate"], 0.8)
        self.assertFalse(
            any(
                event.get("event") in {"retrieval.content_read", "retrieval.expanded"}
                for event in self.compiler.state()["retrieval_events"]
            )
        )

    def test_artifact_locator_and_materialized_view_identity_are_separate(self) -> None:
        self.compiler.observe(
            "artifact",
            {
                "id": "source_artifact",
                "annotation": annotation("Source file", "contains tested implementation"),
                "external_identity": "file:///outside/mission/source.py",
            },
        )
        payload = json.loads(
            resolve_entity_view(self.root, "source_artifact").read_text(encoding="utf-8")
        )
        self.assertEqual(
            payload["full_payload"]["external_identity"],
            "file:///outside/mission/source.py",
        )
        self.assertNotIn("view_ref", payload["full_payload"])
        inspected = json.dumps(self.compiler.inspect("source_artifact"), sort_keys=True)
        self.assertNotIn(".agent/views", inspected)
        self.assertNotIn("file:///outside/mission/source.py", inspected)

    def test_entity_views_rebuild_offline_deterministically(self) -> None:
        self.compiler.observe(
            "evidence",
            {
                "id": "E",
                "annotation": annotation("Trace", "records output"),
            },
        )
        self.compiler.assert_claim(
            {
                "id": "C",
                "annotation": annotation("Behavior", "matches the trace"),
                "proposition": "The behavior matches the trace.",
                "premises": [evidence_ref("E")],
                "warrant": {"statement": "The trace warrants the claim."},
                "argument_id": "A",
                "argument_annotation": annotation("Trace", "warrants behavior"),
            }
        )
        views_root = self.root / ".agent" / "views"
        before = file_hashes(views_root)
        ledger_before = (self.root / ".agent" / "ledger.jsonl").read_bytes()
        retrieval_before = (self.root / ".agent" / "retrieval.jsonl").read_bytes()
        rebuild = self.compiler.rebuild()
        self.assertTrue(rebuild["offline"])
        self.assertEqual(before, file_hashes(views_root))
        self.assertEqual(ledger_before, (self.root / ".agent" / "ledger.jsonl").read_bytes())
        self.assertEqual(
            retrieval_before, (self.root / ".agent" / "retrieval.jsonl").read_bytes()
        )

    def test_semantic_relation_rationale_revision_updates_view_without_losing_history(self) -> None:
        for entity_id in ("left", "right"):
            self.compiler.observe(
                "evidence",
                {
                    "id": entity_id,
                    "annotation": annotation(entity_id, "records a result"),
                },
            )
        self.compiler.update(
            {
                "operations": [
                    {
                        "type": "relation.asserted",
                        "data": {
                            "id": "R",
                            "source": {"type": "evidence", "id": "left"},
                            "target": {"type": "evidence", "id": "right"},
                            "relation": "supersedes",
                            "basis": [{"type": "evidence", "id": "right"}],
                            "rationale": "Initial wording.",
                        },
                    }
                ]
            }
        )
        self.compiler.update(
            {
                "operations": [
                    {
                        "type": "annotation.revised",
                        "data": {
                            "entity_type": "relation",
                            "entity_id": "R",
                            "rationale": "Corrected wording.",
                            "reason": "The first rationale was misleading.",
                        },
                    }
                ]
            }
        )
        inspected = self.compiler.inspect("R")
        self.assertEqual(inspected["nodes"]["R"]["rationale"], "Corrected wording.")
        full = json.loads(resolve_entity_view(self.root, "R").read_text(encoding="utf-8"))
        history_text = json.dumps(full["history"])
        self.assertIn("Initial wording.", history_text)
        self.assertIn("Corrected wording.", history_text)


if __name__ == "__main__":
    import unittest

    unittest.main()
