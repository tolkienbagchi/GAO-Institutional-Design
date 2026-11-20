"""Scenario cards: worked examples in plain language that a person confirms or rejects.

A card is one concrete situation (parameters, seed, who does what) and what the environment
does with it (how it ends, what each participant is told, each payoff). A person reads the card
and says whether that is what they meant. A confirmed card is a regression test: the
environment must keep doing exactly that. A rejected card is one too: the environment must stop.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import ConfigDict, TypeAdapter

from gao_idl.authoring.play import ActionScript, play
from gao_idl.authoring.spec import EnvSpec
from gao_idl.core import events as ev
from gao_idl.core.errors import GaoError
from gao_idl.core.ids import AgentId
from gao_idl.core.paths import Branch, LoopIter
from gao_idl.core.schemas import FrozenModel
from gao_idl.runtime.session import Session


class CardExpect(FrozenModel):
    """What a run did: how it ended, each agent's total payoff, and each agent's reports."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    exit_port: str | None
    failed: bool
    utilities: dict[str, float]
    reports: dict[str, list[dict[str, Any]]]


class Card(FrozenModel):
    """One scenario. ``why`` says which readings of the design this case tells apart."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    title: str
    why: str
    env_ref: str
    params: dict[str, Any]
    seed: int
    script: ActionScript
    expect: CardExpect | None = None
    status: Literal["proposed", "confirmed", "rejected"] = "proposed"
    note: str = ""


_CARDS = TypeAdapter(list[Card])


def _card_agents(session: Session, script: ActionScript) -> set[str]:
    """The agents a card reports on: those in its script, paid, or told something."""
    agents = set(script) | set(session.ledger.snapshot()["increments"])
    for event in session.trace:
        if isinstance(event, ev.ReportDelivered):
            agents.add(str(event.report.agent_id))
    return agents


def _expect(session: Session, script: ActionScript) -> CardExpect:
    agents = _card_agents(session, script)
    result = session.result
    return CardExpect(
        exit_port=None if result is None else str(result.port),
        failed=session.failed,
        utilities={a: session.ledger.utility(AgentId(a)) for a in sorted(agents)},
        reports={
            a: [r.payload for r in session.reports_for(AgentId(a))]
            for a in sorted(agents)
            if session.reports_for(AgentId(a))
        },
    )


TOLERANCE = 1e-9


def _close(a: Any, b: Any) -> bool:
    """Equal as JSON, with numbers compared to within ``TOLERANCE`` (absolute and relative)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return math.isclose(a, b, rel_tol=TOLERANCE, abs_tol=TOLERANCE)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b, strict=True))
    return bool(a == b)


def same_outcome(a: CardExpect, b: CardExpect) -> bool:
    """Whether two outcomes agree, with numbers compared to within ``TOLERANCE``.

    Exact equality would be wrong here: a recorded outcome must hold on every supported Python,
    and the last bits of a float sum differ between them (``sum`` is compensated from 3.12 on).
    """
    return _close(a.model_dump(mode="json"), b.model_dump(mode="json"))


def run_card(card: Card) -> CardExpect:
    """Play the card's scenario and return what happened."""
    return _expect(play(card.env_ref, card.params, card.seed, card.script), card.script)


def freeze(card: Card) -> Card:
    """The card with ``expect`` filled from a run now; its status is unchanged."""
    return card.model_copy(update={"expect": run_card(card)})


def verify_cards(cards: Sequence[Card]) -> list[str]:
    """Problems, one line each; empty means every confirmed and rejected card is honoured.

    A confirmed card must reproduce its ``expect`` (numbers to within ``TOLERANCE``, see
    ``same_outcome``). A rejected card must not: the
    behaviour a person rejected has to be gone (a run that now raises counts as gone). Proposed
    cards are skipped. Ids must be unique.
    """
    problems: list[str] = []
    ids = [c.id for c in cards]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"card id {dup!r} is used more than once")
    for card in cards:
        if card.status == "proposed":
            continue
        if card.expect is None:
            problems.append(f"{card.id}: {card.status} but has no recorded outcome")
            continue
        try:
            now = run_card(card)
        except (GaoError, ValueError, RuntimeError) as e:
            if card.status == "confirmed":
                problems.append(f"{card.id}: confirmed, but the run raised {type(e).__name__}: {e}")
            continue
        if card.status == "confirmed" and not same_outcome(now, card.expect):
            problems.append(
                f"{card.id}: confirmed outcome is no longer reproduced ({_diff(card.expect, now)})"
            )
        if card.status == "rejected" and same_outcome(now, card.expect):
            problems.append(f"{card.id}: rejected outcome is still what the environment does")
    return problems


