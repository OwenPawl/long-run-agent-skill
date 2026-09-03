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
  skill-calls.log
  policy.json
  checkpoint.json
  views/
    current.md
    state.sqlite
    index.json
    entities/<deterministic-id>.json
~~~

ledger.jsonl is the hash-chained semantic authority. retrieval.jsonl is a
separate hash-chained record of recall, suggestion, and feedback behavior;
those records never become epistemic truth implicitly. policy.json controls
bounded context, recall, and derivation. Everything under views/ is disposable
and rebuilt offline. An entity ID deterministically resolves through the view
index to its current full representation; normal search and inspect responses
do not repeat view paths. checkpoint.json contains only verified pointers into
durable state. live.md remains the sole user steering and control file. skill-calls.log
is passive operational observability: it records exact public requests and
surfaced responses but is never replayed as semantic authority or retrieval
telemetry.

## Skill Call Tracking

Every CLI and MCP operation appends one human-readable, clearly delimited block
to the mission's default tracker log:

~~~text
<mission-root>/.agent/skill-calls.log
~~~

Each block contains a unique call ID, timestamp, operation and public surface,
worker/run/session identifiers when available, status, duration, payload sizes,
epistemic generation before and after, and the exact request and surfaced
response payloads. Calls that return failures and operations that raise errors
still close their blocks. A lock-protected append keeps concurrent blocks from
interleaving. Tracker failures are reported operationally but never change the
compiler result.

Watch a mission live with:

~~~bash
tail -f <mission-root>/.agent/skill-calls.log
~~~

Set `LONG_RUN_AGENT_SKILL_CALL_LOG` to an absolute path, or to a path relative
to the mission root, to override the default. Tracking is automatic; workers
should use the skill naturally rather than making artificial monitoring calls.

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
python3 scripts/mission_harness.py --root /tmp/mission update --data '{"operations":[{"type":"artifact.registered","data":{"id":"artifact:test","annotation":{"subject":"Release log","predicate":"records test output"},"external_identity":"/tmp/result.txt"}},{"type":"evidence.registered","data":{"id":"evidence:test","annotation":{"subject":"Release suite","predicate":"passed all checks","scope":"run release"},"provenance_refs":[{"type":"artifact","id":"artifact:test"},{"type":"tool_event","id":"test-command-1"},{"type":"run","id":"release"}],"producer":"test runner"}}]}'
python3 scripts/mission_harness.py --root /tmp/mission assert --data '{"id":"claim:release","annotation":{"subject":"Release","predicate":"is verified"},"proposition":"release is verified","premises":[{"type":"evidence","id":"evidence:test"}],"warrant":{"statement":"passing tests warrant release verification"},"argument_id":"argument:release","argument_annotation":{"subject":"Passing release tests","predicate":"warrant release verification"}}'
python3 scripts/mission_harness.py --root /tmp/mission search "release verification"
python3 scripts/mission_harness.py --root /tmp/mission inspect claim:release --why
python3 scripts/mission_harness.py --root /tmp/mission checkpoint
python3 scripts/mission_harness.py --root /tmp/mission validate
~~~

Use --preview on update or a semantic helper command to inspect consequences
without changing either ledger. update accepts an EpistemicDelta JSON object
and an optional generation precondition.

## Public Interaction

The conceptual epistemic surface is update, search, and inspect. update is the
single semantic compiler boundary. Automatic recall is part of qualifying
update responses. search discovers a broader deterministic candidate landscape
from structured annotations, state, provenance, and graph relationships.
inspect navigates normalized nodes, relations, paths, diagnostics, and bounded
history without recursively embedding full entity payloads.

Canonical entity annotations contain required subject and predicate fields and
an optional scope. intrinsic_name is reserved for entities with a natural name;
extended_annotation adds bounded presentation nuance. Neither provenance nor
derived state is folded into annotation text.

Convenience operations compile into update:

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

inspect facets cover why, why-not, defeaters, assumptions, impact, unknowns,
history, changes-since, revalidation, telemetry, and worker-state. context
returns bounded WorkerScope and ActiveDelta sections with explicit completeness
and continuation metadata. Full entity state is always available through the
deterministic mission-local view for its ID, but view availability never implies
that its content was read.

Provenance uses structured features and extensible typed references. An entity
may reference an artifact, a producing tool event, and a run independently.
Artifact locators may legitimately point outside the mission; materialized views
never do. This seam permits future file/tool activity to resolve through a
provenance reference into an entity and graph region without treating views as
source artifacts. Tool events are behavioral facts. A future adapter may
deterministically promote a well-defined tool result into evidence only when the
observation is entailed by that result; promotion must not invent a semantic
interpretation.

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
an independently recorded content-read event contributes strong content
coverage; references and structural use affect their own dimensions. Retrieval
outcome telemetry remains a separate model rather than an ordinal proxy for
containment. The temporary worker-state model does not infer file consumption
from materialized-view availability.

Recently surfaced unchanged state is inhibited by the same coverage scoring.
Material changes such as new defeaters, support loss, invalidated dependencies,
reopened questions, or supersession can override that inhibition. Noncurrent
claims and evidence are excluded from constructive recall. When a matching old
reasoning basis makes them corrective, the capsule inseparably carries its
historical badge, correction reason, decisive path, and known successor.

Content read, later reference, structural use, and accepted relation suggestions
produce inferred positive outcome telemetry. Dismissal before content read is a
weak contextual negative; dismissal after content read is strong. Neither
globally demotes the entity, and non-use is neutral.

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
mission_decide            mission_update
mission_search            mission_inspect
mission_feedback
mission_checkpoint        mission_resume
mission_close             mission_rebuild
mission_validate
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
passive public-call tracking, canonical annotations, typed provenance seams,
normalized search/inspect, deterministic entity views, and the under-1000-line
source limit.

## License

Apache-2.0. See LICENSE.
