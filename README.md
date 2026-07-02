# Long Run Agent Skill

This repository contains a small Codex skill for long-running work. The mission
harness provides a steerable `.agent/live.md` control plane, watcher scripts,
and a minimal, domain-neutral audit layer for preserving run state over time.

The harness is not an agent OS, planner, or database. It exists to make long
runs less wrong by keeping durable records of goals, commands, verification,
claims, artifacts, failures, friction, and next actions.

## Install

From a fresh checkout, inspect the install plan first:

```bash
python3 scripts/install_skill.py --json
```

Install into detected local skill host directories only when the plan looks
right:

```bash
python3 scripts/install_skill.py --execute --host auto
python3 scripts/install_skill.py --execute --host codex
python3 scripts/install_skill.py --execute --host claude
python3 scripts/install_skill.py --execute --host both
```

The installer copies this skill directory, excludes cache/build artifacts, and
backs up an existing target before replacing it. It does not install system
packages or start a worker.

## Mission Harness v0.1

Use the harness from any project root:

```bash
python3 /path/to/long-run-agent/scripts/mission_harness.py --root /path/to/project init
python3 /path/to/long-run-agent/scripts/mission_harness.py --root /path/to/project run start --goal "bounded task"
python3 /path/to/long-run-agent/scripts/mission_harness.py --root /path/to/project friction add --category verification_gap --description "..." --impact "..." --proposed-harness-need "..."
python3 /path/to/long-run-agent/scripts/mission_harness.py --root /path/to/project friction settle --id <friction-id> --status consolidated --root-cause-id <root-id> --release-disposition deferred --rationale "..."
python3 /path/to/long-run-agent/scripts/mission_harness.py --root /path/to/project friction report --fail-on-ambiguous-open --output friction-readiness.md
python3 /path/to/long-run-agent/scripts/mission_harness.py --root /path/to/project run close --outcome "done" --command "..." --test "..." --next-action "..."
python3 /path/to/long-run-agent/scripts/mission_harness.py --root /path/to/project validate
python3 /path/to/long-run-agent/scripts/mission_harness.py --root /path/to/project state preflight
python3 /path/to/long-run-agent/scripts/mission_harness.py --root /path/to/project state compact-live
python3 /path/to/long-run-agent/scripts/mission_harness.py --root /path/to/project state summarize
python3 /path/to/long-run-agent/scripts/mission_harness.py --root /path/to/project index rebuild
python3 /path/to/long-run-agent/scripts/mission_harness.py --root /path/to/project index search "query terms"
python3 /path/to/long-run-agent/scripts/mission_harness.py --root /path/to/project index status
python3 /path/to/long-run-agent/scripts/mission_records_recent.py --root /path/to/project claims --limit 5
python3 /path/to/long-run-agent/scripts/mission_artifact_materialize.py --root /path/to/project --id <artifact-id>
python3 /path/to/long-run-agent/scripts/mission_artifact_materialize.py --root /path/to/project --path <new-evidence-path>
python3 /path/to/long-run-agent/scripts/mission_artifact_materialize.py --root /path/to/project --path <input-path> --stage-copy /private/tmp/<stable-input>
python3 /path/to/long-run-agent/scripts/watch_live_file.py /path/to/project/.agent/live.md --create --interval 1
python3 /path/to/long-run-agent/scripts/reveal_live_file.py /path/to/project/.agent/live.md --skip-if-open
```

`init` creates:

```text
.agent/
  live.md
  current_state.md
  constitution.md
  known_failures.md
  decisions.md
  runs.jsonl
  claims.json
  artifacts.json
  friction.jsonl
```

`live.md` is the active control plane. Durable facts should be promoted into the
structured files during run close or explicit `claim`, `artifact`, and
`friction` commands. Do not leave important state only in `live.md`.

## Command Surface

- `init`: create the `.agent/` files without overwriting existing files unless `--force` is passed.
- `run start`: append a `run_start` record to `.agent/runs.jsonl` and update `live.md`.
- `run close`: append a `run_close` record with outcome, commands, tests, changed files, failures, claims, artifacts, and next actions.
- `friction add`: append a process/tooling friction record to `.agent/friction.jsonl`. If it is another observation of a known pattern, include `--root-cause-id` instead of creating a disconnected open issue.
- `friction settle`: append release-readiness settlement records for existing friction ids. This preserves the original evidence and makes the latest effective status clear.
- `friction report`: summarize raw records versus effective friction items, root causes, release dispositions, ambiguous open items, and release-blocking items.
- `claim add`: create or replace a claim record in `.agent/claims.json`.
- `artifact add`: append an artifact record to `.agent/artifacts.json`.
- `mission_artifact_materialize.py --id <artifact-id>` or `--path <new-evidence-path>`: resolve recorded or pre-registration evidence, request its macOS File Provider download if it is `dataless`, and perform a bounded basic-readability check before verification or registration. Add `--stage-copy <new-file>` for a long-running consumer that needs an atomic verified local copy; it supports regular files and will not overwrite an existing destination.
- `validate`: parse and sanity-check the basic Markdown, JSON, and JSONL state.
- `state preflight`: on macOS, explicitly request downloads for File Provider `dataless` `.agent` files and perform bounded readability checks for durable state files; on other platforms this is a no-op.
- `state compact-live`: archive the current live control plane under `.agent/archive/`, then replace verbose command/test/claim/artifact/friction sections with an archive pointer while retaining active goal and control sections.
- `state summarize`: regenerate `.agent/current_state.md` from recorded state, preferring active `live.md` goal/failures/next actions while a run is open.
- `index rebuild`: rebuild the derived `.agent/mission_index.sqlite` database from Markdown, JSON, and JSONL state using a fresh replacement database.
- `index search "query terms"`: search the derived SQLite index. Add `--kind claim`, `--kind friction`, or another record kind to narrow results. If the derived database is unreadable, search rebuilds it once from authoritative state.
- `index status`: report whether the derived SQLite index exists, how many records it contains, and whether SQLite FTS is available. If status reports `ok: false`, run `index rebuild`.
- `mission_records_recent.py claims|artifacts|friction|runs --limit N`: list recent authoritative records as JSON without manually guessing each state file's schema.

