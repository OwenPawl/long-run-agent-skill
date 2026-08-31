---
name: long-run-agent
description: Run long Codex tasks through a worker subagent so the main chat stays thin and avoids automatic compaction. Use when the user wants a long-running, autonomous, steerable task with a shared `.agent/live.md` control plane for user direction, agent questions, and user replies; when orchestration should happen in subagents rather than the main thread; or when the user explicitly asks to prevent main-thread context growth during extended work.
---

# Long Run Agent

## Overview

Use one worker subagent as the only place where substantial task state accumulates. Keep the main thread limited to loading this skill, creating or naming the run directory, spawning the worker, and reporting the `.agent/live.md` path.

Create exactly one subagent total: the main thread creates the worker, and the worker must not create any additional subagents.

Keep the parent turn alive when the host provides a subagent wait or mailbox mechanism. A durable control plane preserves state, but it does not by itself create a new user-visible chat message after the parent turn ends.

File edits cannot literally interrupt the model. Treat `.agent/live.md` as a mailbox: the worker runs the watcher script and checks it at startup, after each meaningful batch of work, before risky changes, and whenever it is waiting for user input.

## Mission Harness

For long runs that need durable truth preservation, use the minimal mission
harness:

```bash
python3 <skill-path>/scripts/install_skill.py --json
python3 <skill-path>/scripts/mission_harness.py --root <project-root> init
python3 <skill-path>/scripts/mission_harness.py --root <project-root> run start --goal "bounded task"
python3 <skill-path>/scripts/mission_harness.py --root <project-root> claim add --id <claim-id> --claim "..." --source-path <path> --source-kind <kind> --status tested
python3 <skill-path>/scripts/mission_harness.py --root <project-root> claim disprove --id <claim-id> --evidence-artifact <artifact-id> --notes "..."
python3 <skill-path>/scripts/mission_harness.py --root <project-root> claim history --id <claim-id>
python3 <skill-path>/scripts/mission_harness.py --root <project-root> relation add --source-type artifact --source-id <artifact-id> --relation supports --target-type claim_revision --target-id <revision-id>
python3 <skill-path>/scripts/mission_harness.py --root <project-root> relation list --endpoint-type claim --endpoint-id <claim-id>
python3 <skill-path>/scripts/mission_harness.py --root <project-root> run close --outcome "..." --command "..." --test "..." --next-action "..."
python3 <skill-path>/scripts/mission_harness.py --root <project-root> friction add --category verification_gap --description "..." --impact "..." --proposed-harness-need "..."
python3 <skill-path>/scripts/mission_harness.py --root <project-root> friction settle --id <friction-id> --status consolidated --root-cause-id <root-id> --release-disposition deferred --rationale "..."
python3 <skill-path>/scripts/mission_harness.py --root <project-root> friction report --fail-on-ambiguous-open
python3 <skill-path>/scripts/mission_harness.py --root <project-root> state preflight
python3 <skill-path>/scripts/mission_harness.py --root <project-root> state compact-live
python3 <skill-path>/scripts/mission_harness.py --root <project-root> validate
python3 <skill-path>/scripts/mission_harness.py --root <project-root> state summarize
python3 <skill-path>/scripts/mission_harness.py --root <project-root> index rebuild
python3 <skill-path>/scripts/mission_harness.py --root <project-root> index search "query terms"
python3 <skill-path>/scripts/mission_harness.py --root <project-root> index status
python3 <skill-path>/scripts/mission_records_recent.py --root <project-root> claims --limit 5
python3 <skill-path>/scripts/mission_records_recent.py --root <project-root> relations --limit 5
python3 <skill-path>/scripts/mission_artifact_materialize.py --root <project-root> --id <artifact-id>
python3 <skill-path>/scripts/mission_artifact_materialize.py --root <project-root> --path <new-evidence-path>
python3 <skill-path>/scripts/mission_artifact_materialize.py --root <project-root> --path <input-path> --stage-copy /private/tmp/<stable-input>
python3 <skill-path>/scripts/watch_live_file.py <project-root>/.agent/live.md --create --interval 1
python3 <skill-path>/scripts/reveal_live_file.py <project-root>/.agent/live.md --skip-if-open
```

When a local MCP client is available, `long-run-agent-mcp` exposes the same
authoritative harness through namespaced `mission_*` tools. Prefer
`mission_control_read` at startup and before major steps; pass its previous
SHA-256 so unchanged `live.md` content is not repacked into context. A composed
MCP server may register these tools with
`long_run_agent_skill.mcp_tools.register_mission_tools`, but `.agent/` remains
the only mission truth store.

The harness creates a `.agent/` directory with a human-readable control plane
(`live.md`), durable Markdown state (`current_state.md`, `constitution.md`,
`known_failures.md`, `decisions.md`), and portable structured records
(`runs.jsonl`, `claims.json`, `artifacts.json`, `friction.jsonl`,
`evidence_relations.jsonl`).

