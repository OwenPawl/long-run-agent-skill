#!/usr/bin/env python3
"""Materialize a mission artifact path before its verification command runs."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from mission_state_preflight import READ_TIMEOUT_SECONDS, prepare_agent_state, read_verification_error


class MaterializeError(RuntimeError):
    """Raised when a recorded artifact cannot be selected or materialized."""


def _is_macos_dataless(path: pathlib.Path) -> bool:
    try:
        result = subprocess.run(
            ["ls", "-ldO", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=READ_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and "dataless" in result.stdout


def _read_verified(path: pathlib.Path) -> None:
    error = read_verification_error(path)
    if error:
        raise MaterializeError(error)


def _artifact_record(root: pathlib.Path, artifact_id: str) -> dict[str, Any]:
    state = prepare_agent_state(root / ".agent", ["artifacts.json"])
    if not state["ok"]:
        raise MaterializeError("artifact registry readiness failed: " + "; ".join(state["errors"]))
    registry = root / ".agent" / "artifacts.json"
    try:
        artifacts = json.loads(registry.read_text(encoding="utf-8")).get("artifacts", [])
    except (OSError, json.JSONDecodeError) as exc:
        raise MaterializeError(f"cannot read artifact registry: {exc}") from exc
    for record in reversed(artifacts):
        if record.get("id") == artifact_id:
            return record
    raise MaterializeError(f"artifact id not found: {artifact_id}")


def _stage_verified_copy(source: pathlib.Path, destination: pathlib.Path) -> dict[str, Any]:
    if source.is_dir():
        raise MaterializeError("--stage-copy supports regular files only; select a nested evidence file")
    if destination.exists():
        raise MaterializeError(f"stage-copy destination already exists: {destination}")
    if source.resolve() == destination.resolve():
        raise MaterializeError("--stage-copy destination must differ from the source path")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: pathlib.Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=f".{destination.name}.", delete=False) as handle:
            temporary = pathlib.Path(handle.name)
        shutil.copy2(source, temporary)
        _read_verified(temporary)
        os.replace(temporary, destination)
        temporary = None
        _read_verified(destination)
    except MaterializeError:
        raise
    except OSError as exc:
        raise MaterializeError(f"stage-copy failed: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {
        "path": str(destination),
        "read_verified": True,
        "byte_size": destination.stat().st_size,
    }


def materialize(path: pathlib.Path, artifact_id: str = "", stage_copy: pathlib.Path | None = None) -> dict[str, Any]:
    if not path.exists():
        raise MaterializeError(f"artifact path does not exist: {path}")
    dataless_before = sys.platform == "darwin" and _is_macos_dataless(path)
    requested = False
    errors: list[str] = []
    if dataless_before:
        try:
            result = subprocess.run(
                ["brctl", "download", str(path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"download request failed: {exc}")
        else:
            if result.returncode:
                errors.append(result.stderr.strip() or f"download request exited {result.returncode}")
            else:
                requested = True
    readable = False
    try:
        _read_verified(path)
        readable = True
    except MaterializeError as exc:
        errors.append(str(exc))
    dataless_after = sys.platform == "darwin" and _is_macos_dataless(path)
    staged_copy: dict[str, Any] | None = None
    if not errors and readable and stage_copy is not None:
        try:
            staged_copy = _stage_verified_copy(path, stage_copy)
        except MaterializeError as exc:
            errors.append(str(exc))
    return {
        "schema": "mission-harness.artifact-readiness.v1",
        "ok": not errors and readable,
        "artifact_id": artifact_id,
        "path": str(path),
        "platform": sys.platform,
        "dataless_before": dataless_before,
        "download_requested": requested,
        "read_verified": readable,
        "dataless_after": dataless_after,
        "staged_copy": staged_copy,
        "errors": errors,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="project root containing .agent/artifacts.json")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--id", help="recorded artifact id; latest matching record wins")
    selection.add_argument("--path", help="explicit artifact path, absolute or relative to root")
    parser.add_argument(
        "--stage-copy",
        help="copy a readable regular-file artifact atomically to a new stable input path; relative paths resolve from root",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = pathlib.Path(args.root)
    try:
        record = _artifact_record(root, args.id) if args.id else {}
        raw_path = str(record.get("path") or args.path)
        path = pathlib.Path(raw_path)
        if not path.is_absolute():
            path = root / path
        stage_copy = pathlib.Path(args.stage_copy) if args.stage_copy else None
        if stage_copy is not None and not stage_copy.is_absolute():
            stage_copy = root / stage_copy
        result = materialize(path, args.id or "", stage_copy)
    except MaterializeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
