"""Divergence search: find the situations where two implementations of one design disagree.

Given several factories built from the same spec (``skills/second-implementation``), ``diverge``
replays the same scripts on each and reports where they part ways. Each disagreement marks a
point the spec left open; ``to_choice_cards`` turns it into a question a person can answer:
"in this situation, which of these did you mean?"

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import ConfigDict, TypeAdapter

from gao_idl.authoring.cards import CardExpect, _close, _expect, _pairs, _value, same_outcome
from gao_idl.authoring.check import information
from gao_idl.authoring.play import ActionScript, play
from gao_idl.authoring.spec import EnvSpec
from gao_idl.core.errors import GaoError
from gao_idl.core.schemas import FrozenModel

_ROUTING = ("path", "decision_id", "layer", "t")  # where a thing happened, not what was shown


class Divergence(FrozenModel):
    """One situation in which the candidates did not all do the same thing.

    ``groups`` partitions the candidates by behaviour (candidates in one group agreed);
    ``found`` gives each candidate's outcome, or None where the script could not be played on it
    (``problems`` says why). ``kind`` is the first level at which they differ.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["interface", "exit", "utilities", "information"]
    params: dict[str, Any]
    seed: int
    script: ActionScript
    groups: list[list[str]]
    found: dict[str, CardExpect | None]
    problems: dict[str, str] = {}
    first_difference: str


class ChoiceOption(FrozenModel):
    """One behaviour on offer: its label, what happens, and which candidates do it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    outcome: CardExpect | None
    problem: str = ""
    env_refs: list[str]


class ChoiceCard(FrozenModel):
    """A question for the designer: the same situation, and what each reading does with it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    question: str
    params: dict[str, Any]
    seed: int
    script: ActionScript
    options: list[ChoiceOption]
    chosen: str | None = None
    note: str = ""


_CHOICES = TypeAdapter(list[ChoiceCard])


def _shown(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _shown(v) for k, v in value.items() if k not in _ROUTING}
    if isinstance(value, list):
        return [_shown(v) for v in value]
    return value


def _size(script: ActionScript) -> int:
    return sum(len(entries) for entries in script.values())


def _compare(
    refs: Sequence[str], params: dict[str, Any], seed: int, script: ActionScript
) -> Divergence | None:
    outcomes: dict[str, CardExpect | None] = {}
    shown: dict[str, Any] = {}
    problems: dict[str, str] = {}
    for ref in refs:
        try:
            session = play(ref, params, seed, script)
        except (GaoError, ValueError, RuntimeError, TypeError) as e:
            outcomes[ref] = None
            problems[ref] = f"{type(e).__name__}: {e}"
            continue
        outcomes[ref] = _expect(session, script)
        shown[ref] = {a: _shown(information(session, a)) for a in sorted(script)}

    def level(a: str, b: str) -> tuple[str, str] | None:
        """The first level at which candidates ``a`` and ``b`` differ, with a description."""
        x, y = outcomes[a], outcomes[b]
        if x is None or y is None:
            if x is None and y is None:
                return None
            bad = a if x is None else b
            return "interface", f"the scenario cannot be played on one candidate ({problems[bad]})"
        if (x.exit_port, x.failed) != (y.exit_port, y.failed):
            return "exit", "how the run ends"
        if not _close(x.utilities, y.utilities):
            who = next(
                k
                for k in sorted(set(x.utilities) | set(y.utilities))
                if not _close(x.utilities.get(k), y.utilities.get(k))
            )
            return "utilities", f"the payoff of {who}"
        if not same_outcome(x, y) or not _close(shown[a], shown[b]):
            who = next(
                (k for k in sorted(shown[a]) if not _close(shown[a][k], shown[b].get(k))), ""
            )
            return "information", f"what {who or 'a participant'} is shown or told"
        return None

    groups: list[list[str]] = []
    for ref in refs:
        for group in groups:
            if level(group[0], ref) is None:
                group.append(ref)
                break
        else:
            groups.append([ref])
    if len(groups) == 1:
        return None
    order = ["interface", "exit", "utilities", "information"]
    levels = [lv for g in groups[1:] if (lv := level(groups[0][0], g[0])) is not None]
    kind, what = min(levels, key=lambda lv: order.index(lv[0]))
    return Divergence(
        kind=kind,
        params=params,
        seed=seed,
        script=script,
        groups=groups,
        found=outcomes,
        problems=problems,
        first_difference=what,
    )