Use it when a run needs to preserve claims, artifacts, failures, verification,
and process friction over time. Do not use it to add ceremony. If a piece of
state matters after compaction or handoff, record it outside `live.md`.

Operational rules:

1. At the beginning of substantial work, read `.agent/live.md`,
   `.agent/current_state.md`, `.agent/known_failures.md`,
   `.agent/decisions.md`, `.agent/claims.json`,
   `.agent/evidence_relations.jsonl`, and recent `.agent/runs.jsonl` records
   when they exist.
2. Before major or hard-to-unwind steps, re-read `.agent/live.md`.
3. At run close, record commands, tests, files changed, failures, claims,
   artifacts, evidence relationship ids, and next actions with `run close`.
4. Record process or tooling pain with `friction add` instead of burying it in
   chat-only notes. If the pain repeats a known pattern, attach
   `--root-cause-id` or settle the old item instead of creating another
   disconnected open item.
5. Run `validate` before treating the harness state as reliable.
6. During an open run, `state summarize` copies the active goal, failures, and
   next actions from `live.md` into `current_state.md`; keep those sections current.
7. On macOS, initialized state operations automatically request any evicted
   File Provider state before reading; use `state preflight` when a diagnostic
   readiness report is needed.
8. If an open run makes `live.md` too large to serve as a control plane, first
   record durable claims/artifacts/friction, then run `state compact-live`.
   It validates those durable references and archives the verbose snapshot
   before replacing evidence-list sections with a typed archive pointer. Run
   close recovers genuine verification and claim/artifact references through
   archive chains.
9. If an evidence path may be File Provider-backed or a verification read
   stalls, run `mission_artifact_materialize.py --id <artifact-id>` for a
   recorded artifact, or `--path <new-evidence-path>` before registration.
   This verifies availability only; it does not interpret evidence or upgrade
   a claim.
10. If a long-running verifier must consume an evictable regular-file artifact,
    add `--stage-copy <new-local-path>` and pass the verified staged copy to the
    verifier. The command will not overwrite an existing staged path.
11. When previous work becomes hard to rediscover with direct file reads, run
    `index rebuild` and `index search`. Treat the SQLite database as a derived
    convenience index only; durable truth remains in Markdown, JSON, and JSONL.
12. When you need the latest claims, artifacts, friction, or runs, use
    `mission_records_recent.py` instead of hand-parsing state files and
    guessing each JSON shape.
13. Before release, handoff, or a long run closeout, run `friction report`.
    Ambiguous open friction means the ledger needs triage. Preserve repeated
    evidence, but consolidate it around root causes, release dispositions, and
    verification paths so future agents can tell repeated evidence from
    independent unresolved issues.
14. Treat claims as append-only revisions. Reuse the stable claim id with
    `claim add` to advance it; inspect `claim history` rather than overwriting
    prior conclusions. When evidence disproves a claim, register that evidence
    as uniquely identified artifacts and use `claim disprove`. Never use a
    generic status edit that severs the disproval from its evidence.
15. Use `relation add` for explicit support, refutation, corroboration,
    contradiction, derivation, reproduction, documentation, causation,
    supersession, or retraction. Both typed endpoints must resolve, and an
    artifact endpoint must have a unique id.

## Main-Thread Workflow

1. Pick a run directory outside the chat context. Default to `~/Documents/codex-long-runs/<YYYYMMDD-HHMMSS>-<slug>/`.
2. Initialize the mission harness in that directory and use `<run-dir>/.agent/live.md` as the only active steering file.
3. Spawn exactly one worker subagent for the actual task. Use `fork_context=false` when available so the worker starts from the minimal prompt instead of inheriting the main chat history.
4. Do not spawn any other subagents. The worker also must not spawn child subagents.
5. Pass the worker:
   - the user's task verbatim,
   - this skill path,
   - the run directory,
   - the live control path,
   - the instruction that all orchestration, exploration, implementation, and verification belong inside the worker context without creating more subagents.
6. Do not duplicate the worker's work in the main thread. Do not stream detailed progress back into the main thread. If the user sends steering in chat, append or forward only that steering to `live.md` or the worker.
7. Tell the user where `live.md` is and that they can edit it to steer, answer questions, stop, or request status.
8. If the host provides a subagent wait or mailbox mechanism, keep the parent turn active with that mechanism. Prefer long event-driven waits over busy polling. Do not send a final response merely because the worker has started.
9. When the worker reports completion, a blocker, or a question requiring user input, immediately send the user a self-contained status message. The user must not need to ask for progress to surface an already-recorded terminal state.
10. If the host cannot keep the parent turn active or wake it after return, state before returning that automatic chat notification is unavailable and that `live.md` is the status source. Do not promise a later chat message.
11. Do not treat apparent task completion as the end of the durable worker session. The worker should go dormant and wait for the next `live.md` update unless the user explicitly directs it to stop.

