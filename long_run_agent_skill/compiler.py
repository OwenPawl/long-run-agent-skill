"""High-level semantic API for the standalone epistemic compiler."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .context import ContextQueryMixin
from .errors import LedgerError, SemanticError
from .recall import entity_catalog, operation_entity_ids, recall, relation_suggestions
from .reducer import reduce_operations
from .store import GENESIS_HASH, LedgerStore
from .util import atomic_write_json, random_id, stable_id, utc_now
from .views import sqlite_metadata, state_hash, write_views

SUPPORTED_OPERATIONS = {
    "mission.started",
    "mission.updated",
    "run.started",
    "run.closed",
    "artifact.registered",
    "evidence.registered",
    "verification.recorded",
    "claim.asserted",
    "claim.revised",
    "claim.withdrawn",
    "assumption.asserted",
    "assumption.revised",
    "assumption.withdrawn",
    "argument.asserted",
    "argument.retracted",
    "attack.asserted",
    "attack.retracted",
    "dependency.observed",
    "question.opened",
    "question.updated",
    "question.resolved",
    "question.reopened",
    "decision.recorded",
    "decision.revised",
    "warrant.registered",
    "relation.asserted",
    "annotation.created",
    "annotation.revised",
    "checkpoint.created",
}

ENTITY_COLLECTION = {
    "artifact": "artifacts",
    "evidence": "evidence",
    "verification": "verifications",
    "claim": "claims",
    "assumption": "assumptions",
    "argument": "arguments",
    "attack": "attacks",
    "dependency": "dependencies",
    "question": "questions",
    "decision": "decisions",
    "warrant": "warrants",
    "relation": "relations",
    "suggestion": "suggestions",
}


def operation(kind: str, **data: Any) -> dict[str, Any]:
    return {"type": kind, "data": data}


class EpistemicCompiler(ContextQueryMixin):
    def __init__(self, root: str | Path):
        self.store = LedgerStore(root)

    @property
    def root(self) -> Path:
        return self.store.paths.root

    def initialize(self, goal: str = "", force: bool = False) -> dict[str, Any]:
        initialized = self.store.initialize(goal=goal, force=force)
        if force or not self.store.read_transactions():
            write_views(self.store, reduce_operations([], self.store.load_policy()))
        return {"status": "success", "initialized": initialized}

    def state(self, rebuild_if_missing: bool = True) -> dict[str, Any]:
        self.store.require_initialized()
        operations = self.store.all_operations()
        state = reduce_operations(operations, self.store.load_policy())
        metadata = sqlite_metadata(self.store.paths.state)
        if rebuild_if_missing and (
            not metadata
            or metadata.get("generation") != str(state["generation"])
            or metadata.get("state_hash") != state_hash(state)
        ):
            write_views(self.store, state)
        return state

    def rebuild(self) -> dict[str, Any]:
        transactions = self.store.read_transactions(verify=True)
        telemetry = self.store.read_telemetry_transactions(verify=True)
        state = reduce_operations(self.store.all_operations(), self.store.load_policy())
        materialized = write_views(self.store, state)
        return {
            "status": "success",
            "offline": True,
            "model_calls": 0,
            "ledger_transactions": len(transactions),
            "telemetry_transactions": len(telemetry),
            **materialized,
        }

    def start(self, goal: str, steering: str = "", run_id: str = "") -> dict[str, Any]:
        self.store.initialize(goal=goal)
        state = self.state()
        run_id = run_id or random_id("run")
        operations = []
        if not state.get("mission"):
            operations.append(operation("mission.started", id=random_id("mission"), goal=goal, steering=steering))
        else:
            operations.append(operation("mission.updated", id=state["mission"].get("id"), goal=goal, steering=steering))
        operations.append(operation("run.started", id=run_id, goal=goal, steering=steering, started_at=utc_now()))
        return self.apply({"operations": operations}, run_id=run_id)

    def observe(self, kind: str, data: dict[str, Any], *, preview: bool = False) -> dict[str, Any]:
        mapping = {
            "artifact": "artifact.registered",
            "evidence": "evidence.registered",
            "dependency": "dependency.observed",
            "verification": "verification.recorded",
        }
        if kind not in mapping:
            raise SemanticError("observe kind must be artifact, evidence, dependency, or verification")
        payload = dict(data)
        if kind == "artifact":
            identity = payload.get("content_hash") or payload.get("external_identity")
            payload.setdefault("id", stable_id("artifact", identity) if identity else random_id("artifact"))
        elif kind == "evidence":
            if not payload.get("id"):
                observation_key = payload.get("observation_key")
                payload["id"] = stable_id("evidence", observation_key) if observation_key else random_id("evidence")
            payload.setdefault("timestamp", utc_now())
        else:
            payload.setdefault("id", random_id(kind))
        delta = {"operations": [operation(mapping[kind], **payload)]}
        return self.preview(delta) if preview else self.apply(delta)

    def assert_claim(self, data: dict[str, Any], *, preview: bool = False) -> dict[str, Any]:
        payload = dict(data)
        premises = payload.pop("premises", [])
        assumptions = payload.pop("assumptions", [])
        dependencies = payload.pop("dependencies", [])
        warrant = payload.pop("warrant", {})
        argument_id = payload.pop("argument_id", "")
        argument_name = payload.pop("argument_name", "")
        polarity = payload.pop("polarity", "support")
        if not payload.get("proposition"):
            raise SemanticError("claim proposition is required")
        payload.setdefault("id", random_id("claim"))
        payload.setdefault("name", payload["proposition"])
        payload.setdefault("description", payload["proposition"])
        payload.setdefault("commitment", "asserted")
        operations = [operation("claim.asserted", **payload)]
        if premises:
            argument_data = {
                "id": argument_id,
                "name": argument_name or f"Argument for {payload['name']}",
                "premises": premises,
                "assumptions": assumptions,
                "dependencies": dependencies,
                "warrant": warrant,
                "conclusion": {"type": "claim", "id": payload["id"], "polarity": polarity},
                "subject": payload.get("subject", ""),
            }
            operations.append(operation("argument.asserted", **argument_data))
        delta = {"operations": operations}
        return self.preview(delta) if preview else self.apply(delta)

    def assert_assumption(self, data: dict[str, Any], *, preview: bool = False) -> dict[str, Any]:
        payload = dict(data)
        if not payload.get("proposition"):
            raise SemanticError("assumption proposition is required")
        payload.setdefault("id", random_id("assumption"))
        payload.setdefault("name", payload["proposition"])
        payload.setdefault("description", payload["proposition"])
        payload.setdefault("commitment", "accepted")
        delta = {"operations": [operation("assumption.asserted", **payload)]}
        return self.preview(delta) if preview else self.apply(delta)

    def assert_argument(self, data: dict[str, Any], *, preview: bool = False) -> dict[str, Any]:
        delta = {"operations": [operation("argument.asserted", **data)]}
        return self.preview(delta) if preview else self.apply(delta)

    def attack(self, data: dict[str, Any], *, preview: bool = False) -> dict[str, Any]:
        delta = {"operations": [operation("attack.asserted", **data)]}
        return self.preview(delta) if preview else self.apply(delta)

    def ask(self, data: dict[str, Any], *, preview: bool = False) -> dict[str, Any]:
        payload = dict(data)
        if not payload.get("question"):
            raise SemanticError("question text is required")
        payload.setdefault("id", random_id("question"))
        payload.setdefault("name", payload["question"])
        delta = {"operations": [operation("question.opened", **payload)]}
        return self.preview(delta) if preview else self.apply(delta)

    def decide(self, data: dict[str, Any], *, preview: bool = False) -> dict[str, Any]:
        payload = dict(data)
        if not payload.get("choice"):
            raise SemanticError("decision choice is required")
        payload.setdefault("id", random_id("decision"))
        payload.setdefault("name", payload["choice"])
        payload.setdefault("timestamp", utc_now())
        delta = {"operations": [operation("decision.recorded", **payload)]}
        return self.preview(delta) if preview else self.apply(delta)

    def _existing(self, state: dict[str, Any], entity_type: str, entity_id: str) -> bool:
        collection = ENTITY_COLLECTION.get(entity_type)
        return bool(collection and entity_id in state.get(collection, {}))

    def _prepare_operations(
        self, proposed: list[dict[str, Any]], state: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        operations = deepcopy(proposed)
        deduplicated: list[dict[str, Any]] = []
        artifact_by_hash = {
            item.get("content_hash"): entity_id
            for entity_id, item in state.get("artifacts", {}).items()
            if item.get("content_hash")
        }
        aliases: dict[str, str] = {}
        for item in operations:
            if item.get("type") not in SUPPORTED_OPERATIONS:
                raise SemanticError(f"unsupported semantic operation: {item.get('type')!r}")
            data = item.setdefault("data", {})
            if not isinstance(data, dict):
                raise SemanticError("operation data must be an object")
            if item["type"] == "artifact.registered" and data.get("content_hash") in artifact_by_hash:
                canonical_id = artifact_by_hash[data["content_hash"]]
                aliases[data.get("id", canonical_id)] = canonical_id
                deduplicated.append(
                    {
                        "requested_id": data.get("id"),
                        "canonical_id": canonical_id,
                        "entity_type": "artifact",
                        "reason": "exact_content_identity",
                    }
                )
            if item["type"] == "evidence.registered" and data.get("id") in state.get("evidence", {}):
                existing = state["evidence"][data["id"]]
                comparable = {
                    key: value
                    for key, value in existing.items()
                    if key not in {"generation", "transaction_id", "orphan", "artifact_id", "artifact_content_hash", "artifact_locator"}
                }
                if comparable == data:
                    aliases[data["id"]] = data["id"]
                    deduplicated.append(
                        {
                            "requested_id": data["id"],
                            "canonical_id": data["id"],
                            "entity_type": "evidence",
                            "reason": "same_durable_observation_identity",
                        }
                    )
                else:
                    raise SemanticError(
                        f"evidence identity {data['id']} already exists with different observation provenance"
                    )

        def rewrite_reference(reference: dict[str, Any]) -> None:
            if reference.get("id") in aliases:
                reference["id"] = aliases[reference["id"]]

        prepared: list[dict[str, Any]] = []
        introduced: dict[str, set[str]] = {key: set() for key in ENTITY_COLLECTION}
        dependency_values = {
            entity_id: item.get("value") for entity_id, item in state.get("dependencies", {}).items()
        }
        for item in operations:
            kind = item["type"]
            data = item["data"]
            if kind == "artifact.registered" and data.get("id") in aliases:
                continue
            if kind == "evidence.registered" and data.get("id") in aliases:
                continue
            for reference in data.get("premises", []):
                rewrite_reference(reference)
            for reference in data.get("grounds", []):
                rewrite_reference(reference)
            if isinstance(data.get("target"), dict):
                rewrite_reference(data["target"])
            if isinstance(data.get("artifact_ref"), dict):
                rewrite_reference(data["artifact_ref"])
            if kind == "dependency.observed":
                if not data.get("id"):
                    raise SemanticError("dependency id is required")
                dependency_values[data["id"]] = data.get("value")
                introduced["dependency"].add(data["id"])
            if kind in {"claim.asserted", "claim.revised", "assumption.asserted", "assumption.revised"}:
                entity_type = "claim" if kind.startswith("claim.") else "assumption"
                entity_id = data.get("id")
                if not entity_id or not data.get("proposition"):
                    raise SemanticError(f"{entity_type} id and proposition are required")
                existing = state.get(ENTITY_COLLECTION[entity_type], {}).get(entity_id)
                revision = len(existing.get("revisions", [])) + 1 if existing else 1
                data["revision"] = revision
                data["revision_id"] = f"{entity_id}@{revision}"
                data["supersedes_revision_id"] = existing.get("revision_id") if existing else None
                item["type"] = f"{entity_type}.revised" if existing else f"{entity_type}.asserted"
                introduced[entity_type].add(entity_id)
            elif kind == "argument.asserted":
                semantic = {
                    key: data.get(key)
                    for key in ("premises", "assumptions", "dependencies", "warrant", "conclusion", "subject")
                }
                data["id"] = data.get("id") or stable_id("argument", semantic)
                introduced["argument"].add(data["id"])
            elif kind == "attack.asserted":
                if data.get("attack_type") not in {"rebut", "undercut", "undermine"}:
                    raise SemanticError("attack_type must be rebut, undercut, or undermine")
                if not data.get("grounds") or not data.get("warrant"):
                    raise SemanticError("an attack requires explicit grounds and a warrant")
                semantic = {
                    key: data.get(key)
                    for key in ("attack_type", "target", "grounds", "warrant", "assumptions", "dependencies")
                }
                data["id"] = data.get("id") or stable_id("attack", semantic)
                introduced["attack"].add(data["id"])
            else:
                event_to_type = {
                    "artifact.registered": "artifact",
                    "evidence.registered": "evidence",
                    "verification.recorded": "verification",
                    "question.opened": "question",
                    "decision.recorded": "decision",
                    "warrant.registered": "warrant",
                    "relation.asserted": "relation",
                }
                entity_type = event_to_type.get(kind)
                if entity_type and data.get("id"):
                    introduced[entity_type].add(data["id"])
            for dependency in data.get("dependencies", []):
                dependency_id = dependency.get("dependency_id", "")
                if not dependency_id:
                    raise SemanticError("every dependency binding requires dependency_id")
                if "expected" not in dependency:
                    if dependency_id not in dependency_values:
                        raise SemanticError(f"cannot bind missing dependency: {dependency_id}")
                    dependency["expected"] = dependency_values[dependency_id]
            prepared.append(item)

        available = {
            entity_type: set(state.get(collection, {})) | introduced[entity_type]
            for entity_type, collection in ENTITY_COLLECTION.items()
        }
        for item in prepared:
            data = item["data"]
            references = (
                list(data.get("premises", []))
                + list(data.get("grounds", []))
                + list(data.get("inputs", []))
            )
            if isinstance(data.get("source"), dict):
                references.append(data["source"])
            if isinstance(data.get("target"), dict):
                references.append(data["target"])
            if isinstance(data.get("artifact_ref"), dict):
                references.append({"type": "artifact", "id": data["artifact_ref"].get("id", "")})
            references.extend(
                {"type": "assumption", "id": assumption.get("assumption_id", "")}
                for assumption in data.get("assumptions", [])
            )
            references.extend(
                {"type": "claim", "id": claim_id}
                for claim_id in data.get("basis_claim_ids", [])
            )
            references.extend(
                {"type": "claim", "id": claim_id}
                for claim_id in data.get("candidate_claim_ids", [])
                + data.get("related_claim_ids", [])
                + data.get("resolution_basis_claim_ids", [])
            )
            warrant = data.get("warrant", {})
            if isinstance(warrant, dict) and warrant.get("id") and not warrant.get("statement"):
                references.append({"type": "warrant", "id": warrant["id"]})
            if item["type"] == "argument.asserted":
                conclusion = data.get("conclusion", {})
                conclusion_type = conclusion.get("type", "claim")
                conclusion_id = conclusion.get("id") or conclusion.get("claim_id", "")
                if conclusion_type not in {"claim", "assumption"} or conclusion_id not in available[conclusion_type]:
                    raise SemanticError(f"argument conclusion {conclusion_type} does not exist: {conclusion_id}")
                if data.get("conclusion", {}).get("polarity", "support") not in {"support", "oppose"}:
                    raise SemanticError("argument conclusion polarity must be support or oppose")
                if not data.get("premises"):
                    raise SemanticError("argument requires at least one grounded premise")
                if not data.get("warrant"):
                    raise SemanticError("argument requires an explicit warrant")
            for reference in references:
                entity_type, entity_id = reference.get("type", ""), reference.get("id", "")
                if entity_type not in available or entity_id not in available[entity_type]:
                    raise SemanticError(f"unresolved {entity_type or 'unknown'} reference: {entity_id}")
        return prepared, deduplicated

    @staticmethod
    def _synthetic_operations(
        existing: list[dict[str, Any]], proposed: list[dict[str, Any]], generation: int
    ) -> list[dict[str, Any]]:
        augmented = list(existing)
        for index, item in enumerate(proposed):
            augmented.append(
                {
                    **deepcopy(item),
                    "_generation": generation,
                    "_transaction_id": "preview",
                    "_operation_index": index,
                    "_committed_at": "preview",
                }
            )
        return augmented

    @staticmethod
    def _changes(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        facets = {
            "artifacts": lambda item: {"content_hash": item.get("content_hash"), "external_identity": item.get("external_identity")},
            "evidence": lambda item: {"artifact_ref": item.get("artifact_ref"), "source_run": item.get("source_run"), "orphan": item.get("orphan")},
            "claims": lambda item: item.get("derived", {}),
            "assumptions": lambda item: item.get("derived", {}),
            "arguments": lambda item: {"active": item.get("active"), "blockers": item.get("blockers", [])},
            "attacks": lambda item: {"active": item.get("active")},
            "dependencies": lambda item: {"value": item.get("value"), "status": item.get("status")},
            "questions": lambda item: {"status": item.get("derived_status")},
            "decisions": lambda item: {"basis_changed": item.get("basis_changed")},
        }
        for collection, extract in facets.items():
            old_items, new_items = old.get(collection, {}), new.get(collection, {})
            for entity_id in sorted(set(old_items) | set(new_items)):
                before = extract(old_items.get(entity_id, {})) if entity_id in old_items else None
                after = extract(new_items.get(entity_id, {})) if entity_id in new_items else None
                if before != after:
                    changes.append(
                        {
                            "entity_type": collection.removesuffix("s"),
                            "entity_id": entity_id,
                            "old": before,
                            "new": after,
                        }
                    )
        return changes

    def _preview_parts(
        self, prepared: list[dict[str, Any]], old_state: dict[str, Any], generation: int
    ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        synthetic = self._synthetic_operations(self.store.all_operations(), prepared, generation)
        new_state = reduce_operations(synthetic, self.store.load_policy())
        excluded = self._worker_scope(old_state)
        recalled = recall(old_state, new_state, prepared, self.store.load_policy(), excluded_ids=excluded)
        suggestions = relation_suggestions(old_state, new_state, prepared)
        return new_state, recalled, suggestions, self._changes(old_state, new_state)

    def preview(self, delta: dict[str, Any]) -> dict[str, Any]:
        old_state = self.state()
        head = self.store.ledger_head()
        prepared, deduplicated = self._prepare_operations(delta.get("operations", []), old_state)
        if not prepared:
            return {
                "status": "success",
                "committed": {"status": "not_committed", "operations": []},
                "proposed": {"operations": [], "deduplicated": deduplicated},
                "derived": {"changes": [], "state_hash": state_hash(old_state)},
                "diagnostics": {"items": [], "complete": True},
                "suggested": {"relations": [], "authoritative": False},
                "recall": {"capsules": [], "complete": True},
                "precondition": head,
            }
        new_state, recalled, suggestions, changes = self._preview_parts(
            prepared, old_state, head["generation"] + 1
        )
        return {
            "status": "success",
            "committed": {"status": "not_committed", "operations": []},
            "proposed": {"operations": prepared, "deduplicated": deduplicated},
            "derived": {"changes": changes, "state_hash": state_hash(new_state)},
            "diagnostics": {"items": new_state["diagnostics"], "complete": True},
            "suggested": {"relations": suggestions, "authoritative": False},
            "recall": recalled,
            "precondition": head,
        }

    def _automatic_telemetry(
        self,
        prepared: list[dict[str, Any]],
        old_state: dict[str, Any],
        recalled: dict[str, Any],
        suggestions: list[dict[str, Any]],
        generation: int,
    ) -> list[dict[str, Any]]:
        telemetry: list[dict[str, Any]] = []
        if recalled.get("candidates"):
            event_data = {
                "id": stable_id("retrieval", {"generation": generation, "context": recalled["context_key"]}),
                "mode": "automatic_recall",
                "context_key": recalled["context_key"],
                "seed_entity_ids": recalled["seed_entity_ids"],
                "entity_ids": [item["entity_id"] for item in recalled["capsules"]],
                "candidates": recalled["candidates"],
                "policy_version": self.store.load_policy()["schema_version"],
            }
            telemetry.append(operation("retrieval.surfaced", **event_data))
        surfaced_ids = {
            entity_id
            for event in old_state.get("retrieval_events", [])
            if event.get("event") == "retrieval.surfaced"
            for entity_id in event.get("entity_ids", [])
        }
        structurally_used: set[str] = set()
        use_sources: dict[str, str] = {}
        latest_surface: dict[str, str] = {}
        for event in old_state.get("retrieval_events", []):
            if event.get("event") == "retrieval.surfaced":
                for entity_id in event.get("entity_ids", []):
                    latest_surface[entity_id] = event.get("id", "")
        for item in prepared:
            data = item["data"]
            referenced = {
                reference.get("id", "")
                for reference in data.get("premises", []) + data.get("grounds", [])
            }
            referenced.update(data.get("basis_claim_ids", []))
            referenced.update(data.get("evidence_ids", []))
            referenced.update(
                assumption.get("assumption_id", "") for assumption in data.get("assumptions", [])
            )
            for key in ("source", "target"):
                if isinstance(data.get(key), dict):
                    referenced.add(data[key].get("id", ""))
            for entity_id in referenced & surfaced_ids:
                structurally_used.add(entity_id)
                use_sources[entity_id] = latest_surface.get(entity_id, "")
        if structurally_used:
            telemetry.append(
                operation(
                    "retrieval.structurally_used",
                    id=stable_id("retrieval_use", {"generation": generation, "ids": sorted(structurally_used)}),
                    context_key="structural_use",
                    entity_ids=sorted(structurally_used),
                    source_event_ids=use_sources,
                )
            )
        for suggestion in suggestions:
            telemetry.append(operation("suggestion.created", **suggestion))
        return telemetry

    def apply(
        self,
        delta: dict[str, Any],
        *,
        expected_generation: int | None = None,
        run_id: str = "",
    ) -> dict[str, Any]:
        old_state = self.state()
        head = self.store.ledger_head()
        required_generation = head["generation"] if expected_generation is None else expected_generation
        prepared, deduplicated = self._prepare_operations(delta.get("operations", []), old_state)
        if not prepared:
            return {
                "status": "success",
                "committed": {
                    "status": "no_change",
                    "generation": head["generation"],
                    "operations": [],
                    "deduplicated": deduplicated,
                },
                "derived": {"changes": [], "state_hash": state_hash(old_state)},
                "diagnostics": {"items": [], "complete": True},
                "suggested": {"relations": [], "authoritative": False},
                "recall": {"capsules": [], "complete": True},
            }
        generation = head["generation"] + 1
        self._preview_parts(prepared, old_state, generation)
        transaction = self.store.append(
            prepared,
            run_id=run_id or self._active_run_id(old_state),
            expected_generation=required_generation,
            metadata={"semantic_operation_count": len(prepared)},
        )
        committed_state = reduce_operations(self.store.all_operations(), self.store.load_policy())
        recalled = recall(
            old_state,
            committed_state,
            prepared,
            self.store.load_policy(),
            excluded_ids=self._worker_scope(old_state),
        )
        suggestions = relation_suggestions(old_state, committed_state, prepared)
        telemetry = self._automatic_telemetry(
            prepared, old_state, recalled, suggestions, transaction["sequence"]
        )
        telemetry_transaction = None
        if telemetry:
            telemetry_transaction = self.store.append_telemetry(
                telemetry,
                semantic_generation=transaction["sequence"],
                semantic_transaction_id=transaction["transaction_id"],
            )
        new_state = reduce_operations(self.store.all_operations(), self.store.load_policy())
        materialized = write_views(self.store, new_state)
        changes = self._changes(old_state, new_state)
        affected_ids = {item["entity_id"] for item in changes} | operation_entity_ids(prepared)
        affected_questions = [
            item for item in new_state["questions"].values() if item.get("id") in affected_ids or item.get("derived_status") == "reopened"
        ]
        affected_decisions = [
            item for item in new_state["decisions"].values() if item.get("id") in affected_ids or item.get("basis_changed")
        ]
        return {
            "status": "success",
            "committed": {
                "status": "committed",
                "transaction_id": transaction["transaction_id"],
                "generation": transaction["sequence"],
                "semantic_operations": prepared,
                "deduplicated": deduplicated,
            },
            "derived": {"changes": changes, **materialized},
            "diagnostics": {"items": new_state["diagnostics"], "complete": True},
            "suggested": {"relations": suggestions, "authoritative": False},
            "recall": recalled,
            "telemetry": {
                "transaction_id": telemetry_transaction["transaction_id"] if telemetry_transaction else "",
                "events": telemetry,
                "durable": True,
                "affects_epistemic_truth": False,
            },
            "affected_questions": affected_questions,
            "affected_decisions": affected_decisions,
        }

    @staticmethod
    def _active_run_id(state: dict[str, Any]) -> str:
        active = [item for item in state.get("runs", {}).values() if item.get("event") == "run.started"]
        return max(active, key=lambda item: item.get("generation", 0)).get("id", "") if active else ""

    def _read_checkpoint(self, required: bool = True) -> dict[str, Any]:
        path = self.store.paths.checkpoint
        if not path.is_file():
            if required:
                raise LedgerError("checkpoint.json does not exist")
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise LedgerError(f"checkpoint.json is invalid: {exc}") from exc

    def _record_telemetry(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        head = self.store.ledger_head()
        transaction = self.store.append_telemetry(
            events,
            semantic_generation=head["generation"],
            semantic_transaction_id=head["transaction_id"],
        )
        write_views(
            self.store,
            reduce_operations(self.store.all_operations(), self.store.load_policy()),
        )
        return transaction

    def checkpoint(self, checkpoint_id: str = "") -> dict[str, Any]:
        checkpoint_id = checkpoint_id or random_id("checkpoint")
        write = self.apply(
            {"operations": [operation("checkpoint.created", id=checkpoint_id, created_at=utc_now())]}
        )
        state = self.state()
        head = self.store.ledger_head()
        scope = self._worker_scope(state)
        checkpoint = {
            "schema_version": "epistemic-checkpoint.v1",
            "id": checkpoint_id,
            "ledger_generation": head["generation"],
            "ledger_transaction_hash": head["transaction_hash"],
            "state_hash": state_hash(state),
            "run_id": self._active_run_id(state),
            "goal": state.get("mission", {}).get("goal", ""),
            "steering": state.get("mission", {}).get("steering", ""),
            "open_question_ids": sorted(
                item_id
                for item_id, item in state.get("questions", {}).items()
                if item.get("derived_status") != "resolved"
            ),
            "important_dependency_bindings": [
                {"id": item_id, "value": item.get("value"), "status": item.get("status", "current")}
                for item_id, item in sorted(state.get("dependencies", {}).items())
            ],
            "worker_scope_entity_ids": sorted(scope),
            "context_generation": head["generation"],
        }
        atomic_write_json(self.store.paths.checkpoint, checkpoint)
        return {"status": "success", "checkpoint": checkpoint, "write": write["committed"]}

    def _worker_scope(self, state: dict[str, Any]) -> set[str]:
        run_id = self._active_run_id(state)
        run_generation = state.get("runs", {}).get(run_id, {}).get("generation", 0)
        return {
            entity_id
            for entity_id, entity in entity_catalog(state).items()
            if entity.get("generation", 0) >= run_generation
        }

    def resume(self) -> dict[str, Any]:
        checkpoint = self._read_checkpoint()
        transactions = self.store.read_transactions(verify=True)
        checkpoint_generation = checkpoint["ledger_generation"]
        if checkpoint_generation > len(transactions):
            raise LedgerError("checkpoint points beyond the ledger head")
        actual_hash = GENESIS_HASH if checkpoint_generation == 0 else transactions[checkpoint_generation - 1]["transaction_hash"]
        if actual_hash != checkpoint["ledger_transaction_hash"]:
            raise LedgerError("checkpoint ledger hash does not match authoritative history")
        rebuild = self.rebuild()
        context = self.context(since_generation=checkpoint_generation)
        return {
            "status": "success",
            "model_calls": 0,
            "checkpoint": checkpoint,
            "rebuild": rebuild,
            "changes_since_checkpoint": self.changes_since(checkpoint_generation),
            "context": context,
        }

    def close(self, outcome: str, summary: str = "", next_actions: list[str] | None = None) -> dict[str, Any]:
        state = self.state()
        run_id = self._active_run_id(state) or random_id("run")
        return self.apply(
            {
                "operations": [
                    operation(
                        "run.closed",
                        id=run_id,
                        outcome=outcome,
                        summary=summary,
                        next_actions=next_actions or [],
                        closed_at=utc_now(),
                    )
                ]
            },
            run_id=run_id,
        )

    def validate(self) -> dict[str, Any]:
        transactions = self.store.read_transactions(verify=True)
        telemetry_transactions = self.store.read_telemetry_transactions(verify=True)
        policy = self.store.load_policy()
        state = reduce_operations(self.store.all_operations(), policy)
        expected_hash = state_hash(state)
        metadata = sqlite_metadata(self.store.paths.state)
        derived_current = bool(metadata) and metadata.get("state_hash") == expected_hash
        checkpoint = self._read_checkpoint(required=False)
        checkpoint_valid = True
        if checkpoint:
            generation = checkpoint.get("ledger_generation", -1)
            checkpoint_valid = 0 <= generation <= len(transactions)
            if checkpoint_valid:
                actual = GENESIS_HASH if generation == 0 else transactions[generation - 1]["transaction_hash"]
                checkpoint_valid = actual == checkpoint.get("ledger_transaction_hash")
        return {
            "status": "success" if checkpoint_valid else "failed",
            "ledger_valid": True,
            "retrieval_ledger_valid": True,
            "policy_valid": True,
            "checkpoint_valid": checkpoint_valid,
            "derived_state_current": derived_current,
            "generation": state["generation"],
            "semantic_transactions": len(transactions),
            "telemetry_transactions": len(telemetry_transactions),
            "state_hash": expected_hash,
            "diagnostic_count": len(state["diagnostics"]),
        }
