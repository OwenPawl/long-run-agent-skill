# Long Run Agent

Long Run Agent is a local, standalone epistemic compiler for long-running
agent work. It preserves what was observed, claimed, assumed, supported,
attacked, decided, and left unknown, then derives the current working state
deterministically.

The compiler does not call a model or network service. Its authoritative
semantic ledger is append-only; SQLite and human-readable views are disposable
and can be rebuilt offline.

## Design Boundary

The compiler separates five result classes:

- committed: semantic operations durably appended by the requested write
- derived: deterministic consequences such as support, applicability, and impact
- diagnostics: invalid or newly unsafe reasoning plus concrete repair paths
- suggested: non-authoritative relation and annotation-revision candidates
- recall: bounded historical context selected for the current operation

Evidence and provenance are not arguments. Claims gain support through explicit
arguments with conjunctive premises, warrants, assumptions, and dependency
conditions. Alternative arguments provide independent support when their root
evidence and correlation families are independent. Grounded rebut, undercut,
and undermine attacks can defeat conclusions, warrants, and premises. Pure
support cycles never bootstrap themselves into warranted belief.

## Durable State

Initialization creates this state beneath the selected mission root:

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

ledger.jsonl is the hash-chained semantic authority. retrieval.jsonl is a
separate hash-chained record of recall, suggestion, and feedback behavior;
those records never become epistemic truth implicitly. policy.json controls
bounded context, recall, and derivation. current.md and state.sqlite are
generated views. checkpoint.json contains only verified pointers into durable
state. live.md remains the sole user steering and control file.

## Install

Inspect the skill installation plan before replacing an existing installation:

~~~bash
python3 scripts/install_skill.py --json
python3 scripts/install_skill.py --execute --host codex
~~~

The installer backs up an existing target and excludes repository, cache,
build, and packaging artifacts. It does not install system packages or start a
worker.

Install the Python package and stdio MCP server with:

~~~bash
python3 -m pip install .
long-run-agent --help
long-run-agent-mcp
~~~

## Quick Start

All commands emit JSON. The repository-local launcher and installed command use
the same library:

~~~bash
python3 scripts/mission_harness.py --root /tmp/mission init --goal "verify the release"
python3 scripts/mission_harness.py --root /tmp/mission start --goal "verify the release"
python3 scripts/mission_harness.py --root /tmp/mission observe artifact --data '{"id":"artifact:test","locator":"/tmp/result.txt"}'
python3 scripts/mission_harness.py --root /tmp/mission observe evidence --data '{"id":"evidence:test","artifact_ref":{"id":"artifact:test"},"description":"tests passed","producer":"test runner","source_run":"release"}'
python3 scripts/mission_harness.py --root /tmp/mission assert --data '{"id":"claim:release","proposition":"release is verified","premises":[{"type":"evidence","id":"evidence:test"}],"warrant":{"statement":"passing tests warrant release verification"},"argument_id":"argument:release"}'
python3 scripts/mission_harness.py --root /tmp/mission query belief --id claim:release
python3 scripts/mission_harness.py --root /tmp/mission checkpoint
python3 scripts/mission_harness.py --root /tmp/mission validate
~~~

Use --preview on semantic helper commands, or the raw preview command, to inspect
consequences without changing either ledger. Raw apply accepts an
EpistemicDelta JSON object and an optional generation precondition.

## Semantic Operations

- observe records artifacts, evidence observations, dependency versions, and
  verification results without conflating their identities.
- assert appends a claim revision and may add an explicit supporting argument.
- assumption records a first-class semantic assumption.
- argument records a warranted argument with one or more conjunctive premises.
- attack records a grounded rebut, undercut, or undermine.
- ask opens a question representing known ignorance.
- decide records a decision together with its claim, evidence, assumption, and
  dependency basis.
- feedback dismisses recalled context or explicitly accepts or rejects a
  relation suggestion.
- close records the outcome and next actions without discarding history.

