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


def _size(value: Any) -> str:
    n = len(value)
    if n == 0:
        return "none"
    return f"{n} {'entry' if n == 1 else 'entries'}"


def summarize(payload: dict[str, Any], limit: int = 60) -> str:
    """``payload`` as ``name = value`` pairs for a label, at most about ``limit`` characters.

    Scalar fields come first in the payload's key order, then nested ones shown by size
    (``projects: 4 entries``, an empty one as ``projects: none``). Numbers are shown as
    ``render_card`` shows them. The text is cut on a pair boundary and ends with ``, …`` when
    something was left out; a pair is cut inside only when the first pair alone is longer than
    ``limit``. The first pair is always shown, so with ``, …`` the text may run a little over.
    """
    scalars = [k for k, v in payload.items() if not isinstance(v, dict | list)]
    nested = [k for k, v in payload.items() if isinstance(v, dict | list)]
    pairs = [f"{k} = {_value(payload[k])}" for k in scalars]
    pairs += [f"{k}: {_size(payload[k])}" for k in nested]
    if not pairs:
        return "nothing"
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
