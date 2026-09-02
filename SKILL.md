---
name: long-run-agent
description: Run long Codex tasks through one steerable worker and a standalone local epistemic compiler. Use when substantial work must survive context loss, preserve claims and evidence, expose bounded recall, or accept user steering through a shared .agent/live.md control plane.
---

# Long Run Agent

## Purpose

Keep the main chat thin while one worker owns all substantive architecture,
implementation, verification, durable state, and handoff work. The worker uses
a standalone epistemic compiler to preserve why conclusions are currently
warranted, what could defeat them, and what must be revalidated after change.

Create exactly one worker. The worker must not spawn, delegate to, or request
additional subagents.

The compiler is local and deterministic. It does not require a model or network
for validation, reduction, resume, or rebuild. Its append-only semantic ledger
is authoritative; generated Markdown and SQLite are disposable views.

## Main-Thread Workflow

1. Choose a run directory, normally under
   ~/Documents/codex-long-runs/<timestamp>-<slug>.
2. Initialize the run and start it with the repository or installed CLI.
3. Spawn exactly one worker with the task verbatim, this skill path, run
   directory, live control path, repository path, and branch constraints.
4. Keep the parent turn thin. Forward later user steering into live.md and the
   worker without independently implementing the task.
5. Keep the parent turn alive with host wait or mailbox support when available.
6. Surface a worker question, blocker, or completion promptly and
   self-containedly.
7. Do not treat apparent completion as termination. The worker becomes dormant
   and remains steerable unless the user says FINALIZE AND STOP.

Suggested worker prompt:

~~~text
Use the long-run-agent skill at <skill-path> as the operating procedure.

Task, verbatim:
<user task>

Repository: <repository>
Run directory: <run-directory>
Live control: <run-directory>/.agent/live.md

You are the only worker. Do all substantive work, verification, durable state,
and milestone publication in your own context and artifacts. Do not create or
request child subagents. Start and retain the live.md watcher, reveal live.md,
and check it after every meaningful batch and before hard-to-unwind changes.
Use the epistemic compiler for durable semantics. On completion, notify the
parent with branch, commit, tests, release status, and key artifacts, then
become dormant unless the user says FINALIZE AND STOP.
~~~

## Worker Startup

Use the same root for the compiler and live control:

~~~bash
python3 <skill-path>/scripts/mission_harness.py --root <run-directory> init --goal "<goal>"
python3 <skill-path>/scripts/mission_harness.py --root <run-directory> start --goal "<goal>"
python3 <skill-path>/scripts/watch_live_file.py <run-directory>/.agent/live.md --create --interval 1
python3 <skill-path>/scripts/reveal_live_file.py <run-directory>/.agent/live.md --skip-if-open
~~~

Retain the watcher process for the worker session. Check its output and read
live.md:

- at startup and resume
- after each meaningful implementation or verification batch
- before a branch rewrite, release, install replacement, destructive cleanup,
  or other hard-to-unwind operation
- whenever waiting for user input
- before declaring completion

File edits cannot interrupt a running model invocation. live.md is a mailbox,
not an interrupt primitive. Newer user steering overrides older conflicting
instructions.

## Durable Layout

The compiler owns these files:

~~~text
.agent/
  live.md
  ledger.jsonl
  retrieval.jsonl
  policy.json
  current.md
  state.sqlite
  checkpoint.json
~~~

- ledger.jsonl is the append-only, hash-chained semantic authority.
- retrieval.jsonl is append-only recall, suggestion, and feedback telemetry.
  It is durable but never becomes epistemic truth implicitly.
- policy.json controls deterministic reduction and bounded context.
- current.md is a compact generated human view.
- state.sqlite is a disposable query and projection view.
- checkpoint.json stores verified pointers, not a second summary of truth.
- live.md is the only user steering/control document.

Do not invent parallel CONTROL.md, scratch truth files, or replacement
checkpoints. Durable notes and test logs may live elsewhere in the run
directory, but semantic authority belongs in the compiler ledger.

## Semantic Write Contract

Prefer high-level operations over hand-editing state:

~~~bash
long-run-agent --root <run-directory> observe artifact --data '<json>'
long-run-agent --root <run-directory> observe evidence --data '<json>'
long-run-agent --root <run-directory> observe dependency --data '<json>'
long-run-agent --root <run-directory> observe verification --data '<json>'
long-run-agent --root <run-directory> assert --data '<json>'
long-run-agent --root <run-directory> assumption --data '<json>'
long-run-agent --root <run-directory> argument --data '<json>'
long-run-agent --root <run-directory> attack --data '<json>'
long-run-agent --root <run-directory> ask --data '<json>'
long-run-agent --root <run-directory> decide --data '<json>'
~~~

Every semantic write returns separate categories:

- committed contains only the requested durable semantic operations.
- derived contains deterministic consequences.
- diagnostics contains defects, changed safety, and concrete repair paths.
- suggested contains non-authoritative relationship candidates.
- recall contains bounded historical context relevant to the operation.

