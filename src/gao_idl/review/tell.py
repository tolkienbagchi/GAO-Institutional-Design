"""What people are told, split into the part everyone shares and the part that is their own.

A step often tells every participant the same public facts plus a few of their own. Shown once
per recipient, the public part repeats nine times in a carbon-market card. ``split_told`` pulls
the fields that every recipient got with the same value into one row addressed to everyone, and
groups recipients whose remaining fields are identical. ``summarize`` writes a payload as a short
label (nested values by size, never as JSON). Both are pure functions of their arguments.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

import json
from collections.abc import Sequence
from typing import Any, Literal

from gao_idl.authoring.cards import Story, Told, _value
from gao_idl.core.ids import role_sort_key
from gao_idl.core.schemas import FrozenModel


class ToldRow(FrozenModel):
    """One line of what a step told: ``to`` names the recipients; ``[]`` means everyone told."""

    to: list[str]
    payload: dict[str, Any]


def _same(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def split_told(told: Sequence[Told]) -> list[ToldRow]:
    """One step's reports as rows: the fields everyone shares first, then each one's own.

    With two or more reports, a field present in every payload with the same value goes to one
    row with ``to == []`` (in the first payload's key order). Each recipient then gets a row of
    its remaining fields; one with nothing left gets no row; recipients whose remaining fields
    are identical share a row, listed in natural order. Rows follow their first recipient's
    natural order. A single report is returned as one own row.
    """
    if not told:
        return []
    shared: dict[str, Any] = {}
    if len(told) >= 2:
        first = told[0].payload
        for key, value in first.items():
            if all(key in t.payload and _same(t.payload[key]) == _same(value) for t in told[1:]):
                shared[key] = value
    groups: dict[str, tuple[list[str], dict[str, Any]]] = {}
    for t in sorted(told, key=lambda t: role_sort_key(t.agent)):
        rest = {k: v for k, v in t.payload.items() if k not in shared}
        if not rest:
            continue
        key = _same(rest)
        if key in groups:
            groups[key][0].append(t.agent)
        else:
            groups[key] = ([t.agent], rest)
    rows = [ToldRow(to=[], payload=shared)] if shared else []
    rows += [ToldRow(to=to, payload=payload) for to, payload in groups.values()]
    return rows


INLINE = 4  # a nested value with at most this many plain fields is written out


def _plain(value: Any) -> bool:
    return not isinstance(value, dict | list)


def _inline(value: dict[str, Any]) -> str:
    return "(" + ", ".join(f"{k} = {_value(v)}" for k, v in value.items()) + ")"


def _nested(name: str, value: Any, tables: bool) -> list[str]:
    """The pairs for one nested field: written out when small, by size otherwise.

    With ``tables`` a small table (an order per project) is one pair per row, so that a cut
    drops whole rows.
    """
    if not value:
        return [f"{name}: none"]
    if isinstance(value, dict):
        if len(value) <= INLINE and all(_plain(v) for v in value.values()):
            return [f"{name}: {_inline(value)}"]
        rows = all(
            v is None
            or (isinstance(v, dict) and 0 < len(v) <= INLINE and all(map(_plain, v.values())))
            for v in value.values()
        )
        if tables and rows and len(value) <= 2 * INLINE:
            return [f"{name}.{k}: {'none' if v is None else _inline(v)}" for k, v in value.items()]
    n = len(value)
    return [f"{name}: {n} {'entry' if n == 1 else 'entries'}"]


def summarize(payload: dict[str, Any], limit: int = 60) -> str:
    """``payload`` as ``name = value`` pairs for a label, at most about ``limit`` characters.

    Scalar fields come first in the payload's key order, then nested ones. A nested value with at
    most four plain fields is written out (``own_fills: (s0 = 3, s1 = 2)``), and so is a small
    table of such rows when all of it fits (``bids.a: (price = 8.5, qty = 3)``); anything else is
    shown by size (``projects: 4 entries``), an empty one as ``projects: none``, and never as
    JSON. Numbers are shown as
    ``render_card`` shows them. The text is cut on a pair boundary and ends with ``, …`` when
    something was left out; a pair is cut inside only when the first pair alone is longer than
    ``limit``. The first pair is always shown, so with ``, …`` the text may run a little over.
    """
    scalars = [k for k, v in payload.items() if _plain(v)]
    nested = [k for k, v in payload.items() if not _plain(v)]
    plain = [f"{k} = {_value(payload[k])}" for k in scalars]
    pairs = plain + [p for k in nested for p in _nested(k, payload[k], tables=True)]
    if not pairs:
        return "nothing"
    full = ", ".join(pairs)
    if len(full) <= limit:
        return full
    pairs = plain + [p for k in nested for p in _nested(k, payload[k], tables=False)]
    full = ", ".join(pairs)
    if len(full) <= limit:
        return full
    if len(pairs[0]) > limit:
        return pairs[0][: max(1, limit - 1)] + "…"
    kept = [pairs[0]]
    for pair in pairs[1:]:
        if len(", ".join([*kept, pair])) + len(", …") > limit:
            break
        kept.append(pair)
    return ", ".join(kept) + ", …"


def addressed(row: ToldRow, told: Sequence[Told], everyone: Sequence[str]) -> str:
    """Who a row of ``split_told(told)`` went to, in words: names, or ``everyone``.

    An everyone-row (``to == []``) went to every recipient of the step; it reads ``everyone`` only
    when those are all of ``everyone`` (the run's participants), otherwise it names them, so a
    report to one pair of a parallel step is not described as going to all.
    """
    if row.to:
        return ", ".join(row.to)
    here = sorted({t.agent for t in told}, key=role_sort_key)
    return "everyone" if set(here) >= set(everyone) else ", ".join(here)


def participants(story: Story) -> list[str]:
    """Everyone who acts, is told something or is paid in ``story``, in natural order."""
    people = set(story.utilities)
    for step in story.steps:
        people |= {m.agent for m in step.moves} | {t.agent for t in step.told}
    return sorted(people, key=role_sort_key)


# ------------------------------------------------------------------ the run in plain sentences

MANY_ENDINGS = 4  # with more distinct payoffs than this the ending points to the list


def words(name: str) -> str:
    """A field or step name as words: ``winning_payment`` reads ``winning payment``."""
    return name.replace("_", " ").strip()


def names(people: Sequence[str]) -> str:
    """Names in a sentence: ``a``, ``a and b``, ``a, b and c``."""
    people = list(people)
    if len(people) <= 1:
        return "".join(people)
    return ", ".join(people[:-1]) + " and " + people[-1]


class Tile(FrozenModel):
    """One participant in one step: what it did and what only it (or its group) was told."""

    agent: str
    kind: Literal["chose", "defaulted", "dropped", "told"]
    action: dict[str, Any] | None
    told: dict[str, Any]


class Announcement(FrozenModel):
    """Something several participants of a step were told alike; ``to`` is ``everyone`` or names."""

    to: str
    payload: dict[str, Any]


class RunStep(FrozenModel):
    """One step of a run for the run view: where, the rule as the spec words it, who did what."""

    where: str
    rule: str
    tiles: list[Tile]
    announced: list[Announcement]
    stopped: str | None


def run_steps(story: Story) -> list[RunStep]:
    """The run by participant: one entry per step in which somebody chose or was told.

    Every name and value comes from ``story`` (a run of the built environment); ``rule`` is the
    step's description from the spec. Steps where nobody acts and nothing is told (a round
    counter) are left out. A tile holds a participant's choice and the fields only it was told;
    fields every recipient of the step was told alike are one announcement. Tiles are in natural
    order of the participants' names, so the result does not depend on the order of events.
    """
    everyone = participants(story)
    out: list[RunStep] = []
    for step in story.steps:
        own: dict[str, dict[str, Any]] = {}
        announced: list[Announcement] = []
        for row in split_told(step.told):
            if row.to:
                for agent in row.to:
                    own[agent] = row.payload
            else:
                to = addressed(row, step.told, everyone)
                announced.append(Announcement(to=to, payload=row.payload))
        moves = {m.agent: m for m in step.moves}
        tiles = []
        for agent in sorted(set(moves) | set(own), key=role_sort_key):
            move = moves.get(agent)
            tiles.append(
                Tile(
                    agent=agent,
                    kind=move.kind if move else "told",
                    action=move.action if move else None,
                    told=own.get(agent, {}),
                )
            )
        if not tiles and not announced and step.stopped is None:
            continue
        label = words(step.layer) or "step"
        where = f"{step.place}: {label}" if step.place else label
        out.append(
            RunStep(
                where=where[:1].upper() + where[1:],
                rule=step.description,
                tiles=tiles,
                announced=announced,
                stopped=step.stopped,
            )
        )
    return out


def plain_ending(story: Story) -> list[str]:
    """How the run ends in plain sentences: whether it finished, and what each one ends with."""
    groups: dict[str, list[str]] = {}
    for agent in sorted(story.utilities, key=role_sort_key):
        groups.setdefault(_value(story.utilities[agent]), []).append(agent)
    got = [
        f"{names(who)} {'each have' if len(who) > 1 else 'has'} {value}"
        for value, who in groups.items()
    ]
    if story.failed:
        first = "The run stopped before it finished."
    elif story.exit_port in (None, "done"):
        first = "The run finished."
    else:
        first = f"The run finished ({words(story.exit_port)})."
    if not got:
        return [first]
    if len(got) > MANY_ENDINGS:
        return [first, "What each one ends with is listed under Payoffs."]
    return [first, "At the end " + "; ".join(got) + "."]
