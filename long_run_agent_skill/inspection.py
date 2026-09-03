"""Compact normalized search and graph inspection representations."""

from __future__ import annotations

import re
from collections import deque
from typing import Any

from .annotations import canonical_annotation
from .errors import SemanticError
from .provenance import PROVENANCE_FIELDS, provenance_record, provenance_reference
from .worker_state import noncurrent_context


def entity_state(entity: dict[str, Any]) -> str:
    entity_type = entity.get("entity_type")
    if entity_type == "claim":
        derived = entity.get("derived", {})
        return ", ".join(
            value
            for value in (
                derived.get("support_state"),
                derived.get("applicability_state"),
                derived.get("verification_state"),
            )
            if value
        )
    if entity_type in {"argument", "attack"}:
        applicable = "applicable" if not entity.get("blockers") else "inapplicable"
        return f"{'active' if entity.get('active') else 'inactive'}, {applicable}"
    if entity_type == "question":
        return str(entity.get("derived_status", "opened"))
    if entity_type == "decision":
        return "basis_changed" if entity.get("basis_changed") else "current"
    if entity_type == "evidence":
        return "orphan" if entity.get("orphan") else "integrated"
    return str(entity.get("status", "current"))


def compact_node(
    entity_id: str,
    entity: dict[str, Any],
    state: dict[str, Any],
    *,
    include_extended: bool = False,
) -> tuple[dict[str, Any], tuple[str, dict[str, Any]] | None]:
    correction = noncurrent_context(entity_id, entity, state)
    node: dict[str, Any] = {
        "type": entity["entity_type"],
        "annotation": canonical_annotation(entity, entity["entity_type"]),
        "state": correction["badge"] if correction["noncurrent"] else entity_state(entity),
    }
    for field in ("intrinsic_name", "rationale"):
        if entity.get(field):
            node[field] = entity[field]
    if include_extended and entity.get("extended_annotation"):
        node["extended_annotation"] = entity["extended_annotation"]
    provenance = provenance_record(entity_id, entity, state)
    if provenance:
        reference = provenance_reference(provenance)
        node["provenance_ref"] = reference
        return node, (reference, provenance)
    return node, None


