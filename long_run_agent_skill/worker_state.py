"""Derived worker-state coverage and historical correction helpers."""

from __future__ import annotations

from typing import Any

from .util import sha256


ENTITY_COLLECTIONS = {
    "artifact": "artifacts",
    "evidence": "evidence",
    "claim": "claims",
    "assumption": "assumptions",
    "argument": "arguments",
    "attack": "attacks",
    "dependency": "dependencies",
    "question": "questions",
    "decision": "decisions",
    "verification": "verifications",
    "warrant": "warrants",
    "relation": "relations",
}

SURFACE_EVENT_TYPES = {
    "retrieval.surfaced",
    "retrieval.search_result_surfaced",
    "retrieval.inspect_topology_surfaced",
}


def operation_references(
    operations: list[dict[str, Any]],
) -> tuple[set[str], set[str]]:
    """Return all references and the subset that creates epistemic structure."""
    referenced: set[str] = set()
    structural: set[str] = set()
    structural_types = {
        "argument.asserted",
        "attack.asserted",
        "relation.asserted",
        "decision.recorded",
        "decision.revised",
        "question.opened",
        "question.updated",
        "question.resolved",
        "question.reopened",
        "verification.recorded",
    }
    for operation in operations:
        kind = operation.get("type", "")
        data = operation.get("data", {})
        local: set[str] = set()
        for key in ("premises", "grounds", "inputs", "basis"):
            local.update(
                item.get("id", "")
                for item in data.get(key, [])
                if isinstance(item, dict)
            )
        for key in ("source", "target", "artifact_ref", "conclusion"):
            value = data.get(key, {})
            if isinstance(value, dict):
                local.add(value.get("id") or value.get("claim_id", ""))
        for key in (
            "basis_claim_ids",
            "evidence_ids",
            "candidate_claim_ids",
            "related_claim_ids",
            "resolution_basis_claim_ids",
        ):
            local.update(data.get(key, []))
        local.update(
            item.get("assumption_id", "") for item in data.get("assumptions", [])
        )
        local.update(
            item.get("dependency_id", "") for item in data.get("dependencies", [])
        )
        if data.get("entity_id"):
            local.add(data["entity_id"])
        local.discard("")
        referenced.update(local)
        if kind in structural_types:
            structural.update(local)
    return referenced, structural


def _decay(strength: float, last_generation: int | None, generation: int, horizon: int) -> float:
    if last_generation is None:
        return 0.0
    age = max(generation - int(last_generation), 0)
    return round(strength * (0.5 ** (age / max(horizon, 1))), 6)


