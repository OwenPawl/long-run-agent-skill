#!/usr/bin/env python3
"""Plan or install the long-run-agent skill into local AI host directories.

The installer is conservative: it prints a plan by default and only copies
files when --execute is supplied.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

EXCLUDE_NAMES = {
    ".git",
    ".DS_Store",
    "__pycache__",
}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


@dataclass
class InstallStep:
    id: str
    description: str
    source: str
    target: str
    status: str
    reason: str
    backup: str = ""


class InstallError(RuntimeError):
    """Raised for user-facing installer errors."""


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def host_targets(host: str, custom_target: str | None = None) -> list[Path]:
    if custom_target:
        return [Path(custom_target).expanduser()]
    home = Path.home()
    mapping = {
        "codex": home / ".codex" / "skills" / "long-run-agent",
        "claude": home / ".claude" / "skills" / "long-run-agent",
    }
    if host in {"auto", ""}:
        detected = [path for path in mapping.values() if path.parent.parent.exists()]
        return detected or [mapping["codex"]]
    if host in {"both", "all"}:
        return list(mapping.values())
    if "," in host:
        targets = []
        for item in host.split(","):
            key = item.strip()
            if key not in mapping:
                raise InstallError(f"unknown host: {key!r}")
            targets.append(mapping[key])
        return targets
    if host not in mapping:
        raise InstallError("unknown host: {!r} (expected codex | claude | both | auto)".format(host))
    return [mapping[host]]


def should_ignore(path: Path) -> bool:
    return path.name in EXCLUDE_NAMES or (path.is_file() and path.suffix in EXCLUDE_SUFFIXES)


def copy_tree(source: Path, target: Path) -> None:
    def ignore(directory: str, contents: list[str]) -> set[str]:
        ignored: set[str] = set()
        base = Path(directory)
        for name in contents:
            if should_ignore(base / name):
                ignored.add(name)
        return ignored

    shutil.copytree(source, target, ignore=ignore, dirs_exist_ok=False)


def build_plan(source: Path, targets: list[Path]) -> list[InstallStep]:
    if not (source / "SKILL.md").exists():
        raise InstallError(f"source does not look like a skill repo: {source}")
    steps = []
    for index, target in enumerate(targets, start=1):
        status = "backup_required" if target.exists() else "pending"
        reason = "target exists and will be backed up before copy" if target.exists() else "target does not exist yet"
        steps.append(
            InstallStep(
                id=f"install_{index}",
                description="Install long-run-agent skill files.",
                source=str(source),
                target=str(target),
                status=status,
                reason=reason,
            )
        )
    return steps


def execute_plan(steps: list[InstallStep], force: bool) -> int:
    stamp = timestamp()
    for step in steps:
        source = Path(step.source)
        target = Path(step.target)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            backup = target.parent / f"{target.name}.backup-{stamp}"
            if backup.exists():
                if not force:
                    raise InstallError(f"backup path already exists: {backup}; pass --force to replace it")
                shutil.rmtree(backup)
            shutil.move(str(target), str(backup))
            step.backup = str(backup)
        copy_tree(source, target)
        step.status = "installed"
        step.reason = "copy completed"
    return 0


def payload(steps: list[InstallStep], execute: bool, source: Path) -> dict[str, object]:
    return {
        "ok": all(step.status not in {"failed"} for step in steps),
        "execute": execute,
        "source": str(source),
        "steps": [asdict(step) for step in steps],
        "next_commands": [
            "python3 <target>/scripts/mission_harness.py --root /tmp/long-run-agent-smoke init",
            "python3 <target>/scripts/mission_harness.py --root /tmp/long-run-agent-smoke validate",
        ],
    }


def print_payload(steps: list[InstallStep], execute: bool, source: Path, as_json: bool) -> None:
    data = payload(steps, execute, source)
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return
    mode = "execute" if execute else "dry-run"
    print(f"long-run-agent install ({mode})")
    print(f"Source: {source}")
    for step in steps:
        print(f"- [{step.status}] {step.target}")
        print(f"  reason: {step.reason}")
        if step.backup:
            print(f"  backup: {step.backup}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Copy files into target directories.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable output.")
    parser.add_argument("--host", default="auto", help="Target host: codex | claude | both | auto.")
    parser.add_argument("--source", default=str(ROOT), help="Source skill repo directory.")
    parser.add_argument("--target", help="Custom target directory; overrides --host.")
    parser.add_argument("--force", action="store_true", help="Replace a colliding backup path during execute.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        source = Path(args.source).expanduser().resolve()
        targets = [target.expanduser().resolve() for target in host_targets(args.host, args.target)]
        steps = build_plan(source, targets)
        if args.execute:
            execute_plan(steps, args.force)
        print_payload(steps, args.execute, source, args.json)
        return 0
    except InstallError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
