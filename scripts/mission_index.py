#!/usr/bin/env python3
"""Derived SQLite index for mission harness state.

Markdown and JSON/JSONL remain the source of truth. This module builds a small
search index that can be deleted and rebuilt from `.agent/` records.
"""

from __future__ import annotations

import json
import pathlib
import re
import sqlite3
import time
from typing import Any

from mission_evidence import upgraded_claims


DB_NAME = "mission_index.sqlite"
MARKDOWN_FILES = ["live.md", "current_state.md", "constitution.md", "known_failures.md", "decisions.md"]


def agent_dir(root: pathlib.Path) -> pathlib.Path:
    return root.expanduser().resolve() / ".agent"


def database_path(root: pathlib.Path) -> pathlib.Path:
    return agent_dir(root) / DB_NAME


def read_text(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def load_json(path: pathlib.Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(stringify(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, indent=2, sort_keys=True)
    return str(value)


def compact_payload(payload: dict[str, Any], keys: list[str]) -> str:
    lines = []
    for key in keys:
        value = payload.get(key)
        if value in (None, "", []):
            continue
        lines.append(f"{key}: {stringify(value)}")
    return "\n".join(lines)


def fts_body(record: dict[str, Any]) -> str:
    """Include stable record identifiers in full-text search without changing public previews."""
    return "\n".join(
        part
        for part in [
            str(record.get("id", "")),
            str(record.get("source_id", "")),
            str(record.get("kind", "")),
            str(record.get("status", "")),
            str(record.get("category", "")),
            str(record.get("path", "")),
            str(record.get("body", "")),
        ]
        if part
    )


def make_record(
    *,
    kind: str,
    source_file: str,
    source_id: str,
    title: str,
    body: str,
    payload: dict[str, Any],
    run_id: str = "",
    timestamp: str = "",
    status: str = "",
    category: str = "",
    path: str = "",
) -> dict[str, Any]:
    record_id = f"{source_file}:{kind}:{source_id or title}"
    return {
        "id": record_id,
        "kind": kind,
        "source_file": source_file,
        "source_id": source_id,
        "run_id": run_id,
        "timestamp": timestamp,
        "title": title,
        "body": body,
        "status": status,
        "category": category,
        "path": path,
        "payload_json": json.dumps(payload, sort_keys=True),
    }


def iter_records(root: pathlib.Path) -> list[dict[str, Any]]:
    directory = agent_dir(root)
    records: list[dict[str, Any]] = []

    for name in MARKDOWN_FILES:
        body = read_text(directory / name)
        if body:
            records.append(
                make_record(
                    kind="markdown",
                    source_file=f".agent/{name}",
                    source_id=name,
                    title=name,
                    body=body,
                    payload={"path": f".agent/{name}", "body": body},
                )
            )

    for run in read_jsonl(directory / "runs.jsonl"):
        event = str(run.get("event", "run"))
        run_id = str(run.get("run_id", ""))
        title = str(run.get("goal") or run.get("outcome") or run_id)
        body = compact_payload(
            run,
            [
                "goal",
                "constraints",
                "outcome",
                "summary",
                "commands_run",
                "tests_run",
                "files_changed",
                "failures",
                "claims_touched",
                "artifacts_produced",
                "evidence_relations",
                "next_actions",
                "no_verification_reason",
            ],
        )
        records.append(
            make_record(
                kind=event,
                source_file=".agent/runs.jsonl",
                source_id=run_id,
                run_id=run_id,
                timestamp=str(run.get("timestamp", "")),
                title=title,
                body=body,
                status=str(run.get("status", "")),
                payload=run,
            )
        )

    for item in read_jsonl(directory / "friction.jsonl"):
        records.append(
            make_record(
                kind="friction",
                source_file=".agent/friction.jsonl",
                source_id=str(item.get("id", "")),
                run_id=str(item.get("source_run_id", "")),
                timestamp=str(item.get("timestamp", "")),
                title=str(item.get("description", "")),
                body=compact_payload(item, ["impact", "proposed_harness_need", "severity", "status"]),
                status=str(item.get("status", "")),
                category=str(item.get("category", "")),
                payload=item,
            )
        )

    claims_data = load_json(directory / "claims.json", {"schema_version": "claims.v2", "claims": []})
    claims = upgraded_claims(claims_data)[0].get("claims", [])
    latest_claims: dict[str, dict[str, Any]] = {}
    for claim in claims if isinstance(claims, list) else []:
        if not isinstance(claim, dict):
            continue
        latest_claims[str(claim.get("id", ""))] = claim
        records.append(
            make_record(
                kind="claim_revision",
                source_file=".agent/claims.json",
                source_id=str(claim.get("revision_id", "")),
                run_id=str(claim.get("source_run_id", "")),
                timestamp=str(claim.get("timestamp", "")),
                title=str(claim.get("claim", "")),
                body=compact_payload(
                    claim,
                    [
                        "id",
                        "revision",
                        "source_path",
                        "source_kind",
                        "verification_command",
                        "last_checked_at",
                        "confidence",
                        "notes",
                        "evidence_relation_ids",
                    ],
                ),
                status=str(claim.get("status", "")),
                path=str(claim.get("source_path", "")),
                payload=claim,
            )
        )

    for claim_id, claim in latest_claims.items():
        records.append(
            make_record(
                kind="claim",
                source_file=".agent/claims.json",
                source_id=claim_id,
                run_id=str(claim.get("source_run_id", "")),
                timestamp=str(claim.get("timestamp", "")),
                title=str(claim.get("claim", "")),
                body=compact_payload(
                    claim,
                    [
                        "revision_id",
                        "revision",
                        "source_path",
                        "source_kind",
                        "verification_command",
                        "last_checked_at",
                        "confidence",
                        "notes",
                        "evidence_relation_ids",
                    ],
                ),
                status=str(claim.get("status", "")),
                path=str(claim.get("source_path", "")),
                payload=claim,
            )
        )

    artifacts = load_json(directory / "artifacts.json", {"artifacts": []}).get("artifacts", [])
    for artifact in artifacts if isinstance(artifacts, list) else []:
        if not isinstance(artifact, dict):
            continue
        records.append(
            make_record(
                kind="artifact",
                source_file=".agent/artifacts.json",
                source_id=str(artifact.get("id", "")),
                run_id=str(artifact.get("source_run_id", "")),
                timestamp=str(artifact.get("timestamp", "")),
                title=str(artifact.get("description", "")),
                body=compact_payload(artifact, ["path", "kind", "verification_command", "notes"]),
                path=str(artifact.get("path", "")),
                payload=artifact,
            )
        )

    for relation in read_jsonl(directory / "evidence_relations.jsonl"):
        records.append(
            make_record(
                kind="evidence_relation",
                source_file=".agent/evidence_relations.jsonl",
                source_id=str(relation.get("id", "")),
                run_id=str(relation.get("source_run_id", "")),
                timestamp=str(relation.get("timestamp", "")),
                title=str(relation.get("relation", "")),
                body=compact_payload(relation, ["source", "target", "confidence", "notes"]),
                category=str(relation.get("relation", "")),
                payload=relation,
            )
        )

    return records


def with_unique_ids(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    unique: list[dict[str, Any]] = []
    for record in records:
        base_id = str(record["id"])
        count = counts.get(base_id, 0) + 1
        counts[base_id] = count
        if count > 1:
            record = {**record, "id": f"{base_id}#{count}"}
        unique.append(record)
    return unique


def ensure_schema(conn: sqlite3.Connection) -> bool:
    conn.executescript(
        """
        DROP TABLE IF EXISTS records;
        DROP TABLE IF EXISTS records_fts;
        CREATE TABLE records (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            source_file TEXT NOT NULL,
            source_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL,
            category TEXT NOT NULL,
            path TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE INDEX records_kind_idx ON records(kind);
        CREATE INDEX records_run_id_idx ON records(run_id);
        CREATE INDEX records_timestamp_idx ON records(timestamp);
        """
    )
    try:
        conn.execute("CREATE VIRTUAL TABLE records_fts USING fts5(id UNINDEXED, title, body)")
    except sqlite3.OperationalError:
        return False
    return True


def build_database(db_path: pathlib.Path, records: list[dict[str, Any]]) -> bool:
    with sqlite3.connect(db_path) as conn:
        fts_enabled = ensure_schema(conn)
        for record in records:
            conn.execute(
                """
                INSERT INTO records
                (id, kind, source_file, source_id, run_id, timestamp, title, body, status, category, path, payload_json)
                VALUES
                (:id, :kind, :source_file, :source_id, :run_id, :timestamp, :title, :body, :status, :category, :path, :payload_json)
                """,
                record,
            )
            if fts_enabled:
                conn.execute(
                    "INSERT INTO records_fts (id, title, body) VALUES (?, ?, ?)",
                    (record["id"], record["title"], fts_body(record)),
                )
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('record_count', ?)", (str(len(records)),))
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('fts_enabled', ?)", (str(fts_enabled).lower(),))
    return fts_enabled


def rebuild_index(root: pathlib.Path) -> dict[str, Any]:
    root = pathlib.Path(root)
    directory = agent_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    db_path = database_path(root)
    records = with_unique_ids(iter_records(root))
    temp_path = db_path.with_name(f"{db_path.name}.tmp-{int(time.time() * 1000)}")
    try:
        fts_enabled = build_database(temp_path, records)
        temp_path.replace(db_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return {"ok": True, "db_path": str(db_path), "record_count": len(records), "fts_enabled": fts_enabled}


def db_exists(root: pathlib.Path) -> bool:
    return database_path(pathlib.Path(root)).exists()


def escape_like(query: str) -> str:
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def query_tokens(query: str) -> list[str]:
    return [token for token in re.findall(r"[A-Za-z0-9_./:-]+", query) if token]


def fetch_rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def like_any_rows(conn: sqlite3.Connection, query: str, limit: int, kind: str) -> list[dict[str, Any]]:
    tokens = query_tokens(query)
    if not tokens:
        return []
    clauses = []
    params: list[Any] = []
    for token in tokens:
        pattern = f"%{escape_like(token)}%"
        clauses.append(
            "("
            "title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\' OR path LIKE ? ESCAPE '\\' OR "
            "source_id LIKE ? ESCAPE '\\' OR status LIKE ? ESCAPE '\\' OR category LIKE ? ESCAPE '\\'"
            ")"
        )
        params.extend([pattern, pattern, pattern, pattern, pattern, pattern])
    kind_clause = "AND kind = ?" if kind else ""
    if kind:
        params.append(kind)
    params.append(limit)
    return fetch_rows(
        conn,
        f"""
        SELECT *
        FROM records
        WHERE ({' OR '.join(clauses)}) {kind_clause}
        ORDER BY timestamp DESC, id DESC
        LIMIT ?
        """,
        tuple(params),
    )


def run_search_query(conn: sqlite3.Connection, query: str, limit: int, kind: str) -> tuple[list[dict[str, Any]], bool]:
    kinds = "AND records.kind = ?" if kind else ""
    try:
        params: tuple[Any, ...] = (query, limit) if not kind else (query, kind, limit)
        rows = fetch_rows(
            conn,
            f"""
            SELECT records.*
            FROM records_fts
            JOIN records ON records.id = records_fts.id
            WHERE records_fts MATCH ? {kinds}
            ORDER BY rank
            LIMIT ?
            """,
            params,
        )
        if rows:
            return rows, True
    except sqlite3.OperationalError:
        pass
    return like_any_rows(conn, query, limit, kind), False


def search_index(root: pathlib.Path, query: str, limit: int = 20, kind: str = "") -> dict[str, Any]:
    root = pathlib.Path(root)
    if not db_exists(root):
        rebuild_index(root)
    db_path = database_path(root)
    rebuilt_after_error = False
    try:
        with sqlite3.connect(db_path) as conn:
            rows, used_fts = run_search_query(conn, query, limit, kind)
    except sqlite3.Error:
        rebuild_index(root)
        rebuilt_after_error = True
        try:
            with sqlite3.connect(db_path) as conn:
                rows, used_fts = run_search_query(conn, query, limit, kind)
        except sqlite3.Error as exc:
            return {
                "ok": False,
                "db_path": str(db_path),
                "query": query,
                "kind": kind,
                "rebuilt_after_error": rebuilt_after_error,
                "used_fts": False,
                "error": str(exc),
                "results": [],
            }
    return {
        "ok": True,
        "db_path": str(db_path),
        "query": query,
        "kind": kind,
        "rebuilt_after_error": rebuilt_after_error,
        "used_fts": used_fts,
        "results": [public_result(row) for row in rows],
    }


def public_result(row: dict[str, Any]) -> dict[str, Any]:
    body = row.get("body", "")
    return {
        "id": row.get("id", ""),
        "kind": row.get("kind", ""),
        "source_file": row.get("source_file", ""),
        "source_id": row.get("source_id", ""),
        "run_id": row.get("run_id", ""),
        "timestamp": row.get("timestamp", ""),
        "title": row.get("title", ""),
        "status": row.get("status", ""),
        "category": row.get("category", ""),
        "path": row.get("path", ""),
        "body_preview": body[:240],
    }


def index_status(root: pathlib.Path) -> dict[str, Any]:
    root = pathlib.Path(root)
    db_path = database_path(root)
    if not db_path.exists():
        return {"ok": True, "db_path": str(db_path), "exists": False, "record_count": 0, "fts_enabled": False}
    try:
        with sqlite3.connect(db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            fts_enabled = bool(conn.execute("SELECT name FROM sqlite_master WHERE name = 'records_fts'").fetchone())
    except sqlite3.Error as exc:
        return {
            "ok": False,
            "db_path": str(db_path),
            "exists": True,
            "record_count": 0,
            "fts_enabled": False,
            "error": str(exc),
            "repair_command": "index rebuild",
        }
    return {"ok": True, "db_path": str(db_path), "exists": True, "record_count": count, "fts_enabled": fts_enabled}
