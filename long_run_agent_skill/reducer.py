"""Deterministic epistemic reducer over authoritative semantic operations."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any

from .annotations import canonical_annotation, render_annotation, revise_annotation
from .code_actions import normalize_diagnostics
from .errors import SemanticError
from .graph import strongly_connected_components, support_independence
from .provenance import normalize_provenance_refs
from .util import stable_id
from .worker_state import derive_worker_state, operation_references


def _payload(operation: dict[str, Any]) -> dict[str, Any]:
    data = deepcopy(operation.get("data", {}))
    data["generation"] = operation.get("_generation", 0)
    data["transaction_id"] = operation.get("_transaction_id", "")
    if "_telemetry_sequence" in operation:
        data["telemetry_sequence"] = operation["_telemetry_sequence"]
    return data


def _latest_update(items: dict[str, Any], item: dict[str, Any]) -> None:
    items[item["id"]] = item


def _ref_key(reference: dict[str, Any]) -> tuple[str, str]:
    return reference.get("type", ""), reference.get("id", "")


def _conclusion_key(conclusion: dict[str, Any]) -> tuple[str, str]:
    return conclusion.get("type", "claim"), conclusion.get("id") or conclusion.get("claim_id", "")


def _entity_label(entity: dict[str, Any], fallback: str) -> str:
    try:
        return render_annotation(
            canonical_annotation({**entity, "id": entity.get("id", fallback)}, "entity")
        )
    except SemanticError:
        return fallback


def _diagnostic(code: str, entity_ids: list[str], **details: Any) -> dict[str, Any]:
    body = {"code": code, "entities": sorted(set(entity_ids)), **details}
    return {"id": stable_id("diag", body), **body}


def _collect(operations: list[dict[str, Any]]) -> dict[str, Any]:
    model: dict[str, Any] = {
        "mission": {},
        "runs": {},
        "artifacts": {},
        "evidence": {},
        "claims": {},
        "assumptions": {},
        "arguments": {},
        "attacks": {},
        "dependencies": {},
        "verifications": {},
        "questions": {},
        "decisions": {},
        "warrants": {},
        "relations": {},
        "suggestions": {},
        "annotations": {},
        "attention": {},
        "retrieval_events": [],
        "checkpoints": {},
    }
    for operation in operations:
        kind = operation.get("type", "")
        data = _payload(operation)
        if kind == "mission.started" or kind == "mission.updated":
            model["mission"].update(data)
        elif kind == "run.started" or kind == "run.closed":
            existing = model["runs"].get(data["id"], {})
            model["runs"][data["id"]] = {**existing, **data, "event": kind}
        elif kind == "evidence.registered":
            _latest_update(model["evidence"], data)
        elif kind == "artifact.registered":
            _latest_update(model["artifacts"], data)
        elif kind in {"claim.asserted", "claim.revised"}:
            entry = model["claims"].setdefault(data["id"], {"id": data["id"], "revisions": []})
            entry["revisions"].append(data)
            entry.update(data)
            entry["commitment"] = data.get("commitment", "asserted")
        elif kind == "claim.withdrawn":
            entry = model["claims"].setdefault(data["id"], {"id": data["id"], "revisions": []})
            entry.update(data)
            entry["commitment"] = "withdrawn"
        elif kind in {"assumption.asserted", "assumption.revised"}:
            entry = model["assumptions"].setdefault(data["id"], {"id": data["id"], "revisions": []})
            entry["revisions"].append(data)
            entry.update(data)
            entry["commitment"] = data.get("commitment", "accepted")
        elif kind == "assumption.withdrawn":
            entry = model["assumptions"].setdefault(data["id"], {"id": data["id"], "revisions": []})
            entry.update(data)
            entry["commitment"] = "withdrawn"
        elif kind == "argument.asserted":
            data["retracted"] = False
            _latest_update(model["arguments"], data)
        elif kind == "argument.retracted":
            entry = model["arguments"].setdefault(data["id"], {"id": data["id"]})
            entry.update(data)
            entry["retracted"] = True
        elif kind == "attack.asserted":
            data["retracted"] = False
            _latest_update(model["attacks"], data)
        elif kind == "attack.retracted":
            entry = model["attacks"].setdefault(data["id"], {"id": data["id"]})
            entry.update(data)
            entry["retracted"] = True
        elif kind == "dependency.observed":
            entry = model["dependencies"].setdefault(data["id"], {"id": data["id"], "history": []})
            entry["history"].append(data)
            entry.update(data)
        elif kind == "verification.recorded":
            _latest_update(model["verifications"], data)
        elif kind in {"question.opened", "question.updated", "question.resolved", "question.reopened"}:
            entry = model["questions"].setdefault(data["id"], {"id": data["id"], "history": []})
            entry["history"].append({**data, "event": kind})
            entry.update(data)
            entry["recorded_status"] = kind.removeprefix("question.")
        elif kind in {"decision.recorded", "decision.revised"}:
            entry = model["decisions"].setdefault(data["id"], {"id": data["id"], "history": []})
            entry["history"].append(data)
            entry.update(data)
        elif kind == "warrant.registered":
            _latest_update(model["warrants"], data)
        elif kind == "relation.asserted":
            _latest_update(model["relations"], data)
        elif kind.startswith("suggestion."):
            entry = model["suggestions"].setdefault(data["id"], {"id": data["id"]})
            entry.update(data)
            action = kind.removeprefix("suggestion.")
            entry["status"] = "suggested" if action == "created" else action
            if action == "accepted":
                for entity_id in data.get("entity_ids", []):
                    attention = model["attention"].setdefault(entity_id, {})
                    attention["last_relation_suggestion_accepted_generation"] = data[
                        "generation"
                    ]
                    attention["relation_suggestion_accepted_count"] = (
                        attention.get("relation_suggestion_accepted_count", 0) + 1
                    )
        elif kind in {"annotation.created", "annotation.revised"}:
            key = f"{data.get('entity_type')}:{data.get('entity_id')}"
            model["annotations"][key] = data
        elif kind.startswith("retrieval."):
            event = {**data, "event": kind}
            model["retrieval_events"].append(event)
            for entity_id in data.get("entity_ids", []):
                attention = model["attention"].setdefault(entity_id, {})
                raw_signal = kind.removeprefix("retrieval.")
                signal = (
                    "surfaced"
                    if raw_signal
                    in {"search_result_surfaced", "inspect_topology_surfaced"}
                    else raw_signal
                )
                attention[f"last_{raw_signal}_generation"] = data["generation"]
                attention[f"{raw_signal}_count"] = (
                    attention.get(f"{raw_signal}_count", 0) + 1
                )
                attention[f"last_{signal}_generation"] = data["generation"]
                if signal != raw_signal:
                    attention[f"{signal}_count"] = (
                        attention.get(f"{signal}_count", 0) + 1
                    )
                if signal == "surfaced":
                    material = data.get("entity_states", {}).get(entity_id)
                    if material is not None:
                        attention["last_surfaced_material_state"] = material
                    attention["last_surfaced_telemetry_sequence"] = data.get(
                        "telemetry_sequence"
                    )
                if signal == "expanded":
                    attention["last_read_or_expanded_generation"] = data["generation"]
                if signal == "structurally_used":
                    attention["last_structural_use_generation"] = data["generation"]
                if signal == "dismissed":
                    strength = data.get("feedback_strength", "weak")
                    key = f"dismissed_{strength}_count"
                    attention[key] = attention.get(key, 0) + 1
        elif kind == "checkpoint.created":
            _latest_update(model["checkpoints"], data)
        if not kind.startswith(("retrieval.", "suggestion.")):
            own_id = data.get("id", "")
            if own_id and kind.split(".", 1)[0] not in {"mission", "run", "checkpoint"}:
                model["attention"].setdefault(own_id, {}).setdefault(
                    "created_generation", data["generation"]
                )
            referenced, structural = operation_references([operation])
            for entity_id in referenced:
                if entity_id and entity_id != own_id:
                    model["attention"].setdefault(entity_id, {})[
                        "last_referenced_generation"
                    ] = data["generation"]
            for entity_id in structural:
                if entity_id and entity_id != own_id:
                    model["attention"].setdefault(entity_id, {})[
                        "last_structurally_used_generation"
                    ] = data["generation"]
    annotation_types = {
        "artifacts": "artifact",
        "evidence": "evidence",
        "claims": "claim",
        "assumptions": "assumption",
        "arguments": "argument",
        "attacks": "attack",
        "dependencies": "dependency",
        "verifications": "verification",
        "questions": "question",
        "decisions": "decision",
        "warrants": "warrant",
        "relations": "relation",
    }
    for collection, entity_type in annotation_types.items():
        for entity_id, entity in model[collection].items():
            annotation = model["annotations"].get(f"{entity_type}:{entity_id}")
            current_annotation = canonical_annotation(entity, entity_type)
            if annotation:
                revision = dict(annotation)
                if "annotation" not in revision and (
                    revision.get("name") or revision.get("description")
                ):
                    legacy_update = {}
                    if revision.get("name"):
                        legacy_update["subject"] = revision["name"]
                    if revision.get("description"):
                        legacy_update["predicate"] = revision["description"]
                    revision["annotation"] = legacy_update
                entity["annotation"] = revise_annotation(current_annotation, revision)
                entity.update(
                    {
                        key: annotation[key]
                        for key in (
                            "intrinsic_name",
                            "extended_annotation",
                            "aliases",
                            "tags",
                            "rationale",
                        )
                        if key in annotation
                    }
                )
            else:
                entity["annotation"] = current_annotation
            provenance_refs = normalize_provenance_refs(entity)
            if provenance_refs:
                entity["provenance_refs"] = provenance_refs
    return model


def _dependency_matches(assumption: dict[str, Any], dependencies: dict[str, Any]) -> tuple[bool, str]:
    dependency_id = assumption.get("dependency_id", "")
    dependency = dependencies.get(dependency_id)
    if not dependency:
        return False, f"missing dependency {dependency_id}"
    if dependency.get("status", "current") not in {"current", "valid"}:
        return False, f"dependency {dependency_id} is {dependency.get('status')}"
    if "expected" in assumption and dependency.get("value") != assumption.get("expected"):
        return False, f"dependency {dependency_id} expected {assumption.get('expected')!r}, found {dependency.get('value')!r}"
    return True, ""


def _evidence_roots(evidence: dict[str, Any]) -> dict[str, set[str]]:
    cache: dict[str, set[str]] = {}

    def roots(entity_id: str, visiting: set[str]) -> set[str]:
        if entity_id in cache:
            return cache[entity_id]
        if entity_id in visiting:
            return {entity_id}
        item = evidence.get(entity_id, {})
        parents = item.get("derivation_parents", [])
        if not parents:
            result = {entity_id}
        else:
            result = set()
            for parent in parents:
                parent_id = parent.get("id") if isinstance(parent, dict) else parent
                if parent_id in evidence:
                    result.update(roots(parent_id, visiting | {entity_id}))
            if not result:
                result = {entity_id}
        cache[entity_id] = result
        return result

    for evidence_id in evidence:
        roots(evidence_id, set())
    return cache


def _verification_valid(receipt: dict[str, Any], dependencies: dict[str, Any]) -> tuple[bool, list[str]]:
    blockers: list[str] = []
    if receipt.get("outcome") not in {"pass", "passed", "success", "verified"}:
        blockers.append(f"outcome is {receipt.get('outcome', 'unknown')}")
    for dependency in receipt.get("dependencies", []):
        matches, reason = _dependency_matches(dependency, dependencies)
        if not matches:
            blockers.append(reason)
    return not blockers, blockers


def _positive_grounding(
    arguments: dict[str, Any],
    evidence: dict[str, Any],
    verification_validity: dict[str, bool],
    verification_roots: dict[str, set[str]],
    evidence_roots: dict[str, set[str]],
    accepted_assumptions: set[str],
    blocked_arguments: set[str] | None = None,
) -> tuple[set[str], set[str], set[str], dict[str, set[str]]]:
    blocked = blocked_arguments or set()
    supported_claims: set[str] = set()
    supported_assumptions: set[str] = set(accepted_assumptions)
    grounded_arguments: set[str] = set()
    argument_roots: dict[str, set[str]] = {}
    claim_roots: dict[str, set[str]] = defaultdict(set)

    def grounded(reference: dict[str, Any]) -> bool:
        kind, entity_id = _ref_key(reference)
        if kind == "evidence":
            return entity_id in evidence
        if kind == "verification":
            return verification_validity.get(entity_id, False)
        if kind == "claim":
            return entity_id in supported_claims
        if kind == "assumption":
            return entity_id in supported_assumptions
        return False

    changed = True
    while changed:
        changed = False
        for argument_id, argument in sorted(arguments.items()):
            if argument_id in blocked or argument.get("retracted") or not argument.get("applicable", False):
                continue
            required_assumptions = {
                item.get("assumption_id", "") for item in argument.get("assumptions", [])
            }
            if not required_assumptions <= supported_assumptions:
                continue
            premises = argument.get("premises", [])
            if not premises or not all(grounded(reference) for reference in premises):
                continue
            roots: set[str] = set()
            for reference in premises:
                kind, entity_id = _ref_key(reference)
                if kind == "evidence":
                    roots.update(evidence_roots.get(entity_id, {entity_id}))
                elif kind == "verification":
                    if verification_validity.get(entity_id):
                        roots.update(verification_roots.get(entity_id, set()))
                elif kind == "claim":
                    roots.update(claim_roots.get(entity_id, set()))
            previous_roots = argument_roots.get(argument_id, set())
            if argument_id not in grounded_arguments or roots != previous_roots:
                grounded_arguments.add(argument_id)
                argument_roots[argument_id] = roots
                changed = True
            conclusion = argument.get("conclusion", {})
            if conclusion.get("polarity", "support") == "support":
                target_type = conclusion.get("type", "claim")
                target_id = conclusion.get("id") or conclusion.get("claim_id", "")
                if target_type == "assumption":
                    if target_id and target_id not in supported_assumptions:
                        supported_assumptions.add(target_id)
                        changed = True
                else:
                    before = set(claim_roots[target_id])
                    claim_roots[target_id].update(roots)
                    if target_id and (target_id not in supported_claims or claim_roots[target_id] != before):
                        supported_claims.add(target_id)
                        changed = True
    return supported_claims, supported_assumptions, grounded_arguments, argument_roots


def _active_attacks(
    attacks: dict[str, Any],
    evidence: dict[str, Any],
    verification_validity: dict[str, bool],
    positively_supported: set[str],
    supported_assumptions: set[str],
) -> set[str]:
    def grounded(reference: dict[str, Any]) -> bool:
        kind, entity_id = _ref_key(reference)
        return (
            (kind == "evidence" and entity_id in evidence)
            or (kind == "verification" and verification_validity.get(entity_id, False))
            or (kind == "claim" and entity_id in positively_supported)
            or (kind == "assumption" and entity_id in supported_assumptions)
        )

    return {
        attack_id
        for attack_id, attack in attacks.items()
        if not attack.get("retracted")
        and attack.get("applicable", False)
        and attack.get("grounds")
        and attack.get("warrant")
        and all(grounded(reference) for reference in attack["grounds"])
        and all(item.get("assumption_id", "") in supported_assumptions for item in attack.get("assumptions", []))
    }


def _repair_paths(claim_id: str, arguments: dict[str, Any], costs: dict[str, int]) -> list[dict[str, Any]]:
    paths: list[dict[str, Any]] = []
    for argument_id, argument in arguments.items():
        conclusion = argument.get("conclusion", {})
        target_type, target_id = _conclusion_key(conclusion)
        if target_type != "claim" or target_id != claim_id or conclusion.get("polarity", "support") != "support":
            continue
        actions: list[dict[str, Any]] = []
        for blocker in argument.get("blockers", []):
            if blocker.startswith("dependency"):
                action = "dependency_refresh"
            elif blocker.startswith("verification"):
                action = "rerun_verifier"
            elif blocker.startswith("missing premise"):
                action = "reacquire_evidence"
            else:
                action = "semantic_review"
            actions.append({"action": action, "reason": blocker, "cost": costs[action]})
        if argument.get("defeated_by"):
            actions.append(
                {
                    "action": "semantic_review",
                    "reason": "address defeater " + ",".join(argument["defeated_by"]),
                    "cost": costs["semantic_review"],
                }
            )
        if actions:
            paths.append(
                {
                    "argument_id": argument_id,
                    "actions": actions,
                    "estimated_cost": sum(item["cost"] for item in actions),
                }
            )
    return sorted(paths, key=lambda item: (item["estimated_cost"], item["argument_id"]))


def reduce_operations(operations: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
    model = _collect(operations)
    artifacts = model["artifacts"]
    evidence = model["evidence"]
    claims = model["claims"]
    assumptions = model["assumptions"]
    arguments = model["arguments"]
    attacks = model["attacks"]
    dependencies = model["dependencies"]
    for observation in evidence.values():
        artifact_ref = observation.get("artifact_ref", {})
        artifact = artifacts.get(artifact_ref.get("id", ""), {})
        if artifact:
            observation["artifact_id"] = artifact["id"]
            observation["artifact_content_hash"] = artifact.get("content_hash", "")
            observation["artifact_locator"] = artifact_ref.get("locator")
    evidence_roots = _evidence_roots(evidence)
    verification_validity: dict[str, bool] = {}
    verification_roots: dict[str, set[str]] = {}
    for verification_id, receipt in model["verifications"].items():
        valid, blockers = _verification_valid(receipt, dependencies)
        receipt["valid"] = valid
        receipt["blockers"] = blockers
        receipt["root_evidence"] = sorted(
            {
                root
                for reference in receipt.get("inputs", [])
                if reference.get("type") == "evidence"
                for root in evidence_roots.get(reference.get("id", ""), set())
            }
        )
        verification_validity[verification_id] = valid
        verification_roots[verification_id] = set(receipt["root_evidence"])

    for argument in arguments.values():
        blockers: list[str] = []
        for dependency in argument.get("dependencies", []):
            matches, reason = _dependency_matches(dependency, dependencies)
            if not matches:
                blockers.append(reason)
        argument["applicable"] = not blockers and not argument.get("retracted", False)
        argument["blockers"] = blockers
    for attack in attacks.values():
        blockers = []
        for dependency in attack.get("dependencies", []):
            matches, reason = _dependency_matches(dependency, dependencies)
            if not matches:
                blockers.append(reason)
        attack["applicable"] = not blockers and not attack.get("retracted", False)
        attack["blockers"] = blockers

    accepted_assumptions = {
        assumption_id
        for assumption_id, assumption in assumptions.items()
        if assumption.get("commitment", "accepted") in {"accepted", "asserted"}
    }
    positive_claims, positive_assumptions, _, _ = _positive_grounding(
        arguments,
        evidence,
        verification_validity,
        verification_roots,
        evidence_roots,
        accepted_assumptions,
    )
    active_attacks = _active_attacks(
        attacks, evidence, verification_validity, positive_claims, positive_assumptions
    )
    blocked_arguments: set[str] = set()
    defeated_by: dict[str, list[str]] = defaultdict(list)
    rebut_claims: dict[str, list[str]] = defaultdict(list)
    for attack_id in sorted(active_attacks):
        attack = attacks[attack_id]
        target_type, target_id = _ref_key(attack.get("target", {}))
        attack_type = attack.get("attack_type")
        if attack_type == "undercut" and target_type == "argument":
            blocked_arguments.add(target_id)
            defeated_by[target_id].append(attack_id)
        elif attack_type == "undermine":
            for argument_id, argument in arguments.items():
                if any(_ref_key(reference) == (target_type, target_id) for reference in argument.get("premises", [])):
                    blocked_arguments.add(argument_id)
                    defeated_by[argument_id].append(attack_id)
        elif attack_type == "rebut" and target_type == "claim":
            rebut_claims[target_id].append(attack_id)
        elif attack_type in {"rebut", "undermine"} and target_type == "assumption":
            accepted_assumptions.discard(target_id)

    supported_claims, supported_assumptions, grounded_arguments, argument_roots = _positive_grounding(
        arguments,
        evidence,
        verification_validity,
        verification_roots,
        evidence_roots,
        accepted_assumptions,
        blocked_arguments,
    )
    supporting: dict[str, list[str]] = defaultdict(list)
    opposing: dict[str, list[str]] = defaultdict(list)
    for argument_id, argument in arguments.items():
        argument["defeated_by"] = sorted(defeated_by.get(argument_id, []))
        missing: list[str] = []
        for reference in argument.get("premises", []):
            kind, entity_id = _ref_key(reference)
            if kind == "claim" and entity_id not in supported_claims:
                missing.append(f"missing premise claim {entity_id}")
            elif kind == "verification" and not verification_validity.get(entity_id, False):
                missing.append(f"verification {entity_id} is invalid")
            elif kind == "evidence" and entity_id not in evidence:
                missing.append(f"missing premise evidence {entity_id}")
            elif kind == "assumption" and entity_id not in supported_assumptions:
                missing.append(f"missing premise assumption {entity_id}")
        for assumption in argument.get("assumptions", []):
            assumption_id = assumption.get("assumption_id", "")
            if assumption_id not in supported_assumptions:
                missing.append(f"assumption {assumption_id} is not currently accepted or supported")
        argument["blockers"] = sorted(set(argument.get("blockers", []) + missing))
        argument["active"] = argument_id in grounded_arguments and argument_id not in blocked_arguments
        argument["root_evidence"] = sorted(argument_roots.get(argument_id, set()))
        conclusion = argument.get("conclusion", {})
        if argument["active"]:
            target_type = conclusion.get("type", "claim")
            target = conclusion.get("id") or conclusion.get("claim_id", "")
            if target_type == "claim":
                if conclusion.get("polarity", "support") == "oppose":
                    opposing[target].append(argument_id)
                else:
                    supporting[target].append(argument_id)
    for claim_id, attack_ids in rebut_claims.items():
        opposing[claim_id].extend(f"attack:{item}" for item in attack_ids)

    diagnostics: list[dict[str, Any]] = []
    used_evidence: set[str] = set()
    for argument in arguments.values():
        used_evidence.update(
            reference["id"] for reference in argument.get("premises", []) if reference.get("type") == "evidence"
        )
    for attack in attacks.values():
        used_evidence.update(
            reference["id"] for reference in attack.get("grounds", []) if reference.get("type") == "evidence"
        )
    for receipt in model["verifications"].values():
        used_evidence.update(reference.get("id", "") for reference in receipt.get("inputs", []))
    for question in model["questions"].values():
        used_evidence.update(question.get("evidence_ids", []))
    for decision in model["decisions"].values():
        used_evidence.update(decision.get("evidence_ids", []))

    support_dependency_graph: dict[str, set[str]] = {
        **{f"claim:{claim_id}": set() for claim_id in claims},
        **{f"assumption:{assumption_id}": set() for assumption_id in assumptions},
    }
    for argument in arguments.values():
        conclusion_type, conclusion_id = _conclusion_key(argument.get("conclusion", {}))
        if conclusion_type in {"claim", "assumption"} and conclusion_id:
            conclusion_node = f"{conclusion_type}:{conclusion_id}"
            for reference in argument.get("premises", []):
                if reference.get("type") in {"claim", "assumption"}:
                    premise_node = f"{reference['type']}:{reference['id']}"
                    support_dependency_graph.setdefault(premise_node, set()).add(conclusion_node)
            for assumption in argument.get("assumptions", []):
                premise_node = f"assumption:{assumption.get('assumption_id', '')}"
                support_dependency_graph.setdefault(premise_node, set()).add(conclusion_node)
    circular_entities: set[str] = set()
    grounded_nodes = {f"claim:{item}" for item in supported_claims} | {
        f"assumption:{item}" for item in supported_assumptions
    }
    for component in strongly_connected_components(support_dependency_graph):
        is_cycle = len(component) > 1 or any(
            node in support_dependency_graph.get(node, set()) for node in component
        )
        if is_cycle and not any(node in grounded_nodes for node in component):
            entity_ids = [node.split(":", 1)[1] for node in component]
            circular_entities.update(entity_ids)
            diagnostics.append(
                _diagnostic(
                    "CIRCULAR_JUSTIFICATION",
                    entity_ids,
                    severity="error",
                    why="the support cycle has no grounded evidence or valid verification entering it",
                    relation_paths=[component],
                    fixits=[{"level": 2, "action": "supply an independent grounded premise"}],
                )
            )

    assumption_opposition: dict[str, list[str]] = defaultdict(list)
    for attack_id in active_attacks:
        attack = attacks[attack_id]
        target_type, target_id = _ref_key(attack.get("target", {}))
        if target_type == "assumption" and attack.get("attack_type") in {"rebut", "undermine"}:
            assumption_opposition[target_id].append(attack_id)
    for assumption_id, assumption in assumptions.items():
        support_arguments = sorted(
            argument_id
            for argument_id, argument in arguments.items()
            if argument.get("active")
            and _conclusion_key(argument.get("conclusion", {})) == ("assumption", assumption_id)
            and argument.get("conclusion", {}).get("polarity", "support") == "support"
        )
        opposed_by = sorted(assumption_opposition.get(assumption_id, []))
        if assumption.get("commitment") == "withdrawn":
            support_state = "withdrawn"
        elif assumption_id in supported_assumptions and opposed_by:
            support_state = "contested"
        elif assumption_id in supported_assumptions:
            support_state = "accepted"
        elif opposed_by:
            support_state = "opposed"
        else:
            support_state = "unsupported"
        assumption["derived"] = {
            "support_state": support_state,
            "supporting_arguments": support_arguments,
            "opposing_attacks": opposed_by,
            "applicable": assumption_id in supported_assumptions,
        }

    for claim_id, claim in claims.items():
        positive = sorted(supporting.get(claim_id, []))
        negative = sorted(opposing.get(claim_id, []))
        if positive and negative:
            support_state = "contested"
        elif positive:
            support_state = "supported"
        elif negative:
            support_state = "opposed"
        else:
            support_state = "unsupported"
        historical_support = any(
            _conclusion_key(argument.get("conclusion", {})) == ("claim", claim_id)
            and argument.get("conclusion", {}).get("polarity", "support") == "support"
            for argument in arguments.values()
        )
        if claim.get("commitment") == "withdrawn":
            applicability = "out_of_scope"
        elif positive:
            applicability = "applicable"
        elif historical_support:
            applicability = "revalidation_required"
        else:
            applicability = "unknown"
        independence = support_independence(positive, argument_roots, arguments, evidence, policy)
        repair_paths = _repair_paths(claim_id, arguments, policy["revalidation_costs"])
        verified_paths = [
            argument_id
            for argument_id in positive
            if any(reference.get("type") == "verification" for reference in arguments[argument_id].get("premises", []))
        ]
        claim["derived"] = {
            "revision_state": "current",
            "commitment_state": claim.get("commitment", "asserted"),
            "support_state": support_state,
            "applicability_state": applicability,
            "verification_state": "verified" if verified_paths else "unverified",
            "workflow_state": "ready_for_revalidation" if applicability == "revalidation_required" else "actionable",
            "supporting_arguments": positive,
            "opposing_arguments": negative,
            "historical_support": historical_support,
            "root_evidence": sorted({root for item in positive for root in argument_roots.get(item, set())}),
            "independence": independence,
            "revalidation_plan": repair_paths,
        }
        if claim.get("commitment") == "asserted" and not positive:
            diagnostics.append(
                _diagnostic(
                    "UNSUPPORTED_ASSERTION",
                    [claim_id],
                    severity="warning",
                    why="the asserted current revision has no grounded active supporting argument",
                    new_state=support_state,
                    fixits=[{"level": 2, "action": "assert an explicit grounded argument or revise the commitment"}],
                )
            )
        if support_state == "contested":
            diagnostics.append(
                _diagnostic(
                    "CLAIM_CONTESTED",
                    [claim_id, *positive, *negative],
                    severity="warning",
                    why="both grounded supporting and opposing paths are active",
                    new_state="contested",
                    relation_paths=[positive, negative],
                    fixits=[{"level": 2, "action": "review the competing arguments"}],
                )
            )
            if policy["diagnostics"].get("scope_split"):
                diagnostics.append(
                    _diagnostic(
                        "POSSIBLE_SCOPE_SPLIT",
                        [claim_id],
                        severity="info",
                        why="the proposition has different active evidential outcomes",
                        fixits=[{"level": 2, "action": "consider separate scope-specific claim revisions"}],
                    )
                )
        if len(positive) > 1 and independence["independent_group_count"] < len(positive):
            diagnostics.append(
                _diagnostic(
                    "NONINDEPENDENT_SUPPORT",
                    [claim_id, *positive],
                    severity="info",
                    why="multiple active arguments share evidence or configured provenance constraints",
                    dependence=independence,
                    fixits=[{"level": 2, "action": "obtain a genuinely independent support path"}],
                )
            )
        if applicability == "revalidation_required":
            diagnostics.append(
                _diagnostic(
                    "SUPPORT_LOST",
                    [claim_id],
                    severity="warning",
                    why="historical support exists but no current supporting argument is active",
                    new_state="revalidation_required",
                    relation_paths=[item["argument_id"] for item in repair_paths],
                    fixits=[{"level": 2, "action": "execute a candidate revalidation path", "paths": repair_paths}],
                )
            )

    for attack_id in sorted(active_attacks):
        attack = attacks[attack_id]
        code = {"undercut": "ARGUMENT_UNDERCUT", "undermine": "PREMISE_UNDERMINED"}.get(attack.get("attack_type"))
        if code:
            diagnostics.append(
                _diagnostic(
                    code,
                    [attack_id, attack.get("target", {}).get("id", "")],
                    severity="warning",
                    why=f"grounded {attack.get('attack_type')} attack is active",
                    relation_paths=[[attack_id, attack.get("target", {}).get("id", "")]],
                    fixits=[{"level": 2, "action": "review or rebut the defeater"}],
                )
            )

    if policy["diagnostics"].get("orphan_evidence"):
        for evidence_id in sorted(set(evidence) - used_evidence):
            evidence[evidence_id]["orphan"] = True
            diagnostics.append(
                _diagnostic(
                    "ORPHAN_EVIDENCE",
                    [evidence_id],
                    severity="info",
                    why="the evidence is not used by an argument, attack, verification, question, or decision",
                    fixits=[
                        {"level": 2, "action": "interpret or attach the evidence"},
                        {"level": 2, "action": "mark it informational-only or dismiss it"},
                    ],
                )
            )
        for evidence_id in used_evidence:
            if evidence_id in evidence:
                evidence[evidence_id]["orphan"] = False

    for question_id, question in model["questions"].items():
        candidates = question.get("candidate_claim_ids", [])
        supported_candidates = [
            claim_id for claim_id in candidates if claims.get(claim_id, {}).get("derived", {}).get("support_state") == "supported"
        ]
        recorded = question.get("recorded_status", "opened")
        resolution_basis = question.get("resolution_basis_claim_ids", [])
        basis_changed = any(
            claims.get(claim_id, {}).get("derived", {}).get("support_state") != "supported"
            for claim_id in resolution_basis
        )
        if recorded == "resolved" and basis_changed and policy["diagnostics"].get("auto_reopen_questions"):
            question["derived_status"] = "reopened"
            diagnostics.append(
                _diagnostic(
                    "QUESTION_REOPENED",
                    [question_id, *resolution_basis],
                    severity="warning",
                    why="a claim used to resolve the question no longer has unopposed current support",
                    fixits=[{"level": 0, "action": "derived question state reopened"}],
                )
            )
        else:
            question["derived_status"] = recorded
        if question["derived_status"] in {"opened", "reopened", "updated"} and len(supported_candidates) == 1:
            diagnostics.append(
                _diagnostic(
                    "QUESTION_POSSIBLY_RESOLVED",
                    [question_id, *supported_candidates],
                    severity="info",
                    why="exactly one candidate answer currently has unopposed support",
                    fixits=[{"level": 2, "action": "confirm the semantic answer and resolve the question"}],
                )
            )

    for decision_id, decision in model["decisions"].items():
        changed_basis = [
            claim_id
            for claim_id in decision.get("basis_claim_ids", [])
            if claims.get(claim_id, {}).get("derived", {}).get("support_state") != "supported"
        ]
        changed_dependencies = [
            dependency.get("dependency_id", "")
            for dependency in decision.get("dependencies", [])
            if not _dependency_matches(dependency, dependencies)[0]
        ]
        changed_assumptions = [
            assumption.get("assumption_id", "")
            for assumption in decision.get("assumptions", [])
            if assumption.get("assumption_id", "") not in supported_assumptions
        ]
        decision["basis_changed"] = bool(changed_basis or changed_dependencies or changed_assumptions)
        decision["changed_basis_claim_ids"] = changed_basis
        decision["changed_dependency_ids"] = changed_dependencies
        decision["changed_assumption_ids"] = changed_assumptions
        if decision["basis_changed"]:
            diagnostics.append(
                _diagnostic(
                    "DECISION_BASIS_CHANGED",
                    [decision_id, *changed_basis, *changed_dependencies, *changed_assumptions],
                    severity="warning",
                    why="a recorded decision no longer has the same applicable epistemic basis",
                    fixits=[{"level": 2, "action": "review, revise, or reaffirm the decision"}],
                )
            )

    for dependency in dependencies.values():
        history = dependency.get("history", [])
        dependency["changed"] = len(history) > 1 and history[-1].get("value") != history[-2].get("value")
    for attack_id, attack in attacks.items():
        attack["active"] = attack_id in active_attacks

    edges: list[dict[str, str]] = []
    for argument_id, argument in arguments.items():
        for reference in argument.get("premises", []):
            edges.append({"source": reference.get("id", ""), "relation": "premise_of", "target": argument_id})
        for assumption in argument.get("assumptions", []):
            edges.append({"source": assumption.get("assumption_id", ""), "relation": "assumption_of", "target": argument_id})
        for dependency in argument.get("dependencies", []):
            edges.append({"source": dependency.get("dependency_id", ""), "relation": "dependency_of", "target": argument_id})
        _, conclusion_id = _conclusion_key(argument.get("conclusion", {}))
        edges.append({"source": argument_id, "relation": argument.get("conclusion", {}).get("polarity", "support"), "target": conclusion_id})
    for evidence_id, observation in evidence.items():
        artifact_id = observation.get("artifact_ref", {}).get("id", "")
        if artifact_id:
            edges.append({"source": artifact_id, "relation": "observed_as", "target": evidence_id})
    for attack_id, attack in attacks.items():
        edges.append({"source": attack_id, "relation": attack.get("attack_type", "attack"), "target": attack.get("target", {}).get("id", "")})
    for relation in model["relations"].values():
        edges.append({"source": relation.get("source", {}).get("id", ""), "relation": relation.get("relation", "related"), "target": relation.get("target", {}).get("id", "")})

    generation = max((operation.get("_generation", 0) for operation in operations), default=0)
    result = {
        "schema_version": "epistemic-state.v1",
        "generation": generation,
        "mission": model["mission"],
        "runs": model["runs"],
        "artifacts": artifacts,
        "evidence": evidence,
        "claims": claims,
        "assumptions": assumptions,
        "arguments": arguments,
        "attacks": attacks,
        "dependencies": dependencies,
        "verifications": model["verifications"],
        "questions": model["questions"],
        "decisions": model["decisions"],
        "warrants": model["warrants"],
        "relations": model["relations"],
        "suggestions": model["suggestions"],
        "attention": model["attention"],
        "retrieval_events": model["retrieval_events"],
        "checkpoints": model["checkpoints"],
        "diagnostics": normalize_diagnostics(
            sorted(diagnostics, key=lambda item: (item["code"], item["id"])),
            generation,
        ),
        "edges": sorted(edges, key=lambda item: (item["source"], item["relation"], item["target"])),
        "circular_entity_ids": sorted(circular_entities),
    }
    result["worker_state"] = derive_worker_state(result, policy)
    return result