Queries cover belief, why, why-not, defeaters, assumptions, impact, unknowns,
history, changes-since, revalidate, search, telemetry, and worker-state. context
returns bounded WorkerScope and ActiveDelta sections with explicit completeness
and continuation metadata. expand retrieves the cheapest useful representation
of one entity.

When evidence, assumptions, dependencies, or attacks change, the fixed-point
reducer recomputes affected conclusions, questions, and decisions. Diagnostics
name unsupported conclusions, ungrounded cycles, changed decision bases, and
revalidation work. Historical support remains history; it is not silently
treated as current support.

## Recall And Feedback

Recall is automatic on semantic writes. It maximizes marginal epistemic value
using relevance to current work, material change, historical usefulness,
corrective value, current-state coverage, and correlation-aware representative
diversity. Current worker state tracks content coverage, salience, structural
coverage, and recency independently. A capsule contributes weak coverage;
expansion contributes strong content coverage; references and structural use
affect their own dimensions. Retrieval outcome telemetry remains a separate
model rather than an ordinal proxy for containment.

Recently surfaced unchanged state is inhibited by the same coverage scoring.
Material changes such as new defeaters, support loss, invalidated dependencies,
reopened questions, or supersession can override that inhibition. Noncurrent
claims and evidence are excluded from constructive recall. When a matching old
reasoning basis makes them corrective, the capsule inseparably carries its
historical badge, correction reason, decisive path, and known successor.

Expansion, later reference, structural use, and accepted relation suggestions
produce inferred positive outcome telemetry. Dismissal before expansion is a
weak contextual negative; dismissal after expansion is strong. Neither globally
demotes the entity, and non-use is neutral.

Diagnostics expose non-authoritative code actions with levels, preconditions,
expected consequences, semantic-input requirements, and a proposed
EpistemicDelta where applicable. Preview and acceptance use the ordinary
compiler path. Relations may carry structured basis references and an optional
rationale. Later annotations can revise presentation wording without mutating
the historical relation; opportunistic revision suggestions are generated only
for already-active state, never by a cold-history cleanup scan.

## MCP

The stdio server exposes the same compiler through namespaced tools:

~~~text
mission_init              mission_control_read
mission_start             mission_context
mission_observe           mission_assert
mission_assumption        mission_argument
mission_attack            mission_ask
mission_decide            mission_query
mission_expand            mission_feedback
mission_checkpoint        mission_resume
mission_close             mission_rebuild
mission_validate          mission_preview
mission_apply
~~~

Other MCP servers can register the same tool set with
long_run_agent_skill.mcp_tools.register_mission_tools. There is no subprocess
bridge and no second state store.

## Long-Run Operation

Use one worker for substantive work and keep .agent/live.md as the shared
mailbox. Start scripts/watch_live_file.py and retain it for the worker session;
reveal the file once with scripts/reveal_live_file.py. Check the file after each
meaningful batch, before hard-to-unwind changes, and whenever waiting for input.

At startup or resume, load bounded context from the compiler instead of replaying
the full ledger. Checkpoint before interruption. On apparent completion, record
the release evidence, notify the parent when the host supports it, then become
dormant unless the user explicitly says FINALIZE AND STOP.

## Verification

~~~bash
python3 -m unittest discover -s tests -v
python3 -m py_compile long_run_agent_skill/*.py scripts/*.py
python3 scripts/install_skill.py --json
python3 scripts/mission_harness.py --root /tmp/long-run-agent-smoke init --goal smoke
python3 scripts/mission_harness.py --root /tmp/long-run-agent-smoke validate
~~~

The test suite includes the required 20-step epistemic scenario, offline
deterministic rebuild, fixed-point grounding, cycle rejection, independent
support, all three attack types, dependency invalidation, diagnostics,
questions, decisions, bounded recall/context, telemetry, checkpoint/resume,
multidimensional worker state, repeat inhibition with material-change override,
corrective recall, contextual feedback strength, code actions, revisable
relation rationale, CLI/MCP parity, package installation, installer behavior,
and the under-1000-line source limit.

## License

Apache-2.0. See LICENSE.
