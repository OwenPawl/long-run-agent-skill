"""Disposable SQLite materialization and readable current-state rendering."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .recall import entity_catalog
from .store import LedgerStore
from .util import atomic_write_text, canonical_json, sha256


def state_hash(state: dict[str, Any]) -> str:
    return sha256(state)


def _entity_state(entity: dict[str, Any]) -> str:
    if entity.get("entity_type") == "claim":
        derived = entity.get("derived", {})
        return "/".join(filter(None, [derived.get("support_state"), derived.get("applicability_state")]))
    if entity.get("entity_type") in {"argument", "attack"}:
        return "active" if entity.get("active") else "inactive"
    if entity.get("entity_type") == "question":
        return entity.get("derived_status", "opened")
    if entity.get("entity_type") == "decision":
        return "basis_changed" if entity.get("basis_changed") else "current"
    return entity.get("status", "current")


def _entity_name(entity: dict[str, Any]) -> str:
    return str(
        entity.get("name")
        or entity.get("proposition")
        or entity.get("question")
        or entity.get("choice")
        or entity.get("description")
        or entity.get("id")
    )


def write_sqlite(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode=DELETE;
            PRAGMA synchronous=FULL;
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE entities (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                subject TEXT NOT NULL,
                derived_state TEXT NOT NULL,
                generation INTEGER NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE edges (source TEXT NOT NULL, relation TEXT NOT NULL, target TEXT NOT NULL);
            CREATE INDEX edges_source ON edges(source);
            CREATE INDEX edges_target ON edges(target);
            CREATE TABLE diagnostics (
                id TEXT PRIMARY KEY,
                code TEXT NOT NULL,
                severity TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE retrieval_events (
                id TEXT PRIMARY KEY,
                event TEXT NOT NULL,
                generation INTEGER NOT NULL,
                context_key TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            """
        )
        catalog = entity_catalog(state)
        rows = []
        for entity_id, entity in sorted(catalog.items()):
            rows.append(
                (
                    entity_id,
                    entity["entity_type"],
                    _entity_name(entity),
                    str(entity.get("description") or entity.get("proposition") or ""),
                    str(entity.get("subject", "")),
                    _entity_state(entity),
                    int(entity.get("generation", 0)),
                    canonical_json(entity),
                )
            )
        connection.executemany("INSERT INTO entities VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
        connection.executemany(
            "INSERT INTO edges VALUES (:source, :relation, :target)", state.get("edges", [])
        )
        connection.executemany(
            "INSERT INTO diagnostics VALUES (?, ?, ?, ?)",
            [
                (
                    item["id"],
                    item["code"],
                    item.get("severity", "info"),
                    canonical_json(item),
                )
                for item in state.get("diagnostics", [])
            ],
        )
        connection.executemany(
            "INSERT INTO retrieval_events VALUES (?, ?, ?, ?, ?)",
            [
                (
                    item.get("id") or f"retrieval_{index}",
                    item.get("event", ""),
                    int(item.get("generation", 0)),
                    item.get("context_key", ""),
                    canonical_json(item),
                )
                for index, item in enumerate(state.get("retrieval_events", []))
            ],
        )
        metadata = {
            "schema_version": "epistemic-state-sqlite.v1",
            "generation": str(state.get("generation", 0)),
            "state_hash": state_hash(state),
            "entity_count": str(len(rows)),
            "diagnostic_count": str(len(state.get("diagnostics", []))),
        }
        connection.executemany("INSERT INTO metadata VALUES (?, ?)", sorted(metadata.items()))
        try:
            connection.execute(
                "CREATE VIRTUAL TABLE entity_fts USING fts5(id UNINDEXED, name, description, subject)"
            )
            connection.executemany(
                "INSERT INTO entity_fts VALUES (?, ?, ?, ?)",
                [(row[0], row[2], row[3], row[4]) for row in rows],
            )
            connection.execute("INSERT INTO metadata VALUES ('fts_available', 'true')")
        except sqlite3.OperationalError:
            connection.execute("INSERT INTO metadata VALUES ('fts_available', 'false')")
        connection.commit()
        connection.close()
        os.replace(temporary, path)
    finally:
        try:
            connection.close()
        except sqlite3.Error:
            pass
        temporary.unlink(missing_ok=True)


def _bullet(label: str, identifier: str, state: str = "") -> str:
    suffix = f" [{state}]" if state else ""
    return f"- {label} (`{identifier}`){suffix}"


def render_current(state: dict[str, Any]) -> str:
    mission = state.get("mission", {})
    lines = [
        "# Current Epistemic Frontier",
        "",
        f"- Generation: {state.get('generation', 0)}",
        f"- State hash: `{state_hash(state)}`",
        f"- Goal: {mission.get('goal', 'Not recorded')}",
        f"- Steering: {mission.get('steering', 'None recorded')}",
        "",
        "## Current Claims",
    ]
    claims = state.get("claims", {})
    for claim_id, claim in sorted(claims.items()):
        if claim.get("commitment") == "withdrawn":
            continue
        derived = claim.get("derived", {})
        label = _entity_name({**claim, "id": claim_id})
        state_label = "/".join(
            filter(None, [derived.get("support_state"), derived.get("applicability_state")])
        )
        lines.append(_bullet(label, claim_id, state_label))
    if not any(claim.get("commitment") != "withdrawn" for claim in claims.values()):
        lines.append("- None recorded.")

    lines.extend(["", "## Open Questions"])
    questions = state.get("questions", {})
    open_questions = [
        (question_id, question)
        for question_id, question in sorted(questions.items())
        if question.get("derived_status") not in {"resolved"}
    ]
    for question_id, question in open_questions:
        lines.append(_bullet(_entity_name({**question, "id": question_id}), question_id, question.get("derived_status", "opened")))
    if not open_questions:
        lines.append("- None recorded.")

    lines.extend(["", "## Active Decisions"])
    decisions = state.get("decisions", {})
    for decision_id, decision in sorted(decisions.items()):
        status = "basis changed" if decision.get("basis_changed") else "current"
        lines.append(_bullet(_entity_name({**decision, "id": decision_id}), decision_id, status))
    if not decisions:
        lines.append("- None recorded.")

    lines.extend(["", "## Important Diagnostics"])
    important = [
        item for item in state.get("diagnostics", []) if item.get("severity") in {"error", "warning"}
    ]
    for item in important[:20]:
        lines.append(f"- `{item['code']}`: {item.get('why', '')} ({', '.join(item.get('entities', []))})")
    if not important:
        lines.append("- None.")

    lines.extend(["", "## Revalidation"])
    revalidation = [
        (claim_id, claim)
        for claim_id, claim in sorted(claims.items())
        if claim.get("derived", {}).get("applicability_state") == "revalidation_required"
    ]
    for claim_id, claim in revalidation:
        plans = claim.get("derived", {}).get("revalidation_plan", [])
        lowest = plans[0]["estimated_cost"] if plans else "unknown"
        lines.append(f"- `{claim_id}` requires revalidation; lowest known bounded cost: {lowest}.")
    if not revalidation:
        lines.append("- None.")
    return "\n".join(lines) + "\n"


def write_views(store: LedgerStore, state: dict[str, Any]) -> dict[str, Any]:
    write_sqlite(store.paths.state, state)
    atomic_write_text(store.paths.current, render_current(state))
    return {
        "generation": state.get("generation", 0),
        "state_hash": state_hash(state),
        "sqlite": str(store.paths.state),
        "current": str(store.paths.current),
    }


def sqlite_metadata(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return dict(connection.execute("SELECT key, value FROM metadata"))
    finally:
        connection.close()
