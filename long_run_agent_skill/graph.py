"""Deterministic graph algorithms shared by reduction and retrieval."""

from __future__ import annotations

from typing import Any


def correlation_reasons(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    fields = {
        "artifact_content_hash": "same_content",
        "artifact_id": "same_artifact",
        "source": "shared_source",
        "subject": "shared_subject",
        "measurement_process": "same_measurement_process",
        "producer": "shared_producer",
        "source_run": "same_run",
        "verifier_family": "same_verifier_family",
        "host_generation": "same_host_generation",
    }
    for field, reason in fields.items():
        if left.get(field) not in (None, "", []) and left.get(field) == right.get(field):
            reasons.append(reason)
    return reasons


def strongly_connected_components(graph: dict[str, set[str]]) -> list[list[str]]:
    index = 0
    stack: list[str] = []
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for neighbor in sorted(graph.get(node, set())):
            if neighbor not in indices:
                visit(neighbor)
                lowlinks[node] = min(lowlinks[node], lowlinks[neighbor])
            elif neighbor in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[neighbor])
        if lowlinks[node] == indices[node]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node:
                    break
            components.append(sorted(component))

    for node in sorted(graph):
        if node not in indices:
            visit(node)
    return components


def support_independence(
    argument_ids: list[str],
    argument_roots: dict[str, set[str]],
    arguments: dict[str, Any],
    evidence: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    settings = policy["independence"]
    pairs: list[dict[str, Any]] = []
    adjacency: dict[str, set[str]] = {item: set() for item in argument_ids}
    dependence_features = {
        "same_content",
        "same_artifact",
        "same_measurement_process",
        "same_run",
        "same_host_generation",
        "same_verifier_family",
        "shared_producer",
    }
    for index, left_id in enumerate(argument_ids):
        for right_id in argument_ids[index + 1 :]:
            left_roots = argument_roots.get(left_id, set())
            right_roots = argument_roots.get(right_id, set())
            reasons: list[str] = []
            overlap = sorted(left_roots & right_roots)
            if settings.get("require_disjoint_root_evidence") and overlap:
                reasons.append("shared_root_evidence:" + ",".join(overlap))
            for left_root in left_roots:
                for right_root in right_roots:
                    if left_root != right_root:
                        reasons.extend(
                            item
                            for item in correlation_reasons(
                                evidence.get(left_root, {}), evidence.get(right_root, {})
                            )
                            if item in dependence_features
                        )
            if settings.get("require_different_verifier_runs"):
                left_run = arguments[left_id].get("verifier_run")
                right_run = arguments[right_id].get("verifier_run")
                if left_run and left_run == right_run:
                    reasons.append("same_verifier_run")
            if settings.get("require_different_host_generations"):
                left_host = arguments[left_id].get("host_generation")
                right_host = arguments[right_id].get("host_generation")
                if left_host and left_host == right_host:
                    reasons.append("same_host_generation")
            independent = not reasons
            if not independent:
                adjacency[left_id].add(right_id)
                adjacency[right_id].add(left_id)
            pairs.append(
                {
                    "left": left_id,
                    "right": right_id,
                    "independent": independent,
                    "dependence_reasons": sorted(set(reasons)),
                }
            )
    groups: list[list[str]] = []
    remaining = set(argument_ids)
    while remaining:
        seed = min(remaining)
        group = set()
        queue = [seed]
        while queue:
            current = queue.pop()
            if current in group:
                continue
            group.add(current)
            queue.extend(adjacency[current] - group)
        remaining -= group
        groups.append(sorted(group))
    return {
        "pairs": pairs,
        "dependent_groups": groups,
        "independent_group_count": len(groups),
    }
