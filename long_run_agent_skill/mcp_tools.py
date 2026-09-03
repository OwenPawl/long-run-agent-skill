"""Reusable MCP registration over the standalone epistemic compiler."""

from __future__ import annotations

import inspect
from functools import wraps
from typing import Any

from .call_tracker import track_public_call
from .compiler import EpistemicCompiler
from .mcp_runtime import compiler_call, control_snapshot


def register_mission_tools(server: Any) -> list[str]:
    """Register the small semantic mission API and return its tool names."""
    names: list[str] = []

    def tool(name: str):
        def register(handler):
            names.append(name)
            signature = inspect.signature(handler)

            @wraps(handler)
            def tracked(*args, **kwargs):
                bound = signature.bind_partial(*args, **kwargs)
                request = dict(bound.arguments)
                root = request.get("root", "")
                return track_public_call(
                    root,
                    name,
                    request,
                    lambda: handler(*args, **kwargs),
                    surface="mcp",
                )

            return server.tool(name=name)(tracked)

        return register

    @tool("mission_init")
    def mission_init(root: str, goal: str = "", force: bool = False) -> dict[str, Any]:
        """Initialize ledger, durable retrieval telemetry, policy, and derived views."""
        return compiler_call(root, lambda: EpistemicCompiler(root).initialize(goal=goal, force=force))

    @tool("mission_control_read")
    def mission_control_read(root: str, known_sha256: str = "") -> dict[str, Any]:
        """Read live.md only when its SHA-256 changed."""
        return control_snapshot(root, known_sha256)

    @tool("mission_start")
    def mission_start(root: str, goal: str, steering: str = "", run_id: str = "") -> dict[str, Any]:
        """Start a mission run through one semantic transaction."""
        return compiler_call(root, lambda: EpistemicCompiler(root).start(goal, steering, run_id))

    @tool("mission_context")
    def mission_context(
        root: str,
        cursor: dict[str, Any] | None = None,
        since_generation: int | None = None,
        max_chars: int | None = None,
        max_entities: int | None = None,
    ) -> dict[str, Any]:
        """Return bounded WorkerScope/ActiveDelta context with explicit continuation."""
        return compiler_call(
            root,
            lambda: EpistemicCompiler(root).context(
                cursor=cursor,
                since_generation=since_generation,
                max_chars=max_chars,
                max_entities=max_entities,
            ),
        )

    @tool("mission_observe")
    def mission_observe(root: str, kind: str, data: dict[str, Any], preview: bool = False) -> dict[str, Any]:
        """Record artifact, evidence, dependency, or verification semantics."""
        return compiler_call(root, lambda: EpistemicCompiler(root).observe(kind, data, preview=preview))

    @tool("mission_assert")
    def mission_assert(root: str, data: dict[str, Any], preview: bool = False) -> dict[str, Any]:
        """Assert or revise a claim and optionally its explicit argument."""
        return compiler_call(root, lambda: EpistemicCompiler(root).assert_claim(data, preview=preview))

    @tool("mission_assumption")
    def mission_assumption(root: str, data: dict[str, Any], preview: bool = False) -> dict[str, Any]:
        """Assert or revise a semantic assumption."""
        return compiler_call(root, lambda: EpistemicCompiler(root).assert_assumption(data, preview=preview))

    @tool("mission_argument")
    def mission_argument(root: str, data: dict[str, Any], preview: bool = False) -> dict[str, Any]:
        """Assert an explicit warranted argument."""
        return compiler_call(root, lambda: EpistemicCompiler(root).assert_argument(data, preview=preview))

    @tool("mission_attack")
    def mission_attack(root: str, data: dict[str, Any], preview: bool = False) -> dict[str, Any]:
        """Assert a grounded and warranted rebut, undercut, or undermine."""
        return compiler_call(root, lambda: EpistemicCompiler(root).attack(data, preview=preview))

    @tool("mission_ask")
    def mission_ask(root: str, data: dict[str, Any], preview: bool = False) -> dict[str, Any]:
        """Open a question representing known ignorance."""
        return compiler_call(root, lambda: EpistemicCompiler(root).ask(data, preview=preview))

    @tool("mission_decide")
    def mission_decide(root: str, data: dict[str, Any], preview: bool = False) -> dict[str, Any]:
        """Record a decision with claims, evidence, assumptions, and dependency basis."""
        return compiler_call(root, lambda: EpistemicCompiler(root).decide(data, preview=preview))

    @tool("mission_update")
    def mission_update(
        root: str,
        delta: dict[str, Any],
        preview: bool = False,
        expected_generation: int | None = None,
    ) -> dict[str, Any]:
        """Compile semantic input through the ordinary EpistemicDelta boundary."""
        return compiler_call(
            root,
            lambda: EpistemicCompiler(root).update(
                delta,
                preview=preview,
                expected_generation=expected_generation,
            ),
        )

    @tool("mission_search")
    def mission_search(
        root: str,
        text: str,
        cursor: dict[str, Any] | None = None,
        limit: int = 50,
        entity_types: list[str] | None = None,
    ) -> dict[str, Any]:
        """Discover compact candidates using structured epistemic semantics."""
        return compiler_call(
            root,
            lambda: EpistemicCompiler(root).search(
                text,
                cursor=cursor,
                limit=limit,
                entity_types=entity_types,
            ),
        )

    @tool("mission_inspect")
    def mission_inspect(
        root: str,
        entity_id: str = "",
        facets: list[str] | None = None,
        cursor: dict[str, Any] | None = None,
        limit: int = 20,
        max_depth: int | None = None,
        since_generation: int | None = None,
    ) -> dict[str, Any]:
        """Navigate bounded normalized topology without dumping full payloads."""
        return compiler_call(
            root,
            lambda: EpistemicCompiler(root).inspect(
                entity_id,
                facets=facets,
                cursor=cursor,
                limit=limit,
                max_depth=max_depth,
                since_generation=since_generation,
            ),
        )

    @tool("mission_feedback")
    def mission_feedback(root: str, event_id: str, entity_id: str = "", action: str = "dismissed") -> dict[str, Any]:
        """Dismiss recall contextually or accept/reject a relation suggestion."""
        return compiler_call(
            root, lambda: EpistemicCompiler(root).feedback(event_id, entity_id, action)
        )

    @tool("mission_checkpoint")
    def mission_checkpoint(root: str, checkpoint_id: str = "") -> dict[str, Any]:
        """Persist a compact pointer into authoritative state for later resume."""
        return compiler_call(root, lambda: EpistemicCompiler(root).checkpoint(checkpoint_id))

    @tool("mission_resume")
    def mission_resume(root: str) -> dict[str, Any]:
        """Verify ledgers, rebuild offline, and return a bounded epistemic frontier."""
        return compiler_call(root, lambda: EpistemicCompiler(root).resume())

    @tool("mission_close")
    def mission_close(
        root: str, outcome: str, summary: str = "", next_actions: list[str] | None = None
    ) -> dict[str, Any]:
        """Close the active run without discarding its epistemic history."""
        return compiler_call(
            root, lambda: EpistemicCompiler(root).close(outcome, summary, next_actions)
        )

    @tool("mission_rebuild")
    def mission_rebuild(root: str) -> dict[str, Any]:
        """Delete no authority; deterministically rebuild all disposable views offline."""
        return compiler_call(root, lambda: EpistemicCompiler(root).rebuild())

    @tool("mission_validate")
    def mission_validate(root: str) -> dict[str, Any]:
        """Validate both hash chains, policy, checkpoint, and materialized state hash."""
        return compiler_call(root, lambda: EpistemicCompiler(root).validate())

    return names


MISSION_OPERATING_CONTRACT = (
    "Use one worker and .agent/live.md for steering. Express semantic intent through "
    "mission_update or its semantic front ends, then use mission_search and mission_inspect. "
    "Treat committed "
    "semantics, derived consequences, diagnostics, suggestions, and recall as distinct. "
    "Use mission_context at startup/resume, checkpoint before interruption, and validate "
    "before release. Suggestions never become epistemic authority until explicitly accepted."
)
