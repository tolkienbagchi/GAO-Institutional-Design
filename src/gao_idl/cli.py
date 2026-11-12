"""Command line: ``gao-idl run``, ``replay``, ``spec``, ``cards`` and ``diverge``.

Also available as ``python -m gao_idl.cli``. ``run`` plays an environment from a JSON config and
writes the session snapshot and ledger totals. The config names the environment factory
(``env_ref``), its ``params``, the session ``seed`` and one adapter per agent. An adapter is a
description string understood by the learning package (``"random"``, ``"scripted:<policy>"``,
``"table:<checkpoint path>"``) or ``{"kind": "mock_llm", "reply": {...}}``: a language-model agent
on a mock backend that answers every prompt with the same JSON object. Random agents are seeded
from the session seed, so the same config always produces the same run.

``replay`` rebuilds a session from a file written by ``run`` and checks that it is reproduced. The
snapshot's input log is replayed from scratch (no adapters are needed: whatever the agents did,
including the replies of a language model, is in the log), the replay is checked against the log's
own trace, and the result and ledger totals are compared with the ones the file recorded. Exit
status 0 means identical; 1 means they differ.

``spec render`` writes the reviewable design page of a spec and ``spec check`` compares the spec
with the environment it names (exit status 1 on any mismatch); with ``--cards`` each card supplies
one run to draw on. ``cards review`` shows cards one at a time and asks for a verdict after each,
saving as it goes. ``cards show`` prints cards in plain language, ``cards confirm`` and
``cards reject`` record a person's verdict, and ``cards verify`` checks that confirmed cards are
still reproduced and rejected ones are not (exit status 1 otherwise). ``spec check --fuzz N`` adds
N edge-biased runs and checks the spec's invariants on all runs. ``diverge SPEC REF...`` replays
edge-biased scripts on other implementations of the spec and writes one question per distinct
disagreement (exit status 1 if there is any). See ``docs/verifying.md``.

Stability: internal; the command-line interface is the supported way to use this module.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ConfigDict, ValidationError

from gao_idl.agents.base import AgentAdapter
from gao_idl.core.errors import GaoError
from gao_idl.core.ids import AgentId
from gao_idl.core.schemas import FrozenModel
from gao_idl.learning.opponents import default_opponent_factory
from gao_idl.runtime.runner import run_sync
from gao_idl.runtime.session import Session

FORMAT = "gao_idl.run/1"


class RunConfig(FrozenModel):
    """A run: environment, parameters, seed and the adapter of every agent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    env_ref: str
    params: dict[str, Any] = {}
    seed: int
    agents: dict[str, str | dict[str, Any]]


def build_adapter(agent: str, spec: str | dict[str, Any], seed: int) -> AgentAdapter:
    """The adapter described by ``spec`` for ``agent`` (``seed`` is the session seed)."""
    if isinstance(spec, str):
        return default_opponent_factory()(AgentId(agent), spec, seed)
    if spec.get("kind") == "mock_llm" and set(spec) == {"kind", "reply"}:
        from gao_idl.llm import LLMAgent, MockBackend  # only when asked for: keeps the CLI light

        reply = spec["reply"]
        return LLMAgent(AgentId(agent), MockBackend(lambda messages: reply))
    raise ValueError(f"agent {agent!r}: unknown adapter {spec!r}")


def play(config: RunConfig) -> dict[str, Any]:
    """Run the session to the end; return the record that is written to the output file."""
    session = Session.start(config.env_ref, config.params, config.seed)
    adapters = {
        AgentId(a): build_adapter(a, spec, config.seed) for a, spec in sorted(config.agents.items())
    }
    run_sync(session, adapters)
    return describe_run(config, session)


def describe_run(config: RunConfig, session: Session) -> dict[str, Any]:
    """The output record: the config, the outcome, the ledger totals and the snapshot."""
    agents = sorted(set(config.agents) | set(session.ledger.snapshot()["increments"]))
    result = session.result
    return {
        "format": FORMAT,
        "config": config.model_dump(mode="json"),
        "result": {
            "finished": session.finished,
            "failed": session.failed,
            "port": None if result is None else str(result.port),
            "record": None if result is None else result.record,
        },
        "ledger": {a: session.ledger.utility(AgentId(a)) for a in agents},
        "snapshot": session.snapshot(),
    }


