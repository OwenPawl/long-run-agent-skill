"""Bounded worker context, structured search, inspection, and feedback."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .annotations import canonical_annotation
from .errors import SemanticError
from .inspection import inspect_graph, structured_search
from .recall import entity_catalog, operation_entity_ids
from .util import canonical_json, random_id, stable_id
from .worker_state import SURFACE_EVENT_TYPES, material_state, noncurrent_context


def _event(kind: str, **data: Any) -> dict[str, Any]:
    return {"type": kind, "data": data}


def _conclusion_id(argument: dict[str, Any]) -> str:
    conclusion = argument.get("conclusion", {})
    return conclusion.get("id") or conclusion.get("claim_id", "")


class ContextQueryMixin:
    """Methods mixed into EpistemicCompiler without a second state authority."""

    store: Any

    @staticmethod
    def _coverage_summary(state: dict[str, Any], entity_id: str) -> dict[str, Any]:
        coverage = state.get("worker_state", {}).get(entity_id, {})
        return {
            key: coverage.get(key, {}).get("estimate", 0.0)
            for key in ("content_coverage", "salience", "structural_coverage")
        } | {"recency": coverage.get("recency", {})}

    def _context_entries(self, state: dict[str, Any], since_generation: int) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        catalog = entity_catalog(state)
        changed = [item for item in catalog.values() if item.get("generation", 0) > since_generation]
        for entity in sorted(changed, key=lambda item: (-item.get("generation", 0), item["id"])):
            correction = noncurrent_context(entity["id"], entity, state)
            if correction["noncurrent"]:
                continue
            entries.append(
                {
                    "section": "current_work",
                    "entity_id": entity["id"],
                    "entity_type": entity["entity_type"],
                    "annotation": canonical_annotation(
                        entity, entity["entity_type"]
                    ),
                }
            )
        for claim_id, claim in sorted(state.get("claims", {}).items()):
            derived = claim.get("derived", {})
            claim_entity = {**claim, "entity_type": "claim", "id": claim_id}
            if (
                not noncurrent_context(claim_id, claim_entity, state)["noncurrent"]
                and (
                    derived.get("support_state") in {"supported", "contested"}
                    or derived.get("applicability_state")
                    == "revalidation_required"
                )
            ):
                entries.append(
                    {
                        "section": "current_claims",
                        "entity_id": claim_id,
                        "annotation": canonical_annotation(claim, "claim"),
                        "state": derived,
                    }
                )
        for question_id, question in sorted(state.get("questions", {}).items()):
            if question.get("derived_status") != "resolved":
                entries.append(
                    {
                        "section": "open_questions",
                        "entity_id": question_id,
                        "annotation": canonical_annotation(question, "question"),
                        "state": question.get("derived_status"),
                    }
                )
        for decision_id, decision in sorted(state.get("decisions", {}).items()):
            if decision.get("basis_changed"):
                entries.append(
                    {
                        "section": "affected_decisions",
                        "entity_id": decision_id,
                        "annotation": canonical_annotation(decision, "decision"),
                        "basis_changed": True,
                    }
                )
        for diagnostic in state.get("diagnostics", []):
            if diagnostic.get("severity") in {"error", "warning"}:
                entries.append(
                    {
                        "section": "diagnostics",
                        "id": diagnostic.get("id"),
                        "code": diagnostic.get("code"),
                        "severity": diagnostic.get("severity"),
                        "why": diagnostic.get("why"),
                        "what_changed": diagnostic.get("what_changed"),
                        "affected_entities": diagnostic.get(
                            "affected_entities", []
                        ),
                        "relevant_paths": diagnostic.get("relevant_paths", []),
                        "code_actions": [
                            {
                                "id": action.get("id"),
                                "level": action.get("level"),
                                "title": action.get("title"),
                                "semantic_input_required": action.get(
                                    "semantic_input_required"
                                ),
                            }
                            for action in diagnostic.get("fixits", [])
                        ],
                    }
                )
        for event in reversed(state.get("retrieval_events", [])):
            if event.get("event") != "retrieval.surfaced":
                continue
            surfaced_capsules = {
                item.get("entity_id"): item
                for item in event.get("capsules", [])
                if item.get("entity_id")
            }
            for entity_id in event.get("entity_ids", []):
                entity = catalog.get(entity_id)
                if entity:
                    capsule = surfaced_capsules.get(entity_id)
                    if capsule:
                        entries.append(
                            {
                                "section": "historical_recall",
                                **{
                                    key: capsule.get(key)
                                    for key in (
                                        "entity_id",
                                        "entity_type",
                                        "role",
                                        "historical_noncurrent",
                                        "annotation",
                                        "extended_annotation",
                                        "current_state",
                                        "why_relevant_now",
                                        "why_no_longer_current",
                                        "decisive_path",
                                        "current_successor",
                                        "reference",
                                    )
                                    if key in capsule
                                },
                            }
                        )
                        continue
                    correction = noncurrent_context(entity_id, entity, state)
                    entry = {
                        "section": "historical_recall",
                        "entity_id": entity_id,
                        "entity_type": entity["entity_type"],
                        "annotation": canonical_annotation(
                            entity, entity["entity_type"]
                        ),
                        "reference": {"action": "inspect", "entity_id": entity_id},
                    }
                    if correction["noncurrent"]:
                        entry.update(
                            {
                                "role": "corrective",
                                "historical_noncurrent": True,
                                "current_state": correction["badge"],
                                "why_no_longer_current": correction[
                                    "why_no_longer_current"
                                ],
                                "decisive_path": correction["decisive_path"],
                                "current_successor": correction[
                                    "current_successor"
                                ],
                            }
                        )
                    entries.append(entry)
            break
        for entry in entries:
            entity_id = entry.get("entity_id")
            if entity_id:
                entry.setdefault(
                    "worker_state_coverage",
                    self._coverage_summary(state, entity_id),
                )
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
        selected_ids = [
            entry["entity_id"] for entry in selected if entry.get("entity_id")
        ]
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
            entity_ids=selected_ids,
            entity_states={
                entity_id: material_state(
                    entity_id, entity_catalog(state)[entity_id], state
                )
                for entity_id in selected_ids
                if entity_id in entity_catalog(state)
            },
            used_chars=used,
            max_chars=char_limit,
            entry_count=len(selected),
            max_entities=entity_limit,
            complete=complete,
            raw_evidence_included=False,
        )
        transaction = self._record_telemetry([telemetry])
        response["telemetry_transaction_id"] = transaction["transaction_id"]
        current_state = self.state()
        response["worker_state_after_context"] = {
            entity_id: current_state.get("worker_state", {}).get(entity_id, {})
            for entity_id in selected_ids
        }
        return response

    def search(
        self,
        text: str,
        *,
        cursor: dict[str, Any] | None = None,
        limit: int = 50,
        entity_types: list[str] | None = None,
    ) -> dict[str, Any]:
        state = self.state()
        catalog = entity_catalog(state)
        results = structured_search(
            state, catalog, text, entity_types=entity_types
        )
        offset = int((cursor or {}).get("offset", 0))
        if cursor and cursor.get("generation") != state["generation"]:
            raise SemanticError("search cursor is stale")
        page = results[offset : offset + max(limit, 1)]
        complete = offset + len(page) >= len(results)
        entity_ids = [item["entity_id"] for item in page]
        event = _event(
            "retrieval.search_result_surfaced",
            id=random_id("search"),
            mode="search",
            context_key=stable_id(
                "search_ctx", {"text": text, "entity_types": entity_types or []}
            ),
            entity_ids=entity_ids,
            entity_states={
                item_id: material_state(item_id, catalog[item_id], state)
                for item_id in entity_ids
            },
            canonical_annotation_entity_ids=entity_ids,
            candidates=page,
            policy_version=self.store.load_policy()["schema_version"],
        )
        transaction = self._record_telemetry([event])
        response = {
            "schema_version": "epistemic-search.v1",
            "generation": state["generation"],
            "query": text,
            "results": page,
            "complete": complete,
            "telemetry_transaction_id": transaction["transaction_id"],
        }
        if not complete:
            response["continuation"] = {
                "generation": state["generation"],
                "offset": offset + len(page),
            }
        return response

    def inspect(
        self,
        entity_id: str = "",
        *,
        facets: list[str] | None = None,
        cursor: dict[str, Any] | None = None,
        limit: int = 20,
        max_depth: int | None = None,
        since_generation: int | None = None,
    ) -> dict[str, Any]:
        state = self.state()
        if cursor and cursor.get("generation") != state["generation"]:
            raise SemanticError("inspect cursor is stale")
        catalog = entity_catalog(state)
        built = inspect_graph(
            state,
            catalog,
            self.store.operations(),
            entity_id=entity_id,
            facets=facets,
            since_generation=since_generation,
            max_depth=max_depth
            or self.store.load_policy()["context"]["max_proof_depth"],
        )
        node_items = list(built["nodes"].items())
        offset = int((cursor or {}).get("offset", 0))
        selected_items = node_items[offset : offset + max(limit, 1)]
        selected_ids = {item_id for item_id, _ in selected_items}
        complete = offset + len(selected_items) >= len(node_items)
        nodes = dict(selected_items)
        relations = [
            relation
            for relation in built["relations"]
            if relation["source"] in selected_ids
            and relation["target"] in selected_ids
        ]
        provenance_refs = {
            node["provenance_ref"]
            for node in nodes.values()
            if node.get("provenance_ref")
        }
        event = _event(
            "retrieval.inspect_topology_surfaced",
            id=random_id("inspect"),
            mode="inspect",
            context_key=stable_id(
                "inspect_ctx",
                {"entity_id": entity_id, "facets": sorted(facets or [])},
            ),
            entity_ids=list(nodes),
            canonical_annotation_entity_ids=list(nodes),
            extended_annotation_entity_ids=[
                item_id
                for item_id, node in nodes.items()
                if node.get("extended_annotation")
            ],
            topology_entity_ids=list(nodes),
            materialized_view_available_ids=list(nodes),
            content_read_entity_ids=[],
            entity_states={
                item_id: material_state(item_id, catalog[item_id], state)
                for item_id in nodes
            },
        )
        transaction = self._record_telemetry([event])
        response: dict[str, Any] = {
            "schema_version": "epistemic-inspect.v1",
            "generation": state["generation"],
            "target": entity_id,
            "facets": sorted(facets or []),
            "nodes": nodes,
            "relations": relations,
            "paths": built["paths"],
            "diagnostics": built["diagnostics"],
            "complete": complete,
            "telemetry_transaction_id": transaction["transaction_id"],
        }
        if provenance_refs:
            response["provenance"] = {
                reference: built.get("provenance", {})[reference]
                for reference in sorted(provenance_refs)
            }
        group_offset = int((cursor or {}).get("group_offset", 0))
        history_offset = int((cursor or {}).get("history_offset", 0))
        for field in ("groups", "history", "presentation_guard"):
            if field in built:
                value = built[field]
                if field == "history":
                    value = value[history_offset : history_offset + max(limit, 1)]
                    complete = complete and history_offset + len(value) >= len(
                        built[field]
                    )
                    response["complete"] = complete
                elif field == "groups":
                    value = {
                        name: (
                            group[group_offset : group_offset + max(limit, 1)]
                            if isinstance(group, list)
                            else dict(
                                list(group.items())[
                                    group_offset : group_offset + max(limit, 1)
                                ]
                            )
                            if isinstance(group, dict)
                            else group
                        )
                        for name, group in value.items()
                    }
                    complete = complete and all(
                        not isinstance(group, (list, dict))
                        or group_offset + len(value[name]) >= len(group)
                        for name, group in built[field].items()
                    )
                    response["complete"] = complete
                response[field] = value
        if "telemetry" in set(facets or []):
            response.setdefault("groups", {})["telemetry"] = self._telemetry_metrics(
                self.state()
            )
        if not complete:
            response["continuation"] = {
                "generation": state["generation"],
                "offset": offset + len(selected_items),
                "group_offset": group_offset + max(limit, 1),
                "history_offset": history_offset + max(limit, 1),
            }
        response["worker_state_after_inspect"] = {
            item_id: self.state().get("worker_state", {}).get(item_id, {})
            for item_id in nodes
        }
        return response

    def feedback(self, event_id: str, entity_id: str, action: str) -> dict[str, Any]:
        state = self.state()
        if action == "dismissed":
            source_event = next(
                (event for event in state.get("retrieval_events", []) if event.get("id") == event_id),
                None,
            )
            if not source_event:
                raise SemanticError(f"unknown retrieval event: {event_id}")
            if source_event.get("event") not in SURFACE_EVENT_TYPES:
                raise SemanticError("dismissal requires a surfaced retrieval event")
            if entity_id not in source_event.get("entity_ids", []):
                raise SemanticError(
                    f"entity {entity_id} was not surfaced by retrieval event {event_id}"
                )
            source_sequence = int(source_event.get("telemetry_sequence", 0))
            inspected = any(
                event.get("event") in {"retrieval.expanded", "retrieval.content_read"}
                and entity_id in event.get("entity_ids", [])
                and int(event.get("telemetry_sequence", 0)) > source_sequence
                for event in state.get("retrieval_events", [])
            )
            strength = "strong" if inspected else "weak"
            event = _event(
                "retrieval.dismissed",
                id=random_id("retrieval_dismissal"),
                source_event_id=event_id,
                context_key=source_event.get("context_key", ""),
                entity_ids=[entity_id],
                feedback_strength=strength,
                inspected_before_dismissal=inspected,
                contextual_weight=1.0 if inspected else 0.4,
            )
            transaction = self._record_telemetry([event])
            return {
                "status": "success",
                "committed_semantics": [],
                "telemetry": {"transaction_id": transaction["transaction_id"], "events": [event]},
                "scope": "retrieval_context_only",
                "feedback_strength": strength,
                "globally_demoted": False,
            }
        suggestion = state.get("suggestions", {}).get(event_id)
        if not suggestion or action not in {"accepted", "rejected"}:
            raise SemanticError("feedback action must be dismissed, accepted, or rejected")
        involved_ids = {
            suggestion.get("source", {}).get("id", ""),
            suggestion.get("target", {}).get("id", ""),
            entity_id,
        } - {""}
        event = _event(
            f"suggestion.{action}",
            id=event_id,
            entity_ids=sorted(involved_ids),
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
                            basis=suggestion.get("basis", []),
                            rationale=suggestion.get("rationale", ""),
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
            if item.get("event") == "retrieval.search_result_surfaced"
        ]
        inspections = [
            item
            for item in events
            if item.get("event") == "retrieval.inspect_topology_surfaced"
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
                    + len(inspections)
                    + sum(
                        item.get("type") == "checkpoint.created"
                        for item in semantic_operations
                    )
                ),
                "manual_searches": len(searches),
                "explicit_inspections": len(inspections),
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
                "referenced": counts["retrieval.referenced"],
                "used_structurally": counts["retrieval.structurally_used"],
                "dismissed": counts["retrieval.dismissed"],
                "dismissed_weak": sum(
                    item.get("feedback_strength") == "weak"
                    for item in events
                    if item.get("event") == "retrieval.dismissed"
                ),
                "dismissed_strong": sum(
                    item.get("feedback_strength") == "strong"
                    for item in events
                    if item.get("event") == "retrieval.dismissed"
                ),
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
