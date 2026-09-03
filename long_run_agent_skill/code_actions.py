"""Diagnostic code actions and opportunistic annotation maintenance."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .annotations import canonical_annotation
from .util import stable_id
from .worker_state import ENTITY_COLLECTIONS, operation_references


_EXPECTED = {
    "UNSUPPORTED_ASSERTION": [
        "the unsupported asserted commitment is withdrawn",
        "historical revisions and arguments remain available",
    ],
    "SUPPORT_LOST": [
        "a selected repair path is executed and recorded",
        "support and applicability are recomputed",
    ],
    "CLAIM_CONTESTED": [
        "the competing grounded paths are reviewed",
        "no semantic adjudication occurs without explicit input",
    ],
    "QUESTION_REOPENED": [
        "the derived question state remains reopened until its basis is restored",
    ],
    "DECISION_BASIS_CHANGED": [
        "the decision is reviewed, revised, or explicitly reaffirmed",
    ],
}


def _withdrawal_delta(diagnostic: dict[str, Any]) -> dict[str, Any] | None:
    if diagnostic.get("code") != "UNSUPPORTED_ASSERTION":
        return None
    claim_id = next(iter(diagnostic.get("entities", [])), "")
    if not claim_id:
        return None
    return {
        "operations": [
            {
                "type": "claim.withdrawn",
                "data": {
                    "id": claim_id,
                    "reason": "accepted code action for unsupported assertion",
                },
            }
        ]
    }


def normalize_diagnostics(
    diagnostics: list[dict[str, Any]], generation: int
) -> list[dict[str, Any]]:
    """Give every fix-it an inspectable code-action contract."""
    normalized: list[dict[str, Any]] = []
    for source in diagnostics:
        diagnostic = deepcopy(source)
        diagnostic.setdefault(
            "what_changed",
            {
                "old": diagnostic.get("old_state"),
                "new": diagnostic.get("new_state"),
            },
        )
        diagnostic.setdefault(
            "affected_entities", list(diagnostic.get("entities", []))
        )
        diagnostic.setdefault(
            "relevant_paths", list(diagnostic.get("relation_paths", []))
        )
        actions = []
        for index, raw in enumerate(diagnostic.get("fixits", [])):
            level = int(raw.get("level", 2))
            proposed = raw.get("proposed_EpistemicDelta")
            if proposed is None:
                proposed = _withdrawal_delta(diagnostic)
            action = {
                "schema_version": "epistemic-code-action.v1",
                "id": stable_id(
                    "code_action",
                    {
                        "diagnostic_id": diagnostic["id"],
                        "index": index,
                        "action": raw.get("action", ""),
                    },
                ),
                "level": level,
                "title": raw.get("title")
                or str(raw.get("action", "review diagnostic")).capitalize(),
                "action": raw.get("action", ""),
                "rationale": raw.get("rationale") or diagnostic.get("why", ""),
                "preconditions": raw.get("preconditions")
                or [
                    {
                        "type": "ledger_generation",
                        "equals": generation,
                    },
                    {
                        "type": "diagnostic_present",
                        "id": diagnostic["id"],
                    },
                ],
                "expected_consequences": raw.get("expected_consequences")
                or _EXPECTED.get(
                    diagnostic.get("code", ""),
                    ["the epistemic graph is recompiled after explicit acceptance"],
                ),
                "semantic_input_required": bool(
                    raw.get("semantic_input_required", level == 2)
                ),
                "proposed_EpistemicDelta": proposed,
                "authoritative": False,
            }
            for key, value in raw.items():
                if key not in action:
                    action[key] = value
            actions.append(action)
        diagnostic["fixits"] = actions
        normalized.append(diagnostic)
    return normalized


def _catalog(state: dict[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    result: dict[str, tuple[str, dict[str, Any]]] = {}
    for entity_type, collection in ENTITY_COLLECTIONS.items():
        for entity_id, entity in state.get(collection, {}).items():
            result[entity_id] = (entity_type, entity)
    return result


def annotation_revision_opportunities(
    state: dict[str, Any],
    operations: list[dict[str, Any]],
    recall_result: dict[str, Any],
) -> list[dict[str, Any]]:
    """Suggest label work only for state already active in the current operation."""
    referenced, structural = operation_references(operations)
    corrective_path_ids = {
        step.get("id", "")
        for capsule in recall_result.get("capsules", [])
        if capsule.get("role") == "corrective"
        for step in capsule.get("decisive_path", [])
        if isinstance(step, dict)
    }
    relevant = referenced | structural | corrective_path_ids
    revised = {
        item.get("data", {}).get("entity_id", "")
        for item in operations
        if item.get("type") in {"annotation.created", "annotation.revised"}
    }
    catalog = _catalog(state)
    opportunities = []
    for entity_id in sorted(relevant - revised - {""}):
        if entity_id not in catalog:
            continue
        entity_type, entity = catalog[entity_id]
        coverage = state.get("worker_state", {}).get(entity_id, {})
        content = coverage.get("content_coverage", {}).get("estimate", 0.0)
        salience = coverage.get("salience", {}).get("estimate", 0.0)
        is_structural = entity_id in structural
        is_corrective_path = entity_id in corrective_path_ids
        is_inspected = int(
            state.get("attention", {})
            .get(entity_id, {})
            .get("last_inspect_topology_surfaced_generation", -1)
        ) >= int(state.get("generation", 0)) - 1
        if not is_corrective_path and not is_inspected and not (
            content >= 0.8 and (is_structural or salience >= 0.8)
        ):
            continue
        field = "rationale" if entity_type == "relation" else "annotation"
        current = (
            entity.get("rationale", "")
            if entity_type == "relation"
            else canonical_annotation(entity, entity_type)
        )
        body = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "field": field,
            "current_value": current,
            "reasons": [
                reason
                for reason, present in (
                    ("content was recently expanded", content >= 0.8),
                    ("entity was recently inspected", is_inspected),
                    ("entity is active in new structural reasoning", is_structural),
                    ("corrective explanation traverses this relation", is_corrective_path),
                )
                if present
            ],
        }
        opportunities.append(
            {
                "schema_version": "annotation-revision-opportunity.v1",
                "id": stable_id("annotation_opportunity", body),
                **body,
                "message": "Label or rationale may be misleading; revise only if useful now.",
                "semantic_input_required": (
                    [field]
                    if field == "rationale"
                    else ["annotation.subject", "annotation.predicate"]
                ),
                "proposed_EpistemicDelta_template": {
                    "operations": [
                        {
                            "type": "annotation.revised",
                            "data": {
                                "entity_type": entity_type,
                                "entity_id": entity_id,
                                field: (
                                    "<replacement>"
                                    if field == "rationale"
                                    else {
                                        "subject": "<subject>",
                                        "predicate": "<predicate>",
                                        "scope": "<optional scope>",
                                    }
                                ),
                                "reason": "<why the presentation metadata changed>",
                            },
                        }
                    ]
                },
                "authoritative": False,
            }
        )
    return opportunities
