"""Portable result envelopes and live-control reads for MCP tools."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable

from .errors import EpistemicError


def envelope(status: str, *, data: Any = None, note: str = "", artifacts: list[str] | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "note": note,
        "artifacts": sorted(set(artifacts or [])),
        "data": data,
    }


def compiler_call(root: str, callback: Callable[[], Any]) -> dict[str, Any]:
    agent = str(Path(root).expanduser().resolve() / ".agent")
    try:
        return envelope("success", data=callback(), artifacts=[agent])
    except (EpistemicError, OSError, ValueError) as exc:
        return envelope("failed", note=str(exc), artifacts=[agent])


def control_snapshot(root: str, known_sha256: str = "") -> dict[str, Any]:
    live_path = Path(root).expanduser().resolve() / ".agent" / "live.md"
    if not live_path.is_file():
        return envelope("failed", note="live.md does not exist; initialize the mission first", artifacts=[str(live_path)])
    content = live_path.read_text(encoding="utf-8")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    changed = not known_sha256 or digest != known_sha256
    return envelope(
        "success",
        artifacts=[str(live_path)],
        data={"path": str(live_path), "sha256": digest, "changed": changed, "content": content if changed else ""},
        note="content omitted because the supplied hash is current" if not changed else "",
    )
