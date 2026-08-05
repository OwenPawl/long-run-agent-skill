"""Subprocess adapter and typed result envelopes for the mission harness MCP."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import sysconfig
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUCCESS = "success"
BLOCKED = "blocked"
FAILED = "failed"
UNVERIFIED = "unverified"
OUTPUT_CAP = 20_000


@dataclass(frozen=True)
class HarnessSettings:
    python: str
    harness_script: Path
    reveal_script: Path | None

    @classmethod
    def discover(cls) -> HarnessSettings:
        root_override = os.environ.get("LONG_RUN_AGENT_ROOT")
        candidates: list[Path] = []
        if root_override:
            candidates.append(Path(root_override).expanduser())
        candidates.extend(
            [
                Path(__file__).resolve().parents[1],
                Path(sysconfig.get_path("data")) / "share" / "long-run-agent-skill",
            ]
        )
        for root in candidates:
            harness = root / "scripts" / "mission_harness.py"
            if harness.is_file():
                reveal = root / "scripts" / "reveal_live_file.py"
                return cls(
                    python=sys.executable,
                    harness_script=harness,
                    reveal_script=reveal if reveal.is_file() else None,
                )
        searched = ", ".join(str(path) for path in candidates)
        raise RuntimeError(
            "mission_harness.py was not found; set LONG_RUN_AGENT_ROOT or reinstall "
            f"long-run-agent-skill (searched: {searched})"
        )


def envelope(
    status: str,
    *,
    command: Sequence[str] | None = None,
    exit_code: int | None = None,
    artifacts: list[str] | None = None,
    stdout: str = "",
    stderr: str = "",
    data: Any = None,
    note: str = "",
) -> dict[str, Any]:
    return {
        "status": status,
        "note": note,
        "artifacts": sorted(set(artifacts or [])),
        "warnings": [],
        "command": list(command) if command else [],
        "exit_code": exit_code,
        "stdout": stdout[-OUTPUT_CAP:],
        "stderr": stderr[-OUTPUT_CAP:],
        "data": data,
    }


class HarnessRunner:
    def __init__(self, settings: HarnessSettings):
        self.settings = settings

    def command(self, root: str | Path, args: list[str]) -> list[str]:
        return [
            self.settings.python,
            str(self.settings.harness_script),
            "--root",
            str(Path(root).expanduser().resolve()),
            *args,
        ]

    def run(self, root: str | Path, args: list[str], timeout: int = 180) -> dict[str, Any]:
        command = self.command(root, args)
        try:
            result = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=timeout,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return envelope(
                FAILED,
                command=command,
                stderr=f"timeout after {timeout}s",
            )
        except OSError as exc:
            return envelope(FAILED, command=command, stderr=str(exc))
        parsed = _parse_json(result.stdout)
        status = SUCCESS if result.returncode == 0 else FAILED
        project_root = Path(root).expanduser().resolve()
        artifacts = [str(project_root / ".agent")]
        return envelope(
            status,
            command=command,
            exit_code=result.returncode,
            artifacts=artifacts,
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
            data=parsed,
        )

    def reveal_live(self, root: str | Path) -> dict[str, Any]:
        live_path = Path(root).expanduser().resolve() / ".agent" / "live.md"
        if self.settings.reveal_script is None:
            return envelope(
                UNVERIFIED,
                artifacts=[str(live_path)],
                note="reveal helper is unavailable in this installation",
            )
        command = [
            self.settings.python,
            str(self.settings.reveal_script),
            str(live_path),
            "--skip-if-open",
        ]
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            shell=False,
            check=False,
        )
        return envelope(
            SUCCESS if result.returncode == 0 else UNVERIFIED,
            command=command,
            exit_code=result.returncode,
            artifacts=[str(live_path)],
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
            note="live control reveal requested",
        )


def control_snapshot(root: str | Path, known_sha256: str = "") -> dict[str, Any]:
    live_path = Path(root).expanduser().resolve() / ".agent" / "live.md"
    if not live_path.is_file():
        return envelope(
            FAILED,
            artifacts=[str(live_path)],
            note="live.md does not exist; initialize the mission first",
        )
    content = live_path.read_text(encoding="utf-8")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    changed = not known_sha256 or digest != known_sha256
    return envelope(
        SUCCESS,
        artifacts=[str(live_path)],
        data={
            "path": str(live_path),
            "sha256": digest,
            "changed": changed,
            "content": content if changed else "",
        },
        note="content omitted because the supplied hash is current" if not changed else "",
    )


def _parse_json(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def append_options(args: list[str], flag: str, values: list[str] | None) -> None:
    for value in values or []:
        args.extend([flag, value])


def add_option(args: list[str], flag: str, value: Any) -> None:
    if value not in (None, ""):
        args.extend([flag, str(value)])
