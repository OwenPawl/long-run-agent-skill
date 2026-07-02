#!/usr/bin/env python3
"""Minimal mission harness for long-running agent work.

The harness is intentionally file-based:
- Markdown keeps the live control plane and durable state readable.
- JSON/JSONL keeps run, claim, artifact, and friction records portable.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import sys
import uuid
from typing import Any, Iterable

from mission_harness_lock import atomic_write_text, file_lock
from mission_friction import (
    FrictionError,
    ambiguous_open_records,
    cmd_friction_add,
    cmd_friction_report,
    cmd_friction_settle,
    effective_friction_records,
)


LIVE_SECTIONS = [
    "User Updates",
    "Current Goal",
    "Constraints",
    "Agent Status",
    "Interrupts / Corrections",
    "Decisions Made This Run",
    "Commands Run",
    "Tests / Verification",
    "Failures / Blockers",
    "Claims Touched",
    "Artifacts Produced",
    "Friction Observed",
    "Next Actions",
]

PER_RUN_LIVE_SECTIONS = [
    "Interrupts / Corrections",
    "Decisions Made This Run",
    "Commands Run",
    "Tests / Verification",
    "Failures / Blockers",
    "Claims Touched",
    "Artifacts Produced",
    "Friction Observed",
    "Next Actions",
]

FRICTION_CATEGORIES = [
    "memory_loss",
    "claim_drift",
    "doc_drift",
    "verification_gap",
    "repeated_failure",
    "artifact_discovery_gap",
    "context_packing_gap",
    "roadmap_ambiguity",
    "completion_ambiguity",
    "user_control_gap",
    "state_duplication",
    "overengineering_risk",
]

CLAIM_STATUSES = [
    "proposed",
    "claimed",
    "implemented",
    "tested",
    "documented",
    "broken",
    "stale",
    "removed",
]

AGENT_STATE_FILES = [
    "live.md",
    "current_state.md",
    "constitution.md",
    "known_failures.md",
    "decisions.md",
    "runs.jsonl",
    "claims.json",
    "artifacts.json",
    "friction.jsonl",
]

PLACEHOLDERS = {
    "none",
    "none.",
    "none yet",
    "none yet.",
    "none recorded yet",
    "none recorded yet.",
    "add updates here.",
    "add constraints here.",
    "add corrections here.",
}


class HarnessError(RuntimeError):
    """Raised for user-facing harness failures."""


def utc_now() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def short_id(prefix: str) -> str:
    stamp = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}_{stamp}_{uuid.uuid4().hex[:8]}"


def agent_dir(root: pathlib.Path) -> pathlib.Path:
    return root.expanduser().resolve() / ".agent"


def path_for(root: pathlib.Path, name: str) -> pathlib.Path:
    return agent_dir(root) / name


def read_text(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def write_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def write_json(path: pathlib.Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def update_json(path: pathlib.Path, updater: Any) -> Any:
    with file_lock(path):
        data = load_json(path)
        result = updater(data)
        write_json(path, data)
        return result


def load_json(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HarnessError(f"missing JSON file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise HarnessError(f"invalid JSON in {path}: {exc}") from exc


def append_jsonl(path: pathlib.Path, record: dict[str, Any]) -> None:
    with file_lock(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")


def read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise HarnessError(f"missing JSONL file: {path}")
    records: list[dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise HarnessError(f"invalid JSONL in {path}:{lineno}: {exc}") from exc
        if not isinstance(value, dict):
            raise HarnessError(f"invalid JSONL in {path}:{lineno}: record must be an object")
        records.append(value)
    return records


def ensure_json_file(path: pathlib.Path, default: Any, force: bool) -> None:
    if path.exists() and not force:
        return
    write_json(path, default)


def ensure_text_file(path: pathlib.Path, text: str, force: bool) -> None:
    if path.exists() and not force:
        return
    write_text(path, text)


def ensure_empty_file(path: pathlib.Path, force: bool) -> None:
    if path.exists() and not force:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def clear_per_run_live_sections() -> dict[str, list[str]]:
    return {section: md_items([]) for section in PER_RUN_LIVE_SECTIONS}


def live_template() -> str:
    return """# Live Control

