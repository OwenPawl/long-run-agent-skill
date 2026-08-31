"""Append-only claim revisions and typed evidence relationships."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import uuid
from typing import Any

from mission_harness_lock import atomic_write_text, file_lock


CLAIM_STATUSES = [
    "proposed",
    "claimed",
    "implemented",
    "tested",
    "documented",
    "disproved",
    "broken",
    "stale",
    "removed",
]

ENDPOINT_TYPES = [
    "claim",
    "claim_revision",
    "artifact",
    "run",
    "friction",
    "relation",
]

RELATION_TYPES = [
    "supports",
    "refutes",
    "corroborates",
    "contradicts",
    "derived_from",
    "reproduces",
    "documents",
    "caused_by",
    "supersedes",
    "retracts",
]


class EvidenceError(RuntimeError):
    """Raised for user-facing evidence state failures."""


def utc_now() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def short_id(prefix: str) -> str:
    stamp = dt.datetime.now(tz=dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}_{stamp}_{uuid.uuid4().hex[:8]}"


def agent_dir(root: pathlib.Path) -> pathlib.Path:
    return root.expanduser().resolve() / ".agent"


def default_claims() -> dict[str, Any]:
    return {"schema_version": "claims.v2", "claims": []}


def load_json(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EvidenceError(f"missing JSON file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise EvidenceError(f"invalid JSON in {path}: {exc}") from exc


def read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvidenceError(f"invalid JSONL in {path}:{lineno}: {exc}") from exc
        if not isinstance(record, dict):
            raise EvidenceError(f"invalid JSONL in {path}:{lineno}: record must be an object")
        records.append(record)
    return records


def write_json(path: pathlib.Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def append_jsonl_records(path: pathlib.Path, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(record, sort_keys=True) + "\n" for record in records)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _legacy_revision_id(claim_id: str, revision: int) -> str:
    return f"{claim_id}:r{revision}"


def upgraded_claims(data: Any) -> tuple[dict[str, Any], bool]:
    """Return claims.v2 while preserving every legacy claim as revision one."""
    if not isinstance(data, dict):
        raise EvidenceError("claims.json must be an object")
    schema_version = data.get("schema_version")
    claims = data.get("claims")
    if not isinstance(claims, list) or any(not isinstance(record, dict) for record in claims):
        raise EvidenceError("claims.json claims must be a list of objects")
    if schema_version == "claims.v2":
        return data, False
    if schema_version != "claims.v1":
        raise EvidenceError(f"unsupported claims.json schema_version: {schema_version!r}")

    revisions_by_claim: dict[str, list[dict[str, Any]]] = {}
    upgraded: list[dict[str, Any]] = []
    for legacy in claims:
        raw_claim_id = legacy.get("id")
        claim_id = raw_claim_id if isinstance(raw_claim_id, str) else ""
        prior = revisions_by_claim.setdefault(claim_id, [])
        revision = len(prior) + 1
        revision_id = _legacy_revision_id(claim_id, revision)
        record = {
            **legacy,
            "revision_id": revision_id,
            "revision": revision,
            "timestamp": str(legacy.get("last_checked_at", "")),
            "source_run_id": "",
            "supersedes_revision_id": prior[-1]["revision_id"] if prior else "",
            "evidence_relation_ids": [],
            "legacy_revision": True,
        }
        prior.append(record)
        upgraded.append(record)
    return {"schema_version": "claims.v2", "claims": upgraded}, True


def claim_revisions(data: Any, claim_id: str) -> list[dict[str, Any]]:
    upgraded, _ = upgraded_claims(data)
    return [record for record in upgraded["claims"] if record.get("id") == claim_id]


def latest_claims(data: Any) -> dict[str, dict[str, Any]]:
    upgraded, _ = upgraded_claims(data)
    latest: dict[str, dict[str, Any]] = {}
    for record in upgraded["claims"]:
        latest[str(record.get("id", ""))] = record
    return latest


def _new_claim_revision(args: argparse.Namespace, prior: dict[str, Any] | None) -> dict[str, Any]:
    claim_id = args.id or short_id("claim")
    revision = int(prior.get("revision", 0)) + 1 if prior else 1
    timestamp = utc_now()
    return {
        "id": claim_id,
        "revision_id": _legacy_revision_id(claim_id, revision),
        "revision": revision,
        "timestamp": timestamp,
        "source_run_id": args.run_id or "",
        "supersedes_revision_id": str(prior.get("revision_id", "")) if prior else "",
        "claim": args.claim,
        "source_path": args.source_path,
        "source_kind": args.source_kind,
        "status": args.status,
        "verification_command": args.verification_command or "",
        "last_checked_at": args.last_checked_at or "",
        "confidence": args.confidence,
        "notes": args.notes or "",
        "evidence_relation_ids": [],
    }


def _require_run_id(root: pathlib.Path, run_id: str) -> None:
    if not run_id:
        return
    runs = read_jsonl(agent_dir(root) / "runs.jsonl")
    if run_id not in {str(record.get("run_id", "")) for record in runs}:
        raise EvidenceError(f"unknown source run id: {run_id}")


def cmd_claim_add(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.root)
    path = agent_dir(root) / "claims.json"
    if args.status == "disproved":
        raise EvidenceError("use 'claim disprove' so refuting evidence is linked to the revision")
    _require_run_id(root, args.run_id or "")
    with file_lock(path):
        data, migrated = upgraded_claims(load_json(path))
        prior = latest_claims(data).get(args.id) if args.id else None
        record = _new_claim_revision(args, prior)
        data["claims"].append(record)
        write_json(path, data)
    print(
        json.dumps(
            {
                "id": record["id"],
                "revision_id": record["revision_id"],
                "revision": record["revision"],
                "appended": True,
                "migrated_from_claims_v1": migrated,
            },
            sort_keys=True,
        )
    )
    return 0


def _endpoint(kind: str, record_id: str) -> dict[str, str]:
    return {"type": kind, "id": record_id}


def _relation_record(
    *,
    relation_id: str,
    source_type: str,
    source_id: str,
    relation: str,
    target_type: str,
    target_id: str,
    source_run_id: str,
    confidence: str,
    notes: str,
) -> dict[str, Any]:
    return {
        "schema_version": "evidence_relation.v1",
        "id": relation_id,
        "timestamp": utc_now(),
        "source": _endpoint(source_type, source_id),
        "relation": relation,
        "target": _endpoint(target_type, target_id),
        "source_run_id": source_run_id,
        "confidence": confidence,
        "notes": notes,
    }


def _equivalent_relation(records: list[dict[str, Any]], candidate: dict[str, Any]) -> dict[str, Any] | None:
    keys = ("source", "relation", "target", "source_run_id", "confidence", "notes")
    for record in reversed(records):
        if all(record.get(key) == candidate.get(key) for key in keys):
            return record
    return None


def _record_ids(root: pathlib.Path, relations: list[dict[str, Any]]) -> dict[str, list[str]]:
    directory = agent_dir(root)
    claims, _ = upgraded_claims(load_json(directory / "claims.json"))
    artifact_data = load_json(directory / "artifacts.json")
    if not isinstance(artifact_data, dict) or not isinstance(artifact_data.get("artifacts"), list):
        raise EvidenceError("artifacts.json artifacts must be a list")
    artifacts = artifact_data["artifacts"]
    runs = read_jsonl(directory / "runs.jsonl")
    friction = read_jsonl(directory / "friction.jsonl")
    return {
        "claim": [str(record.get("id", "")) for record in claims["claims"]],
        "claim_revision": [str(record.get("revision_id", "")) for record in claims["claims"]],
        "artifact": [str(record.get("id", "")) for record in artifacts if isinstance(record, dict)],
        "run": [str(record.get("run_id", "")) for record in runs],
        "friction": [str(record.get("id", "")) for record in friction],
        "relation": [str(record.get("id", "")) for record in relations],
    }


def _require_endpoint(root: pathlib.Path, endpoint: dict[str, str], relations: list[dict[str, Any]]) -> None:
    kind = endpoint["type"]
    record_id = endpoint["id"]
    ids = _record_ids(root, relations).get(kind, [])
    if record_id not in ids:
        raise EvidenceError(f"unknown {kind} endpoint: {record_id}")
    if kind == "artifact" and ids.count(record_id) > 1:
        raise EvidenceError(f"ambiguous artifact endpoint has duplicate id: {record_id}")


def cmd_relation_add(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.root)
    path = agent_dir(root) / "evidence_relations.jsonl"
    _require_run_id(root, args.run_id or "")
    with file_lock(path):
        records = read_jsonl(path)
        relation_id = args.id or short_id("relation")
        candidate = _relation_record(
            relation_id=relation_id,
            source_type=args.source_type,
            source_id=args.source_id,
            relation=args.relation,
            target_type=args.target_type,
            target_id=args.target_id,
            source_run_id=args.run_id or "",
            confidence=args.confidence,
            notes=args.notes or "",
        )
        existing_id = next((record for record in records if record.get("id") == relation_id), None)
        if existing_id:
            if _equivalent_relation([existing_id], candidate):
                print(json.dumps({"id": relation_id, "appended": False, "existing": True}, sort_keys=True))
                return 0
            raise EvidenceError(f"evidence relation id already exists: {relation_id}")
        equivalent = _equivalent_relation(records, candidate) if not args.id else None
        if equivalent:
            print(json.dumps({"id": equivalent["id"], "appended": False, "existing": True}, sort_keys=True))
            return 0
        _require_endpoint(root, candidate["source"], records)
        _require_endpoint(root, candidate["target"], records)
        if candidate["source"] == candidate["target"]:
            raise EvidenceError("evidence relation cannot point an endpoint to itself")
        append_jsonl_records(path, [candidate])
    print(json.dumps({"id": relation_id, "appended": True, "existing": False}, sort_keys=True))
    return 0


def _relation_artifact_ids(records: list[dict[str, Any]], relation_ids: list[str]) -> set[str]:
    selected = {record.get("id"): record for record in records if record.get("id") in relation_ids}
    return {
        str(record.get("source", {}).get("id", ""))
        for record in selected.values()
        if record.get("relation") == "refutes" and record.get("source", {}).get("type") == "artifact"
    }


def cmd_claim_disprove(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.root)
    _require_run_id(root, args.run_id or "")
    directory = agent_dir(root)
    claims_path = directory / "claims.json"
    relations_path = directory / "evidence_relations.jsonl"
    requested_artifacts = list(dict.fromkeys(args.evidence_artifact))
    with file_lock(claims_path):
        data, migrated = upgraded_claims(load_json(claims_path))
        prior = latest_claims(data).get(args.id)
        if not prior:
            raise EvidenceError(f"unknown claim id: {args.id}")
        with file_lock(relations_path):
            relations = read_jsonl(relations_path)
            for artifact_id in requested_artifacts:
                _require_endpoint(root, _endpoint("artifact", artifact_id), relations)
            if prior.get("status") == "disproved":
                prior_evidence = _relation_artifact_ids(relations, prior.get("evidence_relation_ids", []))
                if prior_evidence == set(requested_artifacts):
                    print(
                        json.dumps(
                            {
                                "id": args.id,
                                "revision_id": prior["revision_id"],
                                "revision": prior["revision"],
                                "relation_ids": prior.get("evidence_relation_ids", []),
                                "appended": False,
                                "existing": True,
                            },
                            sort_keys=True,
                        )
                    )
                    return 0

            new_relations: list[dict[str, Any]] = []
            relation_ids: list[str] = []
            for artifact_id in requested_artifacts:
                candidate = _relation_record(
                    relation_id=short_id("relation"),
                    source_type="artifact",
                    source_id=artifact_id,
                    relation="refutes",
                    target_type="claim_revision",
                    target_id=str(prior["revision_id"]),
                    source_run_id=args.run_id or "",
                    confidence=args.confidence,
                    notes=args.notes or "",
                )
                existing = _equivalent_relation(relations + new_relations, candidate)
                if existing:
                    relation_ids.append(str(existing["id"]))
                else:
                    new_relations.append(candidate)
                    relation_ids.append(str(candidate["id"]))
            append_jsonl_records(relations_path, new_relations)

            revision = int(prior["revision"]) + 1
            timestamp = utc_now()
            record = {
                **prior,
                "revision_id": _legacy_revision_id(args.id, revision),
                "revision": revision,
                "timestamp": timestamp,
                "source_run_id": args.run_id or "",
                "supersedes_revision_id": prior["revision_id"],
                "status": "disproved",
                "verification_command": args.verification_command or prior.get("verification_command", ""),
                "last_checked_at": args.last_checked_at or timestamp,
                "confidence": args.confidence,
                "notes": args.notes or f"Disproved by artifacts: {', '.join(requested_artifacts)}",
                "evidence_relation_ids": relation_ids,
            }
            record.pop("legacy_revision", None)
            data["claims"].append(record)
            write_json(claims_path, data)
    print(
        json.dumps(
            {
                "id": args.id,
                "revision_id": record["revision_id"],
                "revision": record["revision"],
                "relation_ids": relation_ids,
                "appended": True,
                "migrated_from_claims_v1": migrated,
            },
            sort_keys=True,
        )
    )
    return 0


def cmd_claim_history(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.root)
    directory = agent_dir(root)
    revisions = claim_revisions(load_json(directory / "claims.json"), args.id)
    if not revisions:
        raise EvidenceError(f"unknown claim id: {args.id}")
    revision_ids = {str(record.get("revision_id", "")) for record in revisions}
    all_relations = read_jsonl(directory / "evidence_relations.jsonl")
    relation_ids = {
        relation_id for revision in revisions for relation_id in revision.get("evidence_relation_ids", [])
    }
    relations = [
        record
        for record in all_relations
        if (
            record.get("source") == _endpoint("claim", args.id)
            or record.get("target") == _endpoint("claim", args.id)
            or (
                record.get("source", {}).get("type") == "claim_revision"
                and record.get("source", {}).get("id") in revision_ids
            )
            or (
                record.get("target", {}).get("type") == "claim_revision"
                and record.get("target", {}).get("id") in revision_ids
            )
            or record.get("id") in relation_ids
        )
    ]
    selected_ids = {str(record.get("id", "")) for record in relations}
    while True:
        related = [
            record
            for record in all_relations
            if record not in relations
            and (
                record.get("source", {}).get("type") == "relation"
                and record.get("source", {}).get("id") in selected_ids
                or record.get("target", {}).get("type") == "relation"
                and record.get("target", {}).get("id") in selected_ids
            )
        ]
        if not related:
            break
        relations.extend(related)
        selected_ids.update(str(record.get("id", "")) for record in related)
    print(
        json.dumps(
            {
                "claim_id": args.id,
                "revision_count": len(revisions),
                "latest": revisions[-1],
                "revisions": revisions,
                "relations": relations,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def cmd_relation_list(args: argparse.Namespace) -> int:
    records = read_jsonl(agent_dir(pathlib.Path(args.root)) / "evidence_relations.jsonl")
    if bool(args.endpoint_type) != bool(args.endpoint_id):
        raise EvidenceError("--endpoint-type and --endpoint-id must be used together")
    selected = records
    if args.endpoint_type:
        endpoint = _endpoint(args.endpoint_type, args.endpoint_id)
        selected = [record for record in selected if record.get("source") == endpoint or record.get("target") == endpoint]
    if args.relation:
        selected = [record for record in selected if record.get("relation") == args.relation]
    print(json.dumps({"count": len(selected), "relations": selected}, indent=2, sort_keys=True))
    return 0


def _required_fields(record: dict[str, Any], fields: list[str], label: str) -> list[str]:
    return [f"{label}: missing required field {field}" for field in fields if field not in record]


def _endpoint_errors(
    endpoint: Any,
    label: str,
    ids_by_type: dict[str, list[str]],
) -> list[str]:
    errors: list[str] = []
    if not isinstance(endpoint, dict):
        return [f"{label}: endpoint must be an object"]
    errors.extend(_required_fields(endpoint, ["type", "id"], label))
    kind = endpoint.get("type")
    record_id = endpoint.get("id")
    if kind not in ENDPOINT_TYPES:
        errors.append(f"{label}: unknown endpoint type {kind!r}")
        return errors
    if not isinstance(record_id, str) or not record_id:
        errors.append(f"{label}: endpoint id must be a non-empty string")
        return errors
    candidates = ids_by_type.get(str(kind), [])
    if record_id not in candidates:
        errors.append(f"{label}: unknown {kind} endpoint {record_id!r}")
    elif kind == "artifact" and candidates.count(record_id) > 1:
        errors.append(f"{label}: ambiguous artifact endpoint has duplicate id {record_id!r}")
    return errors


def validate_evidence_state(root: pathlib.Path, claim_statuses: list[str]) -> list[str]:
    """Validate claim revision ordering, relation endpoints, and disproval evidence."""
    directory = agent_dir(root)
    errors: list[str] = []
    try:
        claims, _ = upgraded_claims(load_json(directory / "claims.json"))
        relations = read_jsonl(directory / "evidence_relations.jsonl")
        ids_by_type = _record_ids(root, relations)
    except EvidenceError as exc:
        return [str(exc)]

    revision_ids: set[str] = set()
    relation_by_id: dict[str, dict[str, Any]] = {}
    prior_relation_ids: set[str] = set()
    for index, relation in enumerate(relations, start=1):
        label = f"evidence_relations.jsonl:{index}"
        errors.extend(
            _required_fields(
                relation,
                [
                    "schema_version",
                    "id",
                    "timestamp",
                    "source",
                    "relation",
                    "target",
                    "source_run_id",
                    "confidence",
                    "notes",
                ],
                label,
            )
        )
        if relation.get("schema_version") != "evidence_relation.v1":
            errors.append(f"{label}: schema_version must be evidence_relation.v1")
        relation_id = relation.get("id")
        if not isinstance(relation_id, str) or not relation_id:
            errors.append(f"{label}: id must be a non-empty string")
        elif relation_id in relation_by_id:
            errors.append(f"{label}: duplicate relation id {relation_id!r}")
        else:
            relation_by_id[relation_id] = relation
        if not isinstance(relation.get("timestamp"), str) or not relation.get("timestamp"):
            errors.append(f"{label}: timestamp must be a non-empty string")
        if relation.get("relation") not in RELATION_TYPES:
            errors.append(f"{label}: unknown relation type {relation.get('relation')!r}")
        errors.extend(_endpoint_errors(relation.get("source"), f"{label}.source", ids_by_type))
        errors.extend(_endpoint_errors(relation.get("target"), f"{label}.target", ids_by_type))
        for endpoint_name in ("source", "target"):
            endpoint = relation.get(endpoint_name, {})
            if (
                isinstance(endpoint, dict)
                and endpoint.get("type") == "relation"
                and endpoint.get("id") not in prior_relation_ids
            ):
                errors.append(f"{label}.{endpoint_name}: relation endpoint must reference an earlier record")
        if relation.get("source") == relation.get("target"):
            errors.append(f"{label}: source and target must differ")
        source_run_id = relation.get("source_run_id")
        if source_run_id and source_run_id not in ids_by_type["run"]:
            errors.append(f"{label}: unknown source_run_id {source_run_id!r}")
        if isinstance(relation_id, str) and relation_id:
            prior_relation_ids.add(relation_id)

    revisions_by_claim: dict[str, list[dict[str, Any]]] = {}
    for index, revision in enumerate(claims["claims"], start=1):
        label = f"claims.json:{index}"
        raw_claim_id = revision.get("id")
        claim_id = raw_claim_id if isinstance(raw_claim_id, str) else ""
        revisions_by_claim.setdefault(claim_id, []).append(revision)
        errors.extend(
            _required_fields(
                revision,
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
        revision_id = revision.get("revision_id")
        if not isinstance(raw_claim_id, str) or not claim_id:
            errors.append(f"{label}: id must be a non-empty string")
        if not isinstance(revision.get("claim"), str) or not revision.get("claim"):
            errors.append(f"{label}: claim must be a non-empty string")
        if not isinstance(revision_id, str) or not revision_id:
            errors.append(f"{label}: revision_id must be a non-empty string")
        elif revision_id in revision_ids:
            errors.append(f"{label}: duplicate revision_id {revision_id!r}")
        else:
            revision_ids.add(revision_id)
        revision_number = revision.get("revision")
        if isinstance(revision_number, bool) or not isinstance(revision_number, int) or revision_number < 1:
            errors.append(f"{label}: revision must be a positive integer")
        if not isinstance(revision.get("timestamp"), str):
            errors.append(f"{label}: timestamp must be a string")
        if revision.get("status") not in claim_statuses:
            errors.append(f"{label}: invalid status {revision.get('status')!r}")
        relation_ids = revision.get("evidence_relation_ids")
        if not isinstance(relation_ids, list) or any(not isinstance(value, str) for value in relation_ids):
            errors.append(f"{label}: evidence_relation_ids must be a list of strings")
        elif len(relation_ids) != len(set(relation_ids)):
            errors.append(f"{label}: evidence_relation_ids must not contain duplicates")
        source_run_id = revision.get("source_run_id")
        if source_run_id and source_run_id not in ids_by_type["run"]:
            errors.append(f"{label}: unknown source_run_id {source_run_id!r}")
    for claim_id, revisions in revisions_by_claim.items():
        prior: dict[str, Any] | None = None
        for expected, revision in enumerate(revisions, start=1):
            label = f"claim {claim_id!r} revision {expected}"
            if revision.get("revision") != expected:
                errors.append(f"{label}: revision must be {expected}, got {revision.get('revision')!r}")
            expected_prior = prior.get("revision_id", "") if prior else ""
            if revision.get("supersedes_revision_id") != expected_prior:
                errors.append(
                    f"{label}: supersedes_revision_id must be {expected_prior!r}, "
                    f"got {revision.get('supersedes_revision_id')!r}"
                )
            relation_ids = revision.get("evidence_relation_ids", [])
            for relation_id in relation_ids if isinstance(relation_ids, list) else []:
                if relation_id not in relation_by_id:
                    errors.append(f"{label}: unknown evidence relation {relation_id!r}")
            if revision.get("status") == "disproved":
                if not relation_ids:
                    errors.append(f"{label}: disproved revision requires refuting evidence relationships")
                for relation_id in relation_ids if isinstance(relation_ids, list) else []:
                    relation = relation_by_id.get(relation_id)
                    if relation is None:
                        continue
                    if relation.get("relation") != "refutes":
                        errors.append(f"{label}: evidence relation {relation_id!r} must use 'refutes'")
                    source = relation.get("source")
                    if not isinstance(source, dict) or source.get("type") != "artifact":
                        errors.append(f"{label}: evidence relation {relation_id!r} must originate from an artifact")
                    expected_target = _endpoint("claim_revision", expected_prior)
                    if relation.get("target") != expected_target:
                        errors.append(
                            f"{label}: evidence relation {relation_id!r} must target the superseded revision"
                        )
            prior = revision
    for index, run in enumerate(read_jsonl(directory / "runs.jsonl"), start=1):
        relation_ids = run.get("evidence_relations", [])
        if not isinstance(relation_ids, list) or any(not isinstance(value, str) for value in relation_ids):
            errors.append(f"runs.jsonl:{index}: evidence_relations must be a list of strings")
            continue
        for relation_id in relation_ids:
            if relation_id not in relation_by_id:
                errors.append(f"runs.jsonl:{index}: unknown evidence relation {relation_id!r}")
    return errors


def add_evidence_parsers(subcommands: Any) -> None:
    claim_parser = subcommands.add_parser("claim", help="append and inspect claim revisions")
    claim_subcommands = claim_parser.add_subparsers(dest="claim_command", required=True)
    claim_add = claim_subcommands.add_parser("add", help="append a claim revision")
    claim_add.add_argument("--id", help="stable claim id; omit to create a new claim")
    claim_add.add_argument("--run-id", help="source run id")
    claim_add.add_argument("--claim", required=True)
    claim_add.add_argument("--source-path", required=True)
    claim_add.add_argument("--source-kind", required=True)
    claim_add.add_argument("--status", required=True, choices=CLAIM_STATUSES)
    claim_add.add_argument("--verification-command", help="command that verifies this revision")
    claim_add.add_argument("--last-checked-at", help="ISO timestamp when verification last ran")
    claim_add.add_argument("--confidence", default="unverified")
    claim_add.add_argument("--notes", help="short notes")
    claim_add.set_defaults(func=cmd_claim_add)

    claim_disprove = claim_subcommands.add_parser(
        "disprove",
        help="append a disproved revision linked to refuting artifact evidence",
    )
    claim_disprove.add_argument("--id", required=True, help="stable claim id")
    claim_disprove.add_argument(
        "--evidence-artifact",
        action="append",
        required=True,
        help="unique artifact id containing refuting evidence; repeat as needed",
    )
    claim_disprove.add_argument("--run-id", help="source run id")
    claim_disprove.add_argument("--verification-command", help="command that verifies the disproval")
    claim_disprove.add_argument("--last-checked-at", help="ISO timestamp when disproval was checked")
    claim_disprove.add_argument("--confidence", default="verified")
    claim_disprove.add_argument("--notes", help="why the evidence disproves the prior revision")
    claim_disprove.set_defaults(func=cmd_claim_disprove)

    claim_history = claim_subcommands.add_parser("history", help="show revisions and related evidence")
    claim_history.add_argument("--id", required=True, help="stable claim id")
    claim_history.set_defaults(func=cmd_claim_history)

    relation_parser = subcommands.add_parser("relation", help="append and inspect typed evidence relationships")
    relation_subcommands = relation_parser.add_subparsers(dest="relation_command", required=True)
    relation_add = relation_subcommands.add_parser("add", help="append one typed evidence relationship")
    relation_add.add_argument("--id", help="explicit relation id; otherwise generated")
    relation_add.add_argument("--source-type", required=True, choices=ENDPOINT_TYPES)
    relation_add.add_argument("--source-id", required=True)
    relation_add.add_argument("--relation", required=True, choices=RELATION_TYPES)
    relation_add.add_argument("--target-type", required=True, choices=ENDPOINT_TYPES)
    relation_add.add_argument("--target-id", required=True)
    relation_add.add_argument("--run-id", help="source run id")
    relation_add.add_argument("--confidence", default="unverified")
    relation_add.add_argument("--notes", help="short relationship rationale")
    relation_add.set_defaults(func=cmd_relation_add)

    relation_list = relation_subcommands.add_parser("list", help="list relationships with optional filters")
    relation_list.add_argument("--endpoint-type", choices=ENDPOINT_TYPES)
    relation_list.add_argument("--endpoint-id")
    relation_list.add_argument("--relation", choices=RELATION_TYPES)
    relation_list.set_defaults(func=cmd_relation_list)
