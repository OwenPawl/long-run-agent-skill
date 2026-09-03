"""Canonical structured annotations for durable epistemic entities."""

from __future__ import annotations

from typing import Any

from .errors import SemanticError


def validate_annotation(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise SemanticError("annotation must be an object")
    extra = set(value) - {"subject", "predicate", "scope"}
    if extra:
        raise SemanticError(f"unsupported annotation fields: {', '.join(sorted(extra))}")
    result: dict[str, str] = {}
    for field in ("subject", "predicate"):
        text = value.get(field)
        if not isinstance(text, str) or not text.strip():
            raise SemanticError(f"annotation.{field} is required")
        result[field] = text.strip()
    scope = value.get("scope")
    if scope is not None:
        if not isinstance(scope, str):
            raise SemanticError("annotation.scope must be a string")
        if scope.strip():
            result["scope"] = scope.strip()
    return result


def render_annotation(value: dict[str, Any]) -> str:
    annotation = validate_annotation(value)
    return " | ".join(
        annotation[field]
        for field in ("subject", "predicate", "scope")
        if field in annotation
    )


def _reference_id(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return str(value.get("id") or value.get("claim_id") or "")


def _legacy_subject(data: dict[str, Any], entity_type: str) -> str:
    direct = data.get("subject") or data.get("intrinsic_name") or data.get("name")
    if direct:
        return str(direct)
    if entity_type == "relation":
        return _reference_id(data.get("source"))
    if entity_type == "attack":
        return f"Attack on {_reference_id(data.get('target'))}".strip()
    if entity_type == "argument":
        return f"Argument {_reference_id(data.get('conclusion'))}".strip()
    if entity_type == "verification":
        return str(data.get("verifier") or data.get("producer") or data.get("id", ""))
    return str(data.get("id", ""))


def _legacy_predicate(data: dict[str, Any], entity_type: str) -> str:
    for key in (
        "predicate",
        "proposition",
        "question",
        "choice",
        "description",
        "statement",
        "relation",
    ):
        if data.get(key):
            return str(data[key])
    if entity_type == "argument":
        conclusion = _reference_id(data.get("conclusion"))
        return f"warrants {conclusion}" if conclusion else "records an epistemic argument"
    if entity_type == "attack":
        return f"{data.get('attack_type', 'attacks')} its target"
    defaults = {
        "artifact": "records an artifact identity",
        "evidence": "records an evidence observation",
        "dependency": "records a dependency state",
        "verification": "records a verification result",
        "warrant": "records an inference warrant",
    }
    return defaults.get(entity_type, f"records a {entity_type}")


def canonical_annotation(data: dict[str, Any], entity_type: str) -> dict[str, str]:
    """Return the canonical annotation, with deterministic replay fallback.

    New structured annotations are validated strictly. The legacy fallback keeps
    previously committed ledgers rebuildable without making old names part of
    the new public contract.
    """

    if "annotation" in data:
        return validate_annotation(data["annotation"])
    annotation = {
        "subject": _legacy_subject(data, entity_type),
        "predicate": _legacy_predicate(data, entity_type),
    }
    scope = data.get("scope")
    if isinstance(scope, str) and scope.strip():
        annotation["scope"] = scope.strip()
    return validate_annotation(annotation)


def revise_annotation(
    current: dict[str, Any], revision: dict[str, Any]
) -> dict[str, str]:
    if "annotation" not in revision:
        return validate_annotation(current)
    update = revision["annotation"]
    if not isinstance(update, dict):
        raise SemanticError("annotation revision must be an object")
    return validate_annotation({**current, **update})