## User Updates
- None recorded yet.

## Current Goal
- None recorded yet.

## Constraints
- None recorded yet.

## Agent Status
- Status: initialized

## Interrupts / Corrections
- None recorded yet.

## Decisions Made This Run
- None recorded yet.

## Commands Run
- None recorded yet.

## Tests / Verification
- None recorded yet.

## Failures / Blockers
- None recorded yet.

## Claims Touched
- None recorded yet.

## Artifacts Produced
- None recorded yet.

## Friction Observed
- None recorded yet.

## Next Actions
- None recorded yet.
"""


def constitution_template() -> str:
    return """# Mission Harness Constitution

Reality creates friction. Friction selects features. Audit preserves lessons.
The next cycle should start with less entropy.

## Operating Principles
- Preserve truth over volume.
- Keep human-readable Markdown first.
- Use JSON/JSONL for portable structured records.
- Keep the harness domain-neutral; project-specific analysis belongs in the project tool.
- Do not mark work complete without recorded verification or an explicit reason verification did not run.
- Never leave important context only in `live.md`.
"""


def current_state_template() -> str:
    return """# Current State

Generated state has not been recorded yet.
Run `python3 scripts/mission_harness.py state summarize` after a run starts or closes.
"""


def known_failures_template() -> str:
    return """# Known Failures

No durable failures recorded yet.
"""


def decisions_template() -> str:
    return """# Decisions