def _guard(entity_id: str, entity: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    correction = noncurrent_context(entity_id, entity, state)
    if not correction["noncurrent"]:
        return {"historical_noncurrent": False}
    return {
        "historical_noncurrent": True,
        "current_state": correction["badge"],
        "why_no_longer_current": correction["why_no_longer_current"],
        "decisive_path": correction["decisive_path"],
        "current_successor": correction["current_successor"],
        "obsolete_content_guard": True,
    }


def _relation_details(state: dict[str, Any]) -> list[dict[str, Any]]:
    explicit = {
        (
            item.get("source", {}).get("id", ""),
            item.get("relation", ""),
            item.get("target", {}).get("id", ""),
        ): item
        for item in state.get("relations", {}).values()
    }
    relations: list[dict[str, Any]] = []
    for edge in state.get("edges", []):
        relation = dict(edge)
        semantic = explicit.get((edge["source"], edge["relation"], edge["target"]))
        if semantic:
            if semantic.get("basis"):
                relation["basis"] = semantic["basis"]
            if semantic.get("rationale"):
                relation["rationale"] = semantic["rationale"]
            relation["relation_entity_id"] = semantic["id"]
        relations.append(relation)
    return relations


def _support_graph(
    target: str, state: dict[str, Any], facets: set[str]
) -> tuple[set[str], dict[str, list[list[str]]]]:
    nodes = {target}
    paths: dict[str, list[list[str]]] = {"support": [], "condition": [], "defeater": []}
    arguments = {
        argument_id: argument
        for argument_id, argument in state.get("arguments", {}).items()
        if (
            argument.get("conclusion", {}).get("id")
            or argument.get("conclusion", {}).get("claim_id")
        )
        == target
    }
    if "why" in facets or "assumptions" in facets or "defeaters" in facets:
        for argument_id, argument in sorted(arguments.items()):
            if "why" in facets and not argument.get("active"):
                continue
            nodes.add(argument_id)
            for premise in argument.get("premises", []):
                premise_id = premise.get("id", "")
                if premise_id:
                    nodes.add(premise_id)
                    paths["support"].append([premise_id, argument_id, target])
            for item in argument.get("assumptions", []):
                item_id = item.get("assumption_id", "")
                if item_id:
                    nodes.add(item_id)
                    paths["condition"].append([item_id, argument_id])
            for item in argument.get("dependencies", []):
                item_id = item.get("dependency_id", "")
                if item_id:
                    nodes.add(item_id)
                    paths["condition"].append([item_id, argument_id])
    if "why-not" in facets:
        for argument_id, argument in sorted(arguments.items()):
            if argument.get("active"):
                continue
            nodes.add(argument_id)
            for premise in argument.get("premises", []):
                if premise.get("id"):
                    nodes.add(premise["id"])
                    paths["support"].append([premise["id"], argument_id, target])
    if "defeaters" in facets:
        targets = set(arguments) | {target}
        for attack_id, attack in sorted(state.get("attacks", {}).items()):
            attacked = attack.get("target", {}).get("id", "")
            if attacked not in targets:
                continue
            nodes.add(attack_id)
            nodes.add(attacked)
            paths["defeater"].append([attack_id, attacked])
            for ground in attack.get("grounds", []):
                if ground.get("id"):
                    nodes.add(ground["id"])
                    paths["defeater"].append([ground["id"], attack_id, attacked])
    return nodes, {key: value for key, value in paths.items() if value}


def _walk_impact(target: str, state: dict[str, Any], depth: int) -> set[str]:
    seen = {target}
    frontier = deque([(target, 0)])
    while frontier:
        current, current_depth = frontier.popleft()
        if current_depth >= depth:
            continue
        for edge in state.get("edges", []):
            if edge["source"] != current or edge["target"] in seen:
                continue
            seen.add(edge["target"])
            frontier.append((edge["target"], current_depth + 1))
    return seen


def inspect_graph(
    state: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
    operations: list[dict[str, Any]],
    *,
    entity_id: str = "",
    facets: list[str] | None = None,
    since_generation: int | None = None,
    max_depth: int = 3,
) -> dict[str, Any]:
    requested = set(facets or [])
    valid = {
        "why", "why-not", "defeaters", "assumptions", "history", "impact",
        "unknowns", "revalidation", "changes-since", "telemetry", "worker-state",
    }
    invalid = requested - valid
    if invalid:
        raise SemanticError(f"unsupported inspect facets: {', '.join(sorted(invalid))}")
    if entity_id and entity_id not in catalog:
        raise SemanticError(f"unknown entity: {entity_id}")
    if not entity_id and not requested:
        raise SemanticError("inspect requires an entity id or a global facet")

    node_ids: set[str] = set()
    paths: dict[str, list[list[str]]] = {}
    groups: dict[str, Any] = {}
    history: list[dict[str, Any]] = []
    if entity_id:
        node_ids.add(entity_id)
        entity = catalog[entity_id]
        if entity["entity_type"] == "relation":
            node_ids.update(
                value.get("id", "")
                for value in (entity.get("source", {}), entity.get("target", {}))
            )
        graph_facets = requested or {"immediate"}
        if "immediate" in graph_facets:
            for edge in state.get("edges", []):
                if entity_id in {edge["source"], edge["target"]}:
                    node_ids.update((edge["source"], edge["target"]))
        support_nodes, support_paths = _support_graph(entity_id, state, requested)
        node_ids.update(support_nodes)
        paths.update(support_paths)
        if "impact" in requested:
            node_ids.update(_walk_impact(entity_id, state, max_depth))
        if "history" in requested:
            history = [
                {
                    "generation": item.get("_generation", 0),
                    "transaction_id": item.get("_transaction_id", ""),
                    "type": item.get("type", ""),
                }
                for item in operations
                if entity_id in _operation_ids(item)
            ]
    if "unknowns" in requested:
        unknowns: list[dict[str, str]] = []
        for item_id, item in sorted(state.get("questions", {}).items()):
            if item.get("derived_status") != "resolved":
                node_ids.add(item_id)
                unknowns.append({"kind": "open_question", "entity_id": item_id})
        for item_id, item in sorted(state.get("claims", {}).items()):
            derived = item.get("derived", {})
            if derived.get("support_state") in {"unsupported", "contested"}:
                node_ids.add(item_id)
                unknowns.append({"kind": "unsupported_claim", "entity_id": item_id})
            if derived.get("verification_state") != "verified":
                node_ids.add(item_id)
                unknowns.append({"kind": "verification_gap", "entity_id": item_id})
        groups["unknowns"] = unknowns
    if "revalidation" in requested:
        revalidation = []
        for item_id, item in sorted(state.get("claims", {}).items()):
            derived = item.get("derived", {})
            if derived.get("applicability_state") == "revalidation_required":
                node_ids.add(item_id)
                revalidation.append(
                    {"entity_id": item_id, "paths": derived.get("revalidation_plan", [])}
                )
        groups["revalidation"] = revalidation
    if "changes-since" in requested:
        boundary = int(since_generation or 0)
        groups["changes_since"] = [
            {
                "generation": item.get("_generation", 0),
                "transaction_id": item.get("_transaction_id", ""),
                "type": item.get("type", ""),
                "entity_ids": sorted(_operation_ids(item)),
            }
            for item in operations
            if item.get("_generation", 0) > boundary
        ]
        node_ids.update(
            entity
            for item in groups["changes_since"]
            for entity in item["entity_ids"]
            if entity in catalog
        )
    if "worker-state" in requested:
        groups["worker_state"] = {
            item_id: value
            for item_id, value in sorted(state.get("worker_state", {}).items())
            if not entity_id or item_id == entity_id
        }

    node_ids.discard("")
    nodes: dict[str, Any] = {}
    provenance: dict[str, Any] = {}
    for item_id in sorted(node_ids, key=lambda value: (value != entity_id, value)):
        if item_id not in catalog:
            continue
        node, interned = compact_node(
            item_id, catalog[item_id], state, include_extended=item_id == entity_id
        )
        nodes[item_id] = node
        if interned:
            provenance[interned[0]] = interned[1]
    selected = set(nodes)
    relations = [
        item
        for item in _relation_details(state)
        if item["source"] in selected and item["target"] in selected
    ]
    diagnostics = [
        item
        for item in state.get("diagnostics", [])
        if selected.intersection(item.get("affected_entities", item.get("entities", [])))
    ]
    result: dict[str, Any] = {
        "nodes": nodes,
        "relations": relations,
        "paths": paths,
        "diagnostics": diagnostics,
    }
    if provenance:
        result["provenance"] = provenance
    if groups:
        result["groups"] = groups
    if history:
        result["history"] = history
    if entity_id:
        result["presentation_guard"] = _guard(entity_id, catalog[entity_id], state)
    return result


def _operation_ids(operation: dict[str, Any]) -> set[str]:
    data = operation.get("data", {})
    values = {str(data.get("id", "")), str(data.get("entity_id", ""))}
    for key in ("premises", "grounds", "inputs", "basis"):
        values.update(
            str(item.get("id", ""))
            for item in data.get(key, [])
            if isinstance(item, dict)
        )
    for key in ("source", "target", "artifact_ref", "conclusion"):
        value = data.get(key)
        if isinstance(value, dict):
            values.add(str(value.get("id") or value.get("claim_id") or ""))
    for key in (
        "basis_claim_ids", "evidence_ids", "candidate_claim_ids",
        "related_claim_ids", "resolution_basis_claim_ids",
    ):
        values.update(map(str, data.get(key, [])))
    return values - {""}


def _tokens(value: Any) -> set[str]:
    return set(re.findall(r"[a-z0-9_.:-]+", str(value).lower()))


def structured_search(
    state: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
    text: str,
    *,
    entity_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    terms = _tokens(text)
    if not terms:
        raise SemanticError("search text is required")
    allowed = set(entity_types or [])
    results: list[dict[str, Any]] = []
    for entity_id, entity in sorted(catalog.items()):
        if allowed and entity["entity_type"] not in allowed:
            continue
        annotation = canonical_annotation(entity, entity["entity_type"])
        fields: dict[str, Any] = {
            "annotation.subject": annotation["subject"],
            "annotation.predicate": annotation["predicate"],
            "annotation.scope": annotation.get("scope", ""),
            "intrinsic_name": entity.get("intrinsic_name", ""),
            "extended_annotation": entity.get("extended_annotation", ""),
            "entity_type": entity["entity_type"],
            "current_state": entity_state(entity),
        }
        provenance = provenance_record(entity_id, entity, state)
        fields.update(
            {f"provenance.{key}": value for key, value in provenance.items()}
        )
        related = [
            edge["relation"]
            for edge in state.get("edges", [])
            if entity_id in {edge["source"], edge["target"]}
        ]
        fields["relationships"] = related
        weights = {
            "annotation.subject": 6,
            "annotation.predicate": 5,
            "annotation.scope": 4,
            "intrinsic_name": 5,
            "extended_annotation": 3,
            "entity_type": 2,
            "current_state": 2,
            "relationships": 2,
        }
        matched: list[str] = []
        score = 0
        for field, value in fields.items():
            field_tokens = _tokens(value)
            overlap = terms & field_tokens
            if overlap:
                matched.append(field)
                score += len(overlap) * weights.get(field, 2)
        if not score:
            continue
        correction = noncurrent_context(entity_id, entity, state)
        result: dict[str, Any] = {
            "entity_id": entity_id,
            "entity_type": entity["entity_type"],
            "annotation": annotation,
            "state": correction["badge"] if correction["noncurrent"] else entity_state(entity),
            "match": {"fields": sorted(matched), "score": score},
            "historical_noncurrent": correction["noncurrent"],
            "presentation_status": correction["badge"] or "CURRENT",
        }
        if correction["noncurrent"]:
            result.update(
                {
                    "why_no_longer_current": correction["why_no_longer_current"],
                    "decisive_path": correction["decisive_path"],
                    "current_successor": correction["current_successor"],
                    "obsolete_content_guard": True,
                }
            )
        results.append(result)
    return sorted(
        results,
        key=lambda item: (-item["match"]["score"], item["entity_type"], item["entity_id"]),
    )
