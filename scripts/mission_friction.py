#!/usr/bin/env python3
"""Friction ledger helpers for mission harness state.

The authoritative ledger stays append-only JSONL. Settlement records reuse the
same friction id, so repeated evidence remains auditable while reports can show
the latest effective disposition for each issue.
"""

from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json
import pathlib
import uuid
from typing import Any

from mission_harness_lock import atomic_write_text, file_lock


OPENISH_STATUSES = {"open", "observed"}
RELEASE_BLOCKING_STATUSES = {"release-blocking", "open-release-blocking"}


class FrictionError(RuntimeError):
    """Raised for user-facing friction ledger errors."""


def utc_now() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def short_id(prefix: str) -> str:
    stamp = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}_{stamp}_{uuid.uuid4().hex[:8]}"


def agent_dir(root: pathlib.Path) -> pathlib.Path:
    return root.expanduser().resolve() / ".agent"


def friction_path(root: pathlib.Path) -> pathlib.Path:
    return agent_dir(root) / "friction.jsonl"


def read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FrictionError(f"missing JSONL file: {path}")
    records: list[dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise FrictionError(f"invalid JSONL in {path}:{lineno}: {exc}") from exc
        if not isinstance(value, dict):
            raise FrictionError(f"invalid JSONL in {path}:{lineno}: record must be an object")
        records.append(value)
    return records


def append_jsonl(path: pathlib.Path, record: dict[str, Any]) -> None:
    with file_lock(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")


def write_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, text.rstrip() + "\n")


def effective_friction_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the latest record for each friction id in ledger order."""
    latest: dict[str, dict[str, Any]] = {}
    order: dict[str, int] = {}
    for index, record in enumerate(records):
        friction_id = str(record.get("id", ""))
        if not friction_id:
            friction_id = f"__missing_id_{index}"
        latest[friction_id] = record
        order[friction_id] = index
    return [latest[key] for key in sorted(latest, key=lambda item: order[item])]


def ambiguous_open_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ambiguous: list[dict[str, Any]] = []
    for record in records:
        status = str(record.get("status", "open"))
        has_disposition = bool(record.get("root_cause_id") or record.get("release_disposition"))
        if status in OPENISH_STATUSES and not has_disposition:
            ambiguous.append(record)
    return ambiguous


def release_blocking_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    for record in records:
        status = str(record.get("status", ""))
        disposition = str(record.get("release_disposition", ""))
        if status in RELEASE_BLOCKING_STATUSES or disposition == "release-blocking":
            blockers.append(record)
    return blockers


def ids_from_args(ids: list[str] | None, ids_file: str | None) -> list[str]:
    selected = list(ids or [])
    if ids_file:
        path = pathlib.Path(ids_file).expanduser()
        for line in path.read_text(encoding="utf-8").splitlines():
            item = line.split("#", 1)[0].strip()
            if item:
                selected.append(item)
    seen: set[str] = set()
    unique: list[str] = []
    for item in selected:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    if not unique:
        raise FrictionError("pass --id or --ids-file")
    return unique


def cmd_friction_add(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.root)
    record: dict[str, Any] = {
        "schema_version": "friction.v1",
        "id": args.id or short_id("friction"),
        "timestamp": utc_now(),
        "source_run_id": args.run_id or "",
        "category": args.category,
        "description": args.description,
        "impact": args.impact,
        "proposed_harness_need": args.proposed_harness_need,
        "severity": args.severity,
        "status": args.status,
    }
    for key in ("root_cause_id", "release_disposition", "verification_command", "notes"):
        value = getattr(args, key, "")
        if value:
            record[key] = value
    if args.related_friction_id:
        record["related_friction_ids"] = args.related_friction_id
    append_jsonl(friction_path(root), record)
    print(json.dumps({"id": record["id"]}, sort_keys=True))
    return 0


def cmd_friction_settle(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.root)
    path = friction_path(root)
    records = read_jsonl(path)
    effective = {str(record.get("id", "")): record for record in effective_friction_records(records)}
    selected = ids_from_args(args.id, args.ids_file)
    missing = [item for item in selected if item not in effective]
    if missing:
        raise FrictionError("unknown friction ids: " + ", ".join(missing))
    if args.status == "consolidated" and not args.root_cause_id:
        raise FrictionError("--root-cause-id is required when --status consolidated")

    timestamp = utc_now()
    for friction_id in selected:
        base = effective[friction_id]
        update = {
            "schema_version": "friction.v1",
            "event": "friction_settlement",
            "id": friction_id,
            "timestamp": timestamp,
            "source_run_id": args.run_id or base.get("source_run_id", ""),
            "category": base.get("category", ""),
            "description": base.get("description", ""),
            "impact": base.get("impact", ""),
            "proposed_harness_need": base.get("proposed_harness_need", ""),
            "severity": base.get("severity", "medium"),
            "status": args.status,
            "previous_status": base.get("status", ""),
            "settlement_rationale": args.rationale,
        }
        optional = {
            "root_cause_id": args.root_cause_id,
            "release_disposition": args.release_disposition,
            "verification_command": args.verification_command,
            "next_action": args.next_action,
        }
        for key, value in optional.items():
            if value:
                update[key] = value
        if args.evidence:
            update["settlement_evidence"] = args.evidence
        append_jsonl(path, update)
    print(json.dumps({"settled": len(selected), "status": args.status}, sort_keys=True))
    return 0


def report_payload(records: list[dict[str, Any]]) -> dict[str, Any]:
    effective = effective_friction_records(records)
    effective_by_id = {str(record.get("id", "")): record for record in effective}
    status_counts = collections.Counter(str(record.get("status", "")) for record in effective)
    raw_status_counts = collections.Counter(str(record.get("status", "")) for record in records)
    root_counts = collections.Counter(str(record.get("root_cause_id", "")) for record in effective if record.get("root_cause_id"))
    disposition_counts = collections.Counter(
        str(record.get("release_disposition", "")) for record in effective if record.get("release_disposition")
    )
    ambiguous = ambiguous_open_records(effective)
    blockers = release_blocking_records(effective)
    ambiguous_ids = {str(record.get("id", "")) for record in ambiguous}
    raw_open_ids = {
        str(record.get("id", ""))
        for record in records
        if str(record.get("status", "")) in (OPENISH_STATUSES | {"open-infrastructure-blocker"})
    }
    accounted_raw_open_ids = sorted(item for item in raw_open_ids if item in effective_by_id and item not in ambiguous_ids)
    unaccounted_raw_open_ids = sorted(item for item in raw_open_ids if item in ambiguous_ids)
    non_fixed = [
        record
        for record in effective
        if not str(record.get("status", "")).startswith("fixed") and str(record.get("status", "")) not in {"resolved", "closed"}
    ]
    return {
        "generated_at": utc_now(),
        "raw_record_count": len(records),
        "effective_item_count": len(effective),
        "settlement_record_count": sum(1 for record in records if record.get("event") == "friction_settlement"),
        "duplicate_or_update_record_count": max(0, len(records) - len(effective)),
        "status_counts": dict(sorted(status_counts.items())),
        "raw_status_counts": dict(sorted(raw_status_counts.items())),
        "root_cause_counts": dict(sorted(root_counts.items())),
        "release_disposition_counts": dict(sorted(disposition_counts.items())),
        "ambiguous_open_count": len(ambiguous),
        "release_blocking_count": len(blockers),
        "historical_raw_open_count": len(raw_open_ids),
        "accounted_historical_raw_open_count": len(accounted_raw_open_ids),
        "unaccounted_historical_raw_open_count": len(unaccounted_raw_open_ids),
        "ambiguous_open_ids": [str(record.get("id", "")) for record in ambiguous],
        "release_blocking_ids": [str(record.get("id", "")) for record in blockers],
        "unaccounted_historical_raw_open_ids": unaccounted_raw_open_ids,
        "non_fixed_items": [
            {
                "id": record.get("id", ""),
                "status": record.get("status", ""),
                "root_cause_id": record.get("root_cause_id", ""),
                "release_disposition": record.get("release_disposition", ""),
                "rationale": record.get("settlement_rationale", ""),
            }
            for record in non_fixed
        ],
    }


def markdown_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Friction Release Readiness Report",
        "",
        f"- Generated: {payload['generated_at']}",
        f"- Raw friction records: {payload['raw_record_count']}",
        f"- Effective friction items: {payload['effective_item_count']}",
        f"- Settlement/update records: {payload['settlement_record_count']}",
        f"- Duplicate/update evidence records: {payload['duplicate_or_update_record_count']}",
        f"- Ambiguous open friction: {payload['ambiguous_open_count']}",
        f"- Release-blocking friction: {payload['release_blocking_count']}",
        f"- Historical raw open records accounted by settlement: {payload['accounted_historical_raw_open_count']} / {payload['historical_raw_open_count']}",
        f"- Unaccounted historical raw open records: {payload['unaccounted_historical_raw_open_count']}",
        "",
        "## Effective Status Counts",
    ]
    for status, count in payload["status_counts"].items():
        lines.append(f"- {status or '(blank)'}: {count}")
    lines.append("")
    lines.append("## Release Disposition Counts")
    if payload["release_disposition_counts"]:
        for status, count in payload["release_disposition_counts"].items():
            lines.append(f"- {status}: {count}")
    else:
        lines.append("- None recorded.")
    lines.append("")
    lines.append("## Root Cause Counts")
    if payload["root_cause_counts"]:
        for root, count in payload["root_cause_counts"].items():
            lines.append(f"- {root}: {count}")
    else:
        lines.append("- None recorded.")
    lines.append("")
    lines.append("## Remaining Non-Fixed Items")
    if payload["non_fixed_items"]:
        for item in payload["non_fixed_items"]:
            rationale = item["rationale"] or "No rationale recorded."
            root = item["root_cause_id"] or "no root cause"
            disposition = item["release_disposition"] or "no release disposition"
            lines.append(f"- {item['id']}: {item['status']} ({root}; {disposition}) - {rationale}")
    else:
        lines.append("- None.")
    return "\n".join(lines)


def output_path(root: pathlib.Path, value: str | None) -> pathlib.Path | None:
    if not value:
        return None
    path = pathlib.Path(value).expanduser()
    return path if path.is_absolute() else root / path


def cmd_friction_report(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.root)
    payload = report_payload(read_jsonl(friction_path(root)))
    md_path = output_path(root, args.output)
    json_path = output_path(root, args.json_output)
    if md_path:
        write_text(md_path, markdown_report(payload))
    if json_path:
        write_text(json_path, json.dumps(payload, indent=2, sort_keys=True))
    print(json.dumps(payload, indent=2, sort_keys=True))
    if args.fail_on_ambiguous_open and payload["ambiguous_open_count"]:
        return 1
    if args.fail_on_release_blocking and payload["release_blocking_count"]:
        return 1
    return 0
