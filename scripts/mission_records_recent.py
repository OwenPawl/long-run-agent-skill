#!/usr/bin/env python3
"""List recent mission harness records without guessing file schemas."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any


KIND_FILES = {
    "runs": ("runs.jsonl", "jsonl"),
    "claims": ("claims.json", "json:claims"),
    "artifacts": ("artifacts.json", "json:artifacts"),
    "friction": ("friction.jsonl", "jsonl"),
    "relations": ("evidence_relations.jsonl", "jsonl"),
}


class RecordsError(RuntimeError):
    """Raised for user-facing recent-record errors."""


def agent_dir(root: pathlib.Path) -> pathlib.Path:
    return root.expanduser().resolve() / ".agent"


def read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise RecordsError(f"missing JSONL file: {path}")
    records: list[dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RecordsError(f"invalid JSONL in {path}:{lineno}: {exc}") from exc
        if not isinstance(value, dict):
            raise RecordsError(f"invalid JSONL in {path}:{lineno}: record must be an object")
        records.append(value)
    return records


def read_json_records(path: pathlib.Path, key: str) -> list[dict[str, Any]]:
    if not path.exists():
        raise RecordsError(f"missing JSON file: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RecordsError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RecordsError(f"{path.name} must be a JSON object")
    records = data.get(key)
    if not isinstance(records, list):
        raise RecordsError(f"{path.name} field {key!r} must be a list")
    if any(not isinstance(record, dict) for record in records):
        raise RecordsError(f"{path.name} field {key!r} must contain objects")
    return records


def load_records(root: pathlib.Path, kind: str) -> tuple[pathlib.Path, list[dict[str, Any]]]:
    filename, mode = KIND_FILES[kind]
    path = agent_dir(root) / filename
    if mode == "jsonl":
        return path, read_jsonl(path)
    _, key = mode.split(":", 1)
    return path, read_json_records(path, key)


def recent_records(root: pathlib.Path, kind: str, limit: int) -> dict[str, Any]:
    if limit < 1:
        raise RecordsError("--limit must be greater than zero")
    path, records = load_records(root, kind)
    selected = list(reversed(records[-limit:]))
    return {
        "ok": True,
        "kind": kind,
        "source": str(path),
        "count": len(records),
        "returned": len(selected),
        "records": selected,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="project root containing .agent/")
    parser.add_argument("kind", choices=sorted(KIND_FILES), help="record kind to list")
    parser.add_argument("--limit", type=int, default=10, help="maximum records to return")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload = recent_records(pathlib.Path(args.root), args.kind, args.limit)
    except RecordsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