Worker prompt template:

```text
Use the long-run-agent skill at <skill-path> as the operating procedure.

Task:
<verbatim user task>

Run directory: <run-dir>
Live control: <run-dir>/.agent/live.md

You are the only worker for this long run. Keep the main thread thin: do all substantive orchestration, research, implementation, verification, and state tracking in your own context and artifacts. Do not spawn, delegate to, or request any additional subagents. Create and maintain `.agent/live.md`. Run the watcher script from the skill and check it regularly. If you need user input, write the question into `live.md` and wait for the user's reply there. Treat STOP or PAUSE directives in `live.md` as higher priority than the original task.
Before becoming dormant or waiting for user input, send the parent agent a concise completion, blocked, or question message using the host's subagent mailbox when available. Recording state in `live.md` remains mandatory and is not replaced by the message.
```

## Worker Workflow

1. Create the run directory.
2. Initialize the mission harness, then start the watcher so it creates `live.md` if needed:

```bash
python3 <skill-path>/scripts/mission_harness.py --root <run-dir> init
python3 <skill-path>/scripts/watch_live_file.py <run-dir>/.agent/live.md --create --interval 1
```

3. At the start of the session, reveal `live.md` in Finder, Windows File Explorer, or the local file manager unless it already appears to be open:

```bash
python3 <skill-path>/scripts/reveal_live_file.py <run-dir>/.agent/live.md --skip-if-open
```

4. Keep the watcher session running. Poll its output after each batch of work and before decisions that would be hard to unwind.
5. Do not create child subagents. Complete the long run in this worker context, using local tools and durable run-directory artifacts instead of delegation.
6. Maintain `.agent/live.md` with the stable sections created by `init`.
7. If blocked on a user decision, update `Agent Questions`, set `Run Status` to waiting, notify the parent through the host mailbox when available, and wait for a file change before continuing.
8. If `live.md` says `STOP`, stop after making the workspace consistent and write a brief status. If it says `PAUSE`, stop taking new actions and wait for `RESUME`.
9. Save durable artifacts in the run directory: notes, logs, generated files, validation outputs, and final summaries that would otherwise bloat the main chat.
10. When the current task appears complete, do not exit. Update `Agent Status` to `dormant`, write the completion summary and artifact paths into `live.md` or durable run records, notify the parent through the host mailbox when available, then wait for the next file update.
11. End the worker only when the user gives an explicit termination directive such as `STOP` or `FINALIZE AND STOP`. A plain `FINALIZE` means write a concise final summary and then return to dormant waiting.
12. Keep final responses concise. Point to the run directory and the important artifact paths instead of pasting long logs.

## Live File Rules

Use plain Markdown. Keep the newest user instruction as authoritative when it conflicts with earlier instructions.

Never rely on chat-only steering during the worker run. If important direction arrives in chat, copy it into `.agent/live.md` so the worker's state remains self-contained.

Recommended initial file:

```markdown
# Live Control

## User Updates
- Add steering here. Newer instructions override older conflicting instructions.

## Current Goal
- Add the active goal here.

## Constraints
- Add constraints here.

## Agent Status
- Status: starting

## Interrupts / Corrections
- None recorded yet.

## Decisions Made This Run
- None recorded yet.

## Commands Run
- None recorded yet.

## Tests / Verification
- None recorded yet.

## Failures / Blockers
- None recorded yet.

## Claims Touched
- None recorded yet.

## Artifacts Produced
- None recorded yet.

## Friction Observed
- None recorded yet.

## Next Actions
- None recorded yet.
```

## Watcher Script

Use `scripts/watch_live_file.py` to create `live.md` and print the full file whenever it changes. The script is intentionally simple and portable; it uses file hashing plus polling instead of OS-specific file events.

Typical worker usage:

```bash
python3 /path/to/long-run-agent/scripts/watch_live_file.py /path/to/project/.agent/live.md --create --interval 1
```

For one-shot checks in scripts or diagnostics:

```bash
python3 /path/to/long-run-agent/scripts/watch_live_file.py /path/to/project/.agent/live.md --create --once
```

## File Manager Reveal Script

Use `scripts/reveal_live_file.py` once at session startup. On macOS it runs `open -R` to reveal `live.md` in Finder. On native Windows it runs File Explorer with `/select`. Under WSL it converts the path with `wslpath -w` and then opens Windows File Explorer. On other Linux environments it opens the containing folder with `xdg-open` when available.

With `--skip-if-open`, it first makes a best-effort open-file check. macOS, Linux, and WSL use `lsof` when present. Native Windows uses Sysinternals `handle.exe` or `handle64.exe` when present; otherwise it cannot reliably detect already-open files and will still reveal the file.

```bash
python3 /path/to/long-run-agent/scripts/reveal_live_file.py /path/to/project/.agent/live.md --skip-if-open
```