Never report a suggestion, recall result, generated view, or diagnostic as a
committed fact. Use --preview or the raw preview command before uncertain
writes. Apply raw EpistemicDelta only when a high-level operation cannot express
the intended semantics.

## Reasoning Rules

Evidence and provenance establish what was observed; they are not themselves
arguments. Claims are append-only revisions. An argument has explicit premises,
a warrant, and optional assumptions or dependency conditions. All conjunctive
premises must be warranted. Alternative grounded arguments can provide
independent support.

Support is computed by least fixed point. A support cycle with no grounded
entry evidence does not warrant any member and must produce a diagnostic.
Independent support depends on root evidence and provenance/correlation
families, not merely the number of argument nodes.

Attacks are first-class and require explicit grounds plus a warrant:

- rebut attacks a claim conclusion
- undercut attacks an argument or warrant
- undermine attacks a premise or evidence item

Assumptions are semantic commitments. Dependency bindings are mechanical
conditions such as versions, hashes, or environment identities. Keep them
distinct so changed dependencies can invalidate applicability without erasing
history.

Questions preserve known ignorance. Decisions record their claim, evidence,
assumption, and dependency basis. When support or dependencies change, reopen
affected questions, mark impacted decisions, emit diagnostics, and retain the
historical basis.

Artifact equality and evidence identity are also distinct. Exact stable identity
may collapse duplicate artifact registrations. Similar text or bytes alone
must never merge separate observations.

## Recall And Context

Load bounded context at startup and resume:

~~~bash
long-run-agent --root <run-directory> context
long-run-agent --root <run-directory> context --since-generation <n>
long-run-agent --root <run-directory> query changes-since --id <n>
long-run-agent --root <run-directory> expand <entity-id> --representation structure
~~~

Context is divided into WorkerScope and ActiveDelta. Respect completeness flags
and continuation cursors; do not treat a truncated response as exhaustive.

Automatic recall should provide compact capsules, not inject raw history.
Selection accounts for relevance, graph proximity, recency, prior utility,
negative feedback, root-evidence correlation, and diversity. Expand only the
entities needed at the cheapest useful representation.

Structural use of recalled material may record inferred positive feedback after
a successful semantic commit. Negative feedback must be explicit and contextual:

~~~bash
long-run-agent --root <run-directory> feedback <event-id> --entity-id <id> --action dismissed
long-run-agent --root <run-directory> feedback <suggestion-id> --action accepted
long-run-agent --root <run-directory> feedback <suggestion-id> --action rejected
~~~

Accepting a relation suggestion creates a normal semantic transaction.
Rejection or dismissal changes retrieval behavior only.

## Checkpoint And Resume

Checkpoint before interruption or a major phase boundary:

~~~bash
long-run-agent --root <run-directory> checkpoint
long-run-agent --root <run-directory> resume
~~~

Resume verifies both hash chains, rebuilds disposable state offline, validates
the checkpoint pointers, and returns bounded context. If generated state is
missing or corrupt, rebuild it rather than reconstructing authority from
current.md or memory:

~~~bash
long-run-agent --root <run-directory> rebuild
long-run-agent --root <run-directory> validate
~~~

## Live Control

Maintain stable headings so the user and watcher can scan changes:

~~~markdown
# Live Control

## User Updates
- Newer steering wins when instructions conflict.

## Current Goal
- Active bounded objective.

## Constraints
- Branch, safety, source, publication, and validation boundaries.

## Agent Status
- Run ID:
- Status: running

## Interrupts / Corrections
- Durable corrections to assumptions or scope.

## Decisions Made This Run
- Architecture and release decisions.

## Commands Run
- Important commands and milestones.

## Tests / Verification
- Exact test evidence and known gaps.

## Failures / Blockers
- Honest failures, unknowns, and blocked work.

## Artifacts Produced
- Durable paths and published commits.

## Next Actions
- Next executable steps.
~~~

Keep live.md compact enough for steering. Record detailed logs as run artifacts
and summarize their meaning through semantic observations, claims, questions,
or decisions.

## Release Discipline

Before a milestone push or installed-skill replacement:

1. Read live.md and honor any new steering.
2. Run the full test suite and deterministic rebuild validation.
3. Verify all Python source files remain below 1000 lines.
4. Verify CLI and MCP expose the same compiler behavior.
5. Build and install the package into a clean target.
6. Run the skill installer into an isolated target and verify exclusions.
7. Record failures and unresolved diagnostics honestly.
8. Push only the requested branch. Never merge main without explicit direction.

Replace the active installed skill only after release validation passes. Verify
the installed copy matches the certified repository tree and rerun smoke tests
from the installed location.

## Completion And Dormancy

Close the run semantically, update live.md with branch, commit, tests, release
status, installed-sync status, and key artifacts, then notify the parent when
host messaging is available:

~~~bash
long-run-agent --root <run-directory> close --outcome completed --summary "<summary>"
~~~

After reporting completion, set the worker status to dormant and wait for
steering. Stop the watcher and terminate only when the user explicitly says
FINALIZE AND STOP.
