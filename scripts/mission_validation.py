"""Validation helpers for mission_harness.py state files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def require_fields(record: dict[str, Any], fields: list[str], label: str) -> list[str]:
    return [f"{label}: missing required field {field}" for field in fields if field not in record]


def load_json(path: Path) -> tuple[Any, list[str]]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), []
    except FileNotFoundError:
        return None, [f"missing JSON file: {path}"]
    except json.JSONDecodeError as exc:
        return None, [f"invalid JSON in {path}: {exc}"]


def validate_claims(path: Path, claim_statuses: list[str]) -> list[str]:
    data, errors = load_json(path)
    if errors:
        return errors
    if not isinstance(data, dict):
        return ["claims.json must be an object"]
    schema_version = data.get("schema_version")
    if schema_version not in {"claims.v1", "claims.v2"}:
        errors.append("claims.json schema_version must be claims.v1 or claims.v2")
    claims = data.get("claims")
    if not isinstance(claims, list):
        return errors + ["claims.json claims must be a list"]
    for index, record in enumerate(claims, start=1):
        label = f"claims.json:{index}"
        if not isinstance(record, dict):
            errors.append(f"{label}: record must be an object")
            continue
        errors.extend(
            require_fields(
                record,
                [
                    "id",
                    "claim",
                    "source_path",
                    "source_kind",
                    "status",
                    "verification_command",
                    "last_checked_at",
                    "confidence",
                    "notes",
                ],
                label,
            )
        )
        if schema_version == "claims.v2":
            errors.extend(
                require_fields(
                    record,
                    [
                        "revision_id",
                        "revision",
                        "timestamp",
                        "source_run_id",
                        "supersedes_revision_id",
                        "evidence_relation_ids",
                    ],
                    label,
                )
            )
        if record.get("status") not in claim_statuses:
            errors.append(f"{label}: invalid status {record.get('status')!r}")
    return errors


def validate_artifacts(path: Path) -> list[str]:
    data, errors = load_json(path)
    if errors:
        return errors
    if not isinstance(data, dict):
        return ["artifacts.json must be an object"]
    if data.get("schema_version") != "artifacts.v1":
        errors.append("artifacts.json schema_version must be artifacts.v1")
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list):
        return errors + ["artifacts.json artifacts must be a list"]
    for index, record in enumerate(artifacts, start=1):
        label = f"artifacts.json:{index}"
        if not isinstance(record, dict):
            errors.append(f"{label}: record must be an object")
            continue
        errors.extend(
            require_fields(
                record,
                ["id", "timestamp", "source_run_id", "path", "kind", "description", "verification_command", "notes"],
                label,
            )
        )
    return errors
