"""Explainable historical recall, diversification, and relation suggestions."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from typing import Any

from .graph import correlation_reasons
from .util import bounded_text, stable_id


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
}


def operation_entity_ids(operations: list[dict[str, Any]]) -> set[str]:
    entity_ids: set[str] = set()
    for operation in operations:
        data = operation.get("data", {})
        if data.get("id"):
            entity_ids.add(data["id"])
        if data.get("entity_id"):
            entity_ids.add(data["entity_id"])
        for reference in data.get("premises", []) + data.get("grounds", []):
            if reference.get("id"):
                entity_ids.add(reference["id"])
        conclusion = data.get("conclusion", {})
        conclusion_id = conclusion.get("id") or conclusion.get("claim_id")
        if conclusion_id:
            entity_ids.add(conclusion_id)
        target = data.get("target", {})
        if target.get("id"):
            entity_ids.add(target["id"])
        artifact_ref = data.get("artifact_ref", {})
        if artifact_ref.get("id"):
            entity_ids.add(artifact_ref["id"])
        entity_ids.update(
            item.get("assumption_id", "") for item in data.get("assumptions", [])
        )
        entity_ids.update(
            item.get("dependency_id", "") for item in data.get("dependencies", [])
        )
        entity_ids.update(data.get("basis_claim_ids", []))
        entity_ids.update(data.get("related_claim_ids", []))
    return entity_ids


def entity_catalog(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for entity_type, collection in ENTITY_COLLECTIONS.items():
        for entity_id, source in state.get(collection, {}).items():
            entity = dict(source)
            entity["entity_type"] = entity_type
            entity["id"] = entity_id
            catalog[entity_id] = entity
    return catalog


def _entity_roots(entity: dict[str, Any], state: dict[str, Any]) -> set[str]:
    entity_type = entity.get("entity_type")
    if entity_type == "evidence":
        return {entity["id"]}
    if entity_type == "claim":
        return set(entity.get("derived", {}).get("root_evidence", []))
    if entity_type == "argument":
        return set(entity.get("root_evidence", []))
    if entity_type == "decision":
        roots: set[str] = set()
        for claim_id in entity.get("basis_claim_ids", []):
            roots.update(state.get("claims", {}).get(claim_id, {}).get("derived", {}).get("root_evidence", []))
        return roots
    return set(entity.get("evidence_ids", []))


def _entity_dependencies(entity: dict[str, Any], state: dict[str, Any]) -> set[str]:
    dependencies = {item.get("dependency_id", "") for item in entity.get("dependencies", [])}
    if entity.get("entity_type") == "claim":
        for argument_id in entity.get("derived", {}).get("supporting_arguments", []):
            argument = state.get("arguments", {}).get(argument_id, {})
            dependencies.update(item.get("dependency_id", "") for item in argument.get("dependencies", []))
    return dependencies - {""}


def _subject(entity: dict[str, Any]) -> str:
    return str(entity.get("subject", "")).strip().lower()


def _warrant(entity: dict[str, Any]) -> str:
    warrant = entity.get("warrant", {})
    if isinstance(warrant, str):
        return warrant
    return str(warrant.get("id") or warrant.get("rule") or warrant.get("statement") or "")


def _name(entity: dict[str, Any]) -> str:
    return str(
        entity.get("name")
        or entity.get("proposition")
        or entity.get("question")
        or entity.get("choice")
        or entity.get("description")
        or entity.get("id")
    )


def _description(entity: dict[str, Any]) -> str:
    return str(
        entity.get("description")
        or entity.get("proposition")
        or entity.get("question")
        or entity.get("choice")
        or ""
    )


def _derived_state(entity: dict[str, Any]) -> str:
    if entity.get("entity_type") == "claim":
        derived = entity.get("derived", {})
        return "/".join(
            filter(None, [derived.get("support_state"), derived.get("applicability_state")])
        )
    if entity.get("entity_type") == "argument":
        return "active" if entity.get("active") else "inactive"
    if entity.get("entity_type") == "attack":
        return "active" if entity.get("active") else "inactive"
    if entity.get("entity_type") == "question":
        return entity.get("derived_status", "opened")
    if entity.get("entity_type") == "decision":
        return "basis_changed" if entity.get("basis_changed") else "current"
    if entity.get("entity_type") == "evidence":
        return "orphan" if entity.get("orphan") else "integrated"
    return entity.get("status", "current")


def _correlated(seed_roots: set[str], candidate_roots: set[str], evidence: dict[str, Any]) -> list[str]:
    reasons: set[str] = set()
    for left_id in seed_roots:
        for right_id in candidate_roots:
            if left_id == right_id:
                continue
            reasons.update(correlation_reasons(evidence.get(left_id, {}), evidence.get(right_id, {})))
    return sorted(reasons)


def _family_key(entity: dict[str, Any], roots: set[str], state: dict[str, Any]) -> str:
    if entity.get("entity_type") == "evidence":
        evidence = state.get("evidence", {}).get(entity["id"], {})
        for field in ("artifact_id", "measurement_process", "source_run", "producer", "subject"):
            if evidence.get(field):
                return f"evidence:{field}:{evidence[field]}"
    if roots:
        return f"{entity.get('entity_type')}:{','.join(sorted(roots))}"
    return f"{entity.get('entity_type')}:{entity.get('id')}"


def _dismissed_contexts(state: dict[str, Any]) -> set[tuple[str, str]]:
    dismissed: set[tuple[str, str]] = set()
    for event in state.get("retrieval_events", []):
        if event.get("event") == "retrieval.dismissed":
            for entity_id in event.get("entity_ids", []):
                dismissed.add((event.get("context_key", ""), entity_id))
    return dismissed


def recall(
    old_state: dict[str, Any],
    new_state: dict[str, Any],
    operations: list[dict[str, Any]],
    policy: dict[str, Any],
    *,
    excluded_ids: set[str] | None = None,
) -> dict[str, Any]:
    settings = policy["retrieval"]
    weights = settings["weights"]
    seeds = operation_entity_ids(operations)
    excluded = set(excluded_ids or set()) | seeds
    catalog = entity_catalog(new_state)
    seed_entities = [catalog[item] for item in sorted(seeds) if item in catalog]
    seed_roots = {root for entity in seed_entities for root in _entity_roots(entity, new_state)}
    seed_dependencies = {
        dependency for entity in seed_entities for dependency in _entity_dependencies(entity, new_state)
    }
    seed_subjects = {_subject(entity) for entity in seed_entities if _subject(entity)}
    seed_warrants = {_warrant(entity) for entity in seed_entities if _warrant(entity)}
    context_key = stable_id("recall_ctx", sorted(seeds))
    dismissed = _dismissed_contexts(new_state)

    historical_claim_roots = [
        set(claim.get("derived", {}).get("root_evidence", []))
        for claim in old_state.get("claims", {}).values()
    ]
    root_frequency = Counter(root for roots in historical_claim_roots for root in roots)
    corpus_size = max(len(historical_claim_roots), 1)
    candidates: list[dict[str, Any]] = []
    for entity_id, entity in sorted(catalog.items()):
        if entity_id in excluded or entity_id not in entity_catalog(old_state):
            continue
        roots = _entity_roots(entity, new_state)
        shared_roots = sorted(seed_roots & roots)
        correlated = _correlated(seed_roots, roots, new_state.get("evidence", {}))
        dependencies = _entity_dependencies(entity, new_state)
        features = {
            "shared_root_count": len(shared_roots),
            "shared_root_idf": sum(
                math.log((corpus_size + 1) / (root_frequency[root] + 1)) + 1 for root in shared_roots
            ),
            "correlation_reason_count": len(correlated),
            "same_subject": int(bool(_subject(entity) and _subject(entity) in seed_subjects)),
            "shared_dependency_count": len(seed_dependencies & dependencies),
            "same_warrant": int(bool(_warrant(entity) and _warrant(entity) in seed_warrants)),
            "recently_surfaced": int(
                new_state.get("attention", {}).get(entity_id, {}).get("last_surfaced_generation", -100)
                >= new_state.get("generation", 0) - 2
            ),
            "contextually_dismissed": int((context_key, entity_id) in dismissed),
        }
        score = (
            weights["shared_root"] * features["shared_root_idf"]
            + weights["correlated_root"] * features["correlation_reason_count"]
            + weights["same_subject"] * features["same_subject"]
            + weights["shared_dependency"] * features["shared_dependency_count"]
            + weights["same_warrant"] * features["same_warrant"]
            - weights["novelty_penalty"] * features["recently_surfaced"]
            - weights["dismissal_penalty"] * features["contextually_dismissed"]
        )
        if score <= 0:
            continue
        reasons: list[str] = []
        if shared_roots:
            reasons.append("shared root evidence: " + ", ".join(shared_roots))
        if correlated:
            reasons.append("correlated evidence: " + ", ".join(correlated))
        if features["same_subject"]:
            reasons.append("same subject")
        if features["shared_dependency_count"]:
            reasons.append("shared dependency")
        if features["same_warrant"]:
            reasons.append("same warrant")
        candidates.append(
            {
                "entity_id": entity_id,
                "entity_type": entity["entity_type"],
                "score": round(score, 6),
                "features": features,
                "reasons": reasons,
                "shared_roots": shared_roots,
                "correlation_reasons": correlated,
                "family_key": _family_key(entity, roots, new_state),
                "entity": entity,
            }
        )
    candidates.sort(key=lambda item: (-item["score"], item["entity_type"], item["entity_id"]))
    candidates = candidates[: settings["max_candidates"]]

    selected: list[dict[str, Any]] = []
    remaining = list(candidates)
    while remaining and len(selected) < settings["max_capsules"]:
        family_selection = Counter(item["family_key"] for item in selected)
        eligible = [
            item
            for item in remaining
            if item["entity_type"] != "evidence"
            or family_selection[item["family_key"]] < settings["max_family_members"]
        ]
        if not eligible:
            break

        def diversified_score(item: dict[str, Any]) -> tuple[float, str]:
            similarity = 0.0
            for chosen in selected:
                if item["family_key"] == chosen["family_key"]:
                    similarity = max(similarity, 1.0)
                elif item["entity_type"] == chosen["entity_type"]:
                    similarity = max(similarity, 0.3)
            adjusted = item["score"] * (1 - settings["diversity_penalty"] * similarity)
            return adjusted, item["entity_id"]

        winner = max(eligible, key=diversified_score)
        remaining.remove(winner)
        selected.append(winner)

    family_counts = Counter(item["family_key"] for item in candidates)
    capsules: list[dict[str, Any]] = []
    for rank, item in enumerate(selected, 1):
        entity = item.pop("entity")
        description, complete = bounded_text(_description(entity), settings["capsule_description_chars"])
        capsule = {
            "entity_id": item["entity_id"],
            "entity_type": item["entity_type"],
            "name": _name(entity),
            "description": description,
            "description_complete": complete,
            "current_state": _derived_state(entity),
            "why_relevant": item["reasons"],
            "relation_path": item["shared_roots"][: settings["max_relation_path"]],
            "historical_generation": entity.get("generation", 0),
            "reference": {"action": "expand", "entity_id": item["entity_id"]},
            "rank": rank,
            "score": item["score"],
        }
        if family_counts[item["family_key"]] > 1:
            capsule["represents_family"] = {
                "family_key": item["family_key"],
                "related_candidate_count": family_counts[item["family_key"]] - 1,
            }
        capsules.append(capsule)
    telemetry_candidates = [
        {
            key: value
            for key, value in item.items()
            if key in {"entity_id", "entity_type", "score", "features", "family_key"}
        }
        for item in candidates
    ]
    return {
        "context_key": context_key,
        "seed_entity_ids": sorted(seeds),
        "candidate_count": len(candidates),
        "candidates": telemetry_candidates,
        "capsules": capsules,
        "complete": len(candidates) <= settings["max_capsules"],
        "continuation": None if len(candidates) <= settings["max_capsules"] else {"offset": len(capsules)},
    }


def relation_suggestions(
    old_state: dict[str, Any], new_state: dict[str, Any], operations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    new_claim_ids = {
        operation.get("data", {}).get("id")
        for operation in operations
        if operation.get("type") in {"claim.asserted", "claim.revised"}
    } - {None}
    suggestions: list[dict[str, Any]] = []
    for claim_id in sorted(new_claim_ids):
        claim = new_state.get("claims", {}).get(claim_id, {})
        roots = set(claim.get("derived", {}).get("root_evidence", []))
        for historical_id, historical in sorted(old_state.get("claims", {}).items()):
            if historical_id == claim_id:
                continue
            historical_roots = set(historical.get("derived", {}).get("root_evidence", []))
            shared = sorted(roots & historical_roots)
            same_subject = bool(_subject(claim) and _subject(claim) == _subject(historical))
            similarity = SequenceMatcher(None, claim.get("proposition", ""), historical.get("proposition", "")).ratio()
            if not shared and not (same_subject and similarity >= 0.55):
                continue
            proposed = "refines" if same_subject and similarity >= 0.55 else "related_to"
            body = {
                "source": {"type": "claim", "id": claim_id},
                "target": {"type": "claim", "id": historical_id},
                "relation": proposed,
                "basis": {
                    "shared_root_evidence": shared,
                    "same_subject": same_subject,
                    "text_similarity": round(similarity, 4),
                },
            }
            suggestions.append(
                {
                    "id": stable_id("suggestion", body),
                    **body,
                    "status": "suggested",
                    "authoritative": False,
                }
            )
    return suggestions
