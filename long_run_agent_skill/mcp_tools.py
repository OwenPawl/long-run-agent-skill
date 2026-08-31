"""Reusable mission-tool registration for standalone or composed MCP servers."""

from __future__ import annotations

from typing import Any

from .mcp_runtime import (
    BLOCKED,
    HarnessRunner,
    HarnessSettings,
    add_option,
    append_options,
    control_snapshot,
    envelope,
)


def register_mission_tools(server: Any, settings: HarnessSettings | None = None) -> list[str]:
    """Register the domain-neutral mission surface and return registered names."""
    runner = HarnessRunner(settings or HarnessSettings.discover())
    names: list[str] = []

    def tool(name: str):
        names.append(name)
        return server.tool(name=name)

    @tool("mission_init")
    def mission_init(root: str, force: bool = False, reveal_live: bool = True) -> dict[str, Any]:
        """Initialize .agent state; optionally reveal live.md to the local operator."""
        args = ["init"] + (["--force"] if force else [])
        result = runner.run(root, args)
        if result["status"] == "success" and reveal_live:
            result["data"] = {
                "harness": result.get("data"),
                "reveal": runner.reveal_live(root),
            }
        return result

    @tool("mission_control_read")
    def mission_control_read(root: str, known_sha256: str = "") -> dict[str, Any]:
        """Read live.md only when its SHA-256 differs from the caller's last snapshot."""
        readiness = runner.run(root, ["state", "preflight"])
        if readiness["status"] != "success":
            readiness["note"] = "live control preflight failed before content read"
            return readiness
        return control_snapshot(root, known_sha256)

    @tool("mission_validate")
    def mission_validate(root: str) -> dict[str, Any]:
        """Validate Markdown, JSON, and JSONL mission truth."""
        return runner.run(root, ["validate"])

    @tool("mission_run_start")
    def mission_run_start(
        root: str,
        goal: str,
        constraints: list[str] | None = None,
        run_id: str = "",
    ) -> dict[str, Any]:
        """Start a bounded durable run and return its recorded run id."""
        args = ["run", "start", "--goal", goal]
        append_options(args, "--constraint", constraints)
        add_option(args, "--run-id", run_id)
        return runner.run(root, args)

    @tool("mission_run_close")
    def mission_run_close(
        root: str,
        outcome: str,
        run_id: str = "",
        summary: str = "",
        commands: list[str] | None = None,
        tests: list[str] | None = None,
        files_changed: list[str] | None = None,
        failures: list[str] | None = None,
        claims: list[str] | None = None,
        artifacts: list[str] | None = None,
        relations: list[str] | None = None,
        decisions: list[str] | None = None,
        next_actions: list[str] | None = None,
        no_verification_reason: str = "",
    ) -> dict[str, Any]:
        """Close a run with verification, failures, evidence, and next actions."""
        args = ["run", "close", "--outcome", outcome]
        add_option(args, "--run-id", run_id)
        add_option(args, "--summary", summary)
        append_options(args, "--command", commands)
        append_options(args, "--test", tests)
        append_options(args, "--file-changed", files_changed)
        append_options(args, "--failure", failures)
        append_options(args, "--claim", claims)
        append_options(args, "--artifact", artifacts)
        append_options(args, "--relation", relations)
        append_options(args, "--decision", decisions)
        append_options(args, "--next-action", next_actions)
        add_option(args, "--no-verification-reason", no_verification_reason)
        return runner.run(root, args)

    @tool("mission_friction_add")
    def mission_friction_add(
        root: str,
        category: str,
        description: str,
        impact: str,
        proposed_harness_need: str,
        severity: str = "medium",
        run_id: str = "",
        friction_id: str = "",
        root_cause_id: str = "",
        verification_command: str = "",
        notes: str = "",
    ) -> dict[str, Any]:
        """Record one friction observation, linking recurrence to a root cause when known."""
        args = [
            "friction",
            "add",
            "--category",
            category,
            "--description",
            description,
            "--impact",
            impact,
            "--proposed-harness-need",
            proposed_harness_need,
            "--severity",
            severity,
        ]
        add_option(args, "--run-id", run_id)
        add_option(args, "--id", friction_id)
        add_option(args, "--root-cause-id", root_cause_id)
        add_option(args, "--verification-command", verification_command)
        add_option(args, "--notes", notes)
        return runner.run(root, args)

    @tool("mission_claim_add")
    def mission_claim_add(
        root: str,
        claim: str,
        source_path: str,
        source_kind: str,
        status: str,
        verification_command: str = "",
        claim_id: str = "",
        run_id: str = "",
        last_checked_at: str = "",
        confidence: str = "unverified",
        notes: str = "",
    ) -> dict[str, Any]:
        """Append a claim revision whose trust is tied to a verification path."""
        args = [
            "claim",
            "add",
            "--claim",
            claim,
            "--source-path",
            source_path,
            "--source-kind",
            source_kind,
            "--status",
            status,
            "--confidence",
            confidence,
        ]
        add_option(args, "--id", claim_id)
        add_option(args, "--run-id", run_id)
        add_option(args, "--verification-command", verification_command)
        add_option(args, "--last-checked-at", last_checked_at)
        add_option(args, "--notes", notes)
        return runner.run(root, args)

    @tool("mission_claim_disprove")
    def mission_claim_disprove(
        root: str,
        claim_id: str,
        evidence_artifacts: list[str],
        run_id: str = "",
        verification_command: str = "",
        last_checked_at: str = "",
        confidence: str = "verified",
        notes: str = "",
    ) -> dict[str, Any]:
        """Append a disproved revision and link each artifact as refuting evidence."""
        args = ["claim", "disprove", "--id", claim_id]
        append_options(args, "--evidence-artifact", evidence_artifacts)
        add_option(args, "--run-id", run_id)
        add_option(args, "--verification-command", verification_command)
        add_option(args, "--last-checked-at", last_checked_at)
        add_option(args, "--confidence", confidence)
        add_option(args, "--notes", notes)
        return runner.run(root, args)

    @tool("mission_claim_history")
    def mission_claim_history(root: str, claim_id: str) -> dict[str, Any]:
        """Read every immutable revision and evidence relationship for one claim."""
        return runner.run(root, ["claim", "history", "--id", claim_id])

    @tool("mission_relation_add")
    def mission_relation_add(
        root: str,
        source_type: str,
        source_id: str,
        relation: str,
        target_type: str,
        target_id: str,
        relation_id: str = "",
        run_id: str = "",
        confidence: str = "unverified",
        notes: str = "",
    ) -> dict[str, Any]:
        """Append a typed relationship between durable mission records."""
        args = [
            "relation",
            "add",
            "--source-type",
            source_type,
            "--source-id",
            source_id,
            "--relation",
            relation,
            "--target-type",
            target_type,
            "--target-id",
            target_id,
            "--confidence",
            confidence,
        ]
        add_option(args, "--id", relation_id)
        add_option(args, "--run-id", run_id)
        add_option(args, "--notes", notes)
        return runner.run(root, args)

    @tool("mission_relation_list")
    def mission_relation_list(
        root: str,
        endpoint_type: str = "",
        endpoint_id: str = "",
        relation: str = "",
    ) -> dict[str, Any]:
        """List typed relationships, optionally filtering by endpoint and relation."""
        args = ["relation", "list"]
        add_option(args, "--endpoint-type", endpoint_type)
        add_option(args, "--endpoint-id", endpoint_id)
        add_option(args, "--relation", relation)
        return runner.run(root, args)

    @tool("mission_artifact_add")
    def mission_artifact_add(
        root: str,
        path: str,
        kind: str,
        description: str,
        verification_command: str = "",
        artifact_id: str = "",
        run_id: str = "",
        notes: str = "",
    ) -> dict[str, Any]:
        """Register a durable artifact and its verification path."""
        args = [
            "artifact",
            "add",
            "--path",
            path,
            "--kind",
            kind,
            "--description",
            description,
        ]
        add_option(args, "--id", artifact_id)
        add_option(args, "--run-id", run_id)
        add_option(args, "--verification-command", verification_command)
        add_option(args, "--notes", notes)
        return runner.run(root, args)

    @tool("mission_state_summarize")
    def mission_state_summarize(root: str) -> dict[str, Any]:
        """Regenerate current_state.md from authoritative mission records."""
        return runner.run(root, ["state", "summarize"])

    @tool("mission_index_search")
    def mission_index_search(
        root: str,
        query: str,
        kind: str = "",
        limit: int = 20,
    ) -> dict[str, Any]:
        """Search the derived SQLite index; Markdown and JSON remain authoritative."""
        args = ["index", "search", query, "--limit", str(limit)]
        add_option(args, "--kind", kind)
        return runner.run(root, args)

    @tool("mission_run")
    def mission_run(root: str, argv: list[str]) -> dict[str, Any]:
        """Escape hatch for implemented harness commands not promoted to first-class tools."""
        lowered = [item.lower() for item in argv]
        if not argv or "--root" in lowered or "--force" in lowered:
            return envelope(
                BLOCKED,
                note="empty argv, root overrides, and force operations require a first-class tool",
            )
        return runner.run(root, argv)

    return names


MISSION_OPERATING_CONTRACT = (
    "Use mission_init once, then mission_run_start for substantial work. Read "
    "mission_control_read at startup and before major steps, passing the prior SHA-256 "
    "to avoid duplicate context. Preserve claims as append-only revisions with verification "
    "paths, and use typed evidence relationships when artifacts support or refute them. "
    "Preserve failures and friction in durable records. Repeated friction should reference a "
    "known root cause rather than inflate the actionable open set. Close every run with "
    "commands/tests or an explicit no-verification reason, then summarize and validate."
)
