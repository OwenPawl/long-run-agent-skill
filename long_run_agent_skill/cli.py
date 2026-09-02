"""JSON command-line interface for the epistemic compiler."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .compiler import EpistemicCompiler
from .errors import EpistemicError


def _json_value(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid JSON: {exc}") from exc


def _data(args: argparse.Namespace) -> dict[str, Any]:
    if getattr(args, "file", ""):
        try:
            value = json.loads(Path(args.file).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EpistemicError(f"cannot read JSON input file: {exc}") from exc
    else:
        value = getattr(args, "data", {})
    if not isinstance(value, dict):
        raise EpistemicError("semantic input must be a JSON object")
    return value


def _add_json_input(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--data", type=_json_value, help="JSON object")
    group.add_argument("--file", help="path to a JSON object")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="mission root containing .agent")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="initialize standalone epistemic state")
    init.add_argument("--goal", default="")
    init.add_argument("--force", action="store_true")

    start = commands.add_parser("start", help="start a mission run")
    start.add_argument("--goal", required=True)
    start.add_argument("--steering", default="")
    start.add_argument("--run-id", default="")

    context = commands.add_parser("context", help="build a bounded current worker context")
    context.add_argument("--cursor", type=_json_value)
    context.add_argument("--since-generation", type=int)
    context.add_argument("--max-chars", type=int)
    context.add_argument("--max-entities", type=int)

    observe = commands.add_parser("observe", help="record artifact, evidence, dependency, or verification")
    observe.add_argument("kind", choices=["artifact", "evidence", "dependency", "verification"])
    observe.add_argument("--preview", action="store_true")
    _add_json_input(observe)

    claim = commands.add_parser("assert", help="assert or revise a claim and optional argument")
    claim.add_argument("--preview", action="store_true")
    _add_json_input(claim)

    assumption = commands.add_parser("assumption", help="assert or revise a semantic assumption")
    assumption.add_argument("--preview", action="store_true")
    _add_json_input(assumption)

    argument = commands.add_parser("argument", help="assert a standalone argument")
    argument.add_argument("--preview", action="store_true")
    _add_json_input(argument)

    attack = commands.add_parser("attack", help="assert a warranted rebut, undercut, or undermine")
    attack.add_argument("--preview", action="store_true")
    _add_json_input(attack)

    ask = commands.add_parser("ask", help="open a question")
    ask.add_argument("--preview", action="store_true")
    _add_json_input(ask)

    decide = commands.add_parser("decide", help="record a decision and its epistemic basis")
    decide.add_argument("--preview", action="store_true")
    _add_json_input(decide)

    query = commands.add_parser("query", help="run a deterministic epistemic query")
    query.add_argument(
        "kind",
        choices=[
            "belief",
            "why",
            "why-not",
            "defeaters",
            "assumptions",
            "impact",
            "unknowns",
            "history",
            "changes-since",
            "revalidate",
            "search",
            "telemetry",
        ],
    )
    query.add_argument("--id", default="")
    query.add_argument("--text", default="")
    query.add_argument("--cursor", type=_json_value)
    query.add_argument("--limit", type=int, default=20)

    expand = commands.add_parser("expand", help="expand an entity at the cheapest useful resolution")
    expand.add_argument("id")
    expand.add_argument("--representation", default="structure")
    expand.add_argument("--max-depth", type=int)

    feedback = commands.add_parser("feedback", help="dismiss recall or accept/reject a relation suggestion")
    feedback.add_argument("event_id")
    feedback.add_argument("--entity-id", default="")
    feedback.add_argument("--action", required=True, choices=["dismissed", "accepted", "rejected"])

    checkpoint = commands.add_parser("checkpoint", help="record a pointer-only worker checkpoint")
    checkpoint.add_argument("--id", default="")
    commands.add_parser("resume", help="verify, rebuild, and construct bounded resume context")
    commands.add_parser("rebuild", help="rebuild all derived state offline")
    commands.add_parser("validate", help="validate ledgers, policy, checkpoint, and derived state")

    close = commands.add_parser("close", help="close the current run")
    close.add_argument("--outcome", required=True)
    close.add_argument("--summary", default="")
    close.add_argument("--next-action", action="append", default=[])

    preview = commands.add_parser("preview", help="preview a raw EpistemicDelta")
    _add_json_input(preview)
    apply = commands.add_parser("apply", help="apply a raw EpistemicDelta")
    apply.add_argument("--expected-generation", type=int)
    _add_json_input(apply)
    return parser


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    compiler = EpistemicCompiler(args.root)
    if args.command == "init":
        return compiler.initialize(goal=args.goal, force=args.force)
    if args.command == "start":
        return compiler.start(args.goal, steering=args.steering, run_id=args.run_id)
    if args.command == "context":
        return compiler.context(
            cursor=args.cursor,
            since_generation=args.since_generation,
            max_chars=args.max_chars,
            max_entities=args.max_entities,
        )
    if args.command == "observe":
        return compiler.observe(args.kind, _data(args), preview=args.preview)
    if args.command == "assert":
        return compiler.assert_claim(_data(args), preview=args.preview)
    if args.command == "assumption":
        return compiler.assert_assumption(_data(args), preview=args.preview)
    if args.command == "argument":
        return compiler.assert_argument(_data(args), preview=args.preview)
    if args.command == "attack":
        return compiler.attack(_data(args), preview=args.preview)
    if args.command == "ask":
        return compiler.ask(_data(args), preview=args.preview)
    if args.command == "decide":
        return compiler.decide(_data(args), preview=args.preview)
    if args.command == "query":
        return compiler.query(args.kind, args.id, text=args.text, cursor=args.cursor, limit=args.limit)
    if args.command == "expand":
        return compiler.expand(args.id, representation=args.representation, max_depth=args.max_depth)
    if args.command == "feedback":
        return compiler.feedback(args.event_id, args.entity_id, args.action)
    if args.command == "checkpoint":
        return compiler.checkpoint(args.id)
    if args.command == "resume":
        return compiler.resume()
    if args.command == "rebuild":
        return compiler.rebuild()
    if args.command == "validate":
        return compiler.validate()
    if args.command == "close":
        return compiler.close(args.outcome, summary=args.summary, next_actions=args.next_action)
    if args.command == "preview":
        return compiler.preview(_data(args))
    if args.command == "apply":
        return compiler.apply(_data(args), expected_generation=args.expected_generation)
    raise EpistemicError(f"unhandled command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        result = dispatch(args)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("status", "success") == "success" else 1
    except (EpistemicError, OSError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
