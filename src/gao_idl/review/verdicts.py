"""Verdicts a person gave on the review page, and applying them to the cards and the spec.

The page exports a ``Verdicts`` file; ``gao-idl cards import`` applies it. A card verdict names
the ``outcome_hash`` of the behaviour the person saw, and is applied only if the card still does
exactly that: a verdict is about one exact behaviour. Applying never invents a verdict: it copies
what the file says, refuses what no longer fits, and lists every refusal as a problem.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict

from gao_idl.authoring.cards import Card, run_card, same_outcome
from gao_idl.authoring.spec import EnvSpec
from gao_idl.core.errors import GaoError
from gao_idl.core.schemas import FrozenModel
from gao_idl.review.bundle import outcome_hash


class _Strict(FrozenModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# The page builds these objects in page.js (cardVerdict, assumptionVerdict, exportVerdicts):
#   CardVerdict       {id, verdict: "confirmed" | "rejected", note, outcome_hash}
#   AssumptionVerdict {index, text, verdict: "agreed" | "disputed", note}
#   Verdicts          {bundle_version: 1, spec_title, reviewer, cards: [...], assumptions: [...]}
# Keep the two in step; tests/review/test_page_js.py validates the page's export against these.


class CardVerdict(_Strict):
    """A person's verdict on one card, tied to the hash of the outcome they were shown."""

    id: str
    verdict: Literal["confirmed", "rejected"]
    note: str = ""
    outcome_hash: str


class AssumptionVerdict(_Strict):
    """A person's answer to one open choice of the spec (``index`` into ``spec.assumptions``)."""

    index: int
    text: str
    verdict: Literal["agreed", "disputed"]
    note: str = ""


class Verdicts(_Strict):
    """Everything one person decided on one review page."""

    bundle_version: Literal[1]
    spec_title: str
    reviewer: str
    cards: list[CardVerdict]
    assumptions: list[AssumptionVerdict]


def load_verdicts(path: Path) -> Verdicts:
    """Read a verdicts file written by the review page."""
    return Verdicts.model_validate_json(Path(path).read_text(encoding="utf-8"))


def apply_card_verdicts(cards: Sequence[Card], verdicts: Verdicts) -> tuple[list[Card], list[str]]:
    """The card half of ``apply_verdicts``, for when no spec is at hand (the spec title is not
    checked then)."""
    out = list(cards)
    problems: list[str] = []
    index = {c.id: i for i, c in enumerate(cards)}
    ids = [v.id for v in verdicts.cards]
    repeated = {i for i in ids if ids.count(i) > 1}
    for dup in sorted(repeated):
        problems.append(f"card {dup}: more than one verdict in the file; none applied")
    for verdict in verdicts.cards:
        if verdict.id in repeated:
            continue
        if verdict.id not in index:
            problems.append(f"card {verdict.id}: no such card; verdict not applied")
            continue
        card = out[index[verdict.id]]
        if verdict.verdict == "rejected" and not verdict.note.strip():
            problems.append(f"card {verdict.id}: rejected without a note; verdict not applied")
            continue
        try:
            now = run_card(card)
        except (GaoError, ValueError, RuntimeError) as e:
            problems.append(
                f"card {verdict.id}: cannot be played now ({type(e).__name__}: {e}); "
                "verdict not applied"
            )
            continue
        if outcome_hash(now) != verdict.outcome_hash:
            problems.append(
                f"card {verdict.id}: what the card does has changed since the page was made "
                "(stale outcome); verdict not applied"
            )
            continue
        stale = card.expect is not None and not same_outcome(card.expect, now)
        if stale and verdict.verdict == "confirmed":
            problems.append(
                f"card {verdict.id}: its recorded outcome is out of date, so it cannot be "
                "confirmed; verdict not applied"
            )
            continue
        update: dict[str, object] = {
            "status": verdict.verdict,
            "note": verdict.note.strip(),
            "reviewer": verdicts.reviewer,
        }
        if card.expect is None or stale:  # the verdict is about what the card does now
            update["expect"] = now
        out[index[verdict.id]] = card.model_copy(update=update)
    return out, problems


def _apply_assumptions(spec: EnvSpec, verdicts: Verdicts) -> tuple[EnvSpec, list[str]]:
    items = list(spec.assumptions)
    problems: list[str] = []
    for verdict in verdicts.assumptions:
        if not 0 <= verdict.index < len(items) or items[verdict.index].text != verdict.text:
            problems.append(
                f"choice {verdict.index}: the spec has no such choice with that text "
                "(it may have been edited); answer not applied"
            )
            continue
        if verdict.verdict == "agreed":
            items[verdict.index] = items[verdict.index].model_copy(update={"confirmed": True})
    if items == list(spec.assumptions):
        return spec, problems
    return spec.model_copy(update={"assumptions": items}), problems


def apply_verdicts(
    cards: Sequence[Card], spec: EnvSpec, verdicts: Verdicts
) -> tuple[list[Card], EnvSpec, list[str]]:
    """The cards and spec with ``verdicts`` applied, and one line per verdict not applied.

    A card verdict is applied only if the card exists, a rejection has a note, and the hash of
    what the card does now equals the verdict's ``outcome_hash``. Applying sets the card's
    status, note and ``reviewer`` (from the file), and records the outcome the person saw when
    the card had none or had an out-of-date one (which cannot be confirmed). An agreed choice
    is marked confirmed when its index and text both match; a disputed one stays unconfirmed
    (``disputed`` lists them for the author). A file for a spec with another title is refused
    whole. Cards and choices without a verdict are untouched.
    """
    if verdicts.spec_title != spec.title:
        problem = (
            f"the verdicts are for {verdicts.spec_title!r}, the spec is {spec.title!r}; "
            "nothing applied"
        )
        return list(cards), spec, [problem]
    new_cards, problems = apply_card_verdicts(cards, verdicts)
    new_spec, more = _apply_assumptions(spec, verdicts)
    return new_cards, new_spec, problems + more


def disputed(verdicts: Verdicts) -> list[str]:
    """One line per choice the person disagreed with, with their note: work for the author."""
    return [
        f"choice {v.index} disputed: {v.text}" + (f" Note: {v.note}" if v.note else "")
        for v in verdicts.assumptions
        if v.verdict == "disputed"
    ]


def save_spec(path: Path, spec: EnvSpec) -> None:
    """Write ``spec`` as JSON laid out like the shipped specs (only fields that were set)."""
    text = json.dumps(
        spec.model_dump(mode="json", exclude_unset=True), indent=1, ensure_ascii=False
    )
    Path(path).write_text(text + "\n", encoding="utf-8")