The harness commands above are implemented in `scripts/mission_harness.py`;
artifact materialization and SQLite indexing are small companion modules so the
main CLI remains maintainable. They are covered by smoke tests.

Use `scripts/watch_live_file.py` to print `.agent/live.md` at startup and after
changes. Use `scripts/reveal_live_file.py` once at startup to reveal `live.md`
in Finder, File Explorer, or the host file manager. These scripts keep the
automatic opening/watching workflow without adding a second steering document.

`run start` clears per-run sections in `live.md` so the next close does not
inherit stale commands, claims, artifacts, failures, or next actions. JSON state
mutations use a simple per-file lock and atomic replace to avoid corrupting
state when multiple harness commands run at the same time.

On macOS, commands that read or mutate initialized state automatically perform
the File Provider readiness check first. Use `state preflight` directly when
you need its diagnostic report. If an unusually long open run makes `live.md`
hard to review, record durable claims/artifacts/friction first, then use
`state compact-live`; its archived snapshot preserves removed operational text.

Friction tracking is evidence-preserving, not issue-count inflation. Keep
`.agent/friction.jsonl` append-only, but treat the latest record for each
friction id as the effective disposition. Use `friction settle` when evidence is
fixed, consolidated under a broader root cause, deferred with a release-safe
rationale, blocked by external infrastructure, or out of public scope. Use
`friction report --fail-on-ambiguous-open` before release or handoff so the open
set means unresolved root causes, not repeated observations. Apply the same
discipline to closeout summaries: failures, artifacts, and claims should point
to root causes or release-relevant questions when repeated evidence exists.

## Verification

Run:

```bash
python3 scripts/install_skill.py --json
python3 -m unittest discover -s tests
python3 -m compileall scripts tests
python3 scripts/mission_harness.py --root /tmp/example-agent init
python3 scripts/mission_harness.py --root /tmp/example-agent state preflight
python3 scripts/mission_harness.py --root /tmp/example-agent friction report --fail-on-ambiguous-open
python3 scripts/watch_live_file.py /tmp/example-agent/.agent/live.md --create --once
python3 scripts/reveal_live_file.py /tmp/example-agent/.agent/live.md --help
python3 scripts/mission_harness.py --root /tmp/example-agent state compact-live
python3 scripts/mission_harness.py --root /tmp/example-agent index rebuild
python3 scripts/mission_harness.py --root /tmp/example-agent index search current_state
python3 scripts/mission_harness.py --root /tmp/example-agent index status
python3 scripts/mission_records_recent.py --root /tmp/example-agent claims --limit 5
python3 scripts/mission_harness.py --root /tmp/example-agent validate
python3 scripts/mission_artifact_materialize.py --root /tmp/example-agent --path .agent/current_state.md
python3 scripts/mission_artifact_materialize.py --root /tmp/example-agent --path .agent/current_state.md --stage-copy /tmp/example-agent/staged-state.md
```

## Current Limits

- SQLite is a derived search index only. Markdown and JSON/JSONL remain the authoritative state.
- `validate` performs built-in sanity checks; it does not require the external `jsonschema` package.
- The harness is domain-neutral. Tool-specific or project-specific evidence belongs in the project using the harness.
- `current_state.md` is generated from recorded state plus the active live control sections for an open run, and should not be the only copy of important claims or artifacts.
- File Provider hydration is attempted automatically for initialized state operations on macOS; readiness reads time out rather than leaving the command stalled if content is not available promptly.
- `mission_artifact_materialize.py` verifies the selected file or directory entry itself; for a recorded directory artifact, select a nested evidence file explicitly before verifying that file's contents.
- `--stage-copy` stabilizes a selected regular-file input for a long-running consumer; it does not synchronize verifier output back to durable storage or interpret what the copied file proves.
- `state compact-live` archives operational text but does not independently promote its claims or artifacts; record durable facts before compaction.

## License

This project is licensed under Apache-2.0. See `LICENSE`.
