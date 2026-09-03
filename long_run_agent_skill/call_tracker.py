"""Passive, human-readable logging for public skill calls."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .util import file_lock, random_id

LOGGER = logging.getLogger(__name__)
LOG_ENVIRONMENT_VARIABLE = "LONG_RUN_AGENT_SKILL_CALL_LOG"
DEFAULT_LOG_NAME = "skill-calls.log"


def tracker_log_path(root: str | Path) -> Path:
    """Return the configured log path, relative to the mission root when needed."""
    mission_root = Path(root).expanduser().resolve()
    configured = os.environ.get(LOG_ENVIRONMENT_VARIABLE, "").strip()
    if not configured:
        return mission_root / ".agent" / DEFAULT_LOG_NAME
    path = Path(configured).expanduser()
    resolved = path.resolve() if path.is_absolute() else (mission_root / path).resolve()
    authority_paths = {
        mission_root / ".agent" / "ledger.jsonl",
        mission_root / ".agent" / "retrieval.jsonl",
    }
    if resolved in authority_paths:
        raise ValueError("skill call log cannot replace semantic or retrieval authority")
    return resolved


def _timestamp() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _render_payload(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True)
    except (TypeError, ValueError):
        return repr(value)


def _ledger_snapshot(root: str | Path) -> tuple[int | None, str]:
    ledger = Path(root).expanduser().resolve() / ".agent" / "ledger.jsonl"
    if not ledger.is_file():
        return None, ""
    try:
        with ledger.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            buffer = b""
            while position > 0:
                size = min(4096, position)
                position -= size
                handle.seek(position)
                buffer = handle.read(size) + buffer
                lines = [line for line in buffer.splitlines() if line.strip()]
                if lines:
                    try:
                        transaction = json.loads(lines[-1].decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        if position > 0:
                            continue
                        raise
                    return int(transaction["sequence"]), str(
                        transaction.get("run_id", "")
                    )
        return 0, ""
    except (KeyError, OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return None, ""


def _safe_ledger_snapshot(root: str | Path) -> tuple[int | None, str]:
    try:
        return _ledger_snapshot(root)
    except Exception as exc:
        LOGGER.error("could not inspect epistemic generation for call log: %s", exc)
        return None, ""


def _identifiers(request: Any, run_id: str) -> tuple[str, str, str]:
    values = request if isinstance(request, Mapping) else {}
    requested_run = values.get("run_id", "")
    worker = str(values.get("worker_id", "") or os.environ.get("LONG_RUN_AGENT_WORKER_ID", ""))
    session = str(
        values.get("session_id", "") or os.environ.get("LONG_RUN_AGENT_SESSION_ID", "")
    )
    return worker, str(requested_run or run_id), session


def _response_status(response: Any, exception: Exception | None) -> str:
    if exception is not None:
        return "failed"
    if isinstance(response, Mapping) and response.get("status"):
        return str(response["status"])
    return "success"


def _render_block(
    *,
    call_id: str,
    timestamp: str,
    operation: str,
    surface: str,
    status: str,
    duration_ms: float,
    request_text: str,
    response_text: str,
    generation_before: int | None,
    generation_after: int | None,
    worker_id: str,
    run_id: str,
    session_id: str,
    exception: Exception | None,
) -> str:
    unavailable = "unavailable"
    error_type = type(exception).__name__ if exception is not None else ""
    header = [
        f"=== SKILL CALL {call_id} ===",
        f"timestamp: {timestamp}",
        f"worker/run/session: {worker_id or '-'} / {run_id or '-'} / {session_id or '-'}",
        f"operation: {operation}",
        f"surface: {surface}",
        f"status: {status}",
        f"duration_ms: {duration_ms:.3f}",
        f"request_bytes: {len(request_text.encode('utf-8'))}",
        f"response_bytes: {len(response_text.encode('utf-8'))}",
        "epistemic_generation_before: "
        + (str(generation_before) if generation_before is not None else unavailable),
        "epistemic_generation_after: "
        + (str(generation_after) if generation_after is not None else unavailable),
    ]
    if error_type:
        header.append(f"error_type: {error_type}")
    return "\n".join(
        [
            *header,
            "",
            "--- REQUEST ---",
            request_text,
            "",
            "--- RESPONSE ---",
            response_text,
            "",
            f"=== END {call_id} ===",
            "",
        ]
    )


def _append_block(path: Path, block: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path, timeout=5.0):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(block)
            handle.flush()
            os.fsync(handle.fileno())


def _record_without_affecting_call(path: Path, block: str) -> None:
    try:
        _append_block(path, block)
    except Exception as exc:  # Operational logging must never change compiler semantics.
        LOGGER.error("could not append skill call log %s: %s", path, exc)


def track_public_call(
    root: str | Path,
    operation: str,
    request: Any,
    callback: Callable[[], Any],
    *,
    surface: str,
    error_response: Callable[[Exception], Any] | None = None,
) -> Any:
    """Run one public call and append its exact surfaced payload as one block."""
    call_id = random_id("call")
    timestamp = _timestamp()
    generation_before, run_before = _safe_ledger_snapshot(root)
    started = time.perf_counter_ns()
    response: Any = None
    exception: Exception | None = None
    try:
        response = callback()
    except Exception as exc:
        exception = exc
        response = (
            error_response(exc)
            if error_response is not None
            else {"status": "failed", "error": str(exc), "error_type": type(exc).__name__}
        )
    duration_ms = (time.perf_counter_ns() - started) / 1_000_000
    try:
        generation_after, run_after = _safe_ledger_snapshot(root)
        request_text = _render_payload(request)
        response_text = _render_payload(response)
        worker_id, run_id, session_id = _identifiers(request, run_after or run_before)
        block = _render_block(
            call_id=call_id,
            timestamp=timestamp,
            operation=operation,
            surface=surface,
            status=_response_status(response, exception),
            duration_ms=duration_ms,
            request_text=request_text,
            response_text=response_text,
            generation_before=generation_before,
            generation_after=generation_after,
            worker_id=worker_id,
            run_id=run_id,
            session_id=session_id,
            exception=exception,
        )
        _record_without_affecting_call(tracker_log_path(root), block)
    except Exception as tracker_error:
        LOGGER.error("could not prepare skill call log: %s", tracker_error)
    if exception is not None:
        raise exception
    return response
