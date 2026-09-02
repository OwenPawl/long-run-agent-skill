"""Bounded worker context, expansion, feedback, and procedural queries."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .errors import SemanticError
from .recall import entity_catalog, operation_entity_ids
from .util import canonical_json, random_id, stable_id


def _event(kind: str, **data: Any) -> dict[str, Any]:
    return {"type": kind, "data": data}


def _conclusion_id(argument: dict[str, Any]) -> str:
    conclusion = argument.get("conclusion", {})
    return conclusion.get("id") or conclusion.get("claim_id", "")


class ContextQueryMixin:
    """Methods mixed into EpistemicCompiler without a second state authority."""

    store: Any

    def _context_entries(self, state: dict[str, Any], since_generation: int) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        catalog = entity_catalog(state)
        changed = [item for item in catalog.values() if item.get("generation", 0) > since_generation]
        for entity in sorted(changed, key=lambda item: (-item.get("generation", 0), item["id"])):
            entries.append(
                {
                    "section": "current_work",
                    "entity_id": entity["id"],
                    "entity_type": entity["entity_type"],
                    "name": entity.get("name")
                    or entity.get("proposition")
                    or entity.get("description")
                    or entity["id"],
                }
            )
        for claim_id, claim in sorted(state.get("claims", {}).items()):
            derived = claim.get("derived", {})
            if (
                derived.get("support_state") in {"supported", "contested"}
                or derived.get("applicability_state") == "revalidation_required"
            ):
                entries.append(
                    {
                        "section": "current_claims",
                        "entity_id": claim_id,
                        "name": claim.get("name") or claim.get("proposition"),
                        "state": derived,
                    }
                )
        for question_id, question in sorted(state.get("questions", {}).items()):
            if question.get("derived_status") != "resolved":
                entries.append(
                    {
                        "section": "open_questions",
                        "entity_id": question_id,
                        "name": question.get("name") or question.get("question"),
                        "state": question.get("derived_status"),
                    }
                )
        for decision_id, decision in sorted(state.get("decisions", {}).items()):
            if decision.get("basis_changed"):
                entries.append(
                    {
                        "section": "affected_decisions",
                        "entity_id": decision_id,
                        "name": decision.get("name") or decision.get("choice"),
                        "basis_changed": True,
                    }
                )
        for diagnostic in state.get("diagnostics", []):
            if diagnostic.get("severity") in {"error", "warning"}:
                entries.append({"section": "diagnostics", **diagnostic})
        for event in reversed(state.get("retrieval_events", [])):
            if event.get("event") != "retrieval.surfaced":
                continue
            for entity_id in event.get("entity_ids", []):
                entity = catalog.get(entity_id)
                if entity:
                    entries.append(
                        {
                            "section": "historical_recall",
                            "entity_id": entity_id,
                            "entity_type": entity["entity_type"],
                            "name": entity.get("name") or entity.get("proposition") or entity_id,
                            "reference": {"action": "expand", "entity_id": entity_id},
                        }
                    )
            break
        return entries

    def context(
        self,
        *,
        cursor: dict[str, Any] | None = None,
        since_generation: int | None = None,
        max_chars: int | None = None,
        max_entities: int | None = None,
    ) -> dict[str, Any]:
        state = self.state()
        checkpoint = self._read_checkpoint(required=False)
        boundary = checkpoint.get("context_generation", 0) if since_generation is None else since_generation
        settings = self.store.load_policy()["context"]
        char_limit = max_chars or settings["max_chars"]
        entity_limit = max_entities or settings["max_entities"]
        offset = int((cursor or {}).get("offset", 0))
        if cursor and cursor.get("generation") != state["generation"]:
            raise SemanticError("context cursor is stale; request a fresh context pack")
        entries = self._context_entries(state, boundary)
        selected: list[dict[str, Any]] = []
        used = 0
        for entry in entries[offset:]:
            size = len(canonical_json(entry))
            if len(selected) >= entity_limit or used + size > char_limit:
                break
            selected.append(entry)
            used += size
        consumed = offset + len(selected)
        complete = consumed >= len(entries)
        response = {
            "schema_version": "worker-context.v1",
            "generation": state["generation"],
            "goal": state.get("mission", {}).get("goal", ""),
            "steering": state.get("mission", {}).get("steering", ""),
            "since_generation": boundary,
            "entries": selected,
            "budget": {"max_chars": char_limit, "max_entities": entity_limit, "used_chars": used},
            "complete": complete,
            "continuation": None
            if complete
            else {"generation": state["generation"], "offset": consumed},
            "warning": "correctness-relevant context remains; continue with the cursor"
            if not complete
            else "",
        }
        telemetry = _event(
            "retrieval.context_generated",
            id=random_id("context"),
            context_key=stable_id(
                "context",
                {
                    "generation": state["generation"],
                    "since_generation": boundary,
                    "offset": offset,
                },
            ),
            entity_ids=[
                entry["entity_id"] for entry in selected if entry.get("entity_id")
            ],
            used_chars=used,
            max_chars=char_limit,
            entry_count=len(selected),
            max_entities=entity_limit,
            complete=complete,
            raw_evidence_included=False,
        )
        transaction = self._record_telemetry([telemetry])
        response["telemetry_transaction_id"] = transaction["transaction_id"]
        return response

    def expand(
        self, entity_id: str, representation: str = "structure", max_depth: int | None = None
    ) -> dict[str, Any]:
        state = self.state()
        catalog = entity_catalog(state)
        if entity_id not in catalog:
            raise SemanticError(f"unknown entity: {entity_id}")
        depth = max_depth or self.store.load_policy()["context"]["max_proof_depth"]
        frontier, seen, related = [entity_id], {entity_id}, []
        for _ in range(depth):
            next_frontier: list[str] = []
            for edge in state.get("edges", []):
                if edge["source"] in frontier or edge["target"] in frontier:
                    related.append(edge)
                    other = edge["target"] if edge["source"] in frontier else edge["source"]
                    if other not in seen:
                        seen.add(other)
                        next_frontier.append(other)
            frontier = next_frontier
            if not frontier:
                break
        telemetry = _event(
            "retrieval.expanded",
            id=random_id("retrieval_expand"),
            context_key=f"expand:{entity_id}",
            entity_ids=[entity_id],
            representation=representation,
        )
        telemetry_transaction = self._record_telemetry([telemetry])
        return {
            "entity": catalog[entity_id],
            "representation": representation,
            "relations": related,
            "expanded_entity_ids": sorted(seen),
            "max_depth": depth,
            "complete": not frontier,
            "warning": "proof expansion depth bound reached" if frontier else "",
            "telemetry_transaction_id": telemetry_transaction["transaction_id"],
        }

    def feedback(self, event_id: str, entity_id: str, action: str) -> dict[str, Any]:
        state = self.state()
        if action == "dismissed":
            source_event = next(
                (event for event in state.get("retrieval_events", []) if event.get("id") == event_id),
                None,
            )
            if not source_event:
                raise SemanticError(f"unknown retrieval event: {event_id}")
            event = _event(
                "retrieval.dismissed",
                id=random_id("retrieval_dismissal"),
                source_event_id=event_id,
                context_key=source_event.get("context_key", ""),
                entity_ids=[entity_id],
            )
            transaction = self._record_telemetry([event])
            return {
                "status": "success",
                "committed_semantics": [],
                "telemetry": {"transaction_id": transaction["transaction_id"], "events": [event]},
                "scope": "retrieval_context_only",
            }
        suggestion = state.get("suggestions", {}).get(event_id)
        if not suggestion or action not in {"accepted", "rejected"}:
            raise SemanticError("feedback action must be dismissed, accepted, or rejected")
        event = _event(
            f"suggestion.{action}", id=event_id, entity_ids=[entity_id] if entity_id else []
        )
        if action == "accepted":
            result = self.apply(
                {
                    "operations": [
                        _event(
                            "relation.asserted",
                            id=stable_id("relation", suggestion),
                            source=suggestion["source"],
                            target=suggestion["target"],
                            relation=suggestion["relation"],
                            source_suggestion_id=event_id,
                        )
                    ]
                }
            )
            transaction = self._record_telemetry([event])
            result["suggestion_feedback"] = {
                "status": "accepted",
                "telemetry_transaction_id": transaction["transaction_id"],
            }
            return result
        transaction = self._record_telemetry([event])
        return {
            "status": "success",
            "committed_semantics": [],
            "suggestion_feedback": {
                "status": "rejected",
                "telemetry_transaction_id": transaction["transaction_id"],
            },
        }

    def changes_since(self, generation: int) -> list[dict[str, Any]]:
        changes = []
        for item in self.store.operations():
            if item.get("_generation", 0) > generation:
                changes.append(
                    {
                        "generation": item["_generation"],
                        "transaction_id": item["_transaction_id"],
                        "type": item["type"],
                        "entity_ids": sorted(operation_entity_ids([item])),
                    }
                )
        return changes

    def query(
        self,
        kind: str,
        entity_id: str = "",
        *,
        text: str = "",
        cursor: dict[str, Any] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        state = self.state()
        catalog = entity_catalog(state)
        if kind == "belief":
            claim = state.get("claims", {}).get(entity_id)
            if not claim:
                raise SemanticError(f"unknown claim: {entity_id}")
            result: Any = claim.get("derived", {})
        elif kind == "why":
            claim = state.get("claims", {}).get(entity_id, {})
            result = [
                state["arguments"][item]
                for item in claim.get("derived", {}).get("supporting_arguments", [])
            ]
        elif kind == "why-not":
            result = [
                item
                for item in state.get("arguments", {}).values()
                if _conclusion_id(item) == entity_id and not item.get("active")
            ]
        elif kind == "defeaters":
            argument_ids = {entity_id}
            if entity_id in state.get("claims", {}):
                argument_ids.update(
                    item_id
                    for item_id, item in state["arguments"].items()
                    if _conclusion_id(item) == entity_id
                )
            result = [
                item
                for item in state.get("attacks", {}).values()
                if item.get("target", {}).get("id") in argument_ids | {entity_id}
            ]
        elif kind == "assumptions":
            result = [
                {
                    "argument_id": item_id,
                    "assumptions": item.get("assumptions", []),
                    "dependencies": item.get("dependencies", []),
                    "blockers": item.get("blockers", []),
                }
                for item_id, item in state.get("arguments", {}).items()
                if item_id == entity_id or _conclusion_id(item) == entity_id
            ]
        elif kind == "impact":
            result = self._impact(state, entity_id, limit * 4)
        elif kind == "unknowns":
            result = [
                {"unknown_type": "open_question", "entity": item}
                for item in state.get("questions", {}).values()
                if item.get("derived_status") != "resolved"
            ]
            result.extend(
                {"unknown_type": "unsupported_claim", "entity": item}
                for item in state.get("claims", {}).values()
                if item.get("derived", {}).get("support_state")
                in {"unsupported", "contested"}
            )
            result.extend(
                {"unknown_type": "verification_gap", "entity": item}
                for item in state.get("claims", {}).values()
                if item.get("derived", {}).get("verification_state") != "verified"
            )
        elif kind == "history":
            result = [
                item for item in self.store.operations() if entity_id in operation_entity_ids([item])
            ]
        elif kind == "changes-since":
            result = self.changes_since(int(entity_id or 0))
        elif kind == "revalidate":
            result = [
                {
                    "claim_id": claim_id,
                    "paths": claim.get("derived", {}).get("revalidation_plan", []),
                }
                for claim_id, claim in state.get("claims", {}).items()
                if claim.get("derived", {}).get("applicability_state") == "revalidation_required"
            ]
        elif kind == "search":
            result = self._search(catalog, text)
        elif kind == "telemetry":
            result = self._telemetry_metrics(state)
        else:
            raise SemanticError(
                "query kind must be belief, why, why-not, defeaters, assumptions, impact, "
                "unknowns, history, changes-since, revalidate, search, or telemetry"
            )
        if not isinstance(result, list):
            return {"kind": kind, "generation": state["generation"], "result": result, "complete": True}
        offset = int((cursor or {}).get("offset", 0))
        if cursor and cursor.get("generation") != state["generation"]:
            raise SemanticError("query cursor is stale")
        page = result[offset : offset + limit]
        complete = offset + len(page) >= len(result)
        response = {
            "kind": kind,
            "generation": state["generation"],
            "result": page,
            "complete": complete,
            "continuation": None
            if complete
            else {"generation": state["generation"], "offset": offset + len(page)},
            "warning": "result is incomplete; continue with the cursor" if not complete else "",
        }
        if kind == "search" and result:
            event = _event(
                "retrieval.surfaced",
                id=random_id("search"),
                mode="search",
                context_key=stable_id("search_ctx", text),
                entity_ids=[item["entity_id"] for item in page],
                candidates=result,
                policy_version=self.store.load_policy()["schema_version"],
            )
            self._record_telemetry([event])
        return response

    def _telemetry_metrics(self, state: dict[str, Any]) -> dict[str, Any]:
        events = state.get("retrieval_events", [])
        counts = Counter(item.get("event", "") for item in events)
        automatic = [
            item
            for item in events
            if item.get("event") == "retrieval.surfaced"
            and item.get("mode") == "automatic_recall"
        ]
        searches = [
            item
            for item in events
            if item.get("event") == "retrieval.surfaced"
            and item.get("mode") == "search"
        ]
        contexts = [
            item for item in events if item.get("event") == "retrieval.context_generated"
        ]
        resumes = [
            item for item in events if item.get("event") == "retrieval.resume_completed"
        ]
        automatic_candidates = sum(len(item.get("candidates", [])) for item in automatic)
        automatic_surfaced = sum(len(item.get("entity_ids", [])) for item in automatic)
        semantic_operations = self.store.operations()
        diagnostic_counts = Counter(
            item.get("code", "") for item in state.get("diagnostics", [])
        )
        used_chars = [int(item.get("used_chars", 0)) for item in contexts]
        return {
            "schema_version": "epistemic-telemetry-summary.v1",
            "generation": state["generation"],
            "agent_maintenance": {
                "explicit_memory_management_calls": (
                    counts["retrieval.context_generated"]
                    + counts["retrieval.expanded"]
                    + len(searches)
                    + sum(
                        item.get("type") == "checkpoint.created"
                        for item in semantic_operations
                    )
                ),
                "manual_searches": len(searches),
                "explicit_relation_management_operations": sum(
                    item.get("type") == "relation.asserted"
                    for item in semantic_operations
                ),
                "state_administration_tokens": {
                    "available": False,
                    "value": None,
                },
            },
            "recall": {
                "automatic_events": len(automatic),
                "automatic_candidates": automatic_candidates,
                "automatic_capsules_surfaced": automatic_surfaced,
                "expanded": counts["retrieval.expanded"],
                "used_structurally": counts["retrieval.structurally_used"],
                "dismissed": counts["retrieval.dismissed"],
                "relation_suggestions_accepted": counts["suggestion.accepted"],
                "relation_suggestions_rejected": counts["suggestion.rejected"],
                "correlated_family_compression_ratio": (
                    round(automatic_candidates / automatic_surfaced, 6)
                    if automatic_surfaced
                    else None
                ),
            },
            "epistemic_correctness": {
                "current_diagnostics_by_code": dict(sorted(diagnostic_counts.items())),
                "support_loss_detections": diagnostic_counts["SUPPORT_LOST"],
                "contested_claim_detections": diagnostic_counts["CLAIM_CONTESTED"],
                "decision_basis_warnings": diagnostic_counts["DECISION_BASIS_CHANGED"],
                "question_reopen_warnings": diagnostic_counts["QUESTION_REOPENED"],
            },
            "context_efficiency": {
                "context_packs": len(contexts),
                "total_context_chars": sum(used_chars),
                "maximum_context_chars": max(used_chars, default=0),
                "raw_evidence_avoided": sum(
                    not item.get("raw_evidence_included", False) for item in contexts
                ),
                "expansions_required": counts["retrieval.expanded"],
            },
            "resume": {
                "resume_events": len(resumes),
                "zero_model_call_resumes": sum(
                    item.get("model_calls") == 0 for item in resumes
                ),
                "manual_history_reads": sum(
                    int(item.get("manual_history_reads", 0)) for item in resumes
                ),
                "elapsed_ms": [item.get("elapsed_ms") for item in resumes],
            },
            "complete": True,
        }

    @staticmethod
    def _impact(state: dict[str, Any], entity_id: str, bound: int) -> list[dict[str, Any]]:
        visited = {entity_id}
        frontier = [entity_id]
        paths = []
        while frontier and len(paths) < bound:
            current = frontier.pop(0)
            for edge in state.get("edges", []):
                if edge["source"] != current or edge["target"] in visited:
                    continue
                visited.add(edge["target"])
                frontier.append(edge["target"])
                paths.append(edge)
        return paths

    @staticmethod
    def _search(catalog: dict[str, dict[str, Any]], text: str) -> list[dict[str, Any]]:
        terms = [term.lower() for term in text.split() if term]
        results = []
        for entity_id, entity in catalog.items():
            haystack = " ".join(
                str(entity.get(key, ""))
                for key in ("name", "description", "proposition", "subject", "aliases", "tags")
            ).lower()
            score = sum(haystack.count(term) for term in terms)
            if score:
                results.append(
                    {
                        "entity_id": entity_id,
                        "entity_type": entity["entity_type"],
                        "score": score,
                        "features": {"term_frequency": score},
                    }
                )
        return sorted(results, key=lambda item: (-item["score"], item["entity_id"]))
