"""Versioned default policy for deterministic epistemic reduction."""

from __future__ import annotations

from typing import Any


POLICY_VERSION = "epistemic-policy.v1"


def default_policy() -> dict[str, Any]:
    return {
        "schema_version": POLICY_VERSION,
        "diagnostics": {
            "auto_reopen_questions": True,
            "auto_resolve_questions": False,
            "orphan_evidence": True,
            "scope_split": True,
        },
        "independence": {
            "require_disjoint_root_evidence": True,
            "require_different_verifier_runs": False,
            "require_different_host_generations": False,
        },
        "fixits": {
            "safe_structural": True,
            "semantic": "suggest_only",
        },
        "revalidation_costs": {
            "dependency_refresh": 1,
            "rerun_verifier": 2,
            "reacquire_evidence": 3,
            "semantic_review": 5,
        },
        "retrieval": {
            "weights": {
                "shared_root": 8.0,
                "correlated_root": 4.0,
                "same_subject": 3.0,
                "shared_dependency": 3.0,
                "same_warrant": 2.0,
                "dialectical": 4.0,
                "working_scope": 1.0,
                "historical_usefulness": 1.5,
                "corrective_value": 8.0,
                "material_change": 14.0,
                "coverage_redundancy": 16.0,
                "current_state_redundancy": 10.0,
                "novelty_penalty": 12.0,
                "dismissal_penalty": 8.0,
            },
            "max_candidates": 50,
            "max_capsules": 5,
            "capsule_description_chars": 220,
            "max_relation_path": 4,
            "max_family_members": 1,
            "diversity_penalty": 0.55,
            "surface_inhibition_horizon": 2,
            "corrective_min_basis_signals": 1,
        },
        "worker_state": {
            "content_horizon": 12,
            "salience_horizon": 4,
            "structural_horizon": 16,
        },
        "context": {
            "max_chars": 12000,
            "max_entities": 24,
            "max_diagnostics": 12,
            "max_recall_capsules": 5,
            "max_proof_depth": 4,
        },
    }