def derive_worker_state(state: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Derive independent containment dimensions from attention history."""
    generation = int(state.get("generation", 0))
    active_runs = [
        item
        for item in state.get("runs", {}).values()
        if item.get("event") == "run.started"
    ]
    run_boundary = max(
        (int(item.get("generation", 0)) for item in active_runs), default=0
    )
    settings = policy.get("worker_state", {})
    content_horizon = int(settings.get("content_horizon", 12))
    salience_horizon = int(settings.get("salience_horizon", 4))
    structural_horizon = int(settings.get("structural_horizon", 16))
    result: dict[str, Any] = {}
    for entity_id, attention in sorted(state.get("attention", {}).items()):
        def current(value: Any) -> int | None:
            if value is None or int(value) < run_boundary:
                return None
            return int(value)

        surfaced = current(attention.get("last_surfaced_generation"))
        context_exposed = current(
            attention.get("last_context_generated_generation")
        )
        expanded = current(attention.get("last_expanded_generation"))
        referenced = current(attention.get("last_referenced_generation"))
        structural = current(attention.get("last_structurally_used_generation"))
        content_sources = {
            "recall_capsule": _decay(0.25, surfaced, generation, content_horizon),
            "context_capsule": _decay(0.35, context_exposed, generation, content_horizon),
            "expanded_or_read": _decay(1.0, expanded, generation, content_horizon),
            "structural_summary": _decay(0.35, structural, generation, content_horizon),
        }
        salience_sources = {
            "recently_surfaced": _decay(0.45, surfaced, generation, salience_horizon),
            "included_in_context": _decay(0.55, context_exposed, generation, salience_horizon),
            "expanded_or_read": _decay(0.75, expanded, generation, salience_horizon),
            "referenced": _decay(0.9, referenced, generation, salience_horizon),
            "structurally_used": _decay(1.0, structural, generation, salience_horizon),
        }
        structural_sources = {
            "structurally_used": _decay(1.0, structural, generation, structural_horizon)
        }
        observed = [
            value
            for value in (surfaced, context_exposed, expanded, referenced, structural)
            if value is not None
        ]
        result[entity_id] = {
            "content_coverage": {
                "estimate": max(content_sources.values(), default=0.0),
                "sources": {
                    key: value for key, value in content_sources.items() if value > 0
                },
            },
            "salience": {
                "estimate": max(salience_sources.values(), default=0.0),
                "sources": {
                    key: value for key, value in salience_sources.items() if value > 0
                },
            },
            "structural_coverage": {
                "estimate": max(structural_sources.values(), default=0.0),
                "sources": {
                    key: value for key, value in structural_sources.items() if value > 0
                },
            },
            "recency": {
                "last_exposure_generation": max(
                    value for value in (surfaced, context_exposed, expanded) if value is not None
                )
                if any(value is not None for value in (surfaced, context_exposed, expanded))
                else None,
                "last_reference_generation": referenced,
                "last_structural_use_generation": structural,
                "last_activity_generation": max(observed) if observed else None,
                "generations_since_activity": generation - max(observed) if observed else None,
            },
            "retrieval_outcomes": {
                "surfaced": int(attention.get("surfaced_count", 0)),
                "expanded": int(attention.get("expanded_count", 0)),
                "referenced": int(attention.get("referenced_count", 0)),
                "structurally_used": int(attention.get("structurally_used_count", 0)),
                "relation_suggestion_accepted": int(
                    attention.get("relation_suggestion_accepted_count", 0)
                ),
                "dismissed_weak": int(attention.get("dismissed_weak_count", 0)),
                "dismissed_strong": int(attention.get("dismissed_strong_count", 0)),
            },
            "last_surfaced_material_state": attention.get(
                "last_surfaced_material_state"
            )
            if surfaced is not None
            else None,
            "last_surfaced_telemetry_sequence": (
                attention.get("last_surfaced_telemetry_sequence")
                if surfaced is not None
                else None
            ),
        }
    return result


def material_state(
    entity_id: str, entity: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    """Return the correctness-relevant state used for repeat inhibition."""
    entity_type = entity.get("entity_type", "")
    common = {
        "entity_type": entity_type,
        "revision_id": entity.get("revision_id"),
        "commitment": entity.get("commitment"),
        "status": entity.get("status"),
    }
    if entity_type == "claim":
        derived = entity.get("derived", {})
        successors = sorted(
            relation.get("source", {}).get("id", "")
            for relation in state.get("relations", {}).values()
            if relation.get("target", {}).get("id") == entity_id
            and relation.get("relation") in {"supersedes", "retracts"}
        )
        return {
            **common,
            "support_state": derived.get("support_state"),
            "applicability_state": derived.get("applicability_state"),
            "verification_state": derived.get("verification_state"),
            "supporting_arguments": derived.get("supporting_arguments", []),
            "opposing_arguments": derived.get("opposing_arguments", []),
            "revalidation_plan": derived.get("revalidation_plan", []),
            "decision_ids": sorted(
                item_id
                for item_id, item in state.get("decisions", {}).items()
                if entity_id in item.get("basis_claim_ids", [])
            ),
            "question_states": sorted(
                (
                    item_id,
                    item.get("derived_status"),
                )
                for item_id, item in state.get("questions", {}).items()
                if entity_id
                in (
                    item.get("candidate_claim_ids", [])
                    + item.get("related_claim_ids", [])
                    + item.get("resolution_basis_claim_ids", [])
                )
            ),
            "successor_ids": [item for item in successors if item],
        }
    if entity_type == "argument":
        return {
            **common,
            "active": entity.get("active"),
            "applicable": entity.get("applicable"),
            "blockers": entity.get("blockers", []),
            "defeated_by": entity.get("defeated_by", []),
            "conclusion": entity.get("conclusion", {}),
        }
    if entity_type == "attack":
        return {
            **common,
            "active": entity.get("active"),
            "applicable": entity.get("applicable"),
            "blockers": entity.get("blockers", []),
            "target": entity.get("target", {}),
        }
    if entity_type == "evidence":
        invalidated_by = sorted(
            attack_id
            for attack_id, attack in state.get("attacks", {}).items()
            if attack.get("active")
            and attack.get("attack_type") == "undermine"
            and attack.get("target", {}).get("type") == "evidence"
            and attack.get("target", {}).get("id") == entity_id
        )
        return {
            **common,
            "orphan": entity.get("orphan"),
            "invalidated_for_current_use_by": invalidated_by,
            "artifact_content_hash": entity.get("artifact_content_hash"),
        }
    if entity_type == "assumption":
        return {**common, **entity.get("derived", {})}
    if entity_type in {"dependency", "verification"}:
        return {
            **common,
            "value": entity.get("value"),
            "changed": entity.get("changed"),
            "valid": entity.get("valid"),
            "blockers": entity.get("blockers", []),
        }
    if entity_type == "question":
        return {**common, "derived_status": entity.get("derived_status")}
    if entity_type == "decision":
        return {
            **common,
            "basis_changed": entity.get("basis_changed"),
            "changed_basis_claim_ids": entity.get("changed_basis_claim_ids", []),
        }
    if entity_type == "relation":
        return {
            **common,
            "relation": entity.get("relation"),
            "source": entity.get("source"),
            "target": entity.get("target"),
            "basis": entity.get("basis", []),
            "rationale": entity.get("rationale", ""),
        }
    return common


def material_fingerprint(
    entity_id: str, entity: dict[str, Any], state: dict[str, Any]
) -> str:
    return sha256(material_state(entity_id, entity, state))


def material_changes(previous: dict[str, Any] | None, current: dict[str, Any]) -> list[str]:
    if not previous:
        return []
    return sorted(
        key
        for key in set(previous) | set(current)
        if previous.get(key) != current.get(key)
    )


def noncurrent_context(
    entity_id: str, entity: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    """Explain whether an entity is noncurrent and the decisive correction path."""
    entity_type = entity.get("entity_type", "")
    relations = [
        relation
        for relation in state.get("relations", {}).values()
        if relation.get("target", {}).get("id") == entity_id
        and relation.get("relation") in {"supersedes", "retracts"}
    ]
    relations.sort(key=lambda item: (item.get("generation", 0), item.get("id", "")))
    successor = None
    decisive_path: list[dict[str, Any]] = []
    if relations:
        relation = relations[-1]
        source_id = relation.get("source", {}).get("id", "")
        source = state.get("claims", {}).get(source_id, {})
        successor = {
            "id": source_id,
            "name": source.get("name") or source.get("proposition") or source_id,
            "state": source.get("derived", {}),
        }
        decisive_path.append(
            {
                "type": "relation",
                "id": relation.get("id", ""),
                "relation": relation.get("relation"),
                "basis": relation.get("basis", []),
                "rationale": relation.get("rationale", ""),
            }
        )
        return {
            "noncurrent": True,
            "badge": "SUPERSEDED"
            if relation.get("relation") == "supersedes"
            else "RETRACTED",
            "why_no_longer_current": relation.get("rationale")
            or f"{source_id} {relation.get('relation')} this historical state",
            "decisive_path": decisive_path,
            "current_successor": successor,
            "significance": 1.0,
            "explanation_quality": 1.0
            if relation.get("basis") and relation.get("rationale")
            else 0.75,
        }
    if entity_type == "claim":
        derived = entity.get("derived", {})
        if entity.get("commitment") == "withdrawn":
            return {
                "noncurrent": True,
                "badge": "WITHDRAWN",
                "why_no_longer_current": entity.get("withdrawal_reason")
                or entity.get("reason")
                or "the claim commitment was withdrawn",
                "decisive_path": [],
                "current_successor": None,
                "significance": 0.9,
                "explanation_quality": 0.55,
            }
        if derived.get("applicability_state") in {
            "out_of_scope",
            "revalidation_required",
        }:
            paths = derived.get("revalidation_plan", [])
            return {
                "noncurrent": True,
                "badge": derived.get("applicability_state", "").upper(),
                "why_no_longer_current": (
                    "historical support is not currently applicable; "
                    + "; ".join(
                        action.get("reason", "")
                        for path in paths
                        for action in path.get("actions", [])
                        if action.get("reason")
                    )
                ).rstrip("; "),
                "decisive_path": paths,
                "current_successor": None,
                "significance": 0.85,
                "explanation_quality": 0.75 if paths else 0.4,
            }
    if entity_type == "argument" and (
        entity.get("retracted")
        or not entity.get("active")
        or not entity.get("applicable", True)
    ):
        paths = [
            {"type": "attack", "id": item}
            for item in entity.get("defeated_by", [])
        ]
        paths.extend(
            {"type": "blocker", "reason": item}
            for item in entity.get("blockers", [])
        )
        return {
            "noncurrent": True,
            "badge": "DEFEATED" if entity.get("defeated_by") else "INAPPLICABLE",
            "why_no_longer_current": "; ".join(
                [f"defeated by {item}" for item in entity.get("defeated_by", [])]
                + entity.get("blockers", [])
            )
            or "the argument was retracted",
            "decisive_path": paths,
            "current_successor": None,
            "significance": 0.9,
            "explanation_quality": 0.8 if paths else 0.5,
        }
    if entity_type == "evidence":
        invalidators = material_state(entity_id, entity, state).get(
            "invalidated_for_current_use_by", []
        )
        if invalidators:
            return {
                "noncurrent": True,
                "badge": "CURRENT USE INVALIDATED",
                "why_no_longer_current": (
                    "the observation remains historical evidence, but its current use "
                    f"is undermined by {', '.join(invalidators)}"
                ),
                "decisive_path": [
                    {"type": "attack", "id": item} for item in invalidators
                ],
                "current_successor": None,
                "significance": 0.9,
                "explanation_quality": 0.8,
            }
    if entity_type == "verification" and not entity.get("valid", True):
        return {
            "noncurrent": True,
            "badge": "INVALIDATED",
            "why_no_longer_current": "; ".join(entity.get("blockers", []))
            or "the verification receipt is no longer valid",
            "decisive_path": [
                {"type": "blocker", "reason": item}
                for item in entity.get("blockers", [])
            ],
            "current_successor": None,
            "significance": 0.85,
            "explanation_quality": 0.75,
        }
    return {
        "noncurrent": False,
        "badge": "",
        "why_no_longer_current": "",
        "decisive_path": [],
        "current_successor": None,
        "significance": 0.0,
        "explanation_quality": 0.0,
    }