No durable decisions recorded yet.
"""


def default_claims() -> dict[str, Any]:
    return {"schema_version": "claims.v1", "claims": []}


def default_artifacts() -> dict[str, Any]:
    return {"schema_version": "artifacts.v1", "artifacts": []}


def require_initialized(root: pathlib.Path) -> None:
    missing = [name for name in AGENT_STATE_FILES if not path_for(root, name).exists()]
    if missing:
        raise HarnessError(f"missing .agent files: {', '.join(missing)}; run init first")


def strip_md_marker(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith("- "):
        return stripped[2:].strip()
    if stripped[:3].replace(".", "").isdigit() and len(stripped) > 3:
        return stripped[3:].strip()
    return stripped


def section_entries(live_text: str, section: str) -> list[str]:
    lines = live_text.splitlines()
    in_section = False
    entries: list[str] = []
    for line in lines:
        if line.startswith("## "):
            title = line[3:].strip()
            if in_section and title != section:
                break
            in_section = title == section
            continue
        if not in_section:
            continue
        item = strip_md_marker(line)
        normalized = item.strip("_").strip().lower()
        if not item or normalized in PLACEHOLDERS:
            continue
        entries.append(item)
    return entries


def uniqueness_key(value: str) -> str:
    """Normalize presentation-only Markdown differences for closeout lists."""
    item = value.strip()
    if len(item) >= 2 and item.startswith("`") and item.endswith("`"):
        item = item[1:-1].strip()
    return " ".join(item.split())


def unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = value.strip()
        key = uniqueness_key(item)
        if not item or not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def md_items(items: list[str]) -> list[str]:
    if not items:
        return ["- None recorded yet."]
    return [f"- {item}" for item in items]


def replace_live_sections(path: pathlib.Path, replacements: dict[str, list[str]]) -> None:
    text = read_text(path) or live_template()
    lines = text.splitlines()
    output: list[str] = []
    i = 0
    seen: set[str] = set()
    while i < len(lines):
        line = lines[i]
        if not line.startswith("## "):
            output.append(line)
            i += 1
            continue
        title = line[3:].strip()
        output.append(line)
        i += 1
        if title in replacements:
            seen.add(title)
            output.extend(replacements[title])
            while i < len(lines) and not lines[i].startswith("## "):
                i += 1
            if i < len(lines):
                output.append("")
            continue
        while i < len(lines) and not lines[i].startswith("## "):
            output.append(lines[i])
            i += 1

    for title, body in replacements.items():
        if title not in seen:
            if output and output[-1].strip():
                output.append("")
            output.append(f"## {title}")
            output.extend(body)
    write_text(path, "\n".join(output))


def cmd_init(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.root)
    directory = agent_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    ensure_text_file(directory / "live.md", live_template(), args.force)
    ensure_text_file(directory / "current_state.md", current_state_template(), args.force)
    ensure_text_file(directory / "constitution.md", constitution_template(), args.force)
    ensure_text_file(directory / "known_failures.md", known_failures_template(), args.force)
    ensure_text_file(directory / "decisions.md", decisions_template(), args.force)
    ensure_empty_file(directory / "runs.jsonl", args.force)
    ensure_empty_file(directory / "friction.jsonl", args.force)
    ensure_json_file(directory / "claims.json", default_claims(), args.force)
    ensure_json_file(directory / "artifacts.json", default_artifacts(), args.force)
    print(f"initialized mission harness: {directory}")
    return 0


def cmd_run_start(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.root)
    require_initialized(root)
    run_id = args.run_id or short_id("run")
    timestamp = utc_now()
    record = {
        "schema_version": "agent_run.v1",
        "event": "run_start",
        "run_id": run_id,
        "timestamp": timestamp,
        "goal": args.goal,
        "constraints": args.constraint or [],
        "status": "running",
    }
    append_jsonl(path_for(root, "runs.jsonl"), record)
    replace_live_sections(
        path_for(root, "live.md"),
        {
            **clear_per_run_live_sections(),
            "Current Goal": [f"- {args.goal}"],
            "Constraints": md_items(args.constraint or []),
            "Agent Status": [
                f"- Run ID: {run_id}",
                "- Status: running",
                f"- Started: {timestamp}",
            ],
        },
    )
    summarize_state(root)
    print(json.dumps({"run_id": run_id, "timestamp": timestamp}, sort_keys=True))
    return 0


def latest_open_run(records: list[dict[str, Any]]) -> str | None:
    starts: list[str] = []
    closed: set[str] = set()
    for record in records:
        if record.get("event") == "run_start":
            starts.append(str(record.get("run_id", "")))
        elif record.get("event") == "run_close":
            closed.add(str(record.get("run_id", "")))
    for run_id in reversed(starts):
        if run_id and run_id not in closed:
            return run_id
    return None


def append_durable_section(path: pathlib.Path, title: str, run_id: str, items: list[str]) -> None:
    if not items:
        return
    existing = read_text(path).rstrip()
    block = [existing, "", f"## {title}", f"- Run ID: {run_id}"]
    block.extend(f"- {item}" for item in items)
    write_text(path, "\n".join(block))


def cmd_run_close(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.root)
    require_initialized(root)
    records = read_jsonl(path_for(root, "runs.jsonl"))
    run_id = args.run_id or latest_open_run(records)
    if not run_id:
        raise HarnessError("no open run found; pass --run-id or start a run first")

    live_text = read_text(path_for(root, "live.md"))
    commands = unique((args.command or []) + section_entries(live_text, "Commands Run"))
    tests = unique((args.test or []) + section_entries(live_text, "Tests / Verification"))
    failures = unique((args.failure or []) + section_entries(live_text, "Failures / Blockers"))
    claims = unique((args.claim or []) + section_entries(live_text, "Claims Touched"))
    artifacts = unique((args.artifact or []) + section_entries(live_text, "Artifacts Produced"))
    # Closing next actions are forward-looking. If the caller provides an
    # explicit list, prefer it over stale live.md planning notes from the run.
    next_actions = unique(args.next_action or section_entries(live_text, "Next Actions"))
    decisions = unique((args.decision or []) + section_entries(live_text, "Decisions Made This Run"))

    if not commands and not tests and not args.no_verification_reason:
        raise HarnessError("run close requires --command/--test entries or --no-verification-reason")

    timestamp = utc_now()
    record = {
        "schema_version": "agent_run.v1",
        "event": "run_close",
        "run_id": run_id,
        "timestamp": timestamp,
        "outcome": args.outcome,
        "summary": args.summary or "",
        "commands_run": commands,
        "tests_run": tests,
        "files_changed": args.file_changed or [],
        "failures": failures,
        "claims_touched": claims,
        "artifacts_produced": artifacts,
        "next_actions": next_actions,
        "no_verification_reason": args.no_verification_reason or "",
        "status": "closed",
    }
    append_jsonl(path_for(root, "runs.jsonl"), record)
    append_durable_section(path_for(root, "decisions.md"), f"Run {timestamp}", run_id, decisions)
    append_durable_section(path_for(root, "known_failures.md"), f"Run {timestamp}", run_id, failures)
    replace_live_sections(
        path_for(root, "live.md"),
        {
            "Agent Status": [
                f"- Run ID: {run_id}",
                "- Status: closed",
                f"- Closed: {timestamp}",
                f"- Outcome: {args.outcome}",
            ],
            "Commands Run": md_items(commands),
            "Tests / Verification": md_items(tests),
            "Failures / Blockers": md_items(failures),
            "Claims Touched": md_items(claims),
            "Artifacts Produced": md_items(artifacts),
            "Next Actions": md_items(next_actions),
        },
    )
    summarize_state(root)
    print(json.dumps({"run_id": run_id, "timestamp": timestamp, "status": "closed"}, sort_keys=True))
    return 0


def cmd_claim_add(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.root)
    require_initialized(root)
    path = path_for(root, "claims.json")
    claim_id = args.id or short_id("claim")
    record = {
        "id": claim_id,
        "claim": args.claim,
        "source_path": args.source_path,
        "source_kind": args.source_kind,
        "status": args.status,
        "verification_command": args.verification_command or "",
        "last_checked_at": args.last_checked_at or "",
        "confidence": args.confidence,
        "notes": args.notes or "",
    }

    def updater(data: dict[str, Any]) -> bool:
        claims = data.setdefault("claims", [])
        replaced = False
        for index, existing in enumerate(claims):
            if existing.get("id") == claim_id:
                claims[index] = record
                replaced = True
                break
        if not replaced:
            claims.append(record)
        return replaced

    replaced = update_json(path, updater)
    print(json.dumps({"id": claim_id, "updated": replaced}, sort_keys=True))
    return 0


def cmd_artifact_add(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.root)
    require_initialized(root)
    path = path_for(root, "artifacts.json")
    artifact_id = args.id or short_id("artifact")
    record = {
        "id": artifact_id,
        "timestamp": utc_now(),
        "source_run_id": args.run_id or "",
        "path": args.path,
        "kind": args.kind,
        "description": args.description,
        "verification_command": args.verification_command or "",
        "notes": args.notes or "",
    }

    def updater(data: dict[str, Any]) -> None:
        artifacts = data.setdefault("artifacts", [])
        artifacts.append(record)

    update_json(path, updater)
    print(json.dumps({"id": artifact_id}, sort_keys=True))
    return 0


def latest_record(records: list[dict[str, Any]], event: str) -> dict[str, Any] | None:
    for record in reversed(records):
        if record.get("event") == event:
            return record
    return None


def summarize_state(root: pathlib.Path) -> None:
    require_initialized(root)
    records = read_jsonl(path_for(root, "runs.jsonl"))
    friction = read_jsonl(path_for(root, "friction.jsonl"))
    effective_friction = effective_friction_records(friction)
    ambiguous_friction = ambiguous_open_records(effective_friction)
    claims = load_json(path_for(root, "claims.json")).get("claims", [])
    artifacts = load_json(path_for(root, "artifacts.json")).get("artifacts", [])
    last_start = latest_record(records, "run_start")
    last_close = latest_record(records, "run_close")
    open_run = latest_open_run(records)
    live_text = read_text(path_for(root, "live.md"))
    live_goal = section_entries(live_text, "Current Goal") if open_run else []
    live_next_actions = section_entries(live_text, "Next Actions") if open_run else []
    live_failures = section_entries(live_text, "Failures / Blockers") if open_run else []
    lines = [
        "# Current State",
        "",
        f"- Updated: {utc_now()}",
        f"- Open run: {open_run or 'none'}",
        f"- Total run records: {len(records)}",
        f"- Claims recorded: {len(claims)}",
        f"- Artifacts recorded: {len(artifacts)}",
        f"- Friction records: {len(friction)} raw / {len(effective_friction)} effective",
        f"- Ambiguous open friction: {len(ambiguous_friction)}",
        "",
        "## Latest Run",
    ]
    if last_start:
        goal = "; ".join(live_goal) if live_goal else str(last_start.get("goal", ""))
        lines.extend(
            [
                f"- Last started run: {last_start.get('run_id', '')}",
                f"- Goal: {goal}",
            ]
        )
    else:
        lines.append("- No run has been started.")
    if last_close:
        lines.extend(
            [
                f"- Last closed run: {last_close.get('run_id', '')}",
                f"- Outcome: {last_close.get('outcome', '')}",
            ]
        )
    next_actions = live_next_actions or (last_close.get("next_actions") if last_close else []) or []
    if next_actions:
        lines.append("")
        lines.append("## Next Actions")
        lines.extend(f"- {item}" for item in next_actions)
    failures = live_failures or (last_close.get("failures") if last_close else []) or []
    if failures:
        lines.append("")
        lines.append("## Latest Failures")
        lines.extend(f"- {item}" for item in failures)
    recent_friction = effective_friction[-5:]
    if recent_friction:
        lines.append("")
        lines.append("## Recent Effective Friction")
        for item in recent_friction:
            lines.append(
                f"- {item.get('id', '')}: {item.get('status', '')} / "
                f"{item.get('category', '')} - {item.get('description', '')}"
            )
    write_text(path_for(root, "current_state.md"), "\n".join(lines))


def cmd_state_summarize(args: argparse.Namespace) -> int:
    summarize_state(pathlib.Path(args.root))
    print(f"updated {path_for(pathlib.Path(args.root), 'current_state.md')}")
    return 0


def cmd_state_preflight(args: argparse.Namespace) -> int:
    from mission_state_preflight import prepare_agent_state

    root = pathlib.Path(args.root)
    require_initialized(root)
    result = prepare_agent_state(agent_dir(root), AGENT_STATE_FILES)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


def prepare_command_state(root: pathlib.Path) -> None:
    directory = agent_dir(root)
    if not directory.exists() or any(not (directory / name).exists() for name in AGENT_STATE_FILES):
        return
    from mission_state_preflight import prepare_agent_state
    result = prepare_agent_state(directory, AGENT_STATE_FILES)
    if not result["ok"]:
        raise HarnessError("state readiness failed: " + "; ".join(result["errors"]))


def cmd_state_compact_live(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.root)
    require_initialized(root)
    stamp = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = agent_dir(root) / "archive" / f"live-{stamp}.md"
    atomic_write_text(archive, read_text(path_for(root, "live.md")))
    note = [f"- Compacted history archived at `.agent/{archive.relative_to(agent_dir(root))}`."]
    sections = ("Commands Run", "Tests / Verification", "Claims Touched", "Artifacts Produced", "Friction Observed")
    replace_live_sections(path_for(root, "live.md"), {section: note for section in sections})
    summarize_state(root)
    print(json.dumps({"archive": str(archive), "sections_compacted": list(sections)}, sort_keys=True))
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    from mission_index import index_status, rebuild_index, search_index

    root = pathlib.Path(args.root)
    require_initialized(root)
    if args.index_command == "rebuild":
        payload = rebuild_index(root)
    elif args.index_command == "search":
        payload = search_index(root, args.query, args.limit, args.kind or "")
    else:
        payload = index_status(root)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def require_fields(record: dict[str, Any], fields: list[str], label: str) -> list[str]:
    return [f"{label}: missing required field {field}" for field in fields if field not in record]


def validate_live(root: pathlib.Path) -> list[str]:
    text = read_text(path_for(root, "live.md"))
    errors = []
    if not text.startswith("# Live Control"):
        errors.append("live.md must start with '# Live Control'")
    for section in LIVE_SECTIONS:
        if f"## {section}" not in text:
            errors.append(f"live.md missing section: {section}")
    return errors


def validate_runs(root: pathlib.Path) -> list[str]:
    errors: list[str] = []
    for index, record in enumerate(read_jsonl(path_for(root, "runs.jsonl")), start=1):
        label = f"runs.jsonl:{index}"
        errors.extend(require_fields(record, ["schema_version", "event", "run_id", "timestamp"], label))
        if record.get("schema_version") != "agent_run.v1":
            errors.append(f"{label}: schema_version must be agent_run.v1")
        if record.get("event") == "run_start":
            errors.extend(require_fields(record, ["goal", "constraints", "status"], label))
        elif record.get("event") == "run_close":
            errors.extend(
                require_fields(
                    record,
                    [
                        "outcome",
                        "commands_run",
                        "tests_run",
                        "files_changed",
                        "failures",
                        "claims_touched",
                        "artifacts_produced",
                        "next_actions",
                    ],
                    label,
                )
            )
            if not record.get("commands_run") and not record.get("tests_run") and not record.get("no_verification_reason"):
                errors.append(f"{label}: close record needs commands/tests or no_verification_reason")
        else:
            errors.append(f"{label}: event must be run_start or run_close")
    return errors


def validate_friction(root: pathlib.Path) -> list[str]:
    errors: list[str] = []
    for index, record in enumerate(read_jsonl(path_for(root, "friction.jsonl")), start=1):
        label = f"friction.jsonl:{index}"
        errors.extend(
            require_fields(
                record,
                [
                    "id",
                    "timestamp",
                    "source_run_id",
                    "category",
                    "description",
                    "impact",
                    "proposed_harness_need",
                    "severity",
                    "status",
                ],
                label,
            )
        )
        if record.get("category") not in FRICTION_CATEGORIES:
            errors.append(f"{label}: unknown category {record.get('category')!r}")
    return errors


def validate_claims(root: pathlib.Path) -> list[str]:
    from mission_validation import validate_claims as validate_claims_file

    return validate_claims_file(path_for(root, "claims.json"), CLAIM_STATUSES)


def validate_artifacts(root: pathlib.Path) -> list[str]:
    from mission_validation import validate_artifacts as validate_artifacts_file

    return validate_artifacts_file(path_for(root, "artifacts.json"))


def cmd_validate(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.root)
    require_initialized(root)
    errors: list[str] = []
    try:
        errors.extend(validate_live(root))
        errors.extend(validate_runs(root))
        errors.extend(validate_friction(root))
        errors.extend(validate_claims(root))
        errors.extend(validate_artifacts(root))
    except HarnessError as exc:
        errors.append(str(exc))
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("mission harness state is valid")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="project root containing or receiving .agent/")
    subcommands = parser.add_subparsers(dest="command", required=True)

    init_parser = subcommands.add_parser("init", help="initialize .agent/ in the project root")
    init_parser.add_argument("--force", action="store_true", help="overwrite existing harness files")
    init_parser.set_defaults(func=cmd_init)

    validate_parser = subcommands.add_parser("validate", help="validate .agent/ Markdown and JSON state")
    validate_parser.set_defaults(func=cmd_validate)

    run_parser = subcommands.add_parser("run", help="start or close a run")
    run_subcommands = run_parser.add_subparsers(dest="run_command", required=True)
    start_parser = run_subcommands.add_parser("start", help="append a run_start record")
    start_parser.add_argument("--goal", required=True, help="bounded goal for this run")
    start_parser.add_argument("--constraint", action="append", help="constraint to record on the run")
    start_parser.add_argument("--run-id", help="explicit run id; otherwise generated")
    start_parser.set_defaults(func=cmd_run_start)
    close_parser = run_subcommands.add_parser("close", help="append a run_close record")
    close_parser.add_argument("--run-id", help="run id; defaults to latest open run")
    close_parser.add_argument("--outcome", required=True, help="short outcome summary")
    close_parser.add_argument("--summary", help="optional longer summary")
    close_parser.add_argument("--command", action="append", help="command run during this run")
    close_parser.add_argument("--test", action="append", help="test or verification run during this run")
    close_parser.add_argument(
        "--file-changed",
        "--changed-file",
        action="append",
        dest="file_changed",
        help="file changed during this run",
    )
    close_parser.add_argument("--failure", action="append", help="failure, blocker, or retry condition")
    close_parser.add_argument("--claim", action="append", help="claim id or claim summary touched")
    close_parser.add_argument("--artifact", action="append", help="artifact id or path produced")
    close_parser.add_argument("--decision", action="append", help="durable decision made during this run")
    close_parser.add_argument("--next-action", action="append", help="next action after this run")
    close_parser.add_argument("--no-verification-reason", help="explicit reason no command/test was run")
    close_parser.set_defaults(func=cmd_run_close)

    friction_parser = subcommands.add_parser("friction", help="record harness/process friction")
    friction_subcommands = friction_parser.add_subparsers(dest="friction_command", required=True)
    friction_add = friction_subcommands.add_parser("add", help="append a friction record")
    friction_add.add_argument("--id", help="explicit friction id; otherwise generated")
    friction_add.add_argument("--run-id", "--source-run-id", dest="run_id", help="source run id")
    friction_add.add_argument("--category", required=True, choices=FRICTION_CATEGORIES)
    friction_add.add_argument("--description", required=True)
    friction_add.add_argument("--impact", required=True)
    friction_add.add_argument("--proposed-harness-need", required=True)
    friction_add.add_argument("--severity", default="medium", choices=["low", "medium", "high", "critical"])
    friction_add.add_argument("--status", default="open")
    friction_add.add_argument("--root-cause-id", dest="root_cause_id", help="known root cause this observation supports")
    friction_add.add_argument(
        "--related-friction-id",
        action="append",
        help="existing friction id that this observation relates to",
    )
    friction_add.add_argument("--release-disposition", help="release disposition if known")
    friction_add.add_argument("--verification-command", help="command or evidence path used to check this disposition")
    friction_add.add_argument("--notes", help="short operator notes")
    friction_add.set_defaults(func=cmd_friction_add)
    friction_settle = friction_subcommands.add_parser(
        "settle",
        help="append settlement records for existing friction ids without deleting evidence",
    )
    friction_settle.add_argument("--id", action="append", help="friction id to settle; repeat as needed")
    friction_settle.add_argument("--ids-file", help="file containing friction ids, one per line")
    friction_settle.add_argument("--status", required=True, help="effective status to apply")
    friction_settle.add_argument("--root-cause-id", help="shared root cause id for consolidated evidence")
    friction_settle.add_argument("--release-disposition", help="release readiness disposition")
    friction_settle.add_argument("--rationale", required=True, help="why this settlement is release-safe")
    friction_settle.add_argument("--verification-command", help="verification command, artifact, or proof")
    friction_settle.add_argument("--next-action", help="follow-up condition or next action")
    friction_settle.add_argument("--evidence", action="append", help="supporting evidence path, command, or commit")
    friction_settle.add_argument("--run-id", help="run responsible for this settlement")
    friction_settle.set_defaults(func=cmd_friction_settle)
    friction_report = friction_subcommands.add_parser("report", help="summarize effective friction readiness")
    friction_report.add_argument("--output", help="optional Markdown report path")
    friction_report.add_argument("--json-output", help="optional JSON report path")
    friction_report.add_argument(
        "--fail-on-ambiguous-open",
        action="store_true",
        help="exit nonzero if effective open friction lacks a disposition",
    )
    friction_report.add_argument(
        "--fail-on-release-blocking",
        action="store_true",
        help="exit nonzero if effective friction is still marked release-blocking",
    )
    friction_report.set_defaults(func=cmd_friction_report)

    claim_parser = subcommands.add_parser("claim", help="create or update a claim record")
    claim_subcommands = claim_parser.add_subparsers(dest="claim_command", required=True)
    claim_add = claim_subcommands.add_parser("add", help="add or replace a claim")
    claim_add.add_argument("--id", help="explicit claim id; otherwise generated")
    claim_add.add_argument("--claim", required=True)
    claim_add.add_argument("--source-path", required=True)
    claim_add.add_argument("--source-kind", required=True)
    claim_add.add_argument("--status", required=True, choices=CLAIM_STATUSES)
    claim_add.add_argument("--verification-command", help="command that verifies the claim")
    claim_add.add_argument("--last-checked-at", help="ISO timestamp when verification last ran")
    claim_add.add_argument("--confidence", default="unverified")
    claim_add.add_argument("--notes", help="short notes")
    claim_add.set_defaults(func=cmd_claim_add)

    artifact_parser = subcommands.add_parser("artifact", help="record an artifact")
    artifact_subcommands = artifact_parser.add_subparsers(dest="artifact_command", required=True)
    artifact_add = artifact_subcommands.add_parser("add", help="append an artifact record")
    artifact_add.add_argument("--id", help="explicit artifact id; otherwise generated")
    artifact_add.add_argument("--run-id", help="source run id")
    artifact_add.add_argument("--path", required=True)
    artifact_add.add_argument("--kind", required=True)
    artifact_add.add_argument("--description", required=True)
    artifact_add.add_argument("--verification-command", help="command that verifies the artifact")
    artifact_add.add_argument("--notes", help="short notes")
    artifact_add.set_defaults(func=cmd_artifact_add)

    index_parser = subcommands.add_parser("index", help="build or query the derived SQLite index")
    index_subcommands = index_parser.add_subparsers(dest="index_command", required=True)
    index_rebuild = index_subcommands.add_parser("rebuild", help="rebuild .agent/mission_index.sqlite")
    index_rebuild.set_defaults(func=cmd_index)
    index_search = index_subcommands.add_parser("search", help="search indexed mission state")
    index_search.add_argument("query")
    index_search.add_argument("--kind", help="optional record kind filter")
    index_search.add_argument("--limit", type=int, default=20)
    index_search.set_defaults(func=cmd_index)
    index_status = index_subcommands.add_parser("status", help="show derived index status")
    index_status.set_defaults(func=cmd_index)

    state_parser = subcommands.add_parser("state", help="generate derived state")
    state_subcommands = state_parser.add_subparsers(dest="state_command", required=True)
    state_summarize = state_subcommands.add_parser("summarize", help="update current_state.md from records")
    state_summarize.set_defaults(func=cmd_state_summarize)
    state_preflight = state_subcommands.add_parser(
        "preflight",
        help="request and verify readable macOS File Provider-backed .agent state",
    )
    state_preflight.set_defaults(func=cmd_state_preflight)
    state_compact_live = state_subcommands.add_parser("compact-live", help="archive verbose live evidence sections")
    state_compact_live.set_defaults(func=cmd_state_compact_live)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command != "init" and not (args.command == "state" and args.state_command == "preflight"):
            prepare_command_state(pathlib.Path(args.root))
        return args.func(args)
    except (HarnessError, FrictionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
