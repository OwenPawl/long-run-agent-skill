"""Explainable marginal-value recall and relation suggestions."""

from __future__ import annotations

import math
from collections import Counter
from difflib import SequenceMatcher
from typing import Any

from .annotations import canonical_annotation
from .graph import correlation_reasons
from .provenance import evidence_roots
from .util import bounded_text, stable_id
from .worker_state import (
    ENTITY_COLLECTIONS,
    material_changes,
    material_state,
    noncurrent_context,
    operation_references,
)


def operation_entity_ids(operations: list[dict[str, Any]]) -> set[str]:
    entity_ids, _ = operation_references(operations)
    for operation in operations:
        data = operation.get("data", {})
        if data.get("id"):
            entity_ids.add(data["id"])
        if data.get("entity_id"):
            entity_ids.add(data["entity_id"])
    return entity_ids - {""}


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
        return evidence_roots(entity["id"], state.get("evidence", {}))
    if entity_type == "claim":
        derived = entity.get("derived", {})
        roots = set(derived.get("root_evidence", []))
        if roots:
            return roots
        for argument in _entity_arguments(entity, state):
            roots.update(argument.get("root_evidence", []))
        return roots
    if entity_type == "argument":
        return set(entity.get("root_evidence", []))
    if entity_type == "decision":
        roots: set[str] = set()
        for claim_id in entity.get("basis_claim_ids", []):
            roots.update(
                state.get("claims", {})
                .get(claim_id, {})
                .get("derived", {})
                .get("root_evidence", [])
            )
        return roots
    return set(entity.get("evidence_ids", []))


def _entity_arguments(
    entity: dict[str, Any], state: dict[str, Any]
) -> list[dict[str, Any]]:
    if entity.get("entity_type") == "argument":
        return [entity]
    if entity.get("entity_type") != "claim":
        return []
    return [
        argument
        for argument in state.get("arguments", {}).values()
        if (
            argument.get("conclusion", {}).get("id")
            or argument.get("conclusion", {}).get("claim_id")
        )
        == entity.get("id")
    ]


def _entity_dependencies(entity: dict[str, Any], state: dict[str, Any]) -> set[str]:
    dependencies = {
        item.get("dependency_id", "") for item in entity.get("dependencies", [])
    }
    for argument in _entity_arguments(entity, state):
        dependencies.update(
            item.get("dependency_id", "")
            for item in argument.get("dependencies", [])
        )
    return dependencies - {""}


def _warrant(entity: dict[str, Any]) -> str:
    warrant = entity.get("warrant", {})
    if isinstance(warrant, str):
        return warrant
    return str(
        warrant.get("id")
        or warrant.get("rule")
        or warrant.get("statement")
        or ""
    )


def _entity_warrants(entity: dict[str, Any], state: dict[str, Any]) -> set[str]:
    values = {_warrant(entity)} if _warrant(entity) else set()
    values.update(_warrant(argument) for argument in _entity_arguments(entity, state))
    return values - {""}


def _entity_assumptions(entity: dict[str, Any], state: dict[str, Any]) -> set[str]:
    values = {
        item.get("assumption_id", "") for item in entity.get("assumptions", [])
    }
    for argument in _entity_arguments(entity, state):
        values.update(
            item.get("assumption_id", "")
            for item in argument.get("assumptions", [])
        )
    return values - {""}


def _measurement_patterns(
    roots: set[str], state: dict[str, Any]
) -> set[str]:
    values: set[str] = set()
    for root in roots:
        evidence = state.get("evidence", {}).get(root, {})
        for key in (
            "producer",
            "measurement_process",
            "verifier_family",
            "artifact_id",
            "source_run",
        ):
            if evidence.get(key):
                values.add(f"{key}:{evidence[key]}")
    return values


def _subject(entity: dict[str, Any]) -> str:
    return canonical_annotation(
        entity, entity.get("entity_type", "entity")
    )["subject"].lower()


def _derived_state(entity: dict[str, Any]) -> str:
    entity_type = entity.get("entity_type")
    if entity_type == "claim":
        derived = entity.get("derived", {})
        return "/".join(
            filter(
                None,
                [
                    derived.get("support_state"),
                    derived.get("applicability_state"),
                ],
            )
        )
    if entity_type in {"argument", "attack"}:
        return "active" if entity.get("active") else "inactive"
    if entity_type == "question":
        return entity.get("derived_status", "opened")
    if entity_type == "decision":
        return "basis_changed" if entity.get("basis_changed") else "current"
    if entity_type == "evidence":
        return "orphan" if entity.get("orphan") else "integrated"
    return entity.get("status", "current")