def diverge(
    env_refs: Sequence[str],
    params: dict[str, Any],
    seeds: Sequence[int],
    scripts: Sequence[ActionScript],
) -> list[Divergence]:
    """Where the candidate factories disagree, over every seed and script.

    Each candidate is built with the same ``params`` and played with the same script. Candidates
    are compared on how the run ends, on final payoffs, and on what each agent was shown and
    told (numbers to within 1e-9; paths, decision ids and layer names are ignored, so candidates
    may be structured differently). A script that cannot be played on a candidate is an
    ``interface`` divergence. One divergence is kept per ``kind`` and grouping of candidates:
    the one with the fewest actions, so a result lists the distinct disagreements, not every
    script that shows one. Generate ``scripts`` with ``fuzz.edge_scripts`` on the first candidate.
    """
    if len(set(env_refs)) != len(env_refs) or len(env_refs) < 2:
        raise ValueError("give at least two distinct candidates")
    best: dict[str, Divergence] = {}
    for seed in seeds:
        for script in scripts:
            found = _compare(env_refs, params, seed, script)
            if found is None:
                continue
            key = json.dumps([found.kind, found.groups])
            if key not in best or _size(script) < _size(best[key].script):
                best[key] = found
    return [best[key] for key in sorted(best)]


def to_choice_cards(divergences: Sequence[Divergence]) -> list[ChoiceCard]:
    """One question per divergence, with one option per group of agreeing candidates."""
    cards: list[ChoiceCard] = []
    for index, d in enumerate(divergences, 1):
        options = [
            ChoiceOption(
                label="ABCDEFGHIJ"[i],
                outcome=d.found[group[0]],
                problem=d.problems.get(group[0], ""),
                env_refs=group,
            )
            for i, group in enumerate(d.groups)
        ]
        cards.append(
            ChoiceCard(
                id=f"choice-{index}",
                question=f"The readings differ in {d.first_difference}. Which did you mean?",
                params=d.params,
                seed=d.seed,
                script=d.script,
                options=options,
            )
        )
    return cards


def render_choice(card: ChoiceCard, spec: EnvSpec | None = None) -> str:
    """The question in plain language: the situation, what all options share, where they differ.

    Options are labelled A, B, C with nothing that says which implementation is which. Facts
    that hold under every option are listed once; each option then shows only what is particular
    to it. With ``spec``, only the parameters that differ from the design's are listed.
    """
    out = [f"### {card.question}", "", f"Choice `{card.id}`.", "", "**The situation.**", ""]
    shown = sorted(card.params)
    if spec is not None:
        shown = [k for k in shown if k not in spec.params or spec.params[k] != card.params[k]]
        out.append("As in the design." if not shown else "As in the design, except:")
    out += [f"- {key}: {_value(card.params[key])}" for key in shown]
    out += ["", "**What the participants do, in order.**", ""]
    for agent in sorted(card.script):
        steps = [
            "does not act" if action is None else f"chooses {_pairs(action)}"
            for action in card.script[agent]
        ]
        out.append(f"- {agent}: {'; then '.join(steps)}")
    out.append("")
    facts = [_facts(option) for option in card.options]
    shared = [line for line in facts[0] if all(line in other for other in facts[1:])]
    if shared:
        out += ["**The same in every option.**", ""]
        out += [f"- {line}" for line in shared]
        out.append("")
    for option, lines in zip(card.options, facts, strict=True):
        out += [f"**Option {option.label}.**", ""]
        out += [f"- {line}" for line in lines if line not in shared]
        out.append("")
    answer = f"Chosen: option {card.chosen}." if card.chosen else "Not yet answered."
    out += [answer + (f" Note: {card.note}" if card.note else ""), ""]
    return "\n".join(out)


def _facts(option: ChoiceOption) -> list[str]:
    """What happens under one option, one plain sentence per fact."""
    result = option.outcome
    if result is None:
        return [f"This reading cannot play the situation at all: {option.problem}"]
    ending = "the run fails" if result.failed else f"the run ends through `{result.exit_port}`"
    lines = [f"How it ends: {ending}."]
    for agent in sorted(result.utilities):
        lines.append(f"{agent} ends with a payoff of {_value(result.utilities[agent])}.")
    for agent in sorted(result.reports):
        for i, report in enumerate(result.reports[agent], 1):
            lines.append(f"{agent} is told (message {i}): {_pairs(report)}")
    return lines


def load_choices(path: Path) -> list[ChoiceCard]:
    """Read choice cards from a JSON file."""
    return _CHOICES.validate_json(Path(path).read_text())


def save_choices(path: Path, cards: Sequence[ChoiceCard]) -> None:
    """Write choice cards as JSON (stable key order, one trailing newline)."""
    data = [c.model_dump(mode="json") for c in cards]
    Path(path).write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