def _diff(a: CardExpect, b: CardExpect | None) -> str:
    if b is None:
        return "no outcome"
    x, y = a.model_dump(), b.model_dump()
    for key in ("failed", "exit_port", "utilities", "reports"):
        if not _close(x[key], y[key]):
            if isinstance(x[key], dict):
                inner = next(
                    k
                    for k in sorted(set(x[key]) | set(y[key]))
                    if not _close(x[key].get(k), y[key].get(k))
                )
                return (
                    f"{key} of {inner}: recorded {x[key].get(inner)!r}, now {y[key].get(inner)!r}"
                )
            return f"{key}: recorded {x[key]!r}, now {y[key]!r}"
    return "no difference"


def load_cards(path: Path) -> list[Card]:
    """Read a list of cards from a JSON file."""
    return _CARDS.validate_json(Path(path).read_text())


def save_cards(path: Path, cards: Sequence[Card]) -> None:
    """Write cards as JSON (stable key order, one trailing newline)."""
    data = [c.model_dump(mode="json") for c in cards]
    Path(path).write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")


# ------------------------------------------------------------------ rendering


def _tidy(v: Any) -> Any:
    """Floats rounded to 6 decimals (whole ones as ints), recursively, for display only."""
    if isinstance(v, bool):
        return v
    if isinstance(v, float):
        r = round(v, 6)
        return int(r) if r == int(r) and abs(r) < 1e15 else r
    if isinstance(v, dict):
        return {k: _tidy(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_tidy(x) for x in v]
    return v


def _value(v: Any) -> str:
    v = _tidy(v)
    if isinstance(v, bool) or v is None:
        return {True: "yes", False: "no", None: "none"}[v]
    if isinstance(v, str):
        return v
    if isinstance(v, int | float):
        return str(v)
    return json.dumps(v, sort_keys=True)


def _pairs(d: dict[str, Any]) -> str:
    return ", ".join(f"{k} = {_value(d[k])}" for k in sorted(d)) if d else "nothing"


def _key(path: Sequence[Any]) -> str:
    return "/".join(repr(e) for e in path)


def _where(path: Sequence[Any]) -> str:
    """Rounds and branches of a path in words: ``"round 2, pair_0"``; ``""`` at the top level."""
    parts: list[str] = []
    for e in path:
        if isinstance(e, LoopIter):
            parts.append(f"round {e.i + 1}")
        elif isinstance(e, Branch):
            parts.append(e.name)
    return ", ".join(parts)


# ------------------------------------------------------------------ the story as data


class Move(FrozenModel):
    """What one participant did in a step: chose an action, had the default used, or was left out.

    ``action`` is the action chosen or used by default; None when the participant was dropped.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent: str
    kind: Literal["chose", "defaulted", "dropped"]
    action: dict[str, Any] | None


class Told(FrozenModel):
    """One report: what ``agent`` was told after a step."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent: str
    payload: dict[str, Any]


class StepView(FrozenModel):
    """One layer instance of a run, in words and data.

    ``place`` names the round and branch (``"round 2, pair_0"``, empty at the top level),
    ``layer`` the step, ``description`` the spec's description of it (empty without a spec).
    ``stopped`` is the plain-language reason when the run stops (or a repetition is cut off) in
    this step. A view with an empty ``layer`` holds an event of the rules outside any step, such
    as a repetition cut off at its limit.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    place: str
    layer: str
    description: str
    moves: list[Move]
    told: list[Told]
    stopped: str | None


class Story(FrozenModel):
    """A run told step by step: one ``StepView`` per layer instance in order of entry, the total
    payoff of every agent, and how the run ended."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    steps: list[StepView]
    utilities: dict[str, float]
    exit_port: str | None
    failed: bool


def _story(session: Session, spec: EnvSpec | None, agents: set[str]) -> tuple[Story, list[int]]:
    """The story of ``session`` and, for each report in trace order, the index of its step.

    The second value lets ``render_card`` list what everyone was told in the order it was
    delivered, which under ``Par`` need not be the order the steps were entered.
    """
    requests: dict[str, ev.DecisionRequested] = {}
    layers: dict[str, str] = {}  # a layer's name is not in its path under a Par branch
    silent: set[str] = set()
    for logged in session.input_log:
        if isinstance(logged, ev.TimedOut):
            silent.add(str(logged.decision_id))
    for event in session.trace:
        if isinstance(event, ev.DecisionRequested):
            layers.setdefault(_key(event.path), event.request.layer)
            requests[str(event.request.decision_id)] = event
        elif isinstance(event, ev.LayerCompleted):
            layers.setdefault(_key(event.path), event.layer)
    order: list[str] = []  # one step per layer instance, in order of entry
    parts: dict[str, dict[str, Any]] = {}
    told_at: list[int] = []

    def part(key: str, path: Sequence[Any], layer: str) -> dict[str, Any]:
        if key not in parts:
            order.append(key)
            step = spec.step(layer) if spec is not None and layer else None
            parts[key] = {
                "place": _where(path),
                "layer": layer,
                "description": step.description if step is not None else "",
                "moves": [],
                "told": [],
                "stopped": None,
            }
        return parts[key]

    def stop(view: dict[str, Any], text: str) -> None:
        view["stopped"] = text if view["stopped"] is None else f"{view['stopped']} {text}"

    for event in session.trace:
        key = _key(event.path)
        if isinstance(event, ev.LayerEntered):
            part(key, event.path, layers.get(key, ""))
            continue
        if isinstance(event, ev.ReportDelivered):
            report = event.report
            rkey = _key(report.path)
            view = part(rkey, report.path, report.layer)
            view["told"].append(Told(agent=str(report.agent_id), payload=dict(report.payload)))
            told_at.append(order.index(rkey))
            continue
        view = part(key, event.path, layers.get(key, ""))
        if isinstance(event, ev.ActionAccepted):
            view["moves"].append(
                Move(agent=str(event.agent_id), kind="chose", action=dict(event.action))
            )
        elif isinstance(event, ev.ActionDefaulted):
            who = str(requests[str(event.decision_id)].request.agent_id)
            view["moves"].append(Move(agent=who, kind="defaulted", action=dict(event.action)))
        elif isinstance(event, ev.RoleDropped):
            who = str(requests[str(event.decision_id)].request.agent_id)
            view["moves"].append(Move(agent=who, kind="dropped", action=None))
        elif isinstance(event, ev.Failed):
            quiet = sorted(
                str(r.request.agent_id)
                for d, r in requests.items()
                if d in silent and _key(r.path) == key
            )
            if quiet:
                stop(
                    view,
                    f"{', '.join(quiet)} did not act. Under the rules of this step the "
                    "whole run stops here.",
                )
            else:
                stop(view, f"The run stops here: {event.reason}.")
        elif isinstance(event, ev.Truncated):
            stop(view, f"The repetition is cut off: {event.reason}.")
    # keep layer instances, and places outside them only where the rules did something visible
    kept = [k for k in order if parts[k]["layer"] or parts[k]["told"] or parts[k]["stopped"]]
    index = {k: i for i, k in enumerate(kept)}
    told_at = [index[order[i]] for i in told_at]
    result = session.result
    story = Story(
        steps=[StepView(**parts[k]) for k in kept],
        utilities={a: session.ledger.utility(AgentId(a)) for a in sorted(agents)},
        exit_port=None if result is None else str(result.port),
        failed=session.failed,
    )
    return story, told_at


def _agents(session: Session) -> set[str]:
    agents = set(session.ledger.snapshot()["increments"])
    for event in session.trace:
        if isinstance(event, ev.ReportDelivered):
            agents.add(str(event.report.agent_id))
        elif isinstance(event, ev.DecisionRequested):
            agents.add(str(event.request.agent_id))
    return agents


def tell_session(session: Session, spec: EnvSpec | None = None) -> Story:
    """The story of a finished (or stopped) session: one ``StepView`` per layer instance.

    Payoffs are listed for every agent that was asked to decide, told something or paid. Step
    descriptions come from ``spec`` when given. A pure function of the session's trace.
    """
    return _story(session, spec, _agents(session))[0]


def tell_card(card: Card, spec: EnvSpec | None = None) -> Story:
    """Play the card's scenario and tell it; payoffs are listed for every agent of the script too.

    Raises what ``play`` raises when the scenario cannot be played.
    """
    session = play(card.env_ref, card.params, card.seed, card.script)
    return _story(session, spec, _card_agents(session, card.script))[0]


# ------------------------------------------------------------------ the story in words


def render_story(story: Story, told_at: Sequence[int] | None = None) -> list[str]:
    """The markdown lines of "What happens", "What each participant is told" and "Payoffs"."""
    out = ["**What happens.**", ""]
    for step in story.steps:
        if step.layer:
            where = f"{step.place}, step {step.layer}" if step.place else f"step {step.layer}"
            text = f" {step.description}" if step.description else ""
            out.append(f"- *{where.capitalize()}.*{text}")
        for move in step.moves:
            if move.kind == "chose":
                out.append(f"    - {move.agent} chooses {_pairs(move.action or {})}.")
            elif move.kind == "defaulted":
                out.append(
                    f"    - {move.agent} does not act; the default is used instead: "
                    f"{_pairs(move.action or {})}."
                )
            else:
                out.append(f"    - {move.agent} does not act and is left out of this step.")
        if step.stopped is not None:
            out.append(f"    - {step.stopped}")
    out.append("")
    reports = [(i, t) for i, step in enumerate(story.steps) for t in step.told]
    if told_at is not None:  # restore delivery order (Par branches interleave)
        queues = {i: [t for j, t in reports if j == i] for i in range(len(story.steps))}
        reports = [(i, queues[i].pop(0)) for i in told_at]
    told: list[str] = []
    for i, t in reports:
        step = story.steps[i]
        where = f"{step.place}, step {step.layer}" if step.place else f"step {step.layer}"
        told.append(f"- {t.agent}, after {where}: {_pairs(t.payload)}")
    out += ["**What each participant is told.**", ""]
    out += told or ["- Nobody is told anything."]
    out.append("")
    out += ["**Payoffs in total.**", ""]
    out += [f"- {agent}: {_value(story.utilities[agent])}" for agent in sorted(story.utilities)]
    out.append("")
    return out


def render_card(card: Card, spec: EnvSpec | None = None) -> str:
    """The card as markdown a non-programmer can check: setup, who did what, what each was told.

    The scenario is run again and told with ``tell_card``'s story. If the card has a recorded
    outcome that the run no longer matches, the card says so at the top. Step descriptions come
    from ``spec`` when given.
    """
    out = [f"### {card.title}", "", f"Card `{card.id}` ({card.status}). {card.why}", ""]
    out += ["**The situation.**", ""]
    out += _situation(card, spec)
    out.append("")
    try:
        session = play(card.env_ref, card.params, card.seed, card.script)
    except (GaoError, ValueError, RuntimeError) as e:
        out += [f"**This scenario cannot be played:** {type(e).__name__}: {e}", ""]
        return "\n".join(out)
    now = _expect(session, card.script)
    if card.expect is not None and not same_outcome(card.expect, now):
        out += [
            "**Warning: the recorded outcome is not what happens now** "
            f"({_diff(card.expect, now)}). What follows is what happens now.",
            "",
        ]
    story, told_at = _story(session, spec, _card_agents(session, card.script))
    out += render_story(story, told_at)
    ending = "the run fails" if story.failed else f"the run ends through `{story.exit_port}`"
    out += [f"**How it ends:** {ending}.", ""]
    if card.note:
        out += [f"Note: {card.note}", ""]
    return "\n".join(out)


def _situation(card: Card, spec: EnvSpec | None = None) -> list[str]:
    """The card's parameters as markdown lines; with a matching spec, only what differs from it."""
    out: list[str] = []
    shown = sorted(card.params)
    if spec is not None and spec.env_ref == card.env_ref:
        shown = [k for k in shown if k not in spec.params or spec.params[k] != card.params[k]]
        dropped = sorted(k for k in spec.params if k not in card.params)
        if shown or dropped:
            out.append("As in the design, except:")
            out.append("")
        else:
            out.append("Exactly the parameters of the design.")
        out += [f"- {key}: left at its default" for key in dropped]
    out += [f"- {key}: {_value(card.params[key])}" for key in shown]
    return out
