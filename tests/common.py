from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from long_run_agent_skill.compiler import EpistemicCompiler


def evidence_ref(entity_id: str) -> dict[str, str]:
    return {"type": "evidence", "id": entity_id}


def claim_ref(entity_id: str) -> dict[str, str]:
    return {"type": "claim", "id": entity_id}


class MissionCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.compiler = EpistemicCompiler(self.root)
        self.compiler.initialize(goal="test mission")
        self.compiler.start("test mission", run_id="run_test")

    def artifact(self, entity_id: str, content_hash: str | None = None) -> dict[str, Any]:
        return self.compiler.observe(
            "artifact",
            {
                "id": entity_id,
                "annotation": {
                    "subject": f"Artifact {entity_id}",
                    "predicate": "identifies recorded content",
                },
                "content_hash": content_hash or f"sha256:{entity_id}",
            },
        )

    def evidence(
        self,
        entity_id: str,
        *,
        artifact_id: str = "",
        source_run: str = "run_test",
        producer: str = "test-producer",
        subject: str = "test-subject",
        **extra: Any,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": entity_id,
            "annotation": {
                "subject": subject,
                "predicate": f"records observation {entity_id}",
            },
            "source_run": source_run,
            "producer": producer,
            **extra,
        }
        if artifact_id:
            data["artifact_ref"] = {"id": artifact_id}
        return self.compiler.observe("evidence", data)

    def claim(
        self,
        claim_id: str,
        premise_ids: list[str],
        *,
        argument_id: str = "",
        subject: str = "test-subject",
        dependencies: list[dict[str, Any]] | None = None,
        assumptions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return self.compiler.assert_claim(
            {
                "id": claim_id,
                "annotation": {
                    "subject": subject,
                    "predicate": f"asserts proposition {claim_id}",
                },
                "proposition": f"Proposition {claim_id}",
                "premises": [evidence_ref(item) for item in premise_ids],
                "warrant": {"statement": "The recorded observations warrant this scoped proposition."},
                "argument_id": argument_id or f"arg_{claim_id}",
                "argument_annotation": {
                    "subject": subject,
                    "predicate": f"warrants proposition {claim_id}",
                },
                "dependencies": dependencies or [],
                "assumptions": assumptions or [],
            }
        )
