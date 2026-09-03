"""Deterministic full entity views beneath the implicit mission view root."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

from .annotations import canonical_annotation
from .inspection import entity_state
from .provenance import PROVENANCE_FIELDS, provenance_record
from .util import atomic_write_json, stable_id


def _filename(entity_id: str) -> str:
    encoded = quote(entity_id, safe="-_.~")
    if encoded in {"", ".", ".."} or len(encoded.encode("utf-8")) > 180:
        encoded = stable_id("entity_view", entity_id)
    return f"{encoded}.json"


def resolve_entity_view(root: str | Path, entity_id: str) -> Path:
    views = Path(root).expanduser().resolve() / ".agent" / "views" / "entities"
    candidate = views / _filename(entity_id)
    if candidate.parent.resolve() != views.resolve():
        raise ValueError("materialized entity view escaped the mission view root")
    return candidate


def _operation_ids(operation: dict[str, Any]) -> set[str]:
    data = operation.get("data", {})
    identifiers = {str(data.get("id", "")), str(data.get("entity_id", ""))}
    for key in ("premises", "grounds", "inputs", "basis"):
        identifiers.update(
            str(item.get("id", ""))
            for item in data.get(key, [])
            if isinstance(item, dict)
        )
    for key in ("source", "target", "artifact_ref", "conclusion"):
        value = data.get(key)
        if isinstance(value, dict):
            identifiers.add(str(value.get("id") or value.get("claim_id") or ""))
    return identifiers - {""}


def materialize_entity_views(
    store: Any, state: dict[str, Any], catalog: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    entities_root = store.paths.views / "entities"
    entities_root.mkdir(parents=True, exist_ok=True)
    operations = store.operations()
    expected: set[Path] = set()
    for entity_id, entity in sorted(catalog.items()):
        path = resolve_entity_view(store.paths.root, entity_id)
        expected.add(path)
        annotation = canonical_annotation(entity, entity["entity_type"])
        presentation = {
            key: entity[key]
            for key in ("intrinsic_name", "extended_annotation", "rationale")
            if entity.get(key)
        }
        full_payload = {
            key: value
            for key, value in entity.items()
            if key
            not in set(PROVENANCE_FIELDS)
            | {
                "id", "entity_type", "annotation", "intrinsic_name",
                "extended_annotation", "rationale", "derived", "provenance_refs",
                "artifact_ref", "artifact_id", "source_run", "run",
                "tool_event_id", "derivation_parents",
            }
        }
        incident = [
            edge
            for edge in state.get("edges", [])
            if entity_id in {edge["source"], edge["target"]}
        ]
        history = [
            item for item in operations if entity_id in _operation_ids(item)
        ]
        diagnostics = [
            item
            for item in state.get("diagnostics", [])
            if entity_id
            in item.get("affected_entities", item.get("entities", []))
        ]
        payload = {
            "schema_version": "epistemic-entity-view.v1",
            "generation": state.get("generation", 0),
            "entity_id": entity_id,
            "entity_type": entity["entity_type"],
            "annotation": annotation,
            "state": entity_state(entity),
            **presentation,
            "full_payload": full_payload,
            "derived_state": entity.get("derived", {}),
            "provenance": provenance_record(entity_id, entity, state),
            "relations": incident,
            "diagnostics": diagnostics,
            "history": history,
        }
        atomic_write_json(path, payload)
    for stale in entities_root.glob("*.json"):
        if stale not in expected:
            stale.unlink()
    index = {
        "schema_version": "epistemic-view-index.v1",
        "generation": state.get("generation", 0),
        "resolution": "entity_id",
        "entities": {
            entity_id: _filename(entity_id) for entity_id in sorted(catalog)
        },
    }
    atomic_write_json(store.paths.views / "index.json", index)
    return {"entity_count": len(catalog), "resolution": "entity_id"}
