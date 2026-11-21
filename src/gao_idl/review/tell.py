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
from typing import Any

from gao_idl.authoring.cards import StepView, Story, Told, _value
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
WRITTEN_OUT = 3  # a choice or announcement with more fields than this is counted, not listed


def words(name: str) -> str:
    """A field or step name as words: ``winning_payment`` reads ``winning payment``."""
    return name.replace("_", " ").strip()


def names(people: Sequence[str]) -> str:
    """Names in a sentence: ``a``, ``a and b``, ``a, b and c``."""
    people = list(people)
    if len(people) <= 1:
        return "".join(people)
    return ", ".join(people[:-1]) + " and " + people[-1]


def _short(payload: dict[str, Any]) -> str | None:
    """``payload`` as ``field value, field value``; None when it is too long or nested to say."""
    if not payload or len(payload) > WRITTEN_OUT or not all(_plain(v) for v in payload.values()):
        return None
    return ", ".join(f"{words(k)} {_value(payload[k])}" for k in sorted(payload))


def _parts(payload: dict[str, Any]) -> str:
    n = len(payload)
    return "1 part" if n == 1 else f"{n} parts"


def _move_sentences(step: StepView) -> list[str]:
    out: list[str] = []
    groups: dict[str, tuple[list[str], dict[str, Any]]] = {}
    for move in sorted(step.moves, key=lambda m: role_sort_key(m.agent)):
        if move.kind == "dropped":
            out.append(f"{move.agent} did not answer and was left out of this step.")
            continue
        action = move.action or {}
        if move.kind == "defaulted":
            said = _short(action)
            used = f": {said}" if said else ""
            out.append(f"{move.agent} did not answer, so the standard choice was used{used}.")
            continue
        key = _same(action)
        if key in groups:
            groups[key][0].append(move.agent)
        else:
            groups[key] = ([move.agent], action)
    chose: list[str] = []
    longer: list[str] = []
    for who, action in groups.values():
        said = _short(action)
        if said is None:
            longer += who
        else:
            chose.append(f"{names(who)} chose {said}")
    lines = ["; ".join(chose) + "."] if chose else []
    if longer:
        verb = "each made a choice" if len(longer) > 1 else "made a choice"
        lines.append(f"{names(longer)} {verb} with several parts (listed in the full record).")
    return lines + out


def _told_sentences(step: StepView, everyone: Sequence[str]) -> list[str]:
    out: list[str] = []
    private: list[str] = []
    for row in split_told(step.told):
        if row.to:
            private += row.to
            continue
        to = addressed(row, step.told, everyone)
        who = "Everyone" if to == "everyone" else names(to.split(", "))
        simple = {k: row.payload[k] for k in sorted(row.payload) if _plain(row.payload[k])}
        rest = len(row.payload) - len(simple)
        said = ", ".join(f"{words(k)} {_value(v)}" for k, v in list(simple.items())[:6])
        more = len(simple) - min(len(simple), 6) + rest
        if said and more:
            said += f", and {more} more {'item' if more == 1 else 'items'} (in the full record)"
        elif not said:
            said = f"{more} {'item' if more == 1 else 'items'} listed in the full record"
        verb = "were" if who != "Everyone" and " and " in who else "was"
        out.append(f"{who} {verb} told: {said}.")
    if private:
        if out:
            out.append("Each was also told their own result (in the full record).")
        else:
            who = names(sorted(set(private), key=role_sort_key))
            out.append(f"{who}: each was told their own result (in the full record).")
    return out


class PlainStep(FrozenModel):
    """One step of a run in plain sentences: where, the rule as the spec words it, what happened."""

    where: str
    rule: str
    happened: list[str]


def plain_steps(story: Story) -> list[PlainStep]:
    """The run as plain sentences, one entry per step in which somebody chose or was told.

    Every name and number comes from ``story`` (a run of the built environment); ``rule`` is the
    step's description from the spec. Steps where nobody acts and nothing is announced (a round
    counter) are left out. Long or nested choices are counted here and listed in the full record.
    """
    everyone = participants(story)
    out: list[PlainStep] = []
    for step in story.steps:
        happened = _move_sentences(step) + _told_sentences(step, everyone)
        if step.stopped is not None:
            happened.append(f"The run stopped here: {step.stopped}")
        if not happened:
            continue
        label = words(step.layer) or "step"
        where = f"{step.place}: {label}" if step.place else label
        out.append(
            PlainStep(where=where[:1].upper() + where[1:], rule=step.description, happened=happened)
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
