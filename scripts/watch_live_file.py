#!/usr/bin/env python3
"""Create and watch a Codex long-run live.md file."""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import pathlib
import sys
import time


TEMPLATE = """# Live Control

## User Updates
- Add steering here. Newer instructions override older conflicting instructions.

## Current Goal
- Add the active goal here.

## Constraints
- Add constraints here.

## Agent Status
- Status: starting

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


def read_bytes(path: pathlib.Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return b""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def emit(path: pathlib.Path, data: bytes, reason: str) -> None:
    timestamp = _dt.datetime.now().isoformat(timespec="seconds")
    text = data.decode("utf-8", errors="replace")
    print(f"\n===== LIVE FILE {reason} {timestamp} =====")
    print(f"Path: {path}")
    print("----- BEGIN LIVE FILE -----")
    print(text.rstrip())
    print("----- END LIVE FILE -----", flush=True)


def ensure_file(path: pathlib.Path, create: bool) -> None:
    if path.exists():
        return
    if not create:
        raise SystemExit(f"live file does not exist: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TEMPLATE, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("live_file", help="Markdown live.md file to watch")
    parser.add_argument("--create", action="store_true", help="create the file with a template if missing")
    parser.add_argument("--interval", type=float, default=1.0, help="poll interval in seconds")
    parser.add_argument("--once", action="store_true", help="print once and exit")
    parser.add_argument("--quiet-initial", action="store_true", help="do not print the initial file content")
    args = parser.parse_args()

    path = pathlib.Path(args.live_file).expanduser().resolve()
    ensure_file(path, args.create)

    data = read_bytes(path)
    last_digest = digest(data)
    if not args.quiet_initial:
        emit(path, data, "INITIAL")
    if args.once:
        return 0

    while True:
        time.sleep(max(args.interval, 0.1))
        data = read_bytes(path)
        current_digest = digest(data)
        if current_digest != last_digest:
            last_digest = current_digest
            emit(path, data, "UPDATED")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nwatcher stopped", file=sys.stderr)
        raise SystemExit(130)