def replay_file(data: dict[str, Any]) -> list[str]:
    """Replay ``data`` (parsed output of ``gao_idl.run``); returns the differences (empty: ok)."""
    if data.get("format") != FORMAT:
        raise ValueError(f"not a {FORMAT} file (format={data.get('format')!r})")
    session = Session.restore(data["snapshot"])
    session.check_determinism()
    problems: list[str] = []
    result = session.result
    seen = {
        "finished": session.finished,
        "failed": session.failed,
        "port": None if result is None else str(result.port),
        "record": None if result is None else result.record,
    }
    if seen != data["result"]:
        problems.append(f"result differs: recorded {data['result']!r}, replayed {seen!r}")
    for agent, total in sorted(data["ledger"].items()):
        replayed = session.ledger.utility(AgentId(agent))
        if replayed != total:
            problems.append(f"utility of {agent!r}: recorded {total!r}, replayed {replayed!r}")
    return problems


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        config = RunConfig.model_validate_json(args.config.read_text())
    except (OSError, ValidationError) as e:
        print(f"cannot read the config {args.config}: {e}", file=sys.stderr)
        return 2
    try:
        record = play(config)
    except (ValueError, RuntimeError, GaoError) as e:
        print(f"the run failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    args.out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    outcome = record["result"]
    print(f"{config.env_ref} seed={config.seed}: exit={outcome['port']} -> {args.out}")
    for agent, total in record["ledger"].items():
        print(f"  {agent:<10}{total:>10.4f}")
    return 0 if outcome["finished"] and not outcome["failed"] else 1


def _cmd_replay(args: argparse.Namespace) -> int:
    try:
        data = json.loads(args.file.read_text())
        problems = replay_file(data)
    except (OSError, ValueError, KeyError, GaoError) as e:
        print(f"replay failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if problems:
        print("replay differs from the recording:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(f"replay identical: {len(data['snapshot']['log'])} inputs, exit={data['result']['port']}")
    return 0


def _load(args: argparse.Namespace) -> tuple[Any, list[Any], list[Any]]:
    """The spec, its cards (or none) and one run per card; imported late to keep ``run`` light."""
    from gao_idl.authoring import Run, load_cards, load_spec

    spec = load_spec(args.spec)
    cards = load_cards(args.cards) if args.cards is not None else []
    runs = [Run(params=c.params, seed=c.seed, script=c.script) for c in cards]
    return spec, cards, runs


def _cmd_spec_render(args: argparse.Namespace) -> int:
    from gao_idl.authoring import knowledge_table, play, render_design
    from gao_idl.runtime.registry import resolve

    try:
        spec, _, runs = _load(args)
        process, _cfg = resolve(spec.env_ref)(**spec.params)
        sessions = [play(spec.env_ref, r.params, r.seed, r.script) for r in runs]
        text = render_design(spec, process, knowledge_table(sessions))
    except (OSError, ValueError, RuntimeError, GaoError) as e:
        print(f"cannot render the design: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    args.out.write_text(text)
    print(f"{spec.title}: {len(runs)} runs -> {args.out}")
    return 0


def _cmd_spec_check(args: argparse.Namespace) -> int:
    from gao_idl.authoring import Run, check_invariants, check_spec, edge_scripts

    try:
        spec, _, runs = _load(args)
        if args.fuzz:
            scripts = edge_scripts(spec.env_ref, spec.params, args.seed, args.fuzz)
            runs += [Run(params=spec.params, seed=args.seed, script=s) for s in scripts]
        found = check_spec(spec, runs) + check_invariants(spec, runs, secrets=False)
    except (OSError, ValueError, RuntimeError, GaoError) as e:
        print(f"cannot check the spec: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    notes = [m for m in found if m.kind == "invariant_unchecked"]
    found = [m for m in found if m.kind != "invariant_unchecked"]
    for m in found:
        print(f"{m.kind:<20} {m.where}: {m.message}")
    for m in notes:
        print(f"note                 {m.where}: {m.message}")
    open_items = sum(not a.confirmed for a in spec.assumptions)
    print(
        f"{spec.title}: {len(found)} mismatches over {len(runs)} runs; "
        f"{open_items} assumptions not yet confirmed; {len(notes)} invariants not checked"
    )
    return 1 if found else 0


def _cmd_diverge(args: argparse.Namespace) -> int:
    from gao_idl.authoring import (
        diverge,
        edge_scripts,
        load_spec,
        render_choice,
        save_choices,
        to_choice_cards,
    )

    try:
        spec = load_spec(args.spec)
        refs = [spec.env_ref, *args.refs]
        scripts = edge_scripts(spec.env_ref, spec.params, args.seed, args.n)
        found = diverge(refs, spec.params, [args.seed], scripts)
        cards = to_choice_cards(found)
    except (OSError, ValueError, RuntimeError, GaoError) as e:
        print(f"cannot compare the candidates: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    save_choices(args.out, cards)
    for card in cards:
        print(render_choice(card, spec))
        print("---\n")
    deep = sorted({d.kind for d in found} - {"information"})
    agree = (
        f"they disagree on {', '.join(deep)}"
        if deep
        else "endings and payoffs agreed on every script"
    )
    print(
        f"{len(refs)} candidates, {args.n} scripts: {len(cards)} distinct disagreements; "
        f"{agree} -> {args.out}",
        file=sys.stderr,
    )
    return 1 if cards else 0


def _cmd_cards_show(args: argparse.Namespace) -> int:
    from gao_idl.authoring import load_cards, load_spec, render_card

    try:
        cards = load_cards(args.cards)
        spec = load_spec(args.spec) if args.spec is not None else None
    except (OSError, ValueError) as e:
        print(f"cannot read: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    chosen = [c for c in cards if args.status in (None, c.status)]
    for card in chosen:
        print(render_card(card, spec))
        print("---\n")
    print(f"{len(chosen)} of {len(cards)} cards shown", file=sys.stderr)
    return 0


def _cmd_cards_mark(args: argparse.Namespace) -> int:
    from gao_idl.authoring import freeze, load_cards, save_cards

    try:
        cards = load_cards(args.cards)
    except (OSError, ValueError) as e:
        print(f"cannot read {args.cards}: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    wanted = list(args.ids)
    unknown = sorted(set(wanted) - {c.id for c in cards})
    if unknown:
        print(f"no such card: {', '.join(unknown)}", file=sys.stderr)
        return 2
    marked = []
    for card in cards:
        if card.id in wanted:
            if card.expect is None:
                card = freeze(card)
            update: dict[str, Any] = {"status": args.status}
            if args.note is not None:
                update["note"] = args.note
            card = card.model_copy(update=update)
        marked.append(card)
    save_cards(args.cards, marked)
    print(f"{len(wanted)} cards marked {args.status} in {args.cards}")
    return 0


_REVIEW_KEYS = "[c] yes, this is what I meant   [r] no, reject   [s] skip   [b] back   [q] stop"


def _cmd_cards_review(args: argparse.Namespace) -> int:
    """Show cards one at a time and record a verdict after each; saves after every answer."""
    from gao_idl.authoring import freeze, load_cards, load_spec, render_card, save_cards

    try:
        cards = load_cards(args.cards)
        spec = load_spec(args.spec) if args.spec is not None else None
    except (OSError, ValueError) as e:
        print(f"cannot read: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    queue = [i for i, c in enumerate(cards) if args.status in ("all", c.status)]
    if not queue:
        print(f"no {args.status} cards in {args.cards}")
        return 0

    def ask(prompt: str) -> str | None:
        try:
            return input(prompt).strip()
        except EOFError:
            return None

    at = 0
    decided = 0
    while at < len(queue):
        card = cards[queue[at]]
        print(f"\n{'=' * 72}\nCard {at + 1} of {len(queue)}\n")
        print(render_card(card, spec))
        answer = ask(f"{_REVIEW_KEYS}\n> ")
        if answer is None or answer.lower() in ("q", "quit", "stop"):
            break
        key = answer.lower()[:1]
        if key == "b":
            at = max(0, at - 1)
            continue
        if key == "s" or answer == "":
            at += 1
            continue
        if key not in ("c", "y", "r", "n"):
            print(f"did not understand {answer!r}")
            continue
        update: dict[str, Any] = {"status": "confirmed" if key in ("c", "y") else "rejected"}
        if update["status"] == "rejected":
            note = ask("What should happen instead? > ")
            if not note:
                print("a rejected card needs a note; nothing recorded")
                continue
            update["note"] = note
        if card.expect is None:
            card = freeze(card)
        cards[queue[at]] = card.model_copy(update=update)
        save_cards(args.cards, cards)
        decided += 1
        print(f"recorded: {card.id} {update['status']}")
        at += 1
    left = sum(c.status == "proposed" for c in cards)
    print(f"\n{decided} cards decided this time; {left} still proposed in {args.cards}")
    return 0


def _cmd_cards_verify(args: argparse.Namespace) -> int:
    from gao_idl.authoring import load_cards, verify_cards

    try:
        cards = load_cards(args.cards)
    except (OSError, ValueError) as e:
        print(f"cannot read {args.cards}: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    problems = verify_cards(cards)
    for problem in problems:
        print(problem, file=sys.stderr)
    counts = {s: sum(c.status == s for c in cards) for s in ("confirmed", "rejected", "proposed")}
    print(
        f"{counts['confirmed']} confirmed, {counts['rejected']} rejected, "
        f"{counts['proposed']} still proposed (not checked); {len(problems)} problems"
    )
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point (``gao-idl``); returns the exit status."""
    parser = argparse.ArgumentParser(
        prog="gao-idl", description="Run and replay gao_idl sessions; check specs and cards."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="play a JSON config and write the session file")
    run_p.add_argument("config", type=Path, help="JSON config (see examples/configs/)")
    run_p.add_argument("--out", type=Path, default=Path("run.json"), help="output file")
    run_p.set_defaults(func=_cmd_run)
    replay_p = sub.add_parser("replay", help="replay a session file and check it is reproduced")
    replay_p.add_argument("file", type=Path, help="file written by `gao-idl run`")
    replay_p.set_defaults(func=_cmd_replay)

    spec_p = sub.add_parser("spec", help="render or check an environment spec")
    spec_sub = spec_p.add_subparsers(dest="spec_command", required=True)
    render_p = spec_sub.add_parser("render", help="write the design page a person reviews")
    check_p = spec_sub.add_parser("check", help="compare the spec with the built environment")
    for p, func in ((render_p, _cmd_spec_render), (check_p, _cmd_spec_check)):
        p.add_argument("spec", type=Path, help="spec JSON file")
        p.add_argument("--cards", type=Path, default=None, help="cards whose runs to draw on")
        p.set_defaults(func=func)
    render_p.add_argument("--out", type=Path, required=True, help="markdown file to write")
    check_p.add_argument("--fuzz", type=int, default=0, metavar="N", help="add N edge-biased runs")
    check_p.add_argument("--seed", type=int, default=0, help="seed for the fuzzed runs")

    diverge_p = sub.add_parser(
        "diverge", help="find situations where other implementations of a spec disagree"
    )
    diverge_p.add_argument("spec", type=Path, help="spec JSON file (its env_ref is candidate one)")
    diverge_p.add_argument(
        "refs", nargs="+", metavar="REF", help="other factories, module:function"
    )
    diverge_p.add_argument("--n", type=int, default=200, help="number of edge-biased scripts")
    diverge_p.add_argument("--seed", type=int, default=0)
    diverge_p.add_argument("--out", type=Path, required=True, help="choice cards JSON to write")
    diverge_p.set_defaults(func=_cmd_diverge)

    cards_p = sub.add_parser("cards", help="show, confirm, reject or verify scenario cards")
    cards_sub = cards_p.add_subparsers(dest="cards_command", required=True)
    show_p = cards_sub.add_parser("show", help="print cards in plain language")
    show_p.add_argument("cards", type=Path)
    show_p.add_argument("--status", choices=["proposed", "confirmed", "rejected"], default=None)
    show_p.add_argument("--spec", type=Path, default=None, help="spec for step descriptions")
    show_p.set_defaults(func=_cmd_cards_show)
    review_p = cards_sub.add_parser(
        "review", help="go through cards one at a time and confirm or reject each"
    )
    review_p.add_argument("cards", type=Path)
    review_p.add_argument("--spec", type=Path, default=None, help="spec for step descriptions")
    review_p.add_argument(
        "--status", choices=["proposed", "confirmed", "rejected", "all"], default="proposed"
    )
    review_p.set_defaults(func=_cmd_cards_review)
    confirm_p = cards_sub.add_parser("confirm", help="record that these cards are as intended")
    confirm_p.add_argument("cards", type=Path)
    confirm_p.add_argument("ids", nargs="+", metavar="ID")
    confirm_p.add_argument("--note", default=None)
    confirm_p.set_defaults(func=_cmd_cards_mark, status="confirmed")
    reject_p = cards_sub.add_parser("reject", help="record that a card is not what was meant")
    reject_p.add_argument("cards", type=Path)
    reject_p.add_argument("ids", nargs=1, metavar="ID")
    reject_p.add_argument("--note", required=True, help="what should happen instead")
    reject_p.set_defaults(func=_cmd_cards_mark, status="rejected")
    verify_p = cards_sub.add_parser("verify", help="check confirmed and rejected cards")
    verify_p.add_argument("cards", type=Path)
    verify_p.set_defaults(func=_cmd_cards_verify)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
