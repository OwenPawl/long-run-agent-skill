"""Small deterministic and filesystem helpers."""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import os
import time
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from .errors import LedgerError


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def sha256(value: Any) -> str:
    data = value if isinstance(value, bytes) else canonical_json(value).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def stable_id(prefix: str, value: Any, size: int = 16) -> str:
    return f"{prefix}_{sha256(value)[:size]}"


def random_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


@contextlib.contextmanager
def file_lock(path: Path, timeout: float = 30.0) -> Iterator[None]:
    lock_dir = path.with_name(f".{path.name}.lockdir")
    started = time.monotonic()
    while True:
        try:
            lock_dir.mkdir(parents=False)
            break
        except FileExistsError as exc:
            if time.monotonic() - started >= timeout:
                raise LedgerError(f"timed out waiting for ledger lock: {lock_dir}") from exc
            time.sleep(0.05)
    try:
        yield
    finally:
        lock_dir.rmdir()


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def bounded_text(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, True
    if limit <= 3:
        return text[:limit], False
    return text[: limit - 3] + "...", False
