"""Readiness checks for File Provider-backed mission harness state."""

from __future__ import annotations

import pathlib
import subprocess
import sys
import time
from typing import Any, Iterable


READ_TIMEOUT_SECONDS = 3
READ_VERIFY_ATTEMPTS = 2
READ_VERIFY_RETRY_DELAY_SECONDS = 0.2


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


def read_verification_error(path: pathlib.Path) -> str:
    probe = (
        "import pathlib,sys; "
        "p=pathlib.Path(sys.argv[1]); "
        "next(p.iterdir(), None) if p.is_dir() else p.open('rb').read(1)"
    )
    for attempt in range(1, READ_VERIFY_ATTEMPTS + 1):
        try:
            result = subprocess.run(
                [sys.executable, "-c", probe, str(path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=READ_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            if attempt == READ_VERIFY_ATTEMPTS:
                return (
                    f"read verification timed out after {READ_VERIFY_ATTEMPTS} "
                    f"attempts of {READ_TIMEOUT_SECONDS}s"
                )
            time.sleep(READ_VERIFY_RETRY_DELAY_SECONDS)
            continue
        except OSError as exc:
            return f"read verification failed: {exc}"
        if result.returncode:
            detail = result.stderr.strip() or f"exit {result.returncode}"
            return f"read verification failed: {detail}"
        return ""
    return ""


def prepare_agent_state(directory: pathlib.Path, names: Iterable[str]) -> dict[str, Any]:
    """Request downloads for evicted state, then verify that each file is readable."""
    paths = [directory / name for name in names]
    if sys.platform != "darwin":
        return {
            "ok": True,
            "status": "not_applicable",
            "platform": sys.platform,
            "dataless_before": [],
            "downloads_requested": [],
            "read_verified": [],
            "dataless_after": [],
            "errors": [],
        }

    dataless_before = [path.name for path in paths if _is_macos_dataless(path)]
    requested: list[str] = []
    errors: list[str] = []
    for path in paths:
        if path.name not in dataless_before:
            continue
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
            errors.append(f"{path.name}: download request failed: {exc}")
            continue
        if result.returncode:
            detail = result.stderr.strip() or f"exit {result.returncode}"
            errors.append(f"{path.name}: download request failed: {detail}")
            continue
        requested.append(path.name)

    readable: list[str] = []
    for path in paths:
        error = read_verification_error(path)
        if not error:
            readable.append(path.name)
        else:
            errors.append(f"{path.name}: {error}")

    dataless_after = [path.name for path in paths if _is_macos_dataless(path)]
    status = "unreadable" if errors else ("ready" if not dataless_after else "readable_with_dataless_flags_remaining")
    return {
        "ok": not errors,
        "status": status,
        "platform": sys.platform,
        "dataless_before": dataless_before,
        "downloads_requested": requested,
        "read_verified": readable,
        "dataless_after": dataless_after,
        "errors": errors,
    }
