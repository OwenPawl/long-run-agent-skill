"""Small cross-platform file locking helpers for mission harness state."""

from __future__ import annotations

import contextlib
import os
import pathlib
import time
import uuid
from typing import Iterable


class LockTimeoutError(RuntimeError):
    """Raised when a state-file lock cannot be acquired in time."""


def atomic_write_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(text, encoding="utf-8")
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


@contextlib.contextmanager
def file_lock(path: pathlib.Path, timeout_seconds: float = 30.0) -> Iterable[None]:
    """Acquire an atomic lock directory beside ``path``."""

    lock_dir = path.with_name(f".{path.name}.lockdir")
    start = time.monotonic()
    while True:
        try:
            lock_dir.mkdir()
            break
        except FileExistsError as exc:
            if time.monotonic() - start >= timeout_seconds:
                raise LockTimeoutError(f"timed out waiting for lock: {lock_dir}") from exc
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lock_dir.rmdir()
        except FileNotFoundError:
            pass