def _correlated(
    seed_roots: set[str],
    candidate_roots: set[str],
    evidence: dict[str, Any],
) -> list[str]:
    reasons: set[str] = set()
    for left_id in seed_roots:
        for right_id in candidate_roots:
            if left_id == right_id:
                continue
            reasons.update(
                correlation_reasons(
                    evidence.get(left_id, {}), evidence.get(right_id, {})
                )
            )
    return sorted(reasons)


def _family_key(
    entity: dict[str, Any], roots: set[str], state: dict[str, Any]
) -> str:
    if entity.get("entity_type") == "evidence":
        evidence = state.get("evidence", {}).get(entity["id"], {})
        for field in (
            "artifact_id",
            "measurement_process",
            "source_run",
            "producer",
            "subject",
        ):
            if evidence.get(field):
                return f"evidence:{field}:{evidence[field]}"
    if roots:
        return f"{entity.get('entity_type')}:{','.join(sorted(roots))}"
    return f"{entity.get('entity_type')}:{entity.get('id')}"


def _dismissal_feedback(state: dict[str, Any]) -> dict[tuple[str, str], float]:
    dismissed: dict[tuple[str, str], float] = {}
    for event in state.get("retrieval_events", []):
        if event.get("event") != "retrieval.dismissed":
            continue
        for entity_id in event.get("entity_ids", []):
            key = (event.get("context_key", ""), entity_id)
            weight = 1.0 if event.get("feedback_strength") == "strong" else 0.4
            dismissed[key] = max(dismissed.get(key, 0.0), weight)
    return dismissed


def _coverage_strength(coverage: dict[str, Any]) -> float:
    return min(
        1.0,
        float(coverage.get("content_coverage", {}).get("estimate", 0.0))
        + 0.5 * float(coverage.get("salience", {}).get("estimate", 0.0))
        + float(coverage.get("structural_coverage", {}).get("estimate", 0.0)),
    )


def _pair_redundancy(
    left: dict[str, Any],
    left_roots: set[str],
    right: dict[str, Any],
    state: dict[str, Any],
) -> float:
    if left.get("id") == right.get("id"):
        return 1.0
    right_roots = _entity_roots(right, state)
    overlap = (
        len(left_roots & right_roots) / len(left_roots | right_roots)
        if left_roots | right_roots
        else 0.0
    )
    correlated = bool(
        _correlated(left_roots, right_roots, state.get("evidence", {}))
    )
    same_subject = bool(_subject(left) and _subject(left) == _subject(right))
    same_warrant = bool(
        _entity_warrants(left, state) & _entity_warrants(right, state)
    )
    return min(
        1.0,
        overlap
        + (0.45 if correlated else 0.0)
        + (0.25 if same_subject else 0.0)
        + (0.2 if same_warrant else 0.0),
    )


