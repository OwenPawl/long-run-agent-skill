"""Authoritative append-only ledger and mission filesystem layout."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ConflictError, LedgerError
from .policy import POLICY_VERSION, default_policy
from .util import atomic_write_json, atomic_write_text, canonical_json, deep_merge, file_lock, sha256, stable_id, utc_now

LEDGER_VERSION = "epistemic-ledger.v1"
TELEMETRY_VERSION = "retrieval-ledger.v1"
GENESIS_HASH = "0" * 64

LIVE_TEMPLATE = """# Live Control

## User Updates
- Add steering here. Newer instructions override older conflicting instructions.

## Current Goal
- {goal}

## Constraints
- None recorded yet.

## Agent Status
- Status: starting

## Interrupts / Corrections
- None recorded yet.

## Agent Questions
- None recorded yet.

## Next Actions
- None recorded yet.
"""


@dataclass(frozen=True)
class AgentPaths:
    root: Path
    agent: Path
    live: Path
    ledger: Path
    retrieval: Path
    policy: Path
    state: Path
    current: Path
    checkpoint: Path
    archive: Path

    @classmethod
    def for_root(cls, root: str | Path) -> "AgentPaths":
        resolved = Path(root).expanduser().resolve()
        agent = resolved / ".agent"
        return cls(
            root=resolved,
            agent=agent,
            live=agent / "live.md",
            ledger=agent / "ledger.jsonl",
            retrieval=agent / "retrieval.jsonl",
            policy=agent / "policy.json",
            state=agent / "state.sqlite",
            current=agent / "current.md",
            checkpoint=agent / "checkpoint.json",
            archive=agent / "archive",
        )


class LedgerStore:
    def __init__(self, root: str | Path):
        self.paths = AgentPaths.for_root(root)

    def initialize(self, goal: str = "Add the active goal here.", force: bool = False) -> dict[str, Any]:
        paths = self.paths
        paths.agent.mkdir(parents=True, exist_ok=True)
        paths.archive.mkdir(parents=True, exist_ok=True)
        if force:
            paths.ledger.unlink(missing_ok=True)
            paths.retrieval.unlink(missing_ok=True)
            paths.state.unlink(missing_ok=True)
            paths.checkpoint.unlink(missing_ok=True)
        for path, content in (
            (paths.live, LIVE_TEMPLATE.format(goal=goal or "Add the active goal here.")),
            (paths.ledger, ""),
            (paths.retrieval, ""),
            (paths.current, "# Current Epistemic Frontier\n\nNo semantic state recorded yet.\n"),
        ):
            if not path.exists():
                atomic_write_text(path, content)
        if not paths.policy.exists() or force:
            atomic_write_json(paths.policy, default_policy())
        return {
            "root": str(paths.root),
            "agent": str(paths.agent),
            "files": [
                str(paths.live),
                str(paths.ledger),
                str(paths.retrieval),
                str(paths.policy),
                str(paths.current),
            ],
        }

    def require_initialized(self) -> None:
        if not self.paths.ledger.is_file() or not self.paths.policy.is_file():
            raise LedgerError(f"mission is not initialized: {self.paths.agent}")

    def load_policy(self) -> dict[str, Any]:
        self.require_initialized()
        try:
            configured = json.loads(self.paths.policy.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LedgerError(f"invalid policy.json: {exc}") from exc
        if configured.get("schema_version") != POLICY_VERSION:
            raise LedgerError(f"unsupported policy schema: {configured.get('schema_version')!r}")
        return deep_merge(default_policy(), configured)

    def read_transactions(self, verify: bool = True) -> list[dict[str, Any]]:
        self.require_initialized()
        transactions: list[dict[str, Any]] = []
        previous = GENESIS_HASH
        for line_number, raw in enumerate(self.paths.ledger.read_text(encoding="utf-8").splitlines(), 1):
            if not raw.strip():
                continue
            try:
                transaction = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise LedgerError(f"ledger line {line_number} is invalid JSON: {exc}") from exc
            if verify:
                self._verify_transaction(transaction, len(transactions) + 1, previous)
            previous = transaction.get("transaction_hash", "")
            transactions.append(transaction)
        return transactions

    def read_telemetry_transactions(self, verify: bool = True) -> list[dict[str, Any]]:
        self.require_initialized()
        if not self.paths.retrieval.is_file():
            raise LedgerError(f"retrieval telemetry ledger is missing: {self.paths.retrieval}")
        transactions: list[dict[str, Any]] = []
        previous = GENESIS_HASH
        for line_number, raw in enumerate(self.paths.retrieval.read_text(encoding="utf-8").splitlines(), 1):
            if not raw.strip():
                continue
            try:
                transaction = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise LedgerError(f"retrieval line {line_number} is invalid JSON: {exc}") from exc
            if verify:
                if transaction.get("schema_version") != TELEMETRY_VERSION:
                    raise LedgerError(f"retrieval transaction {line_number} has unsupported schema")
                if transaction.get("sequence") != len(transactions) + 1:
                    raise LedgerError(f"retrieval sequence mismatch at {line_number}")
                if transaction.get("previous_hash") != previous:
                    raise LedgerError(f"retrieval hash chain mismatch at {line_number}")
                recorded = transaction.get("transaction_hash", "")
                unsigned = {key: value for key, value in transaction.items() if key != "transaction_hash"}
                if recorded != sha256(unsigned):
                    raise LedgerError(f"retrieval content hash mismatch at {line_number}")
                if not transaction.get("events"):
                    raise LedgerError(f"retrieval transaction {line_number} contains no events")
            previous = transaction.get("transaction_hash", "")
            transactions.append(transaction)
        return transactions

    @staticmethod
    def _verify_transaction(transaction: dict[str, Any], sequence: int, previous: str) -> None:
        if transaction.get("schema_version") != LEDGER_VERSION:
            raise LedgerError(f"transaction {sequence} has unsupported schema")
        if transaction.get("sequence") != sequence:
            raise LedgerError(f"transaction sequence mismatch at {sequence}")
        if transaction.get("previous_hash") != previous:
            raise LedgerError(f"transaction hash chain mismatch at {sequence}")
        recorded = transaction.get("transaction_hash", "")
        unsigned = {key: value for key, value in transaction.items() if key != "transaction_hash"}
        if recorded != sha256(unsigned):
            raise LedgerError(f"transaction content hash mismatch at {sequence}")
        if not isinstance(transaction.get("operations"), list) or not transaction["operations"]:
            raise LedgerError(f"transaction {sequence} contains no operations")

    def append(
        self,
        operations: list[dict[str, Any]],
        *,
        run_id: str = "",
        expected_generation: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not operations:
            raise LedgerError("refusing to append an empty semantic transaction")
        self.require_initialized()
        with file_lock(self.paths.ledger):
            transactions = self.read_transactions(verify=True)
            sequence = len(transactions) + 1
            if expected_generation is not None and expected_generation != len(transactions):
                raise ConflictError(
                    f"ledger generation changed: expected {expected_generation}, found {len(transactions)}"
                )
            previous = transactions[-1]["transaction_hash"] if transactions else GENESIS_HASH
            semantic_hash = sha256({"previous": previous, "sequence": sequence, "operations": operations})
            transaction = {
                "schema_version": LEDGER_VERSION,
                "sequence": sequence,
                "transaction_id": stable_id("tx", semantic_hash),
                "previous_hash": previous,
                "committed_at": utc_now(),
                "run_id": run_id,
                "operations": operations,
                "metadata": metadata or {},
            }
            transaction["transaction_hash"] = sha256(transaction)
            with self.paths.ledger.open("a", encoding="utf-8") as handle:
                handle.write(canonical_json(transaction) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return transaction

    def append_telemetry(
        self,
        events: list[dict[str, Any]],
        *,
        semantic_generation: int,
        semantic_transaction_id: str = "",
    ) -> dict[str, Any]:
        if not events:
            raise LedgerError("refusing to append an empty telemetry transaction")
        self.require_initialized()
        with file_lock(self.paths.retrieval):
            transactions = self.read_telemetry_transactions(verify=True)
            sequence = len(transactions) + 1
            previous = transactions[-1]["transaction_hash"] if transactions else GENESIS_HASH
            content = {
                "previous": previous,
                "sequence": sequence,
                "semantic_generation": semantic_generation,
                "events": events,
            }
            transaction = {
                "schema_version": TELEMETRY_VERSION,
                "sequence": sequence,
                "transaction_id": stable_id("rtx", content),
                "previous_hash": previous,
                "committed_at": utc_now(),
                "semantic_generation": semantic_generation,
                "semantic_transaction_id": semantic_transaction_id,
                "events": events,
            }
            transaction["transaction_hash"] = sha256(transaction)
            with self.paths.retrieval.open("a", encoding="utf-8") as handle:
                handle.write(canonical_json(transaction) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return transaction

    def operations(self, transactions: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        flattened: list[dict[str, Any]] = []
        for transaction in transactions if transactions is not None else self.read_transactions():
            for index, operation in enumerate(transaction["operations"]):
                item = dict(operation)
                item["_generation"] = transaction["sequence"]
                item["_transaction_id"] = transaction["transaction_id"]
                item["_operation_index"] = index
                item["_committed_at"] = transaction["committed_at"]
                flattened.append(item)
        return flattened

    def telemetry_operations(
        self, transactions: list[dict[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        flattened: list[dict[str, Any]] = []
        source = transactions if transactions is not None else self.read_telemetry_transactions()
        for transaction in source:
            for index, event in enumerate(transaction["events"]):
                item = dict(event)
                item["_generation"] = transaction["semantic_generation"]
                item["_transaction_id"] = transaction["transaction_id"]
                item["_operation_index"] = index
                item["_committed_at"] = transaction["committed_at"]
                item["_telemetry_sequence"] = transaction["sequence"]
                flattened.append(item)
        return flattened

    def all_operations(self) -> list[dict[str, Any]]:
        semantic = [dict(item, _stream_order=0) for item in self.operations()]
        telemetry = [dict(item, _stream_order=1) for item in self.telemetry_operations()]
        return sorted(
            semantic + telemetry,
            key=lambda item: (
                item.get("_generation", 0),
                item.get("_stream_order", 0),
                item.get("_telemetry_sequence", 0),
                item.get("_operation_index", 0),
            ),
        )

    def ledger_head(self) -> dict[str, Any]:
        transactions = self.read_transactions()
        if not transactions:
            return {"generation": 0, "transaction_hash": GENESIS_HASH, "transaction_id": ""}
        last = transactions[-1]
        return {
            "generation": last["sequence"],
            "transaction_hash": last["transaction_hash"],
            "transaction_id": last["transaction_id"],
        }
