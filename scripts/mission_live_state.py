"""Live control parsing, archival, and compare-and-swap helpers."""

from __future__ import annotations

import pathlib
import re
from collections.abc import Iterable

from mission_harness_lock import atomic_write_text, file_lock


ARCHIVE_MARKER_TYPE = "mission-harness.live-archive.v1"
COMPACTED_SECTIONS = (
    "Commands Run",
    "Tests / Verification",
    "Claims Touched",
    "Artifacts Produced",
    "Friction Observed",
)
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
_TYPED_ARCHIVE = re.compile(r"^`mission-harness\.live-archive\.v1`:\s*`([^`]+)`$")
_LEGACY_ARCHIVE = re.compile(r"^Compacted history archived at `([^`]+)`\.$")
_MAX_ARCHIVE_DEPTH = 64


class LiveStateError(RuntimeError):
    """Raised when live state cannot be safely read or replaced."""


def _strip_md_marker(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith("- "):
        return stripped[2:].strip()
    if stripped[:3].replace(".", "").isdigit() and len(stripped) > 3:
        return stripped[3:].strip()
    return stripped


def _section_items(text: str, section: str) -> list[str]:
    in_section = False
    entries: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            title = line[3:].strip()
            if in_section and title != section:
                break
            in_section = title == section
            continue
        if not in_section:
            continue
        item = _strip_md_marker(line)
        normalized = item.strip("_").strip().lower()
        if item and normalized not in PLACEHOLDERS:
            entries.append(item)
    return entries


def archive_reference(item: str) -> str | None:
    """Return a typed or legacy archive path without treating it as evidence."""
    for pattern in (_TYPED_ARCHIVE, _LEGACY_ARCHIVE):
        match = pattern.fullmatch(item.strip())
        if match:
            return match.group(1)
    return None


def archive_marker(relative_path: str) -> str:
    return f"- `{ARCHIVE_MARKER_TYPE}`: `{relative_path}`"


def section_entries(text: str, section: str) -> list[str]:
    return [item for item in _section_items(text, section) if archive_reference(item) is None]


def _resolve_archive(agent_directory: pathlib.Path, reference: str) -> pathlib.Path:
    project_root = agent_directory.parent.resolve()
    archive_root = (agent_directory / "archive").resolve()
    candidate = (project_root / reference).resolve()
    if not candidate.is_relative_to(archive_root):
        raise LiveStateError(f"live archive reference escapes .agent/archive: {reference}")
    if not candidate.is_file():
        raise LiveStateError(f"live archive reference is missing: {reference}")
    return candidate


def section_entries_with_archives(
    agent_directory: pathlib.Path,
    text: str,
    section: str,
) -> list[str]:
    """Recover real entries through repeated compact-live archive chains."""
    visited: set[pathlib.Path] = set()

    def collect(source: str, depth: int) -> list[str]:
        if depth > _MAX_ARCHIVE_DEPTH:
            raise LiveStateError("live archive chain exceeds safety limit")
        entries: list[str] = []
        for item in _section_items(source, section):
            reference = archive_reference(item)
            if reference is None:
                entries.append(item)
                continue
            archive = _resolve_archive(agent_directory, reference)
            if archive in visited:
                raise LiveStateError(f"cyclic live archive reference: {reference}")
            visited.add(archive)
            entries.extend(collect(archive.read_text(encoding="utf-8"), depth + 1))
        return entries

    return collect(text, 0)


def render_live_sections(text: str, replacements: dict[str, list[str]]) -> str:
    lines = text.splitlines()
    output: list[str] = []
    index = 0
    seen: set[str] = set()
    while index < len(lines):
        line = lines[index]
        if not line.startswith("## "):
            output.append(line)
            index += 1
            continue
        title = line[3:].strip()
        output.append(line)
        index += 1
        if title in replacements:
            seen.add(title)
            output.extend(replacements[title])
            while index < len(lines) and not lines[index].startswith("## "):
                index += 1
            if index < len(lines):
                output.append("")
            continue
        while index < len(lines) and not lines[index].startswith("## "):
            output.append(lines[index])
            index += 1

    for title, body in replacements.items():
        if title not in seen:
            if output and output[-1].strip():
                output.append("")
            output.append(f"## {title}")
            output.extend(body)
    return "\n".join(output).rstrip() + "\n"


def replace_live_sections(path: pathlib.Path, replacements: dict[str, list[str]]) -> None:
    text = path.read_text(encoding="utf-8")
    atomic_write_text(path, render_live_sections(text, replacements))


def _next_archive_path(agent_directory: pathlib.Path, stamp: str) -> pathlib.Path:
    archive_directory = agent_directory / "archive"
    candidate = archive_directory / f"live-{stamp}.md"
    suffix = 2
    while candidate.exists():
        candidate = archive_directory / f"live-{stamp}-{suffix}.md"
        suffix += 1
    return candidate


def compact_live_snapshot(
    agent_directory: pathlib.Path,
    live_path: pathlib.Path,
    expected_text: str,
    stamp: str,
    sections: Iterable[str] = COMPACTED_SECTIONS,
) -> pathlib.Path:
    """Archive and compact only if live.md still matches the validated snapshot."""
    with file_lock(live_path):
        if live_path.read_text(encoding="utf-8") != expected_text:
            raise LiveStateError("live.md changed during compaction; retry with the latest state")
        archive = _next_archive_path(agent_directory, stamp)
        relative = archive.relative_to(agent_directory.parent).as_posix()
        compacted = render_live_sections(
            expected_text,
            {section: [archive_marker(relative)] for section in sections},
        )
        atomic_write_text(archive, expected_text.rstrip() + "\n")
        if live_path.read_text(encoding="utf-8") != expected_text:
            archive.unlink(missing_ok=True)
            raise LiveStateError("live.md changed during compaction; retry with the latest state")
        atomic_write_text(live_path, compacted)
    return archive
