"""Structured provenance lineage shared by search, inspect, and views."""

from __future__ import annotations

import re
from typing import Any

from .errors import SemanticError
from .util import stable_id

PROVENANCE_FIELDS = (
    "source",
    "producer",
    "host",
    "host_generation",
    "process",
    "measurement_process",
    "verifier",
    "verifier_family",
    "artifact_content_hash",
)


def normalize_provenance_refs(data: dict[str, Any]) -> list[dict[str, str]]:
    refs = list(data.get("provenance_refs", []))
    artifact_ref = data.get("artifact_ref")
    if isinstance(artifact_ref, dict) and artifact_ref.get("id"):
        refs.append({"type": "artifact", "id": artifact_ref["id"]})
    if data.get("artifact_id"):
        refs.append({"type": "artifact", "id": data["artifact_id"]})
    run_id = data.get("source_run") or data.get("run")
    if run_id:
        refs.append({"type": "run", "id": run_id})
    if data.get("tool_event_id"):
        refs.append({"type": "tool_event", "id": data["tool_event_id"]})
    normalized: dict[tuple[str, str], dict[str, str]] = {}
    for reference in refs:
        if not isinstance(reference, dict):
            raise SemanticError("provenance references must be objects")
        reference_type = reference.get("type")
        reference_id = reference.get("id")
        if not isinstance(reference_type, str) or not re.fullmatch(
            r"[a-z][a-z0-9_.-]*", reference_type
        ):
            raise SemanticError("provenance reference type is invalid")
        if not isinstance(reference_id, str) or not reference_id.strip():
            raise SemanticError("provenance reference id is required")
        normalized[(reference_type, reference_id.strip())] = {
            "type": reference_type,
            "id": reference_id.strip(),
        }
    return [normalized[key] for key in sorted(normalized)]


def evidence_roots(
    evidence_id: str, evidence: dict[str, dict[str, Any]], visiting: set[str] | None = None
) -> set[str]:
    visiting = set() if visiting is None else visiting
    if evidence_id in visiting:
        return {evidence_id}
    item = evidence.get(evidence_id, {})
    parents = item.get("derivation_parents", [])
    if not parents:
        return {evidence_id}
    roots: set[str] = set()
    for parent in parents:
        parent_id = parent.get("id") if isinstance(parent, dict) else parent
        if parent_id in evidence:
            roots.update(evidence_roots(parent_id, evidence, visiting | {evidence_id}))
    return roots or {evidence_id}


def provenance_record(
    entity_id: str, entity: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    features = {
        field: entity[field]
        for field in PROVENANCE_FIELDS
        if entity.get(field) not in (None, "", [], {})
    }
    refs = normalize_provenance_refs(entity)
    lineage: dict[str, Any] = {}
    if entity.get("entity_type") == "evidence":
        parents = [
            parent.get("id") if isinstance(parent, dict) else parent
            for parent in entity.get("derivation_parents", [])
        ]
        if parents:
            lineage["derivation_parents"] = sorted(
                str(parent) for parent in parents if parent
            )
        roots = sorted(evidence_roots(entity_id, state.get("evidence", {})))
        if roots != [entity_id]:
            lineage["derivation_roots"] = roots
    record: dict[str, Any] = {}
    if refs:
        record["refs"] = refs
    if features:
        record["features"] = features
    if lineage:
        record["lineage"] = lineage
    return record


def provenance_reference(record: dict[str, Any]) -> str:
    return stable_id("provenance", record) if record else ""