def _basis_features(
    entity: dict[str, Any],
    roots: set[str],
    seed_roots: set[str],
    seed_dependencies: set[str],
    seed_warrants: set[str],
    seed_assumptions: set[str],
    seed_patterns: set[str],
    state: dict[str, Any],
) -> dict[str, Any]:
    correlated = _correlated(seed_roots, roots, state.get("evidence", {}))
    dependencies = _entity_dependencies(entity, state)
    warrants = _entity_warrants(entity, state)
    assumptions = _entity_assumptions(entity, state)
    patterns = _measurement_patterns(roots, state)
    return {
        "shared_roots": sorted(seed_roots & roots),
        "correlation_reasons": correlated,
        "shared_dependencies": sorted(seed_dependencies & dependencies),
        "shared_warrants": sorted(seed_warrants & warrants),
        "shared_assumptions": sorted(seed_assumptions & assumptions),
        "shared_measurement_patterns": sorted(seed_patterns & patterns),
    }


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
    old_catalog = entity_catalog(old_state)
    seed_entities = [catalog[item] for item in sorted(seeds) if item in catalog]
    seed_roots = {
        root
        for entity in seed_entities
        for root in _entity_roots(entity, new_state)
    }
    seed_dependencies = {
        dependency
        for entity in seed_entities
        for dependency in _entity_dependencies(entity, new_state)
    }
    seed_warrants = {
        warrant
        for entity in seed_entities
        for warrant in _entity_warrants(entity, new_state)
    }
    seed_assumptions = {
        assumption
        for entity in seed_entities
        for assumption in _entity_assumptions(entity, new_state)
    }
    seed_patterns = {
        pattern
        for entity in seed_entities
        for pattern in _measurement_patterns(
            _entity_roots(entity, new_state), new_state
        )
    }
    seed_subjects = {
        _subject(entity) for entity in seed_entities if _subject(entity)
    }
    context_key = stable_id("recall_ctx", sorted(seeds))
    dismissed = _dismissal_feedback(new_state)
    worker_state = new_state.get("worker_state", {})

    covered: dict[str, float] = {}
    for entity_id in catalog:
        coverage = _coverage_strength(worker_state.get(entity_id, {}))
        if entity_id in excluded:
            coverage = 1.0
        if coverage > 0:
            covered[entity_id] = coverage

    historical_claim_roots = [
        set(claim.get("derived", {}).get("root_evidence", []))
        for claim in old_state.get("claims", {}).values()
    ]
    root_frequency = Counter(
        root for roots in historical_claim_roots for root in roots
    )
    corpus_size = max(len(historical_claim_roots), 1)
    candidates: list[dict[str, Any]] = []
    for entity_id, entity in sorted(catalog.items()):
        if (
            entity_id in excluded
            or entity_id not in old_catalog
            or entity.get("entity_type") in {"relation", "warrant"}
        ):
            continue
        roots = _entity_roots(entity, new_state)
        basis = _basis_features(
            entity,
            roots,
            seed_roots,
            seed_dependencies,
            seed_warrants,
            seed_assumptions,
            seed_patterns,
            new_state,
        )
        coverage = worker_state.get(entity_id, {})
        current_material = material_state(entity_id, entity, new_state)
        previous_material = coverage.get("last_surfaced_material_state")
        changed_fields = material_changes(previous_material, current_material)
        material_changed = bool(previous_material and changed_fields)
        attention = new_state.get("attention", {}).get(entity_id, {})
        last_surface = attention.get("last_surfaced_generation", -100)
        recently_surfaced = int(
            last_surface
            >= new_state.get("generation", 0)
            - int(settings["surface_inhibition_horizon"])
        )
        self_redundancy = (
            0.0 if material_changed else _coverage_strength(coverage)
        )
        current_redundancy = 0.0
        redundant_with = ""
        for covered_id, strength in covered.items():
            if covered_id == entity_id or covered_id not in catalog:
                continue
            overlap = _pair_redundancy(
                entity, roots, catalog[covered_id], new_state
            ) * strength
            if overlap > current_redundancy:
                current_redundancy = overlap
                redundant_with = covered_id

        same_subject = int(
            bool(_subject(entity) and _subject(entity) in seed_subjects)
        )
        shared_root_idf = sum(
            math.log((corpus_size + 1) / (root_frequency[root] + 1)) + 1
            for root in basis["shared_roots"]
        )
        outcome = coverage.get("retrieval_outcomes", {})
        historical_usefulness = min(
            2.0,
            0.2 * outcome.get("expanded", 0)
            + 0.5 * outcome.get("referenced", 0)
            + outcome.get("structurally_used", 0)
            + 0.5 * outcome.get("relation_suggestion_accepted", 0),
        )
        correction = noncurrent_context(entity_id, entity, new_state)
        correction_basis_count = sum(
            bool(basis[key])
            for key in (
                "shared_roots",
                "correlation_reasons",
                "shared_dependencies",
                "shared_warrants",
                "shared_assumptions",
                "shared_measurement_patterns",
            )
        )
        if correction["noncurrent"] and correction_basis_count < int(
            settings["corrective_min_basis_signals"]
        ):
            continue
        role = "corrective" if correction["noncurrent"] else "constructive"
        corrective_value = (
            correction["significance"]
            * correction["explanation_quality"]
            * (1 + 0.15 * max(correction_basis_count - 1, 0))
            if role == "corrective"
            else 0.0
        )
        dismissal_weight = dismissed.get((context_key, entity_id), 0.0)
        relevance = (
            weights["shared_root"] * shared_root_idf
            + weights["correlated_root"] * len(basis["correlation_reasons"])
            + weights["same_subject"] * same_subject
            + weights["shared_dependency"] * len(basis["shared_dependencies"])
            + weights["same_warrant"] * len(basis["shared_warrants"])
            + 2.0 * len(basis["shared_assumptions"])
            + 1.5 * len(basis["shared_measurement_patterns"])
        )
        score_components = {
            "relevance_to_current_work": relevance,
            "epistemic_surprise_or_material_change": (
                weights["material_change"] if material_changed else 0.0
            ),
            "historical_usefulness": (
                weights["historical_usefulness"] * historical_usefulness
            ),
            "corrective_value": weights["corrective_value"] * corrective_value,
            "coverage_redundancy": -weights["coverage_redundancy"]
            * self_redundancy,
            "current_state_redundancy": -weights["current_state_redundancy"]
            * current_redundancy,
            "recent_unchanged_redundancy": (
                -weights["novelty_penalty"] * recently_surfaced
                if not material_changed
                else 0.0
            ),
            "contextual_negative_feedback": -weights["dismissal_penalty"]
            * dismissal_weight,
        }
        score = sum(score_components.values())
        if score <= 0:
            continue
        reasons: list[str] = []
        if basis["shared_roots"]:
            reasons.append(
                "shared root evidence: " + ", ".join(basis["shared_roots"])
            )
        if basis["correlation_reasons"]:
            reasons.append(
                "correlated evidence: "
                + ", ".join(basis["correlation_reasons"])
            )
        if same_subject:
            reasons.append("same subject")
        if basis["shared_dependencies"]:
            reasons.append(
                "shared dependency: " + ", ".join(basis["shared_dependencies"])
            )
        if basis["shared_warrants"]:
            reasons.append("same or similar warrant")
        if basis["shared_assumptions"]:
            reasons.append("shared assumption")
        if basis["shared_measurement_patterns"]:
            reasons.append("shared measurement/provenance pattern")
        if material_changed:
            reasons.append(
                "material epistemic change: " + ", ".join(changed_fields)
            )
        if role == "corrective":
            reasons.append("historical correction matches the current reasoning basis")
        features = {
            "shared_root_count": len(basis["shared_roots"]),
            "shared_root_idf": round(shared_root_idf, 6),
            "correlation_reason_count": len(basis["correlation_reasons"]),
            "same_subject": same_subject,
            "shared_dependency_count": len(basis["shared_dependencies"]),
            "same_warrant_count": len(basis["shared_warrants"]),
            "shared_assumption_count": len(basis["shared_assumptions"]),
            "shared_measurement_pattern_count": len(
                basis["shared_measurement_patterns"]
            ),
            "recently_surfaced": recently_surfaced,
            "material_changed": material_changed,
            "material_change_fields": changed_fields,
            "content_coverage": coverage.get("content_coverage", {}).get(
                "estimate", 0.0
            ),
            "salience": coverage.get("salience", {}).get("estimate", 0.0),
            "structural_coverage": coverage.get(
                "structural_coverage", {}
            ).get("estimate", 0.0),
            "self_redundancy": round(self_redundancy, 6),
            "current_state_redundancy": round(current_redundancy, 6),
            "redundant_with_entity_id": redundant_with,
            "contextual_negative_weight": dismissal_weight,
            "corrective_basis_signal_count": correction_basis_count,
            "score_components": {
                key: round(value, 6)
                for key, value in score_components.items()
            },
        }
        candidates.append(
            {
                "entity_id": entity_id,
                "entity_type": entity["entity_type"],
                "role": role,
                "score": round(score, 6),
                "features": features,
                "reasons": reasons,
                "shared_roots": basis["shared_roots"],
                "family_key": _family_key(entity, roots, new_state),
                "entity": entity,
                "correction": correction,
                "current_material_state": current_material,
            }
        )
    candidates.sort(
        key=lambda item: (
            -item["score"],
            item["role"],
            item["entity_type"],
            item["entity_id"],
        )
    )
    candidates = candidates[: settings["max_candidates"]]

    selected: list[dict[str, Any]] = []
    remaining = list(candidates)
    while remaining and len(selected) < settings["max_capsules"]:
        family_selection = Counter(item["family_key"] for item in selected)
        eligible = [
            item
            for item in remaining
            if family_selection[item["family_key"]]
            < settings["max_family_members"]
        ]
        if not eligible:
            break

        def diversified_score(item: dict[str, Any]) -> tuple[float, str]:
            redundancy = 0.0
            for chosen in selected:
                if item["family_key"] == chosen["family_key"]:
                    redundancy = max(redundancy, 1.0)
                elif (
                    item["entity_type"] == chosen["entity_type"]
                    and item["role"] == chosen["role"]
                ):
                    redundancy = max(redundancy, 0.4)
                elif item["role"] == chosen["role"]:
                    redundancy = max(redundancy, 0.2)
            adjusted = item["score"] * (
                1 - settings["diversity_penalty"] * redundancy
            )
            return adjusted, item["entity_id"]

        winner = max(eligible, key=diversified_score)
        remaining.remove(winner)
        selected.append(winner)

    family_counts = Counter(item["family_key"] for item in candidates)
    capsules: list[dict[str, Any]] = []
    for rank, item in enumerate(selected, 1):
        entity = item["entity"]
        correction = item["correction"]
        annotation = canonical_annotation(entity, item["entity_type"])
        if item["role"] == "corrective":
            current_state = correction["badge"]
        else:
            current_state = _derived_state(entity)
        capsule = {
            "entity_id": item["entity_id"],
            "entity_type": item["entity_type"],
            "role": item["role"],
            "historical_noncurrent": item["role"] == "corrective",
            "annotation": annotation,
            "current_state": current_state,
            "why_relevant": item["reasons"],
            "why_relevant_now": item["reasons"],
            "relation_path": item["shared_roots"][
                : settings["max_relation_path"]
            ],
            "worker_state_coverage": new_state.get("worker_state", {}).get(
                item["entity_id"], {}
            ),
            "material_change": {
                "changed": item["features"]["material_changed"],
                "fields": item["features"]["material_change_fields"],
            },
            "historical_generation": entity.get("generation", 0),
            "reference": {
                "action": "inspect",
                "entity_id": item["entity_id"],
            },
            "rank": rank,
            "score": item["score"],
        }
        if entity.get("extended_annotation"):
            extended, extended_complete = bounded_text(
                str(entity["extended_annotation"]),
                settings["capsule_description_chars"],
            )
            capsule["extended_annotation"] = extended
            capsule["extended_annotation_complete"] = extended_complete
        if item["role"] == "corrective":
            capsule.update(
                {
                    "why_no_longer_current": correction[
                        "why_no_longer_current"
                    ],
                    "decisive_path": correction["decisive_path"][
                        : settings["max_relation_path"]
                    ],
                    "current_successor": correction["current_successor"],
                    "obsolete_content_guard": True,
                }
            )
        if family_counts[item["family_key"]] > 1:
            capsule["represents_family"] = {
                "family_key": item["family_key"],
                "related_candidate_count": family_counts[item["family_key"]]
                - 1,
            }
        capsules.append(capsule)

    telemetry_candidates = [
        {
            key: value
            for key, value in item.items()
            if key
            in {
                "entity_id",
                "entity_type",
                "role",
                "score",
                "features",
                "family_key",
            }
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
        "continuation": (
            None
            if len(candidates) <= settings["max_capsules"]
            else {"offset": len(capsules)}
        ),
    }


def relation_suggestions(
    old_state: dict[str, Any],
    new_state: dict[str, Any],
    operations: list[dict[str, Any]],
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
        for historical_id, historical in sorted(
            old_state.get("claims", {}).items()
        ):
            if historical_id == claim_id:
                continue
            historical_roots = set(
                historical.get("derived", {}).get("root_evidence", [])
            )
            shared = sorted(roots & historical_roots)
            same_subject = bool(
                _subject(claim) and _subject(claim) == _subject(historical)
            )
            similarity = SequenceMatcher(
                None,
                claim.get("proposition", ""),
                historical.get("proposition", ""),
            ).ratio()
            if not shared and not (same_subject and similarity >= 0.55):
                continue
            proposed = (
                "refines" if same_subject and similarity >= 0.55 else "related_to"
            )
            basis = [
                {"type": "evidence", "id": entity_id} for entity_id in shared
            ]
            if same_subject:
                basis.append(
                    {
                        "type": "feature",
                        "name": "same_subject",
                        "value": canonical_annotation(claim, "claim")["subject"],
                    }
                )
            body = {
                "source": {"type": "claim", "id": claim_id},
                "target": {"type": "claim", "id": historical_id},
                "relation": proposed,
                "basis": basis,
                "rationale": (
                    f"The claims share subject and evidence; text similarity is "
                    f"{similarity:.4f}."
                ),
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
